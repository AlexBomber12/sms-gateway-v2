from __future__ import annotations

from collections.abc import Iterator

import pytest

from sms_gateway_v2.config import get_settings


@pytest.fixture(autouse=True)
def clean_settings_cache() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
