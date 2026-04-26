from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sms_gateway_v2 import __main__ as main_module
from sms_gateway_v2 import __version__
from sms_gateway_v2.app import create_app
from sms_gateway_v2.config import Settings, get_settings


@pytest.mark.e2e
def test_e2e_marker_placeholder(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOST", "127.0.0.1")
    monkeypatch.setenv("PORT", "8091")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    get_settings.cache_clear()

    with TestClient(create_app()) as client:
        healthz_response = client.get("/healthz")
        index_response = client.get("/")

    settings = Settings()
    calls: list[dict[str, object]] = []

    def fake_run(app: object, *, host: str, port: int, log_level: str) -> None:
        calls.append({"app": app, "host": host, "port": port, "log_level": log_level})

    monkeypatch.setattr(main_module.uvicorn, "run", fake_run)
    main_module.main()

    assert healthz_response.status_code == 200
    assert healthz_response.json() == {"status": "ok", "version": __version__}
    assert index_response.status_code == 200
    assert "SMS Gateway v2" in index_response.text
    assert settings.host == "127.0.0.1"
    assert settings.port == 8091
    assert settings.log_level == "INFO"
    assert len(calls) == 1
    assert isinstance(calls[0]["app"], FastAPI)
    assert calls[0]["host"] == "127.0.0.1"
    assert calls[0]["port"] == 8091
    assert calls[0]["log_level"] == "info"
    get_settings.cache_clear()
    assert True
