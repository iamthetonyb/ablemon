#!/bin/bash
# ABLE Gateway — persistent service wrapper
# Launched by macOS LaunchAgent: com.able.gateway.plist
#
# Loads .env, sets PYTHONPATH, starts the gateway.
# LaunchAgent keeps this alive across reboots and crashes.

set -euo pipefail

ABLE_ROOT="/Users/abenton333/Desktop/ABLE"
VENV_PYTHON="${ABLE_ROOT}/venv/bin/python3"
ENV_FILE="${ABLE_ROOT}/able/.env"
LOG_DIR="${ABLE_ROOT}/logs"

# Ensure log directory exists
mkdir -p "${LOG_DIR}"

# Load environment from .env
if [ -f "${ENV_FILE}" ]; then
    set -a
    # shellcheck source=/dev/null
    source <(grep -v '^#' "${ENV_FILE}" | grep -v '^$')
    set +a
fi

# Set PYTHONPATH so ABLE modules resolve
export PYTHONPATH="${ABLE_ROOT}"

# Working directory must be the ABLE package dir (config/gateway.json lives there)
cd "${ABLE_ROOT}/able"

# Start the gateway (blocks forever — LaunchAgent restarts on exit)
exec "${VENV_PYTHON}" -m able serve 2>&1
