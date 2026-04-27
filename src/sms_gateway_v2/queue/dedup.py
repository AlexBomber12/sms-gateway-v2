from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite

from sms_gateway_v2.queue.exceptions import DuplicateMessage, ItemNotFound, QueueError
from sms_gateway_v2.queue.models import ItemStatus

CREATE_SEEN_MESSAGES = """
CREATE TABLE IF NOT EXISTS seen_messages (
    content_hash TEXT PRIMARY KEY,
    item_id TEXT NOT NULL,
    first_seen_at REAL NOT NULL,
    status TEXT NOT NULL,
    last_status_at REAL NOT NULL
)
"""


class DedupStore:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._connection: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        if self._connection is not None:
            return

        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = await aiosqlite.connect(self._db_path)
        await connection.execute("PRAGMA journal_mode=WAL")
        await connection.execute(CREATE_SEEN_MESSAGES)
        await connection.commit()
        self._connection = connection

    async def close(self) -> None:
        if self._connection is None:
            return

        await self._connection.close()
        self._connection = None

    async def is_duplicate(self, content_hash: str) -> bool:
        connection = self._connection_or_raise()
        cursor = await connection.execute(
            "SELECT 1 FROM seen_messages WHERE content_hash = ?",
            (content_hash,),
        )
        row = await cursor.fetchone()
        await cursor.close()
        return row is not None

    async def record_new(self, content_hash: str, item_id: str) -> None:
        connection = self._connection_or_raise()
        now = _now_epoch()
        try:
            await connection.execute(
                """
                INSERT INTO seen_messages (
                    content_hash,
                    item_id,
                    first_seen_at,
                    status,
                    last_status_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (content_hash, item_id, now, ItemStatus.PENDING.value, now),
            )
        except sqlite3.IntegrityError as exc:
            raise DuplicateMessage(f"duplicate SMS content hash: {content_hash}") from exc
        await connection.commit()

    async def update_status(self, content_hash: str, status: ItemStatus) -> None:
        connection = self._connection_or_raise()
        cursor = await connection.execute(
            """
            UPDATE seen_messages
            SET status = ?, last_status_at = ?
            WHERE content_hash = ?
            """,
            (status.value, _now_epoch(), content_hash),
        )
        await connection.commit()
        if cursor.rowcount == 0:
            raise ItemNotFound(f"content hash not found in dedup store: {content_hash}")

    async def reconcile_status(
        self,
        content_hash: str,
        item_id: str,
        status: ItemStatus,
    ) -> None:
        connection = self._connection_or_raise()
        now = _now_epoch()
        await connection.execute(
            """
            INSERT INTO seen_messages (
                content_hash,
                item_id,
                first_seen_at,
                status,
                last_status_at
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(content_hash) DO UPDATE SET
                status = excluded.status,
                last_status_at = excluded.last_status_at
            WHERE seen_messages.status != excluded.status
            """,
            (content_hash, item_id, now, status.value, now),
        )
        await connection.commit()

    async def delete_by_item_id(self, item_id: str) -> int:
        connection = self._connection_or_raise()
        cursor = await connection.execute(
            "DELETE FROM seen_messages WHERE item_id = ?",
            (item_id,),
        )
        await connection.commit()
        return cursor.rowcount

    async def item_ids_older_than(self, status: ItemStatus, max_age_days: int) -> list[str]:
        connection = self._connection_or_raise()
        cutoff = (datetime.now(UTC) - timedelta(days=max_age_days)).timestamp()
        cursor = await connection.execute(
            """
            SELECT item_id FROM seen_messages
            WHERE status = ? AND last_status_at < ?
            ORDER BY item_id
            """,
            (status.value, cutoff),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [str(row[0]) for row in rows]

    async def item_ids(self) -> set[str]:
        connection = self._connection_or_raise()
        cursor = await connection.execute("SELECT item_id FROM seen_messages")
        rows = await cursor.fetchall()
        await cursor.close()
        return {str(row[0]) for row in rows}

    async def purge_older_than(self, status: ItemStatus, max_age_days: int) -> int:
        connection = self._connection_or_raise()
        cutoff = (datetime.now(UTC) - timedelta(days=max_age_days)).timestamp()
        cursor = await connection.execute(
            """
            DELETE FROM seen_messages
            WHERE status = ? AND last_status_at < ?
            """,
            (status.value, cutoff),
        )
        await connection.commit()
        return cursor.rowcount

    def _connection_or_raise(self) -> aiosqlite.Connection:
        if self._connection is None:
            raise QueueError("dedup store is not initialized")
        return self._connection


def _now_epoch() -> float:
    return datetime.now(UTC).timestamp()
