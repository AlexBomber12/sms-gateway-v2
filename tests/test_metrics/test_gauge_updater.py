from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from sms_gateway_v2.metrics import MetricsRegistry, QueueGaugeUpdater
from sms_gateway_v2.metrics import gauge_updater as gauge_updater_module
from sms_gateway_v2.modem import IncomingSms
from sms_gateway_v2.queue import Queue, QueueItem


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    return tmp_path / "state"


@pytest.fixture
async def queue(state_dir: Path) -> AsyncIterator[Queue]:
    queue = Queue(state_dir, dedup_window_minutes=1)
    await queue.initialize()
    try:
        yield queue
    finally:
        await queue.close()


def _write_item(directory: Path, item_id: str, sms: IncomingSms) -> None:
    item = QueueItem(
        id=item_id,
        sms=sms,
        first_seen_at=datetime(2026, 4, 26, 10, 41, 33, tzinfo=UTC),
    )
    (directory / f"{item_id}.json").write_text(item.to_json(), encoding="utf-8")


@pytest.fixture
def sample_sms() -> IncomingSms:
    return IncomingSms(
        object_path="/org/freedesktop/ModemManager1/SMS/1",
        number="+15551234567",
        text="hello",
        timestamp=datetime(2026, 4, 26, 10, 41, 33, tzinfo=UTC),
        pdu_type="deliver",
    )


async def test_update_gauges_reflects_files_in_each_state(
    queue: Queue,
    sample_sms: IncomingSms,
) -> None:
    metrics = MetricsRegistry()
    dirs = queue.state_dirs()
    _write_item(dirs["pending"], "1714149693000-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", sample_sms)
    _write_item(dirs["processing"], "1714149693001-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", sample_sms)
    _write_item(dirs["sent"], "1714149693002-cccccccccccccccccccccccccccccccc", sample_sms)
    _write_item(dirs["sent"], "1714149693003-dddddddddddddddddddddddddddddddd", sample_sms)
    _write_item(dirs["failed"], "1714149693004-eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee", sample_sms)
    updater = QueueGaugeUpdater(queue=queue, metrics=metrics, interval_seconds=30.0)

    await updater._update_gauges()

    assert metrics.registry.get_sample_value("queue_pending_count") == 1.0
    assert metrics.registry.get_sample_value("queue_processing_count") == 1.0
    assert metrics.registry.get_sample_value("queue_sent_count") == 2.0
    assert metrics.registry.get_sample_value("queue_failed_count") == 1.0


async def test_queue_gauge_updates_queue_counts_on_state(
    queue: Queue,
    sample_sms: IncomingSms,
) -> None:
    metrics = MetricsRegistry()
    captured: list[tuple[int, int]] = []
    dirs = queue.state_dirs()
    _write_item(dirs["pending"], "1714149693000-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", sample_sms)
    _write_item(dirs["failed"], "1714149693001-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", sample_sms)
    _write_item(dirs["failed"], "1714149693002-cccccccccccccccccccccccccccccccc", sample_sms)
    updater = QueueGaugeUpdater(
        queue=queue,
        metrics=metrics,
        interval_seconds=30.0,
        queue_counts_callback=lambda pending, failed: captured.append((pending, failed)),
    )

    await updater._update_gauges()

    assert captured == [(1, 2)]


async def test_run_loops_until_stop_called(
    queue: Queue,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metrics = MetricsRegistry()
    updater = QueueGaugeUpdater(queue=queue, metrics=metrics, interval_seconds=30.0)
    update_calls = 0

    async def stopping_update() -> None:
        nonlocal update_calls
        update_calls += 1
        updater.stop()

    monkeypatch.setattr(updater, "_update_gauges", stopping_update)

    await updater.run()

    assert update_calls == 1


async def test_update_gauges_swallows_filesystem_errors(
    queue: Queue,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metrics = MetricsRegistry()
    updater = QueueGaugeUpdater(queue=queue, metrics=metrics, interval_seconds=30.0)

    def explode(_: Path) -> int:
        raise OSError("disk on fire")

    monkeypatch.setattr(gauge_updater_module, "_count_json_files", explode)

    log_events: list[tuple[str, dict[str, Any]]] = []

    class CapturingLogger:
        def warning(self, event: str, **kwargs: Any) -> None:
            log_events.append((event, kwargs))

    monkeypatch.setattr(gauge_updater_module, "logger", CapturingLogger())

    await updater._update_gauges()

    assert any(event == "gauge_update_failed" for event, _ in log_events)
    assert metrics.registry.get_sample_value("queue_pending_count") == 0.0


async def test_run_continues_after_filesystem_error(
    queue: Queue,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metrics = MetricsRegistry()
    updater = QueueGaugeUpdater(queue=queue, metrics=metrics, interval_seconds=30.0)

    counter = {"calls": 0}

    def flaky_count(_: Path) -> int:
        counter["calls"] += 1
        updater.stop()
        raise OSError("transient")

    monkeypatch.setattr(gauge_updater_module, "_count_json_files", flaky_count)

    await updater.run()

    assert counter["calls"] == 1
