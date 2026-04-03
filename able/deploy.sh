#!/bin/bash
# ABLE v2 Deployment Script
# Usage: ./deploy.sh [server_ip] [ssh_key_path]
#
# This script:
# 1. Connects to your Digital Ocean server
# 2. Clones/updates the ABLE repo
# 3. Builds and runs the Docker container
# 4. Verifies the deployment

set -e

# Configuration
SERVER_IP="${1:-your_server_ip}"
SSH_KEY="${2:-~/.ssh/id_rsa}"
REPO_URL="https://github.com/iamthetonyb/ablemon.git"
BRANCH="${3:-main}"
REMOTE_PATH="/opt/able"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${GREEN}═══════════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}           ABLE v2 Deployment Script                          ${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════════════════${NC}"
echo ""

if [ "$SERVER_IP" == "your_server_ip" ]; then
    echo -e "${RED}Error: Please provide server IP${NC}"
    echo "Usage: ./deploy.sh <server_ip> [ssh_key_path] [branch]"
    exit 1
fi

echo -e "${YELLOW}Target: ${SERVER_IP}${NC}"
echo -e "${YELLOW}Branch: ${BRANCH}${NC}"
echo ""

# SSH function
ssh_cmd() {
    ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "root@$SERVER_IP" "$@"
}

scp_cmd() {
    scp -i "$SSH_KEY" -o StrictHostKeyChecking=no "$@"
}

# Step 1: Install Docker if not present
echo -e "${GREEN}[1/6] Checking Docker installation...${NC}"
ssh_cmd << 'REMOTE'
if ! command -v docker &> /dev/null; then
    echo "Installing Docker..."
    curl -fsSL https://get.docker.com | sh
    systemctl enable docker
    systemctl start docker
fi
docker --version
REMOTE

# Step 2: Create directory structure
echo -e "${GREEN}[2/6] Setting up directories...${NC}"
ssh_cmd << REMOTE
mkdir -p $REMOTE_PATH
mkdir -p $REMOTE_PATH/secrets
mkdir -p $REMOTE_PATH/data
REMOTE

# Step 3: Clone/update repository
echo -e "${GREEN}[3/6] Updating repository...${NC}"
ssh_cmd << REMOTE
cd $REMOTE_PATH
if [ -d "ablemon" ]; then
    cd ablemon
    git fetch origin
    git checkout $BRANCH
    git pull origin $BRANCH
else
    git clone --branch $BRANCH $REPO_URL
    cd ablemon
fi
REMOTE

# Step 4: Check for .env file
echo -e "${GREEN}[4/6] Checking environment configuration...${NC}"
ssh_cmd << REMOTE
cd $REMOTE_PATH/ablemon/able
if [ ! -f ".env" ]; then
    echo "Creating .env from example..."
    cp .env.example .env
    echo ""
    echo "⚠️  IMPORTANT: You need to configure .env on the server!"
    echo "Run: ssh root@$SERVER_IP 'nano $REMOTE_PATH/ablemon/able/.env'"
fi
REMOTE

# Step 5: Build and run
echo -e "${GREEN}[5/6] Building and starting ABLE...${NC}"
ssh_cmd << REMOTE
cd $REMOTE_PATH/ablemon/able

# Stop existing container if running
docker stop able-gateway 2>/dev/null || true
docker rm able-gateway 2>/dev/null || true

# Build new image
docker build -t able-gateway .

# Run container
docker run -d \
    --name able-gateway \
    --restart unless-stopped \
    -p 8080:8080 \
    --env-file .env \
    -v $REMOTE_PATH/data:/home/able/.able \
    -v $REMOTE_PATH/secrets:/home/able/.able/.secrets:ro \
    able-gateway

echo ""
echo "Container started. Waiting for health check..."
sleep 10
docker ps | grep able-gateway
REMOTE

# Step 6: Verify deployment
echo -e "${GREEN}[6/6] Verifying deployment...${NC}"
ssh_cmd << REMOTE
echo "Container logs (last 20 lines):"
docker logs able-gateway --tail 20

echo ""
echo "Health check:"
curl -s http://localhost:8080/health || echo "Health endpoint not responding (may need more startup time)"
REMOTE

echo ""
echo -e "${GREEN}═══════════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}           Deployment Complete!                                ${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════════════════${NC}"
echo ""
echo "Next steps:"
echo "1. Configure .env on server: ssh root@$SERVER_IP 'nano $REMOTE_PATH/ablemon/able/.env'"
echo "2. Add your API keys (TELEGRAM_BOT_TOKEN, OLLAMA_API_KEY, etc.)"
echo "3. Restart: ssh root@$SERVER_IP 'docker restart able-gateway'"
echo "4. Check logs: ssh root@$SERVER_IP 'docker logs -f able-gateway'"
echo ""
echo "Your ABLE bot should be responding on Telegram within a few minutes."
