from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.test_modem.factories import make_fake_messaging_proxy


@pytest.fixture
def fake_bus() -> MagicMock:
    bus = MagicMock()
    bus.connected = True
    bus.connect = AsyncMock(return_value=bus)
    bus.disconnect = MagicMock()
    bus.introspect = AsyncMock(return_value=object())
    bus.get_proxy_object = MagicMock()
    return bus


@pytest.fixture
def fake_modem_props() -> MagicMock:
    props = MagicMock()
    props.get_manufacturer = AsyncMock(return_value="Quectel")
    props.get_model = AsyncMock(return_value="EC25-EUX")
    props.get_equipment_identifier = AsyncMock(return_value="867698040000001")
    props.get_primary_port = AsyncMock(return_value="ttyUSB2")
    props.get_state = AsyncMock(return_value="registered")
    props.get_signal_quality = AsyncMock(return_value=(76, True))
    props.get_sim = AsyncMock(return_value="/org/freedesktop/ModemManager1/SIM/0")
    props.call_enable = AsyncMock(return_value=None)
    return props


@pytest.fixture
def fake_modem_proxy(fake_modem_props: MagicMock) -> MagicMock:
    modem_3gpp = MagicMock()
    modem_3gpp.get_registration_state = AsyncMock(return_value=5)
    modem_3gpp.get_operator_name = AsyncMock(return_value="vodafone IT")
    modem_3gpp.get_operator_code = AsyncMock(return_value="22210")

    proxy = MagicMock()
    proxy.modem_3gpp = modem_3gpp
    proxy.get_interface.side_effect = lambda name: {
        "org.freedesktop.ModemManager1.Modem": fake_modem_props,
        "org.freedesktop.ModemManager1.Modem.Modem3gpp": modem_3gpp,
    }[name]
    return proxy


@pytest.fixture
def fake_messaging_proxy() -> MagicMock:
    return make_fake_messaging_proxy()


@pytest.fixture
def fake_sim_proxy() -> MagicMock:
    sim = MagicMock()
    sim.get_imsi = AsyncMock(return_value="250010123456789")
    sim.get_operator_name = AsyncMock(return_value="MTS RUS")
    sim.get_operator_identifier = AsyncMock(return_value="25001")

    proxy = MagicMock()
    proxy.sim = sim
    proxy.get_interface.return_value = sim
    return proxy


@pytest.fixture
def fake_sms_proxy() -> MagicMock:
    sms = MagicMock()
    sms.get_number = AsyncMock(return_value="+15551234567")
    sms.get_text = AsyncMock(return_value="hello")
    sms.get_timestamp = AsyncMock(return_value="2026-04-26T10:41:33+00:00")
    sms.get_pdu_type = AsyncMock(return_value="deliver")

    proxy = MagicMock()
    proxy.sms = sms
    proxy.get_interface.return_value = sms
    return proxy
