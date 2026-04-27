from __future__ import annotations

from typing import cast

from fastapi import Request, Response
from prometheus_client import CONTENT_TYPE_LATEST

from sms_gateway_v2.metrics.registry import MetricsRegistry


async def metrics_endpoint(request: Request) -> Response:
    registry = cast(MetricsRegistry, request.app.state.metrics)
    return Response(content=registry.render(), media_type=CONTENT_TYPE_LATEST)
