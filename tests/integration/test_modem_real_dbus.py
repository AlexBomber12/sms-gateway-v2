"""Requires sudo access to system D-Bus and a working ModemManager service."""

from __future__ import annotations

import pytest


@pytest.mark.integration
async def test_real_modem_manager_lists_modems() -> None:
    from sms_gateway_v2.modem import ModemManagerClient, ModemManagerUnavailable

    client = ModemManagerClient()
    try:
        try:
            await client.connect()
        except ModemManagerUnavailable as exc:
            pytest.skip(f"system D-Bus or ModemManager unavailable: {exc}")
        modem_path = await client.find_modem()
        assert modem_path.startswith("/org/freedesktop/ModemManager1/Modem/")
    finally:
        await client.disconnect()
