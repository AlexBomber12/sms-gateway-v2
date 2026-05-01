#!/usr/bin/env python3
"""Pre-populate the v2 dedup database from a legacy gammu inbox.

This is a one-time tool used during the cutover from the AI-Server gammu
relay to sms-gateway-v2. It reads gammu's SMS inbox (SQLite by default;
MySQL DSN support is intentionally left as a future extension) and
inserts a content hash into ``state/dedup.db`` for every message that
gammu already received. The running v2 relay then treats those messages
as duplicates if they appear again in the modem inbox at cutover time,
which closes the short window during which a message physically
present on the SIM could be forwarded twice.

Schema notes:
- The default gammu schema names the inbox table ``inbox`` with columns
  ``Number``, ``TextDecoded``, ``ReceivingDateTime`` (DATETIME). Some
  installations keep older messages in the ``sentitems`` table; this
  script only reads ``inbox`` because the dedup window in v2 only cares
  about messages the modem may re-deliver.
- If the operator's installation differs (e.g. MySQL, or a custom dump
  format), the simplest path is to export to CSV with header
  ``number,text,timestamp`` and pass ``--csv path.csv``. The main loop
  is "for each (number, text, timestamp) compute hash, insert".

The hash formula MUST stay identical to ``Queue._content_hash`` in
``src/sms_gateway_v2/queue/queue.py``. Both call sites are noted with a
comment so a future change to the bucket logic updates both.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import sqlite3
import sys
import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

# DEDUP HASH FORMULA: keep this in sync with
# src/sms_gateway_v2/queue/queue.py::Queue._content_hash. Any change to
# the bucket math here MUST be mirrored there or the running relay will
# stop recognising hashes produced by this importer.


@dataclass(frozen=True)
class GammuMessage:
    number: str
    text: str
    timestamp: datetime


def _content_hash(message: GammuMessage, *, dedup_window_minutes: int) -> str:
    timestamp = message.timestamp
    window_seconds = dedup_window_minutes * 60
    bucket_seconds = int(timestamp.timestamp()) // window_seconds * window_seconds
    bucket = datetime.fromtimestamp(bucket_seconds, tz=timestamp.tzinfo).isoformat()
    payload = f"{message.number}|{message.text}|{bucket}"
    return hashlib.sha256(payload.encode()).hexdigest()


def _parse_timestamp(raw: str) -> datetime:
    text = raw.strip()
    if not text:
        raise ValueError("empty timestamp")
    candidate = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        else:
            raise ValueError(f"unrecognised timestamp format: {raw!r}") from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _read_gammu_sqlite(path: Path, *, table: str) -> Iterator[GammuMessage]:
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        cursor = connection.execute(f"SELECT Number, TextDecoded, ReceivingDateTime FROM {table}")
        for row in cursor:
            number = row["Number"]
            text = row["TextDecoded"]
            timestamp_raw = row["ReceivingDateTime"]
            if number is None or text is None or timestamp_raw is None:
                continue
            yield GammuMessage(
                number=str(number),
                text=str(text),
                timestamp=_parse_timestamp(str(timestamp_raw)),
            )


def _read_csv(path: Path) -> Iterator[GammuMessage]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        required = {"number", "text", "timestamp"}
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            raise ValueError(
                f"CSV must have header columns {sorted(required)}; got {reader.fieldnames!r}"
            )
        for row in reader:
            yield GammuMessage(
                number=row["number"],
                text=row["text"],
                timestamp=_parse_timestamp(row["timestamp"]),
            )


def _insert_hashes(
    target_db: Path,
    messages: Iterable[GammuMessage],
    *,
    dedup_window_minutes: int,
) -> tuple[int, int, int]:
    target_db.parent.mkdir(parents=True, exist_ok=True)
    rows_read = 0
    inserted = 0
    duplicates = 0
    now = time.time()
    with sqlite3.connect(target_db) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS seen_messages (
                content_hash TEXT PRIMARY KEY,
                item_id TEXT NOT NULL,
                first_seen_at REAL NOT NULL,
                status TEXT NOT NULL,
                last_status_at REAL NOT NULL
            )
            """
        )
        for message in messages:
            rows_read += 1
            content_hash = _content_hash(
                message,
                dedup_window_minutes=dedup_window_minutes,
            )
            item_id = f"gammu-import-{content_hash[:16]}"
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO seen_messages (
                    content_hash,
                    item_id,
                    first_seen_at,
                    status,
                    last_status_at
                )
                VALUES (?, ?, ?, 'sent', ?)
                """,
                (content_hash, item_id, now, now),
            )
            if cursor.rowcount == 1:
                inserted += 1
            else:
                duplicates += 1
        connection.commit()
    return rows_read, inserted, duplicates


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Pre-populate the sms-gateway-v2 dedup database with hashes "
            "of messages already received by the legacy gammu relay."
        ),
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--gammu-db",
        type=Path,
        help="Path to the gammu SQLite database (read-only).",
    )
    source.add_argument(
        "--csv",
        type=Path,
        help=(
            "Path to a CSV dump with header 'number,text,timestamp' "
            "(use this when the gammu install is on MySQL or a custom schema)."
        ),
    )
    parser.add_argument(
        "--target-db",
        type=Path,
        required=True,
        help="Path to the v2 dedup.db (created if missing).",
    )
    parser.add_argument(
        "--gammu-table",
        default="inbox",
        help="Table name to read from the gammu SQLite db (default: inbox).",
    )
    parser.add_argument(
        "--dedup-window-minutes",
        type=int,
        default=1,
        help=(
            "Bucket size used by the relay (must match Settings.dedup_window_minutes; default: 1)."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    if args.dedup_window_minutes < 1:
        print("--dedup-window-minutes must be >= 1", file=sys.stderr)
        return 2
    if args.gammu_db is not None:
        if not args.gammu_db.exists():
            print(f"gammu db not found: {args.gammu_db}", file=sys.stderr)
            return 2
        messages = _read_gammu_sqlite(args.gammu_db, table=args.gammu_table)
    else:
        if not args.csv.exists():
            print(f"csv file not found: {args.csv}", file=sys.stderr)
            return 2
        messages = _read_csv(args.csv)
    rows_read, inserted, duplicates = _insert_hashes(
        args.target_db,
        messages,
        dedup_window_minutes=args.dedup_window_minutes,
    )
    print(f"rows_read={rows_read} inserted={inserted} duplicates_skipped={duplicates}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
