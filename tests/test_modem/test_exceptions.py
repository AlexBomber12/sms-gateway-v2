from __future__ import annotations

from sms_gateway_v2.modem import (
    MessageDeleteFailed,
    ModemBusy,
    ModemError,
    ModemManagerUnavailable,
    ModemNotFound,
)


def test_exception_hierarchy() -> None:
    assert issubclass(ModemManagerUnavailable, ModemError)
    assert issubclass(ModemNotFound, ModemError)
    assert issubclass(ModemBusy, ModemError)
    assert issubclass(MessageDeleteFailed, ModemError)
