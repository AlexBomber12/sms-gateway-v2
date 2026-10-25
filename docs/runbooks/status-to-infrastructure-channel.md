# Route SMS Gateway status to the private infrastructure Telegram channel

This runbook uses the **existing** SMS Gateway v2 heartbeat scheduler. It does not deploy a monitoring service, move SMS messages, modify Telegram credentials, or change the heartbeat interval.

## Verified prerequisites (2026-10-25)

- The production container `sms-gateway-v2` on SERVER is healthy.
- Its actual Docker Compose path is `/home/alexey/repos/sms-gateway-v2/deploy/docker-compose.yml`.
- The current container does not have `HEARTBEAT_TELEGRAM_CHAT_ID` set.
- Its existing SMS bot (`@MTS_SMS_Forwarder_bot`) was verified as a publishing admin in the private infrastructure channel.
- A one-time message to the infrastructure channel via that bot was delivered.
- No infrastructure notification token needs to be installed on SERVER for this route.

## Design

`TELEGRAM_CHAT_ID` remains the private **SMS delivery** destination. Only `HEARTBEAT_TELEGRAM_CHAT_ID` is added to `deploy/.env`. The existing heartbeat scheduler sends status to the new channel once per 86400 seconds of process uptime (normally daily). It does not detect total host failures, and is not an independent uptime monitor.

`deploy/.env` contains credentials. It is gitignored, must remain owner-only mode 0600, and must be backed up through the normal encrypted secret recovery procedure. **Never commit or print it.**

## Read-only checks before change

Run on SERVER after obtaining a reviewed source commit. The script may be run from a **separate clean checkout** and must point explicitly to the current production `deploy/.env` file; it does not assume the script checkout contains production credentials:

```bash
cd /home/alexey/repos/sms-gateway-v2
python3 scripts/route_heartbeat_chat.py check --env-file /home/alexey/repos/sms-gateway-v2/deploy/.env

docker inspect -f '{{index .Config.Labels "com.docker.compose.project.working_dir"}}' sms-gateway-v2
docker inspect -f '{{index .Config.Labels "com.docker.compose.project.config_files"}}' sms-gateway-v2
docker inspect -f '{{index .Config.Labels "com.docker.compose.project"}}' sms-gateway-v2
docker inspect -f '{{.Image}}' sms-gateway-v2
docker image inspect ghcr.io/alexbomber12/sms-gateway-v2:latest --format '{{.Id}}'

docker compose --env-file deploy/.env -f deploy/docker-compose.yml config --quiet
```

Before any Compose recreation, verify that the cached `:latest` image ID is **identical** to the currently running container's immutable image ID. Otherwise abort and prepare a reviewed pinned image update separately. The `--pull never` option prevents fetching a new image during this configuration-only operation.

Confirm no active, unrecoverable SMS/modem operation before a controlled restart. Durable SMS queue persists in the existing named volume; never use `docker compose down -v`.

## Apply exactly one configuration key

Use a **reviewed Git checkout** of this script. The channel ID is private operator configuration, not stored in this public repository.

```bash
: "${INFRA_CHANNEL_ID:?Set the private numeric infrastructure channel ID}"
python3 scripts/route_heartbeat_chat.py set --env-file /home/alexey/repos/sms-gateway-v2/deploy/.env --chat-id="$INFRA_CHANNEL_ID"
python3 scripts/route_heartbeat_chat.py check --env-file /home/alexey/repos/sms-gateway-v2/deploy/.env
```

This idempotent program preserves `TELEGRAM_CHAT_ID` and `TELEGRAM_BOT_TOKEN` byte-for-byte, rejects duplicates/invalid destinations, refuses insecure or symlinked `deploy/.env`, and writes the file atomically. It does **not** restart the container.

Once ready, apply the already reviewed local image **to this service only**. Respect the existing Compose project name, obtained from Docker rather than guessing:

```bash
project="$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project"}}' sms-gateway-v2)"
test -n "$project"
docker compose -p "$project" --env-file deploy/.env -f deploy/docker-compose.yml \
  up -d --no-deps --no-build --pull never sms-gateway-v2
```

Verify:

```bash
docker inspect -f '{{.State.Health.Status}}' sms-gateway-v2
curl --fail --silent http://127.0.0.1:8091/healthz
python3 scripts/route_heartbeat_chat.py check --env-file /home/alexey/repos/sms-gateway-v2/deploy/.env
```

Verify with a safe container environment metadata check (print only `True`/`False`) that the status chat is configured and `TELEGRAM_CHAT_ID` did not change from the original. Do not dump `docker compose config` or `docker inspect .Config.Env` to terminal because these contain the real bot token and SMS destination.

The first scheduled status post can occur **up to 24 hours after the SMS Gateway process restarts**. The one-time Telegram API permission/delivery test was already performed independently; do not shorten the heartbeat interval just to force another message.

## Rollback

If the user decides to revert the route, restore the original lack of a dedicated heartbeat setting and recreate only SMS Gateway using the same verified, locally cached image:

```bash
python3 scripts/route_heartbeat_chat.py unset --env-file /home/alexey/repos/sms-gateway-v2/deploy/.env
python3 scripts/route_heartbeat_chat.py check --env-file /home/alexey/repos/sms-gateway-v2/deploy/.env
project="$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project"}}' sms-gateway-v2)"
docker compose -p "$project" --env-file deploy/.env -f deploy/docker-compose.yml \
  up -d --no-deps --no-build --pull never sms-gateway-v2
```

Removing this optional key restores the original fallback to the existing SMS chat for heartbeat status. **It does not affect SMS delivery.** It is not necessary to stop the service or restart the host to revert a pending but not-yet-applied `.env` edit.

## Acceptance

1. Previously used SMS chat remains unchanged, with SMS relay and queue still healthy.
2. Telegram infrastructure channel receives a daily status from the SMS Gateway bot.
3. No new service, database, credential copy, polling process, or server restart is introduced.
4. Source code and this runbook are in Git; the only deployment-specific setting remains inside the existing protected `deploy/.env`.
