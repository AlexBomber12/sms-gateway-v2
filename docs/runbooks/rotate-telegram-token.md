# Rotate Telegram Bot Token

## When to rotate

Rotate the Telegram bot token when any of these conditions apply:

- The token was exposed in a chat, screenshot, screen share, terminal recording, log, or
  other surface that another person or system can read.
- An operator with access to the token leaves the project or no longer needs access.
- The annual rotation window arrives, or a major release is being prepared.

## Prerequisites

- SSH access to the NAS host running the `sms-gateway-v2` container.
- Ability to chat with `@BotFather` from the Telegram account that owns or administers
  the bot.
- The production chat ID, currently `136565198`, or the operator-specific chat ID
  documented in `deploy/.env`.
- The new token must be handled only through the system clipboard. Do not paste it into
  chat windows, shell commands, issue trackers, or other logged surfaces.

## Procedure

1. Open a chat with `@BotFather`, send `/mybots`, select the bot used by
   `sms-gateway-v2`, choose `API Token`, then choose `Revoke current token`. Confirm the
   revocation. BotFather replies with the new token.

2. Copy the new token to the system clipboard. Do not paste it into chat windows,
   terminal commands, or any logged surface.

3. SSH to the production host.

   ```bash
   ssh 192.168.50.2
   ```

4. Change to the deployment directory.

   ```bash
   cd ~/repos/sms-gateway-v2/deploy
   ```

5. Verify that `.env` exists and is readable only by the owner.

   ```bash
   ls -la .env
   ```

6. Copy `.env` to a temporary replacement file, preserving permissions.

   ```bash
   cp -p .env .env.new
   ```

7. Open the temporary replacement file in an editor.

   ```bash
   ${EDITOR:-nano} .env.new
   ```

8. Replace the `TELEGRAM_BOT_TOKEN` value with the new token from the clipboard, using
   this shape, then save and close the editor.

   ```dotenv
   TELEGRAM_BOT_TOKEN=<NEW_TOKEN>
   ```

9. Verify the token setting without dumping the full value.

   ```bash
   grep TELEGRAM_BOT_TOKEN .env.new | head -c 30 && echo
   ```

10. Atomically replace the active env file.

    ```bash
    mv .env.new .env
    ```

11. Restart the container. The durable queue and deduplication database are stored in the
    Docker volume, so this recreate does not discard in-flight queue items.

    ```bash
    docker compose -p sms-gateway-v2 up -d --force-recreate
    ```

12. Watch logs for the next heartbeat or SMS delivery.

    ```bash
    docker compose -p sms-gateway-v2 logs --tail=20 -f
    ```

13. Confirm that the logs contain a successful Telegram delivery event.

    ```text
    telegram_send_success status_code=200
    ```

14. Verify in Telegram that the bot's next message appears in the expected chat. The
    first heartbeat may not arrive for up to 24 hours per `HEARTBEAT_INTERVAL_SECONDS`;
    for an immediate test, send any SMS to the modem.

## Rollback

If the new token fails and logs show `telegram_send_failed` events with
`401 Unauthorized`, the old token is already revoked and cannot be restored.

Recover by immediately generating another brand-new token through the same BotFather
flow: open `@BotFather`, send `/mybots`, select the bot, choose `API Token`, choose
`Revoke current token`, and apply the new token with the procedure above.

There is no way to go back to the prior token.

## Cleanup

After 24 hours of stable operation, search the operator's chat history and any external
systems that might have captured the old token string, such as Slack or email drafts,
and purge any traces.

Revoked tokens are dead on Telegram's side; cleanup is for credential hygiene, not for
restoring security after revocation.
