from __future__ import annotations

from sms_gateway_v2.relay.exceptions import RelayError, RelayNotRunning


def test_relay_exceptions_share_base_class() -> None:
    assert issubclass(RelayNotRunning, RelayError)
