from __future__ import annotations

import stat
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_apparmor_profile_allows_modem_enable() -> None:
    profile = (PROJECT_ROOT / "deploy/apparmor/sms-gateway-v2").read_text()

    assert "Modem.Enable for disabled-state recovery" in profile
    assert "member={Reset,Enable}," in profile


def test_modem_enable_service_invokes_dynamic_script() -> None:
    service = (PROJECT_ROOT / "deploy/systemd/sms-gateway-modem-enable.service").read_text()

    assert "PartOf=ModemManager.service" in service
    assert "ExecStartPre=/bin/sleep 10" in service
    assert "ExecStart=/usr/local/sbin/sms-gateway-modem-enable.sh" in service


def test_modem_enable_script_is_executable_and_dynamic() -> None:
    script_path = PROJECT_ROOT / "deploy/systemd/sms-gateway-modem-enable.sh"
    script = script_path.read_text()

    assert script_path.stat().st_mode & stat.S_IXUSR
    assert "MAX_ATTEMPTS=10" in script
    assert "find_modem_index()" in script
    assert "registered|enabled|searching|connecting|connected)" in script
    assert "power state is ${power_state}; physical power cycle required" in script


def test_deploy_docs_cover_modem_recovery_scenarios() -> None:
    docs = (PROJECT_ROOT / "docs/deploy.md").read_text()

    assert "ModemManager restart leaves modem disabled" in docs
    assert "D-Bus path migration after USB re-enumeration" in docs
    assert "Firmware freeze with power state off" in docs
    assert "sudo install -m 0755 deploy/systemd/sms-gateway-modem-enable.sh" in docs
    assert "sudo apparmor_parser -r -W /etc/apparmor.d/sms-gateway-v2" in docs
