from __future__ import annotations

from collections.abc import Iterator

import pytest

from sms_gateway_v2.config import get_settings


@pytest.fixture(autouse=True)
def clean_settings_cache() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def pytest_configure(config: pytest.Config) -> None:
    if config.option.markexpr in {"integration", "e2e"}:
        config.option.cov_fail_under = 0


def pytest_sessionstart(session: pytest.Session) -> None:
    if session.config.option.markexpr not in {"integration", "e2e"}:
        return

    cov_plugin = session.config.pluginmanager.getplugin("_cov")
    if cov_plugin is not None:
        cov_plugin.options.cov_fail_under = 0
