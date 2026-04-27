from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from sms_gateway_v2.relay import SmsRelay
from sms_gateway_v2.worker import DeliveryWorker


async def test_worker_task_failure_is_logged_and_stop_still_completes(
    relay: SmsRelay,
    worker: DeliveryWorker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = MagicMock()
    monkeypatch.setattr("sms_gateway_v2.relay.relay.logger", logger)

    async def fail_run() -> None:
        raise RuntimeError("worker crashed")

    worker.run = fail_run

    await relay.start()
    try:
        deadline = asyncio.get_running_loop().time() + 1.0
        while not logger.exception.called and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)

        logger.exception.assert_called_once_with(
            "relay_worker_task_failed",
            error="worker crashed",
        )
        assert relay.state().status == "running"
    finally:
        await relay.stop()

    assert relay.state().status == "stopped"
