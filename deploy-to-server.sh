#!/bin/bash
# ATLAS v2 - Pre-configured deployment to Digital Ocean
# Server: 196.190.142.68
# Usage: bash deploy-to-server.sh
#
# PREREQUISITES:
#   1. Your SSH public key must be added to the server's authorized_keys
#      (Do this via Digital Ocean dashboard → Droplet → Access → Add SSH key)
#   2. Docker must be available on the server (this script installs it)

set -e

SERVER_IP="196.190.142.68"
SSH_KEY="$HOME/.ssh/id_ed25519"
REPO_URL="https://github.com/iamthetonyb/AIDE.git"
REMOTE_PATH="/opt/atlas"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${CYAN}═══════════════════════════════════════════════════════════════${NC}"
echo -e "${CYAN}           ATLAS v2 → Digital Ocean Deployment                 ${NC}"
echo -e "${CYAN}           Server: ${SERVER_IP}                                ${NC}"
echo -e "${CYAN}═══════════════════════════════════════════════════════════════${NC}"
echo ""

# Test SSH connection first
echo -e "${YELLOW}Testing SSH connection to ${SERVER_IP}...${NC}"
if ! ssh -i "$SSH_KEY" -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=no root@$SERVER_IP "echo 'SSH OK'" 2>/dev/null; then
    echo -e "${RED}❌ Cannot connect to server. Check:${NC}"
    echo "   1. Server is running (Digital Ocean dashboard)"
    echo "   2. Your SSH key is added: ssh-copy-id -i ~/.ssh/id_ed25519.pub root@${SERVER_IP}"
    echo "   3. Firewall allows port 22"
    echo ""
    echo -e "${YELLOW}To add your SSH key to the server, run:${NC}"
    echo "   ssh-copy-id -i ~/.ssh/id_ed25519.pub root@${SERVER_IP}"
    exit 1
fi
echo -e "${GREEN}✅ SSH connection successful${NC}"
echo ""

ssh_run() {
    ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no root@$SERVER_IP "$@"
}

# Step 1: Install Docker
echo -e "${GREEN}[1/6] Checking Docker...${NC}"
ssh_run 'if ! command -v docker &>/dev/null; then
    echo "Installing Docker..."
    curl -fsSL https://get.docker.com | sh
    systemctl enable docker && systemctl start docker
fi
docker --version
if ! command -v docker-compose &>/dev/null && ! docker compose version &>/dev/null 2>&1; then
    echo "Installing docker-compose..."
    curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
    chmod +x /usr/local/bin/docker-compose
fi'

# Step 2: Setup directories
echo -e "${GREEN}[2/6] Setting up directories on server...${NC}"
ssh_run "mkdir -p ${REMOTE_PATH}/AIDE/atlas-v2/secrets ${REMOTE_PATH}/data"

# Step 3: Clone/update repo
echo -e "${GREEN}[3/6] Cloning ATLAS repo...${NC}"
ssh_run "
cd ${REMOTE_PATH}
if [ -d 'AIDE/.git' ]; then
    cd AIDE && git pull origin main
else
    rm -rf AIDE
    git clone ${REPO_URL} AIDE
fi
"

# Step 4: Upload .env with credentials
echo -e "${GREEN}[4/6] Uploading environment configuration...${NC}"
scp -i "$SSH_KEY" -o StrictHostKeyChecking=no \
    "$(dirname "$0")/atlas-v2/.env" \
    root@${SERVER_IP}:${REMOTE_PATH}/AIDE/atlas-v2/.env
echo -e "${GREEN}✅ .env uploaded${NC}"

# Step 5: Build and start container
echo -e "${GREEN}[5/6] Building and starting ATLAS container...${NC}"
ssh_run "
cd ${REMOTE_PATH}/AIDE/atlas-v2

# Stop old container if running
docker stop atlas-v2 2>/dev/null || true
docker rm atlas-v2 2>/dev/null || true

# Build
docker build -t atlas-v2 .

# Start with docker-compose if available, else docker run
if command -v docker-compose &>/dev/null; then
    docker-compose up -d
elif docker compose version &>/dev/null 2>&1; then
    docker compose up -d
else
    docker run -d \
        --name atlas-v2 \
        --restart unless-stopped \
        -p 8080:8080 \
        --env-file .env \
        -v ${REMOTE_PATH}/data:/home/atlas/.atlas \
        atlas-v2
fi
"

# Step 6: Verify
echo -e "${GREEN}[6/6] Verifying deployment...${NC}"
sleep 8
ssh_run "
echo '--- Container Status ---'
docker ps | grep atlas-v2 || echo 'Container not running - check logs'
echo ''
echo '--- Last 30 log lines ---'
docker logs atlas-v2 --tail 30 2>&1
"

echo ""
echo -e "${CYAN}═══════════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}✅ Deployment Complete!${NC}"
echo -e "${CYAN}═══════════════════════════════════════════════════════════════${NC}"
echo ""
echo "Useful commands:"
echo "  View logs:     ssh root@${SERVER_IP} 'docker logs -f atlas-v2'"
echo "  Restart:       ssh root@${SERVER_IP} 'docker restart atlas-v2'"
echo "  Stop:          ssh root@${SERVER_IP} 'docker stop atlas-v2'"
echo "  Edit .env:     ssh root@${SERVER_IP} 'nano ${REMOTE_PATH}/AIDE/atlas-v2/.env'"
echo ""
echo "Your ATLAS Telegram bot should be live within 60 seconds."
echo "Message your bot on Telegram to verify it's running."
