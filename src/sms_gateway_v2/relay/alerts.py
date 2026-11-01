from __future__ import annotations

import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Literal

import structlog
from pydantic import BaseModel, Field, ValidationError

from sms_gateway_v2.relay.heartbeat import HeartbeatScheduler
from sms_gateway_v2.relay.models import RelayState
from sms_gateway_v2.relay.relay import SmsRelay
from sms_gateway_v2.telegram import TelegramClient
from sms_gateway_v2.telegram.exceptions import TelegramError, TelegramRateLimited
from sms_gateway_v2.telegram.models import TelegramMessage

logger = structlog.get_logger(__name__)

POLL_SECONDS = 60.0
PERSIST_SECONDS = 900.0
RECOVERY_SECONDS = 120.0
REMINDER_SECONDS = 86400.0
RUNBOOK = (
    "https://github.com/AlexBomber12/knowledge-vault/blob/main/"
    "20-homelab/notifications/sms-gateway.md"
)
Reason = Literal["relay", "modem", "delivery", "pending", "unknown"]
TITLES: dict[Reason, str] = {
    "relay": "пересылка SMS не работает",
    "modem": "модем недоступен или не зарегистрирован в сети",
    "delivery": "в очереди остались недоставленные SMS",
    "pending": "очередь SMS не опустошается и нет подтверждённого продвижения",
    "unknown": "не удаётся подтвердить состояние очереди SMS",
}


class _Incident(BaseModel):
    reason: Reason | None = None
    first_bad: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    sent_at: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    healthy_since: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    next_attempt: float = Field(default=0, ge=0, allow_inf_nan=False)
    failures: int = Field(default=0, ge=0, le=64)
    progress: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    observed_at: float = Field(default=0, ge=0, allow_inf_nan=False)


def _reason(state: RelayState) -> Reason | None:
    if state.status != "running":
        return "relay"
    if state.modem_state != "registered":
        return "modem"
    if state.queue_failed_count is None or state.queue_pending_count is None:
        return "unknown"
    if state.queue_failed_count > 0:
        return "delivery"
    if state.queue_pending_count > 0:
        return "pending"
    return None


def _text(reason: Reason | None) -> str:
    if reason is None:
        return (
            "SERVER — SMS Gateway: ранее сообщённая неисправность устранена.\n"
            "Состояние шлюза рабочее, модем зарегистрирован, очередь пуста.\n"
            "Действие по предыдущему уведомлению больше не требуется.\n"
            f"Инструкция: {RUNBOOK}"
        )
    return (
        f"Требует внимания — SERVER: {TITLES[reason]}.\n"
        "Неисправность шлюза наблюдается не менее 15 минут. Получение SMS может быть нарушено.\n"
        "Штатное самовосстановление не отключено; результат пока не подтверждён.\n"
        "Передайте сообщение в ChatGPT для проверки. Очередь SMS удалять нельзя.\n"
        f"Диагностика: SMS Gateway v2 / {reason}.\n"
        f"Инструкция: {RUNBOOK}"
    )


class AlertOnlyScheduler(HeartbeatScheduler):
    """One quiet health observer; its SQLite file never contains SMS or credentials."""

    def __init__(
        self, telegram_client: TelegramClient, relay: SmsRelay, chat_id: str, state_dir: Path
    ) -> None:
        super().__init__(telegram_client, relay, chat_id, POLL_SECONDS)
        self._path = state_dir / "notification-incidents.sqlite3"

    def _claim(self, now: float, reason: Reason | None, progress: float | None) -> _Incident | None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self._path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        with closing(sqlite3.connect(self._path, timeout=0.1)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS incident (id INTEGER PRIMARY KEY, state TEXT)")
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state FROM incident WHERE id=1").fetchone()
            state = _Incident.model_validate_json(row[0]) if row else _Incident()
            if now - state.observed_at > POLL_SECONDS * 3 or now < state.observed_at:
                state.first_bad = None
                state.healthy_since = None
            state.observed_at = now
            if reason is not None:
                if reason == "pending" and progress is not None and progress != state.progress:
                    state.first_bad = None
                if state.first_bad is None:
                    state.first_bad = now
                state.reason = reason
                state.healthy_since = None
                ready = now - state.first_bad >= PERSIST_SECONDS and (
                    state.sent_at is None or now - state.sent_at >= REMINDER_SECONDS
                )
            else:
                state.first_bad = None
                if state.sent_at is None:
                    state = _Incident(observed_at=now)
                    ready = False
                else:
                    if state.healthy_since is None:
                        state.healthy_since = now
                    ready = now - state.healthy_since >= RECOVERY_SECONDS
            ready = ready and now >= state.next_attempt
            state.progress = progress
            if ready:
                # Persist a lease before HTTP; a restart cannot immediately repeat the attempt.
                state.next_attempt = now + 300
            db.execute("INSERT OR REPLACE INTO incident VALUES (1, ?)", (state.model_dump_json(),))
            return state if ready else None

    def _finish(self, state: _Incident) -> None:
        with closing(sqlite3.connect(self._path, timeout=0.1)) as db, db:
            db.execute("UPDATE incident SET state=? WHERE id=1", (state.model_dump_json(),))

    async def _send_heartbeat(self) -> None:
        try:
            await self._poll()
        except (OSError, sqlite3.Error, ValidationError) as exc:
            # A broken notifier must not stop the relay or expose exception/secret text.
            logger.error("alert_observer_failed", error_type=type(exc).__name__)

    async def _poll(self) -> None:
        now = time.time()
        snapshot = self._relay.state()
        reason = _reason(snapshot)
        progress = snapshot.last_telegram_success_at
        incident = self._claim(now, reason, progress.timestamp() if progress is not None else None)
        if incident is None:
            return
        # Do not send a stale error/recovery if health changed while observing the database.
        if _reason(self._relay.state()) != reason:
            return
        try:
            await self._telegram_client.send_message(
                TelegramMessage(chat_id=self._chat_id, text=_text(reason), parse_mode=None)
            )
        except TelegramError as exc:
            incident.failures = min(incident.failures + 1, 64)
            delay = min(3600.0, 60.0 * 2 ** min(incident.failures, 6))
            if isinstance(exc, TelegramRateLimited):
                delay = max(delay, min(exc.retry_after, 86400.0))
            incident.next_attempt = now + delay
            logger.warning("alert_send_failed", error_type=type(exc).__name__)
        else:
            logger.info("alert_sent", recovery=reason is None)
            incident = (
                _Incident()
                if reason is None
                else incident.model_copy(update={"sent_at": now, "failures": 0, "next_attempt": 0})
            )
        self._finish(incident)
