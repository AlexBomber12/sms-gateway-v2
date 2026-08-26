from __future__ import annotations

import stat
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_apparmor_profile_allows_modem_enable() -> None:
    profile = (PROJECT_ROOT / "deploy/apparmor/sms-gateway-v2").read_text()

    assert "Modem.Enable for disabled-state recovery" in profile
    assert "member={Reset,Enable}," in profile


def test_apparmor_profile_accepts_lifecycle_signals_without_peer() -> None:
    profile = (PROJECT_ROOT / "deploy/apparmor/sms-gateway-v2").read_text()
    lifecycle_rule = "signal (receive) set=(term, kill, cont, stop, hup, int, quit, usr1, usr2),"

    assert lifecycle_rule in profile
    assert all(
        "peer=" not in line
        for line in profile.splitlines()
        if line.lstrip().startswith("signal (receive)")
    )


def test_apparmor_profile_keeps_self_peer_send_rule() -> None:
    profile = (PROJECT_ROOT / "deploy/apparmor/sms-gateway-v2").read_text()

    assert (
        "signal (send, receive) set=(term, kill, cont, stop, hup, int, quit, usr1, usr2) "
        "peer=sms-gateway-v2,"
    ) in profile


def test_apparmor_profile_documents_peer_omission() -> None:
    profile = (PROJECT_ROOT / "deploy/apparmor/sms-gateway-v2").read_text()

    assert "Do not add a peer= clause to this inbound rule" in profile
    assert "runtime label varies" in profile


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


def test_deploy_docs_document_slow_recreate_symptom() -> None:
    docs = (PROJECT_ROOT / "docs/deploy.md").read_text()

    assert "`docker compose up -d --force-recreate` completes in roughly one second" in docs
    assert "ten seconds or more means SIGTERM is not reaching the process" in docs
    assert 'operation="signal"' in docs
