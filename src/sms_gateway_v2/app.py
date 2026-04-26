from __future__ import annotations

import textwrap

from fastapi import APIRouter, FastAPI
from fastapi.responses import HTMLResponse

from sms_gateway_v2 import __version__
from sms_gateway_v2.config import get_settings

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
    app = FastAPI(title="SMS Gateway v2")
    app.state.settings = get_settings()
    app.include_router(router)
    return app
