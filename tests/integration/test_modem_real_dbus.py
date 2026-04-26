"""Requires sudo access to system D-Bus and a working ModemManager service."""

from __future__ import annotations

import pytest


@pytest.mark.integration
async def test_real_modem_manager_lists_modems() -> None:
    from sms_gateway_v2.modem import ModemManagerClient

    client = ModemManagerClient()
    try:
        await client.connect()
        modem_path = await client.find_modem()
        assert modem_path.startswith("/org/freedesktop/ModemManager1/Modem/")
    finally:
        await client.disconnect()
