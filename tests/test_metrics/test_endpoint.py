from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from sms_gateway_v2.metrics import MetricsRegistry, metrics_endpoint


def test_metrics_endpoint_returns_prometheus_exposition() -> None:
    app = FastAPI()
    app.state.metrics = MetricsRegistry()
    app.add_api_route("/metrics", metrics_endpoint)

    with TestClient(app) as client:
        response = client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "sms_received_total" in response.text
    assert len(response.content) > 0
