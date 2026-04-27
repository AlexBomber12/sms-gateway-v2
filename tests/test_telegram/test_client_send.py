from __future__ import annotations

from collections.abc import Sequence
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call

import httpx
import pytest

from sms_gateway_v2.telegram import (
    TelegramAuthError,
    TelegramClient,
    TelegramError,
    TelegramMessage,
    TelegramRateLimited,
    TelegramTransportError,
)


def telegram_response(status_code: int, payload: dict[str, object]) -> httpx.Response:
    return httpx.Response(status_code=status_code, json=payload)


def ok_response() -> httpx.Response:
    return telegram_response(200, {"ok": True})


def non_json_response(status_code: int) -> httpx.Response:
    return httpx.Response(status_code=status_code, text="not-json")


def non_object_json_response(status_code: int) -> httpx.Response:
    return httpx.Response(status_code=status_code, json=["not", "an", "object"])


async def send_with_post_side_effects(
    monkeypatch: pytest.MonkeyPatch,
    side_effects: Sequence[httpx.Response | Exception],
    *,
    max_retries: int = 3,
) -> tuple[AsyncMock, AsyncMock]:
    post = AsyncMock(side_effect=side_effects)
    sleep = AsyncMock()
    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    monkeypatch.setattr("sms_gateway_v2.telegram.client.asyncio.sleep", sleep)
    message = TelegramMessage(chat_id="-100", text="hello")

    async with TelegramClient("token", "-100", max_retries=max_retries) as client:
        await client.send_message(message)

    return post, sleep


async def test_send_message_returns_successfully_on_ok_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    post, sleep = await send_with_post_side_effects(monkeypatch, [ok_response()])

    post.assert_awaited_once_with(
        "sendMessage",
        json={"chat_id": "-100", "text": "hello", "parse_mode": "HTML"},
    )
    sleep.assert_not_awaited()


async def test_send_message_uses_configured_chat_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    post = AsyncMock(side_effect=[ok_response()])
    monkeypatch.setattr(httpx.AsyncClient, "post", post)

    async with TelegramClient("token", "-configured") as client:
        await client.send_message(TelegramMessage(chat_id="-message", text="hello"))

    post.assert_awaited_once_with(
        "sendMessage",
        json={"chat_id": "-configured", "text": "hello", "parse_mode": "HTML"},
    )


async def test_send_message_raises_telegram_error_on_not_ok_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    post = AsyncMock(side_effect=[telegram_response(200, {"ok": False, "description": "bad"})])
    sleep = AsyncMock()
    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    monkeypatch.setattr("sms_gateway_v2.telegram.client.asyncio.sleep", sleep)

    async with TelegramClient("token", "-100") as client:
        with pytest.raises(TelegramError, match="bad"):
            await client.send_message(TelegramMessage(chat_id="-100", text="hello"))

    assert post.await_count == 1
    sleep.assert_not_awaited()


async def test_send_message_raises_auth_error_on_401_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    post = AsyncMock(
        side_effect=[telegram_response(401, {"ok": False, "description": "unauthorized"})]
    )
    sleep = AsyncMock()
    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    monkeypatch.setattr("sms_gateway_v2.telegram.client.asyncio.sleep", sleep)

    async with TelegramClient("token", "-100", max_retries=3) as client:
        with pytest.raises(TelegramAuthError, match="unauthorized"):
            await client.send_message(TelegramMessage(chat_id="-100", text="hello"))

    assert post.await_count == 1
    sleep.assert_not_awaited()


async def test_send_message_retries_429_with_retry_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    post, sleep = await send_with_post_side_effects(
        monkeypatch,
        [
            telegram_response(
                429,
                {"ok": False, "description": "too many", "parameters": {"retry_after": 2}},
            ),
            ok_response(),
        ],
    )

    assert post.await_count == 2
    assert sleep.await_args_list == [call(2.0)]


async def test_send_message_retries_429_with_default_retry_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    post, sleep = await send_with_post_side_effects(
        monkeypatch,
        [telegram_response(429, {"ok": False, "description": "too many"}), ok_response()],
    )

    assert post.await_count == 2
    assert sleep.await_args_list == [call(1.0)]


async def test_send_message_retries_500_with_exponential_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    post, sleep = await send_with_post_side_effects(
        monkeypatch,
        [telegram_response(500, {"ok": False}) for _ in range(7)] + [ok_response()],
        max_retries=8,
    )

    assert post.await_count == 8
    assert sleep.await_args_list == [
        call(1.0),
        call(2.0),
        call(4.0),
        call(8.0),
        call(16.0),
        call(30.0),
        call(30.0),
    ]


async def test_send_message_retries_timeout_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    post, sleep = await send_with_post_side_effects(
        monkeypatch,
        [httpx.TimeoutException("timeout"), ok_response()],
    )

    assert post.await_count == 2
    assert sleep.await_args_list == [call(1.0)]


async def test_send_message_retries_network_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    post, sleep = await send_with_post_side_effects(
        monkeypatch,
        [httpx.NetworkError("network"), ok_response()],
    )

    assert post.await_count == 2
    assert sleep.await_args_list == [call(1.0)]


async def test_send_message_retries_non_json_200_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    post, sleep = await send_with_post_side_effects(
        monkeypatch,
        [non_json_response(200), ok_response()],
    )

    assert post.await_count == 2
    assert sleep.await_args_list == [call(1.0)]


async def test_send_message_retries_non_object_json_200_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    post, sleep = await send_with_post_side_effects(
        monkeypatch,
        [non_object_json_response(200), ok_response()],
    )

    assert post.await_count == 2
    assert sleep.await_args_list == [call(1.0)]


async def test_send_message_exhausting_500_retries_raises_transport_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    post = AsyncMock(side_effect=[telegram_response(500, {"ok": False}) for _ in range(3)])
    sleep = AsyncMock()
    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    monkeypatch.setattr("sms_gateway_v2.telegram.client.asyncio.sleep", sleep)

    async with TelegramClient("token", "-100", max_retries=3) as client:
        with pytest.raises(TelegramTransportError, match="HTTP 500"):
            await client.send_message(TelegramMessage(chat_id="-100", text="hello"))

    assert post.await_count == 3
    assert sleep.await_args_list == [call(1.0), call(2.0)]


async def test_send_message_exhausting_429_retries_raises_rate_limited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    post = AsyncMock(side_effect=[telegram_response(429, {"ok": False}) for _ in range(2)])
    sleep = AsyncMock()
    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    monkeypatch.setattr("sms_gateway_v2.telegram.client.asyncio.sleep", sleep)

    async with TelegramClient("token", "-100", max_retries=2) as client:
        with pytest.raises(TelegramRateLimited) as exc_info:
            await client.send_message(TelegramMessage(chat_id="-100", text="hello"))

    assert exc_info.value.retry_after == 1.0
    assert post.await_count == 2
    assert sleep.await_args_list == [call(1.0)]


async def test_send_message_raises_telegram_error_for_other_http_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    post = AsyncMock(side_effect=[telegram_response(400, {"ok": False})])
    monkeypatch.setattr(httpx.AsyncClient, "post", post)

    async with TelegramClient("token", "-100") as client:
        with pytest.raises(TelegramError, match="HTTP 400"):
            await client.send_message(TelegramMessage(chat_id="-100", text="hello"))


async def test_send_message_raises_auth_error_for_non_json_401(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    post = AsyncMock(side_effect=[non_json_response(401)])
    sleep = AsyncMock()
    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    monkeypatch.setattr("sms_gateway_v2.telegram.client.asyncio.sleep", sleep)

    async with TelegramClient("token", "-100") as client:
        with pytest.raises(TelegramAuthError, match="authentication failed"):
            await client.send_message(TelegramMessage(chat_id="-100", text="hello"))

    assert post.await_count == 1
    sleep.assert_not_awaited()


async def test_send_message_retries_non_json_429_with_default_retry_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    post, sleep = await send_with_post_side_effects(
        monkeypatch,
        [non_json_response(429), ok_response()],
    )

    assert post.await_count == 2
    assert sleep.await_args_list == [call(1.0)]


def test_constructor_raises_value_error_on_empty_bot_token() -> None:
    with pytest.raises(ValueError, match="bot_token"):
        TelegramClient("", "-100")


def test_constructor_raises_value_error_on_empty_chat_id() -> None:
    with pytest.raises(ValueError, match="chat_id"):
        TelegramClient("token", "")


def test_attempts_remaining_returns_configured_max_retries() -> None:
    client = TelegramClient("token", "-100", max_retries=5)

    assert client.attempts_remaining == 5


async def test_send_message_without_context_raises_transport_error() -> None:
    client = TelegramClient("token", "-100")

    with pytest.raises(TelegramTransportError, match="not open"):
        await client.send_message(TelegramMessage(chat_id="-100", text="hello"))


async def test_async_context_manager_opens_and_closes_async_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_client = SimpleNamespace(aclose=AsyncMock())
    factory = Mock(return_value=async_client)
    monkeypatch.setattr("sms_gateway_v2.telegram.client.httpx.AsyncClient", factory)

    async with TelegramClient(
        "token",
        "-100",
        api_base="https://telegram.example/",
        timeout_seconds=5.0,
    ) as client:
        assert client._client is async_client

    factory.assert_called_once_with(
        timeout=5.0,
        base_url="https://telegram.example/bottoken",
    )
    async_client.aclose.assert_awaited_once()
    assert client._client is None
