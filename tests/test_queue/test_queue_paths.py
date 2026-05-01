from __future__ import annotations

from pathlib import Path

import pytest

from sms_gateway_v2.queue import Queue, QueueError


async def test_state_dirs_returns_subdir_mapping(queue: Queue) -> None:
    dirs = queue.state_dirs()

    for name in ("pending", "processing", "sent", "failed"):
        assert name in dirs
        assert dirs[name].is_dir()


async def test_state_dirs_returns_independent_copy(queue: Queue) -> None:
    first = queue.state_dirs()
    first["pending"] = Path("/tmp/garbage")

    second = queue.state_dirs()

    assert second["pending"] != Path("/tmp/garbage")


async def test_state_dirs_raises_when_not_initialized(state_dir: Path) -> None:
    queue = Queue(state_dir, dedup_window_minutes=1)

    with pytest.raises(QueueError, match="queue is not initialized"):
        queue.state_dirs()
