#!/usr/bin/env python3
"""Route SMS Gateway's existing daily status without changing SMS delivery.

Operator-owned deploy/.env stays outside Git. This tool never prints credentials,
never invokes Compose, and makes only one atomic key change.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import stat
import tempfile

KEY = "HEARTBEAT_TELEGRAM_CHAT_ID"
CHANNEL_ID = re.compile(r"-100[0-9]{7,}\Z")


def _get_assignment(line: str, name: str) -> str | None:
    left, sep, right = line.partition("=")
    if sep and left.strip() == name:
        return right.strip().strip('"').strip("'")
    return None


def rewrite_env(contents: str, channel: str | None) -> str:
    """Pure, idempotent replacement; never modify TELEGRAM_CHAT_ID."""
    lines = contents.splitlines(keepends=True)
    primary = [_get_assignment(line, "TELEGRAM_CHAT_ID") for line in lines]
    primary = [x for x in primary if x is not None]
    tokens = [_get_assignment(line, "TELEGRAM_BOT_TOKEN") for line in lines]
    tokens = [x for x in tokens if x is not None]
    if len(primary) != 1 or not primary[0] or len(tokens) != 1 or not tokens[0]:
        raise ValueError("expected exactly one existing SMS destination and bot token")
    if channel is not None:
        if channel == primary[0]:
            raise ValueError("status channel must differ from SMS destination")
        if not CHANNEL_ID.fullmatch(channel):
            raise ValueError("status destination must be a Telegram private channel ID")

    positions = [i for i, line in enumerate(lines) if _get_assignment(line, KEY) is not None]
    if len(positions) > 1:
        raise ValueError("duplicate status channel assignments")

    if positions:
        if channel is None:
            del lines[positions[0]]
        else:
            end = "\r\n" if lines[positions[0]].endswith("\r\n") else "\n"
            lines[positions[0]] = f"{KEY}={channel}{end}"
    elif channel is not None:
        if lines and not lines[-1].endswith("\n"):
            lines.append("\n")
        lines.append(f"{KEY}={channel}\n")
    return "".join(lines)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=("check", "set", "unset"))
    p.add_argument("--chat-id")
    p.add_argument("--env-file", type=Path, required=True)
    a = p.parse_args()

    path = a.env_file
    if not path.is_absolute() or path.name != ".env" or path.parent.name != "deploy":
        raise SystemExit("Refusing: provide an absolute path to the real deploy/.env")
    s = path.lstat()
    if not stat.S_ISREG(s.st_mode) or s.st_uid != os.geteuid() or stat.S_IMODE(s.st_mode) != 0o600:
        raise SystemExit("Refusing: deploy/.env must be a regular 0600 file owned by the invoking user")

    current = path.read_text(encoding="utf-8")
    if a.action == "check":
        rewrite_env(current, None)  # validate primary delivery and duplication
        configured = any(_get_assignment(x, KEY) for x in current.splitlines())
        print("STATUS CHAT: configured" if configured else "STATUS CHAT: not configured")
        print("SMS DELIVERY: unchanged")
        return 0
    if (a.action == "set") != (a.chat_id is not None):
        p.error("set requires --chat-id; unset must not receive it")

    wanted = rewrite_env(current, a.chat_id if a.action == "set" else None)
    if wanted == current:
        print("NO CHANGE: already in desired state")
        return 0

    tmp_name = None
    try:
        fd, tmp_name = tempfile.mkstemp(prefix=".env.tmp-", dir=path.parent)
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(wanted)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_name, path)
        tmp_name = None
    finally:
        if tmp_name is not None:
            os.unlink(tmp_name)
    print("STATUS CHAT CONFIGURATION: UPDATED")
    print("SMS DESTINATION: PRESERVED")
    print("RESTART REQUIRED: compose service not touched")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
