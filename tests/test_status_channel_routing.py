"""Offline tests for routing heartbeat without touching SMS delivery."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from route_heartbeat_chat import rewrite_env

SAMPLE = "RELAY_ENABLED=true\nTELEGRAM_BOT_TOKEN=test123:private\nTELEGRAM_CHAT_ID=123456\n"
CHANNEL = "-1001234567890"


def test_add_status_channel_keeps_sms_credentials_and_is_idempotent():
    result = rewrite_env(SAMPLE, CHANNEL)
    assert result == SAMPLE + "HEARTBEAT_TELEGRAM_CHAT_ID=" + CHANNEL + "\n"
    assert rewrite_env(result, CHANNEL) == result


def test_change_existing_and_restore_original_configuration():
    existing = SAMPLE + "HEARTBEAT_TELEGRAM_CHAT_ID=-1009999999999\n"
    changed = rewrite_env(existing, CHANNEL)
    assert "HEARTBEAT_TELEGRAM_CHAT_ID=" + CHANNEL in changed
    assert rewrite_env(changed, None) == SAMPLE


def test_missing_final_newline():
    result = rewrite_env(SAMPLE.rstrip("\n"), CHANNEL)
    assert "TELEGRAM_CHAT_ID=123456\nHEARTBEAT_TELEGRAM_CHAT_ID=" + CHANNEL in result


def test_reject_duplicate_or_missing_primary():
    with pytest.raises(ValueError, match="SMS destination"):
        rewrite_env("TELEGRAM_BOT_TOKEN=token\n", CHANNEL)
    with pytest.raises(ValueError, match="SMS destination"):
        rewrite_env(SAMPLE + "TELEGRAM_CHAT_ID=another\n", CHANNEL)
    with pytest.raises(ValueError, match="status channel"):
        rewrite_env(
            SAMPLE + "HEARTBEAT_TELEGRAM_CHAT_ID=1\nHEARTBEAT_TELEGRAM_CHAT_ID=2\n",
            CHANNEL,
        )


@pytest.mark.parametrize("invalid", ["999999", "-10042", "not-a-chat", "-100abc", ""])
def test_reject_invalid_channel(invalid):
    with pytest.raises(ValueError, match="private channel"):
        rewrite_env(SAMPLE, invalid)


def test_never_route_sms_to_infrastructure_channel():
    with pytest.raises(ValueError, match="must differ"):
        rewrite_env(SAMPLE, "123456")


def test_preserve_original_secret_fields_byte_for_byte():
    original = (
        "# comment\n"
        "RELAY_ENABLED=true\n"
        "TELEGRAM_BOT_TOKEN='fake-token'\n"
        'TELEGRAM_CHAT_ID="123456"\n'
    )
    result = rewrite_env(original, CHANNEL)
    assert result.startswith(original)
    assert rewrite_env(result, None) == original
