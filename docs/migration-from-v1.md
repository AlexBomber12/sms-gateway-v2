# Migration from the AI-Server gammu relay (v1)

This runbook is the one-time procedure for cutting over from the legacy
SMS gateway running on AI-Server (`192.168.50.4`, gammu + bash watchdog,
Huawei E353 dongle) to `sms-gateway-v2` running on the NAS host
(`192.168.50.2`, Docker, Quectel EC25-EUX dongle). Follow it
top-to-bottom on cutover day; the SMS outage window is bounded by the
SIM swap (a few minutes) and a smoke-test SMS.

The expected SMS gap during cutover is under 5 minutes if everything
goes to plan.

## Pre-flight (do this 24 hours before cutover)

These checks run on the NAS host while the legacy relay is still
serving production. They confirm v2 is ready before the SIM is moved.

1. **Modem detected.** The Quectel EC25-EUX must already be plugged
   into the NAS, but with no SIM yet. ModemManager should still see
   the device:
   ```bash
   mmcli -L
   ```
   The output should list a modem path such as
   `/org/freedesktop/ModemManager1/Modem/0`. If nothing is listed,
   stop and resolve the USB/ModemManager problem first — see
   `docs/deploy.md` for the polkit and systemd prerequisites.
2. **Container starts cleanly with the relay disabled.** Bring v2 up
   in metrics-only mode so it cannot accidentally forward anything:
   ```bash
   cp .env.example deploy/.env
   # Edit deploy/.env:
   #   HOST=0.0.0.0
   #   RELAY_ENABLED=false
   #   TELEGRAM_BOT_TOKEN=<bot token>
   #   TELEGRAM_CHAT_ID=<numeric chat id>
   docker compose -f deploy/docker-compose.yml up -d
   curl -s http://127.0.0.1:8091/healthz
   curl -s http://127.0.0.1:8091/metrics | head
   ```
   `/healthz` must return `{"status":"ok"}`. Stop the container after
   verifying:
   ```bash
   docker compose -f deploy/docker-compose.yml down
   ```
3. **Telegram credentials are valid.** Send a manual test message
   directly via the Bot API so you know the token and chat id work
   without involving v2:
   ```bash
   curl -sS "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
       --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
       --data-urlencode "text=v2 cutover preflight"
   ```
   The response JSON must contain `"ok":true`. If you plan to do the
   smoke test in a separate test chat first, set
   `TELEGRAM_CHAT_ID` in `deploy/.env` to the test chat now and switch
   to the production chat after the smoke test passes (see "Smoke
   test alternative" at the bottom of this document).
4. **Pre-populate the dedup database.** Copy the gammu inbox database
   off AI-Server and run the importer so v2 already knows about
   messages the legacy relay forwarded recently:
   ```bash
   scp ai-server:/var/spool/gammu/sms.db /tmp/gammu-inbox.db
   python scripts/import-gammu-dedup.py \
       --gammu-db /tmp/gammu-inbox.db \
       --target-db ./state/dedup.db
   ```
   The script prints `rows_read=N inserted=M duplicates_skipped=K`.
   `inserted` should match the size of the gammu inbox; `duplicates`
   only means the script was run twice. If your legacy relay uses
   MySQL or a non-standard schema, dump the inbox to CSV with header
   `number,text,timestamp` and pass `--csv` instead of `--gammu-db`.

## Cutover (the actual switchover)

1. **Stop the legacy relay on AI-Server.** SMS forwarding stops here;
   the cutover clock starts:
   ```bash
   ssh ai-server "sudo systemctl stop sms-gateway-v1"
   ```
   (Substitute the actual unit name if different — check
   `systemctl list-units | grep -i sms` on AI-Server first.)
2. **Move the MTS SIM to the NAS.** Pull the SIM from the Huawei E353
   on AI-Server and insert it in the Quectel EC25-EUX on the NAS. The
   EC25-EUX takes a standard mini-SIM; bring a full-size adapter if
   the SIM is a micro/nano in a tray. Confirm the modem now sees the
   SIM:
   ```bash
   mmcli -m 0
   mmcli -m 0 --messaging-list
   ```
   The state should be `registered` (or `connected`) and the
   operator should match MTS-roaming-on-Italian-network.
3. **Start v2 in relay-enabled mode.** Edit `deploy/.env` so
   `RELAY_ENABLED=true` and the Telegram credentials point at the
   production chat. Then:
   ```bash
   docker compose -f deploy/docker-compose.yml up -d
   docker compose -f deploy/docker-compose.yml logs -f
   ```
   Look for the `relay_started` event with `recovered=0` (the queue
   is empty on first start). The container should reach
   `Up (healthy)` within ~40 seconds.
4. **Smoke test.** From a second phone, send an SMS to the MTS
   number. Within 30 seconds it should appear in the Telegram chat
   you configured. While you wait, watch for log events
   `queue_item_enqueued`, `queue_item_claimed`, and `queue_item_sent`
   in order.

If the smoke-test SMS arrives in Telegram, cutover is done. Move on
to "Post-cutover".

## Post-cutover

1. **Leave the legacy unit installed but stopped for one week.** Do
   not uninstall gammu yet — keep it as a fallback path for the first
   week:
   ```bash
   ssh ai-server "sudo systemctl status sms-gateway-v1"
   # active (exited) or inactive (dead) is the expected state
   ```
2. **Wire v2 into Prometheus.** On the monitoring host, add a scrape
   target for `http://192.168.50.2:8091/metrics`. The dead-man
   heartbeat metric exposed by v2 is the canonical liveness signal —
   add an alert that fires when `last_sms_received_seconds` exceeds 7
   days *and* no heartbeat has been delivered in the same window
   (heartbeat-only silence is normal during low-volume periods; see
   PR-007 for the heartbeat semantics).
3. **Decommission the legacy relay after one week.** Once v2 has run
   clean for seven calendar days:
   ```bash
   ssh ai-server "sudo systemctl disable --now sms-gateway-v1"
   ssh ai-server "sudo apt-get remove --purge gammu gammu-smsd"
   ssh ai-server "sudo crontab -l"   # remove any sms-gateway-v1 cron lines
   ```
   Update homelab notes to point at the NAS host and reflect that
   AI-Server no longer owns SMS forwarding.

## Fallback (if v2 misbehaves in the first hour)

If the smoke test fails, or v2 emits errors that block forwarding
within the first hour after cutover, revert to v1:

1. **Stop v2.**
   ```bash
   docker compose -f deploy/docker-compose.yml down
   ```
2. **Move the SIM back to the Huawei E353 on AI-Server.** Reverse the
   physical swap from step 2 of cutover.
3. **Start the legacy unit.**
   ```bash
   ssh ai-server "sudo systemctl start sms-gateway-v1"
   ```
   Confirm SMS forwarding works again by sending a test SMS from a
   second phone — it should land in Telegram via the legacy relay
   within a minute.
4. **File a bug.** Open an issue in this repository titled
   `cutover failed on YYYY-MM-DD`, attaching:
   - `docker compose -f deploy/docker-compose.yml logs --no-color sms-gateway-v2 > v2.log`
   - The output of `mmcli -m 0` and `mmcli -m 0 --messaging-list`
     captured before reverting
   - Anything Telegram itself returned (HTTP errors, 4xx codes)

## Smoke test alternative: separate test chat

If you would rather smoke-test against a dedicated test Telegram chat
before sending production traffic to the real chat, do this during
cutover step 3:

1. Set `TELEGRAM_CHAT_ID` in `deploy/.env` to the test chat id.
2. Run the cutover steps and verify the smoke-test SMS lands in the
   test chat.
3. Edit `deploy/.env` to point at the production chat id and
   `docker compose -f deploy/docker-compose.yml restart`.
4. Send a second SMS to confirm it now lands in the production chat.

The dedup database remembers the smoke-test SMS via its content hash,
so you do not need to worry about the test message being re-forwarded
to the production chat after the restart.
