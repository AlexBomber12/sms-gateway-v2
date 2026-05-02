from __future__ import annotations

import asyncio
import textwrap
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Protocol

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from sms_gateway_v2 import __version__
from sms_gateway_v2.config import Settings, get_settings
from sms_gateway_v2.metrics import MetricsRegistry, metrics_endpoint
from sms_gateway_v2.relay import build_relay

router = APIRouter()

BACKGROUND_TASK_SHUTDOWN_TIMEOUT_SECONDS = 2.0


class SupportsStop(Protocol):
    def stop(self) -> None: ...


async def _stop_background_task(
    scheduler: SupportsStop,
    task: asyncio.Task[None],
    timeout: float = BACKGROUND_TASK_SHUTDOWN_TIMEOUT_SECONDS,
) -> None:
    scheduler.stop()
    try:
        await asyncio.wait_for(task, timeout=timeout)
    except TimeoutError:
        task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await task


@router.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@router.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    html = textwrap.dedent(
        """\
    <!doctype html>
    <html lang="en">
      <head>
        <meta charset="utf-8">
        <title>SMS Gateway v2</title>
      </head>
      <body>
        <h1>SMS Gateway v2</h1>
      </body>
    </html>
    """
    )
    return HTMLResponse(content=html)


def create_app() -> FastAPI:
    settings = get_settings()
    metrics = MetricsRegistry()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if settings.relay_enabled:
            await _startup_relay(app, settings, metrics)
            try:
                yield
            finally:
                await _shutdown_relay(app)
        else:
            yield

    app = FastAPI(title="SMS Gateway v2", lifespan=lifespan)
    app.state.settings = settings
    app.state.metrics = metrics
    app.add_api_route(settings.metrics_path, metrics_endpoint, methods=["GET"])
    app.add_api_route("/state", state_endpoint, methods=["GET"])
    app.include_router(router)
    return app


async def state_endpoint(request: Request) -> JSONResponse:
    relay = getattr(request.app.state, "relay", None)
    if relay is None:
        return JSONResponse({"detail": "relay is not enabled"}, status_code=503)
    return JSONResponse(relay.state().model_dump(mode="json"))


async def _startup_relay(app: FastAPI, settings: Settings, metrics: MetricsRegistry) -> None:
    relay, gauge_updater, watchdog, cleanup_scheduler, heartbeat_scheduler = build_relay(
        settings, metrics
    )
    telegram_client = relay.telegram_client
    await telegram_client.__aenter__()
    try:
        await relay.start()
    except BaseException:
        await telegram_client.__aexit__(None, None, None)
        raise
    gauge_task = asyncio.create_task(gauge_updater.run())
    cleanup_task = asyncio.create_task(cleanup_scheduler.run())
    watchdog_task = asyncio.create_task(watchdog.run())
    heartbeat_task: asyncio.Task[None] | None = None
    if heartbeat_scheduler is not None:
        heartbeat_task = asyncio.create_task(heartbeat_scheduler.run())
    app.state.relay = relay
    app.state.telegram_client = telegram_client
    app.state.gauge_updater = gauge_updater
    app.state.gauge_task = gauge_task
    app.state.cleanup_scheduler = cleanup_scheduler
    app.state.cleanup_task = cleanup_task
    app.state.watchdog = watchdog
    app.state.watchdog_task = watchdog_task
    app.state.heartbeat_scheduler = heartbeat_scheduler
    app.state.heartbeat_task = heartbeat_task


async def _shutdown_relay(app: FastAPI) -> None:
    relay = app.state.relay
    telegram_client = app.state.telegram_client
    heartbeat_scheduler = app.state.heartbeat_scheduler
    heartbeat_task = app.state.heartbeat_task
    await _stop_background_task(app.state.gauge_updater, app.state.gauge_task)
    await _stop_background_task(app.state.cleanup_scheduler, app.state.cleanup_task)
    await _stop_background_task(app.state.watchdog, app.state.watchdog_task)
    if heartbeat_scheduler is not None and heartbeat_task is not None:
        await _stop_background_task(heartbeat_scheduler, heartbeat_task)
    try:
        await relay.stop()
    finally:
        await telegram_client.__aexit__(None, None, None)
