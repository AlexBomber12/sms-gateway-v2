from __future__ import annotations

from fastapi.testclient import TestClient

from sms_gateway_v2.app import create_app


def test_metrics_endpoint_returns_prometheus_exposition() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "sms_received_total" in response.text
    assert len(response.content) > 0
