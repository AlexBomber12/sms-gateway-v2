from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI

from sms_gateway_v2 import __main__ as main_module


def test_main_invokes_uvicorn_with_settings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOST", "0.0.0.0")
    monkeypatch.setenv("PORT", "9000")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    calls: list[dict[str, object]] = []

    def fake_run(app: object, *, host: str, port: int, log_level: str) -> None:
        calls.append({"app": app, "host": host, "port": port, "log_level": log_level})

    monkeypatch.setattr(main_module.uvicorn, "run", fake_run)

    main_module.main()

    assert len(calls) == 1
    call = calls[0]
    assert isinstance(call["app"], FastAPI)
    assert call["host"] == "0.0.0.0"
    assert call["port"] == 9000
    assert call["log_level"] == "debug"
