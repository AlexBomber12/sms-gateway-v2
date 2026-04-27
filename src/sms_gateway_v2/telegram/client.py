from __future__ import annotations

import asyncio
import math
import time
from types import TracebackType
from typing import cast

import httpx
import structlog

from sms_gateway_v2.telegram.exceptions import (
    TelegramAuthError,
    TelegramError,
    TelegramRateLimited,
    TelegramTransportError,
)
from sms_gateway_v2.telegram.models import TelegramMessage

logger = structlog.get_logger(__name__)

RETRYABLE_HTTP_STATUS = 500
MAX_RETRY_DELAY_SECONDS = 30.0


class TelegramClient:
    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        api_base: str = "https://api.telegram.org",
        timeout_seconds: float = 10.0,
        max_retries: int = 3,
    ) -> None:
        if bot_token == "":
            msg = "bot_token must not be empty"
            raise ValueError(msg)
        if chat_id == "":
            msg = "chat_id must not be empty"
            raise ValueError(msg)
        if max_retries < 1:
            msg = "max_retries must be greater than or equal to 1"
            raise ValueError(msg)

        self.bot_token = bot_token
        self.chat_id = chat_id
        self.api_base = api_base.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> TelegramClient:
        self._client = httpx.AsyncClient(
            timeout=self.timeout_seconds,
            base_url=f"{self.api_base}/bot{self.bot_token}",
        )
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._client is not None:
            await self._client.aclose()
        self._client = None

    @property
    def attempts_remaining(self) -> int:
        return self._max_retries

    async def send_message(self, message: TelegramMessage) -> None:
        client = self._client_or_raise()
        for attempt in range(1, self.attempts_remaining + 1):
            started_at = time.monotonic()
            logger.info(
                "telegram_send_attempt",
                attempt=attempt,
                status_code=None,
                duration_ms=0,
            )
            try:
                payload = {"chat_id": self.chat_id, "text": message.text}
                if message.parse_mode is not None:
                    payload["parse_mode"] = message.parse_mode
                response = await client.post(
                    "sendMessage",
                    json=payload,
                )
                self._handle_response(
                    response, attempt=attempt, duration_ms=_elapsed_ms(started_at)
                )
            except TelegramAuthError:
                raise
            except TelegramRateLimited as exc:
                await self._retry_or_raise(
                    exc,
                    attempt=attempt,
                    status_code=429,
                    duration_ms=_elapsed_ms(started_at),
                    delay_seconds=exc.retry_after,
                )
            except TelegramTransportError as exc:
                await self._retry_or_raise(
                    exc,
                    attempt=attempt,
                    status_code=None,
                    duration_ms=_elapsed_ms(started_at),
                    delay_seconds=_retry_delay(attempt),
                )
            except httpx.TransportError as exc:
                transport_error = TelegramTransportError(str(exc))
                logger.warning(
                    "telegram_send_transport_error",
                    attempt=attempt,
                    status_code=None,
                    duration_ms=_elapsed_ms(started_at),
                    error=str(exc),
                )
                await self._retry_or_raise(
                    transport_error,
                    attempt=attempt,
                    status_code=None,
                    duration_ms=_elapsed_ms(started_at),
                    delay_seconds=_retry_delay(attempt),
                )
            else:
                return

    def _client_or_raise(self) -> httpx.AsyncClient:
        if self._client is None:
            msg = "Telegram client is not open"
            raise TelegramTransportError(msg)
        return self._client

    def _handle_response(self, response: httpx.Response, *, attempt: int, duration_ms: int) -> None:
        status_code = response.status_code
        if status_code == 200:
            payload = _response_json(response)
            if payload is not None and payload.get("ok") is True:
                logger.info(
                    "telegram_send_success",
                    attempt=attempt,
                    status_code=status_code,
                    duration_ms=duration_ms,
                )
                return
            if payload is None:
                transport_error = TelegramTransportError(
                    "Telegram API returned invalid JSON response"
                )
                logger.warning(
                    "telegram_send_transport_error",
                    attempt=attempt,
                    status_code=status_code,
                    duration_ms=duration_ms,
                    error=str(transport_error),
                )
                raise transport_error
            api_error = TelegramError(_description(payload, "Telegram API request failed"))
            logger.warning(
                "telegram_send_giving_up",
                attempt=attempt,
                status_code=status_code,
                duration_ms=duration_ms,
                error=str(api_error),
            )
            raise api_error

        if status_code == 401:
            payload = _response_json(response)
            auth_error = TelegramAuthError(_description(payload, "Telegram authentication failed"))
            logger.warning(
                "telegram_send_auth_failed",
                attempt=attempt,
                status_code=status_code,
                duration_ms=duration_ms,
                error=str(auth_error),
            )
            raise auth_error

        if status_code == 429:
            payload = _response_json(response)
            retry_after = _retry_after(payload)
            rate_limit_error = TelegramRateLimited(
                _description(payload, "Telegram rate limited"),
                retry_after=retry_after,
            )
            logger.warning(
                "telegram_send_rate_limited",
                attempt=attempt,
                status_code=status_code,
                duration_ms=duration_ms,
                retry_after=retry_after,
                error=str(rate_limit_error),
            )
            raise rate_limit_error

        if status_code >= RETRYABLE_HTTP_STATUS:
            transport_error = TelegramTransportError(f"Telegram API returned HTTP {status_code}")
            logger.warning(
                "telegram_send_transport_error",
                attempt=attempt,
                status_code=status_code,
                duration_ms=duration_ms,
                error=str(transport_error),
            )
            raise transport_error

        payload = _response_json(response)
        api_error = TelegramError(
            _description(payload, f"Telegram API returned HTTP {status_code}")
        )
        logger.warning(
            "telegram_send_giving_up",
            attempt=attempt,
            status_code=status_code,
            duration_ms=duration_ms,
            error=str(api_error),
        )
        raise api_error

    async def _retry_or_raise(
        self,
        error: TelegramRateLimited | TelegramTransportError,
        *,
        attempt: int,
        status_code: int | None,
        duration_ms: int,
        delay_seconds: float,
    ) -> None:
        if attempt >= self._max_retries:
            logger.warning(
                "telegram_send_giving_up",
                attempt=attempt,
                status_code=status_code,
                duration_ms=duration_ms,
                error=str(error),
            )
            raise error
        logger.info(
            "telegram_send_retry",
            attempt=attempt,
            status_code=status_code,
            duration_ms=duration_ms,
            delay_seconds=delay_seconds,
        )
        await asyncio.sleep(delay_seconds)


def _response_json(response: httpx.Response) -> dict[str, object] | None:
    try:
        payload: object = response.json()
    except ValueError:
        return None
    if isinstance(payload, dict):
        return cast(dict[str, object], payload)
    return None


def _description(payload: dict[str, object] | None, default: str) -> str:
    if payload is not None:
        description = payload.get("description")
        if isinstance(description, str):
            return description
    return default


def _retry_after(payload: dict[str, object] | None) -> float:
    if payload is not None:
        parameters = payload.get("parameters")
        if isinstance(parameters, dict):
            retry_after = parameters.get("retry_after")
            if (
                isinstance(retry_after, int | float)
                and math.isfinite(retry_after)
                and retry_after > 0
            ):
                return float(retry_after)
    return 1.0


def _retry_delay(attempt: int) -> float:
    return min(2.0 ** (attempt - 1), MAX_RETRY_DELAY_SECONDS)


def _elapsed_ms(started_at: float) -> int:
    return int((time.monotonic() - started_at) * 1000)
