# Telegram Webhook Setup

ABLE supports Telegram in `off`, `polling`, and `webhook` modes. Production should use webhook mode so Telegram pushes each update to one HTTPS endpoint instead of multiple processes competing through `getUpdates`.

## Fast Path With No Domain

Run this on the DigitalOcean server as `root`:

```bash
curl -fsSL https://raw.githubusercontent.com/iamthetonyb/ablemon/main/scripts/setup-telegram-webhook-https.sh -o /tmp/setup-telegram-webhook-https.sh
bash /tmp/setup-telegram-webhook-https.sh
```

With no argument, the script uses `<server-public-ip>.sslip.io`. For the current server, that becomes:

```text
https://146.190.142.68.sslip.io/webhook/telegram
```

The script installs Caddy, reverse-proxies public HTTPS to `127.0.0.1:8080`, writes `/opt/able/.env`, restarts the Docker container if compose is present, and prints the GitHub secret commands needed to keep future deploys in webhook mode.

## Domain Path

If you own a domain, point an `A` record at the server IP first:

```text
able.example.com -> 146.190.142.68
```

Then run:

```bash
curl -fsSL https://raw.githubusercontent.com/iamthetonyb/ablemon/main/scripts/setup-telegram-webhook-https.sh -o /tmp/setup-telegram-webhook-https.sh
bash /tmp/setup-telegram-webhook-https.sh able.example.com
```

## Keep Future GitHub Deploys In Webhook Mode

After the script prints the generated secret, run the printed commands on your Mac:

```bash
gh secret set ABLE_TELEGRAM_MODE --repo iamthetonyb/ablemon --body webhook
gh secret set ABLE_TELEGRAM_WEBHOOK_URL --repo iamthetonyb/ablemon --body https://146.190.142.68.sslip.io/webhook/telegram
gh secret set ABLE_TELEGRAM_WEBHOOK_SECRET --repo iamthetonyb/ablemon --body <printed-secret>
```

Then redeploy:

```bash
gh workflow run deploy.yml --repo iamthetonyb/ablemon
```

## Verify

On the server:

```bash
cd /opt/able
curl -fsS http://127.0.0.1:8080/health | python3 -m json.tool
docker compose logs --since=5m able | grep -iE 'Conflict|getUpdates' || echo 'no Telegram polling conflicts'
```

Expected health fields:

```json
{
  "telegram_mode": "webhook",
  "telegram_polling_enabled": false,
  "telegram_webhook_enabled": true
}
```

## Notes

- Telegram requires public HTTPS for webhooks. `http://146.190.142.68:8080` is not enough.
- If public health fails but local health works, open ports `80/tcp` and `443/tcp` in the DigitalOcean firewall for the droplet.
- The deploy workflow now preserves existing server webhook env values when GitHub webhook secrets are blank. Setting GitHub secrets is still preferred because it makes the remote state explicit.
- Do not run `docker volume prune`; ABLE cron and runtime state live in Docker volumes.
