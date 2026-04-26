from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture
def fake_bus() -> MagicMock:
    bus = MagicMock()
    bus.connected = True
    bus.connect = AsyncMock(return_value=bus)
    bus.disconnect = MagicMock()
    bus.introspect = AsyncMock(return_value=object())
    bus.get_proxy_object = MagicMock()
    return bus
