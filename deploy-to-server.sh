#!/bin/bash
# Deploy the packaged ABLE runtime to the DigitalOcean server.
# Usage: bash deploy-to-server.sh [git-ref]

set -euo pipefail

SERVER_IP="146.190.142.68"
SSH_KEY="${ABLE_SSH_KEY:-$HOME/.ssh/id_ed25519}"
REPO_URL="https://github.com/iamthetonyb/ABLE.git"
REPO_PATH="/opt/able/ABLE"
RUNTIME_HOME="/home/able/.able"
APP_USER="able"
APP_GROUP="able"
TARGET_REF="${1:-main}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${CYAN}═══════════════════════════════════════════════════════════════${NC}"
echo -e "${CYAN}                  ABLE Server Deploy                          ${NC}"
echo -e "${CYAN}  Host: ${SERVER_IP} | Ref: ${TARGET_REF}                        ${NC}"
echo -e "${CYAN}═══════════════════════════════════════════════════════════════${NC}"
echo ""

ssh_run() {
  ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no root@"$SERVER_IP" "$@"
}

echo -e "${YELLOW}Checking SSH connectivity...${NC}"
ssh_run "echo connected >/dev/null"
echo -e "${GREEN}SSH ready${NC}"
echo ""

echo -e "${GREEN}[1/6] Ensuring runtime user...${NC}"
ssh_run "
set -euo pipefail
if ! getent group ${APP_GROUP} >/dev/null 2>&1; then
  groupadd --system ${APP_GROUP}
fi
if ! id -u ${APP_USER} >/dev/null 2>&1; then
  useradd --system --gid ${APP_GROUP} --create-home --home-dir /home/${APP_USER} --shell /bin/bash ${APP_USER}
fi
install -d -o ${APP_USER} -g ${APP_GROUP} /opt/able ${RUNTIME_HOME}
"

echo -e "${GREEN}[2/6] Syncing repository...${NC}"
ssh_run "
set -euo pipefail
run_as_able() {
  runuser -u ${APP_USER} -- \"\$@\"
}
mkdir -p /opt/able
if [ -d ${REPO_PATH} ]; then
  chown -R ${APP_USER}:${APP_GROUP} ${REPO_PATH}
else
  install -d -o ${APP_USER} -g ${APP_GROUP} ${REPO_PATH}
fi
if [ ! -d ${REPO_PATH}/.git ]; then
  run_as_able git clone ${REPO_URL} ${REPO_PATH}
fi
cd ${REPO_PATH}
run_as_able git fetch --tags origin ${TARGET_REF}
run_as_able git checkout -B deployed FETCH_HEAD
"

echo -e "${GREEN}[3/6] Preparing runtime directories...${NC}"
ssh_run "
set -euo pipefail
mkdir -p ${RUNTIME_HOME}
mkdir -p ${REPO_PATH}/data
chown -R ${APP_USER}:${APP_GROUP} ${RUNTIME_HOME} ${REPO_PATH}/data
"

echo -e "${GREEN}[4/6] Installing Python environment...${NC}"
ssh_run "
set -euo pipefail
apt-get update -qq >/dev/null
apt-get install -y -qq python3-venv python3-pip >/dev/null
if [ ! -d ${RUNTIME_HOME}/venv ]; then
  python3 -m venv ${RUNTIME_HOME}/venv
fi
${RUNTIME_HOME}/venv/bin/pip install --quiet --upgrade pip
${RUNTIME_HOME}/venv/bin/pip install --quiet --upgrade -r ${REPO_PATH}/able/requirements.txt
${RUNTIME_HOME}/venv/bin/pip install --quiet --upgrade -e "${REPO_PATH}[observability]"
"

echo -e "${GREEN}[5/6] Installing systemd unit...${NC}"
ssh_run "
set -euo pipefail
cp ${REPO_PATH}/able/able.service /etc/systemd/system/able.service
systemctl daemon-reload
systemctl restart able
"

echo -e "${GREEN}[6/6] Verifying service...${NC}"
ssh_run "
set -euo pipefail
systemctl is-active --quiet able
curl -fsS http://127.0.0.1:8080/health >/dev/null
journalctl -u able -n 40 --no-pager
"

echo ""
echo -e "${GREEN}Deploy complete.${NC}"
echo "Service status: ssh root@${SERVER_IP} 'systemctl status able'"
echo "Health check:   ssh root@${SERVER_IP} 'curl http://127.0.0.1:8080/health'"
