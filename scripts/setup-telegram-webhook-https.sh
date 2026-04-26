#!/usr/bin/env bash
# Configure public HTTPS + Telegram webhook mode for the ABLE gateway on the
# DigitalOcean host. Run on the server as root.
#
# Usage:
#   bash scripts/setup-telegram-webhook-https.sh [domain]
#
# If no domain is supplied, the script uses <public-ip>.sslip.io, which resolves
# automatically and is enough to get Telegram webhooks working without buying or
# configuring DNS.

set -euo pipefail

APP_DIR="${ABLE_SERVER_DIR:-/opt/able}"
ENV_FILE="${APP_DIR}/.env"
PORT="${ABLE_GATEWAY_PORT:-8080}"
DOMAIN="${1:-}"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run this on the server as root."
  exit 1
fi

if ! command -v curl >/dev/null 2>&1; then
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y curl
fi

if ! command -v openssl >/dev/null 2>&1; then
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y openssl
fi

strip_domain() {
  printf '%s' "$1" | sed -E 's#^https?://##; s#/.*$##; s#/$##'
}

if [ -z "$DOMAIN" ]; then
  PUBLIC_IP="${PUBLIC_IP:-$(curl -fsS https://api.ipify.org || true)}"
  if [ -z "$PUBLIC_IP" ]; then
    PUBLIC_IP="$(hostname -I | awk '{print $1}')"
  fi
  DOMAIN="${PUBLIC_IP}.sslip.io"
else
  DOMAIN="$(strip_domain "$DOMAIN")"
fi

if [ -z "$DOMAIN" ]; then
  echo "Could not determine a domain. Pass one explicitly."
  exit 1
fi

install -d -m 700 "$APP_DIR"
touch "$ENV_FILE"
chmod 600 "$ENV_FILE"

env_value() {
  local key="$1"
  grep "^${key}=" "$ENV_FILE" 2>/dev/null | tail -n 1 | cut -d= -f2- || true
}

upsert_env() {
  local key="$1"
  local value="$2"
  local tmp
  tmp="$(mktemp)"
  awk -v key="$key" -v value="$value" '
    BEGIN { written = 0 }
    $0 ~ "^" key "=" {
      print key "=" value
      written = 1
      next
    }
    { print }
    END {
      if (!written) {
        print key "=" value
      }
    }
  ' "$ENV_FILE" > "$tmp"
  cat "$tmp" > "$ENV_FILE"
  rm -f "$tmp"
}

WEBHOOK_SECRET="${ABLE_TELEGRAM_WEBHOOK_SECRET:-$(env_value ABLE_TELEGRAM_WEBHOOK_SECRET)}"
if [ -z "$WEBHOOK_SECRET" ]; then
  WEBHOOK_SECRET="$(openssl rand -hex 32)"
fi

WEBHOOK_BASE="https://${DOMAIN}"
WEBHOOK_ENDPOINT="${WEBHOOK_BASE}/webhook/telegram"

echo "=== Configuring Caddy HTTPS reverse proxy ==="
if ! command -v caddy >/dev/null 2>&1; then
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y caddy
fi

cat > /etc/caddy/Caddyfile <<EOF
${DOMAIN} {
  encode gzip
  reverse_proxy 127.0.0.1:${PORT}
}
EOF

caddy validate --config /etc/caddy/Caddyfile
systemctl enable --now caddy
systemctl reload caddy

if command -v ufw >/dev/null 2>&1 && ufw status | grep -q "Status: active"; then
  ufw allow 80/tcp >/dev/null || true
  ufw allow 443/tcp >/dev/null || true
fi

echo "=== Writing ABLE webhook env ==="
upsert_env "ABLE_TELEGRAM_MODE" "webhook"
upsert_env "ABLE_TELEGRAM_WEBHOOK_URL" "$WEBHOOK_ENDPOINT"
upsert_env "ABLE_TELEGRAM_WEBHOOK_SECRET" "$WEBHOOK_SECRET"
upsert_env "ABLE_TELEGRAM_ENABLED" "1"
upsert_env "ABLE_CRON_ENABLED" "1"
chmod 600 "$ENV_FILE"

if [ -f "${APP_DIR}/docker-compose.yml" ] && command -v docker >/dev/null 2>&1; then
  echo "=== Restarting ABLE container ==="
  (cd "$APP_DIR" && docker compose up -d --force-recreate)
fi

echo "=== Local health ==="
curl -fsS "http://127.0.0.1:${PORT}/health" || true
echo

echo "=== Public health ==="
curl -fsS "${WEBHOOK_BASE}/health" || true
echo

cat <<EOF

Telegram webhook endpoint:
  ${WEBHOOK_ENDPOINT}

Set these GitHub secrets from your Mac so future deploys keep webhook mode:
  gh secret set ABLE_TELEGRAM_MODE --repo iamthetonyb/ablemon --body webhook
  gh secret set ABLE_TELEGRAM_WEBHOOK_URL --repo iamthetonyb/ablemon --body ${WEBHOOK_ENDPOINT}
  gh secret set ABLE_TELEGRAM_WEBHOOK_SECRET --repo iamthetonyb/ablemon --body ${WEBHOOK_SECRET}

Verify after restart:
  cd /opt/able
  docker compose logs --since=5m able | grep -iE 'Conflict|getUpdates' || echo 'no Telegram polling conflicts'
  curl -fsS http://127.0.0.1:${PORT}/health | python3 -m json.tool

Expected health fields:
  "telegram_mode": "webhook"
  "telegram_polling_enabled": false
  "telegram_webhook_enabled": true
EOF
