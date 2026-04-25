#!/bin/bash
# Deploy ABLE gateway to any server via Docker.
#
# Usage:
#   ./deploy-to-server.sh <server_ip> [ssh_key]
#
# Prerequisites on server: SSH access as root. Docker will be auto-installed.
# The script pulls a pre-built image from GHCR — no building on the server.
#
# For secrets: copy able/.env to the server, or set them in GitHub Actions.

set -euo pipefail

SERVER_IP="${1:?Usage: ./deploy-to-server.sh <server_ip> [ssh_key]}"
SSH_KEY="${2:-${ABLE_SSH_KEY:-$HOME/.ssh/id_ed25519}}"
IMAGE="${ABLE_IMAGE:-ghcr.io/iamthetonyb/able-gateway:latest}"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${GREEN}═══════════════════════════════════════${NC}"
echo -e "${GREEN}  ABLE Gateway Deploy (Docker)         ${NC}"
echo -e "${GREEN}  Server: ${SERVER_IP}                 ${NC}"
echo -e "${GREEN}  Image:  ${IMAGE}                     ${NC}"
echo -e "${GREEN}═══════════════════════════════════════${NC}"

ssh_run() {
  ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "root@$SERVER_IP" "$@"
}

echo -e "${YELLOW}[1/4] Ensuring Docker...${NC}"
ssh_run '
  if ! command -v docker &>/dev/null; then
    curl -fsSL https://get.docker.com | sh
    systemctl enable --now docker
  fi
  docker --version
'

echo -e "${YELLOW}[2/4] Setting up /opt/able...${NC}"
ssh_run 'install -d -m 700 /opt/able'

# Upload .env if it exists locally
if [ -f "able/.env" ]; then
  echo -e "${YELLOW}Uploading .env...${NC}"
  scp -i "$SSH_KEY" -o StrictHostKeyChecking=no able/.env "root@$SERVER_IP:/opt/able/.env"
  ssh_run 'chmod 600 /opt/able/.env'
fi

# The server is the only cron leader. Local/dev gateways default to follower mode.
ssh_run "touch /opt/able/.env && \
  if grep -q '^ABLE_CRON_ENABLED=' /opt/able/.env; then \
    sed -i 's/^ABLE_CRON_ENABLED=.*/ABLE_CRON_ENABLED=1/' /opt/able/.env; \
  else \
    printf '\nABLE_CRON_ENABLED=1\n' >> /opt/able/.env; \
  fi && chmod 600 /opt/able/.env"

AUTH_VOLUME_LINE=""
if [ -f "$HOME/.able/auth.json" ]; then
  echo -e "${YELLOW}Uploading OAuth token...${NC}"
  ssh_run 'mkdir -p /opt/able/auth'
  scp -i "$SSH_KEY" -o StrictHostKeyChecking=no "$HOME/.able/auth.json" "root@$SERVER_IP:/opt/able/auth/auth.json"
  ssh_run 'chown 1000:1000 /opt/able/auth/auth.json && chmod 600 /opt/able/auth/auth.json'
  AUTH_VOLUME_LINE='      - /opt/able/auth/auth.json:/home/able/.able/auth.json:ro'
fi

echo -e "${YELLOW}[3/4] Writing docker-compose.yml...${NC}"
ssh_run "cat > /opt/able/docker-compose.yml" <<COMPOSE
services:
  able:
    image: ${IMAGE}
    container_name: able-gateway
    restart: unless-stopped
    ports:
      - "8080:8080"
    env_file:
      - .env
    environment:
      - ABLE_HOME=/home/able/.able
      - PYTHONUNBUFFERED=1
      - ABLE_CRON_ENABLED=1
    volumes:
      - able_data:/home/able/.able
      - able_db:/home/able/app/able/data
${AUTH_VOLUME_LINE}
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8080/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 15s
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "5"

volumes:
  able_data:
  able_db:
COMPOSE

echo -e "${YELLOW}[4/4] Pulling and starting...${NC}"
ssh_run "
  # Stop old systemd service if running
  systemctl stop able 2>/dev/null || true
  systemctl disable able 2>/dev/null || true

  cd /opt/able
  docker pull '${IMAGE}' 2>&1 | tail -3
  docker compose down 2>/dev/null || true
  docker compose up -d
  sleep 8
  docker compose ps
  echo ''
  curl -fsS http://127.0.0.1:8080/health && echo '✅ Health OK' || echo '⚠ Health not ready yet'
  echo ''
  docker compose logs --tail 20
"

echo ""
echo -e "${GREEN}Deploy complete.${NC}"
echo "Logs:    ssh root@${SERVER_IP} 'cd /opt/able && docker compose logs -f'"
echo "Status:  ssh root@${SERVER_IP} 'cd /opt/able && docker compose ps'"
echo "Restart: ssh root@${SERVER_IP} 'cd /opt/able && docker compose restart'"
