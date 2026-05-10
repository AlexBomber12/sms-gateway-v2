# Deployment Guide

This guide covers running `sms-gateway-v2` on the NAS host
(`192.168.50.2`, Ubuntu 24.04) with Docker Compose.

For Telegram credential rotation, see
[`docs/runbooks/rotate-telegram-token.md`](runbooks/rotate-telegram-token.md).

## Prerequisites

- Ubuntu 24.04 host with `modemmanager` installed and active:
  ```bash
  sudo apt-get install -y modemmanager
  sudo systemctl enable --now ModemManager
  ```
- Docker Engine with the `compose` plugin (`docker compose version`).
- A Quectel EC25-EUX (or compatible) USB modem detected by ModemManager:
  ```bash
  mmcli -L
  ```
  The output should list at least one modem path such as
  `/org/freedesktop/ModemManager1/Modem/0`.

## Polkit configuration

The container runs as uid `1000` and talks to ModemManager over the host
system D-Bus. ModemManager gates SMS deletion behind polkit action
`org.freedesktop.ModemManager1.Messaging`, which normally requires an admin
authentication prompt. Containers and SSH sessions cannot satisfy that prompt
reliably, so the host must grant the service through a dedicated group.

Create the host group that the compose service joins:

```bash
sudo groupadd --system sms-gateway
SMS_GATEWAY_GROUP_GID="$(getent group sms-gateway | cut -d: -f3)"
```

Install the checked-in polkit rule:

```bash
sudo install -m 0644 deploy/polkit/10-sms-gateway.rules /etc/polkit-1/rules.d/
```

The rule grants ModemManager actions to callers in the `sms-gateway` group:

```javascript
polkit.addRule(function(action, subject) {
    if (action.id.indexOf("org.freedesktop.ModemManager1.") === 0 &&
        subject.isInGroup("sms-gateway")) {
        return polkit.Result.YES;
    }
});
```

Polkit picks up the rule on its next restart or after a host reboot. For
immediate effect, restart polkit:

```bash
sudo systemctl restart polkit
```

The compose service uses the numeric `SMS_GATEWAY_GROUP_GID` value for
`group_add`. Keep the host group name as `sms-gateway` for polkit, but pass the
numeric gid to Docker so startup does not depend on the container image having a
matching group name in `/etc/group`.

Verify the policy from a temporary tools container before recreating the service. The
runtime `ghcr.io/alexbomber12/sms-gateway-v2:latest` image intentionally does not
include `mmcli`, so build a throwaway image that contains only the ModemManager CLI:

```bash
cat >/tmp/sms-gateway-mmcli.Dockerfile <<'EOF'
FROM ubuntu:24.04
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        modemmanager \
    && rm -rf /var/lib/apt/lists/*
EOF

docker build -t sms-gateway-mmcli:local -f /tmp/sms-gateway-mmcli.Dockerfile /tmp

docker run --rm -it \
  --user 1000:1000 \
  --group-add "${SMS_GATEWAY_GROUP_GID}" \
  -v /var/run/dbus/system_bus_socket:/var/run/dbus/system_bus_socket:ro \
  sms-gateway-mmcli:local \
  mmcli -m 0 --messaging-list-sms

docker run --rm -it \
  --user 1000:1000 \
  --group-add "${SMS_GATEWAY_GROUP_GID}" \
  -v /var/run/dbus/system_bus_socket:/var/run/dbus/system_bus_socket:ro \
  sms-gateway-mmcli:local \
  mmcli -m 0 --messaging-delete-sms=0
```

Both commands should succeed. `--messaging-list-sms` confirms D-Bus access;
`--messaging-delete-sms=0` exercises the polkit-protected path. Use an SMS id
that exists on the modem when verifying deletion.

## AppArmor

On Ubuntu hosts, Docker's default `docker-default` AppArmor profile blocks
D-Bus method calls from the container to the system bus, including
ModemManager1 introspection. When this happens, startup fails with
`An AppArmor policy prevents this sender from sending this message to this recipient`.

The production compose file uses a custom `sms-gateway-v2` AppArmor profile.
Install and load it on the host before recreating the service:

```bash
sudo install -m 0644 deploy/apparmor/sms-gateway-v2 /etc/apparmor.d/sms-gateway-v2
sudo apparmor_parser -r -W /etc/apparmor.d/sms-gateway-v2
sudo aa-status | grep sms-gateway-v2
```

Recreate the container after the profile is loaded:

```bash
cd deploy
docker compose -p sms-gateway-v2 up -d --force-recreate
```

After about 30 seconds, check the host audit log for profile denials:

```bash
sudo dmesg | grep -i apparmor | tail -20
```

Any `apparmor="DENIED"` line that mentions `sms-gateway-v2` means the profile is
missing a rule for an observed runtime access. Report the denial upstream as a
follow-up PR with the full audit line and the operation that triggered it.
Absence of fresh DENIED entries after the recreate is the expected state.

If the custom profile breaks production behavior and the service must be brought
back online while debugging, temporarily change the host's local compose file to
`apparmor=unconfined` and recreate the container. Keep that change uncommitted.
This restores the pre-PR-014 posture and is acceptable for short operational
windows while collecting the missing AppArmor denial.

## First run

From the repository root on the NAS host:

```bash
cat >deploy/.env <<'EOF'
RELAY_ENABLED=true
SMS_GATEWAY_GROUP_GID=<output from getent group sms-gateway | cut -d: -f3>
TELEGRAM_BOT_TOKEN=<bot token from BotFather>
TELEGRAM_CHAT_ID=<numeric chat id, can be negative for groups>
EOF
chmod 600 deploy/.env

docker compose --env-file deploy/.env -f deploy/docker-compose.yml up -d
```

All other variables documented in `.env.example` have safe production defaults
and only need overriding if the operator wants different behavior.

`HOST` can be left unset (or set to any value) in `deploy/.env` — the compose
file pins `HOST=0.0.0.0` at the service level via `environment:`, which takes
precedence over `env_file:` and the image default. Uvicorn always binds to
`0.0.0.0` inside the container; the published `127.0.0.1:8091:8091` mapping
keeps the port reachable from the host only.

The image is pulled from `ghcr.io/alexbomber12/sms-gateway-v2:latest`. On
first start the named volume `sms-gateway-state` is created under
`/var/lib/docker/volumes/` and holds the durable queue plus the dedup
SQLite database. If you prefer a host bind mount, replace the volume entry
with:

```yaml
    volumes:
      - /var/run/dbus/system_bus_socket:/var/run/dbus/system_bus_socket:ro
      - /srv/sms-gateway-v2/state:/app/state
```

and `mkdir -p /srv/sms-gateway-v2/state && chown 1000:1000 /srv/sms-gateway-v2/state`
before starting the stack.

## Verifying

```bash
docker compose --env-file deploy/.env -f deploy/docker-compose.yml ps
# STATUS column should read "Up (healthy)" within ~40 seconds of startup.

curl -s http://127.0.0.1:8091/healthz
# {"status":"ok"}

curl -s http://127.0.0.1:8091/metrics | grep modem_state
# modem_state{state="..."} 1
```

If `/metrics` does not show `modem_state` labels, ModemManager is either
not detecting the modem or the polkit rule is not in effect. Check
`mmcli -L` on the host and `journalctl -u polkit` for denied actions.

## Updating

```bash
docker compose --env-file deploy/.env -f deploy/docker-compose.yml pull
docker compose --env-file deploy/.env -f deploy/docker-compose.yml up -d
```

The named volume persists across image upgrades, so the queue and dedup
database survive the restart.

## Published images

Merges to `main` and release tag pushes publish
`ghcr.io/alexbomber12/sms-gateway-v2:latest`, which is the tag consumed by
`deploy/docker-compose.yml`. The image workflow also publishes immutable
`sha-<short>` tags for main commits and `v*` tags for release pushes, so
operators can pin or roll back to a specific build.

After the first successful publish, make the GHCR package publicly readable one
time in GitHub repo Settings -> Packages. Without that package visibility
change, unauthenticated hosts without a GHCR token cannot run
`docker compose pull`.

## Modem state after host reboot

On Ubuntu 24.04, ModemManager does not consistently auto-enable USB modems on
boot. After a host reboot, `mmcli -m 0` may show `state: disabled` and
`signal: 0%`, which causes the relay to log `signal_read percent=0` and
`registration_read registration=unknown`.

Enable the modem once from the host:

```bash
sudo mmcli -m 0 --enable
```

This is a known limitation. A future systemd unit tracked separately will
automate the enable step. The relay container itself is healthy in this state
and resumes normal operation as soon as the modem registers; no container
restart is needed.

## Logs

```bash
docker compose --env-file deploy/.env -f deploy/docker-compose.yml logs -f sms-gateway-v2
```

Logs are emitted as `structlog` JSON on stdout. Filter with `jq` if needed:

```bash
docker compose --env-file deploy/.env -f deploy/docker-compose.yml logs --no-color sms-gateway-v2 \
    | jq -r 'select(.level == "error")'
```
