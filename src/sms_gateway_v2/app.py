from __future__ import annotations

import textwrap
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.responses import HTMLResponse

from sms_gateway_v2 import __version__
from sms_gateway_v2.config import Settings, get_settings
from sms_gateway_v2.metrics import MetricsRegistry, metrics_endpoint
from sms_gateway_v2.relay import build_relay

router = APIRouter()


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
    app.include_router(router)
    return app


async def _startup_relay(app: FastAPI, settings: Settings, metrics: MetricsRegistry) -> None:
    relay = build_relay(settings, metrics)
    telegram_client = relay.telegram_client
    await telegram_client.__aenter__()
    try:
        await relay.start()
    except BaseException:
        await telegram_client.__aexit__(None, None, None)
        raise
    app.state.relay = relay
    app.state.telegram_client = telegram_client


async def _shutdown_relay(app: FastAPI) -> None:
    relay = app.state.relay
    telegram_client = app.state.telegram_client
    try:
        await relay.stop()
    finally:
        await telegram_client.__aexit__(None, None, None)
