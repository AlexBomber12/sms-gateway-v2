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
system D-Bus. With Docker's default (no user-namespace remapping) the
container's uid `1000` is the same identity as the host account that owns
uid `1000`, so polkit sees that host user as the caller. ModemManager gates
most of its interfaces behind polkit, so granting access requires a
dedicated rule.

Polkit `.rules` files use a JavaScript API where `subject.user` is the
caller's **username string**, not a UID — the legacy `"#1000"` syntax only
works in the deprecated `.pkla` format and silently never matches here.
The portable fix is to match on group membership instead: create a
dedicated group, add the host user that owns uid `1000` to it, then have
the rule check `subject.isInGroup(...)`.

Create the group and add the host user that maps to the container uid:

```bash
sudo groupadd -f sms-gateway
sudo usermod -aG sms-gateway "$(getent passwd 1000 | cut -d: -f1)"
```

Create `/etc/polkit-1/rules.d/50-sms-gateway-v2.rules` on the host with:

```javascript
// Allow members of the sms-gateway group (which includes the host user
// whose uid the container shares) to manage modems and read SMS via
// ModemManager. Limit to ModemManager interfaces only.
polkit.addRule(function(action, subject) {
    if (action.id.indexOf("org.freedesktop.ModemManager1.") === 0 &&
        subject.isInGroup("sms-gateway")) {
        return polkit.Result.YES;
    }
});
```

Reload polkit so the rule takes effect, then restart the container so its
process picks up the new group membership:

```bash
sudo systemctl restart polkit
docker compose -f deploy/docker-compose.yml restart sms-gateway-v2
```

### Alternative: run the container as root

If the polkit setup is too painful for your environment, you can run the
container as `root` by adding `user: "0:0"` to the service in
`deploy/docker-compose.yml`. This bypasses the uid mapping problem entirely
because polkit grants root unconditional access to ModemManager. The
tradeoff is that any compromise of the container process runs as root on
the host's D-Bus, which weakens isolation. The polkit-rule path is
preferred for a single-tenant home setup.

## First run

From the repository root on the NAS host:

```bash
cp .env.example deploy/.env
# Edit deploy/.env and set at least:
#   RELAY_ENABLED=true
#   TELEGRAM_BOT_TOKEN=<bot token from BotFather>
#   TELEGRAM_CHAT_ID=<numeric chat id, can be negative for groups>

docker compose -f deploy/docker-compose.yml up -d
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
docker compose -f deploy/docker-compose.yml ps
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
docker compose -f deploy/docker-compose.yml pull
docker compose -f deploy/docker-compose.yml up -d
```

The named volume persists across image upgrades, so the queue and dedup
database survive the restart.

## Logs

```bash
docker compose -f deploy/docker-compose.yml logs -f sms-gateway-v2
```

Logs are emitted as `structlog` JSON on stdout. Filter with `jq` if needed:

```bash
docker compose -f deploy/docker-compose.yml logs --no-color sms-gateway-v2 \
    | jq -r 'select(.level == "error")'
```
