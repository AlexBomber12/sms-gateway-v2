from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sms_gateway_v2 import __version__
from sms_gateway_v2.app import create_app
from sms_gateway_v2.metrics import MetricsRegistry


@pytest.fixture(autouse=True)
def settings_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOST", "127.0.0.1")
    monkeypatch.setenv("PORT", "8091")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    yield


def test_healthz_returns_ok() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_healthz_returns_package_version() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json()["version"] == __version__


def test_index_returns_sms_gateway_title() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "SMS Gateway v2" in response.text


def test_app_state_metrics_is_metrics_registry() -> None:
    app = create_app()

    assert isinstance(app.state.metrics, MetricsRegistry)
