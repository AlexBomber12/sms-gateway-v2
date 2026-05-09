# Deployment Guide

This guide covers running `sms-gateway-v2` on the NAS host
(`192.168.50.2`, Ubuntu 24.04) with Docker Compose.

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

PR-014 will add a custom AppArmor profile to replace `apparmor=unconfined`.
When enabling that profile, keep the numeric `group_add` setting so polkit
continues to see the supplementary group on the container process.

## AppArmor

On Ubuntu hosts, Docker's default `docker-default` AppArmor profile blocks
D-Bus method calls from the container to the system bus, including
ModemManager1 introspection. When this happens, startup fails with
`An AppArmor policy prevents this sender from sending this message to this recipient`.

The production deploy compose file ships with `security_opt: [apparmor=unconfined]`
for the `sms-gateway-v2` service to bypass that host AppArmor denial. This means
the container loses Docker's default AppArmor mediation. The service is already
privileged with respect to ModemManager because it must talk to the host system
bus to function, while still running as uid `1000` instead of root and exposing
its only published port on `127.0.0.1`.

A future hardening improvement can replace the unconfined opt-out with a custom
AppArmor profile that narrowly permits only the required ModemManager D-Bus
paths.

## First run

From the repository root on the NAS host:

```bash
cp .env.example deploy/.env
# Edit deploy/.env and set at least:
#   RELAY_ENABLED=true
#   SMS_GATEWAY_GROUP_GID=<output from getent group sms-gateway | cut -d: -f3>
#   TELEGRAM_BOT_TOKEN=<bot token from BotFather>
#   TELEGRAM_CHAT_ID=<numeric chat id, can be negative for groups>

docker compose --env-file deploy/.env -f deploy/docker-compose.yml up -d
```

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

Merges to `main` publish `ghcr.io/alexbomber12/sms-gateway-v2:latest`, which is
the tag consumed by `deploy/docker-compose.yml`. The image workflow also
publishes immutable `sha-<short>` tags for main commits and `v*` tags for
release pushes, so operators can pin or roll back to a specific build.

After the first successful publish, make the GHCR package publicly readable one
time in GitHub repo Settings -> Packages. Without that package visibility
change, unauthenticated hosts without a GHCR token cannot run
`docker compose pull`.

## Logs

```bash
docker compose --env-file deploy/.env -f deploy/docker-compose.yml logs -f sms-gateway-v2
```

Logs are emitted as `structlog` JSON on stdout. Filter with `jq` if needed:

```bash
docker compose --env-file deploy/.env -f deploy/docker-compose.yml logs --no-color sms-gateway-v2 \
    | jq -r 'select(.level == "error")'
```
