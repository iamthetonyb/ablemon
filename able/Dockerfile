# ABLE — Autonomous Business & Learning Engine
#
# Two build profiles:
#   SLIM (default): Gateway + Telegram + Cron + LLM routing (~200MB)
#   FULL:           + ML embeddings, browser, billing (~2GB+)
#
# Build from the repo root:
#   docker build -t able -f able/Dockerfile .                          # slim
#   docker build -t able -f able/Dockerfile --build-arg PROFILE=full . # full
#
# Run:
#   docker compose up -d

ARG PYTHON_VERSION=3.14

# ── Stage 1: Builder ──────────────────────────────────────────
FROM python:${PYTHON_VERSION}-slim AS builder

ARG PROFILE=slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential git \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY able/requirements-core.txt able/requirements-full.txt able/requirements-observability.txt able/requirements.txt /tmp/

# Install deps based on profile
# Profiles: slim (default), full, observability (slim + phoenix + openinference)
RUN pip install --no-cache-dir --upgrade pip && \
    if [ "$PROFILE" = "full" ]; then \
        pip install --no-cache-dir -r /tmp/requirements-full.txt && \
        pip install --no-cache-dir -r /tmp/requirements-observability.txt; \
    elif [ "$PROFILE" = "observability" ]; then \
        pip install --no-cache-dir -r /tmp/requirements-core.txt && \
        pip install --no-cache-dir -r /tmp/requirements-observability.txt; \
    else \
        pip install --no-cache-dir -r /tmp/requirements-core.txt; \
    fi

# ── Stage 2: Runtime ─────────────────────────────────────────
FROM python:${PYTHON_VERSION}-slim AS runtime

# Minimal runtime deps only
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Non-root user
RUN useradd -m -s /bin/bash able

# Copy venv from builder (no build tools, compilers, etc.)
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy application code + shared config from the repo root build context
COPY --chown=able:able able /home/able/app/able
COPY --chown=able:able config /home/able/app/able/config
ENV PYTHONPATH="/home/able/app"

# Create persistent directories
USER able
RUN mkdir -p /home/able/.able/memory/daily \
    /home/able/.able/sessions \
    /home/able/.able/distillation \
    /home/able/.able/.secrets \
    /home/able/app/able/data \
    /home/able/app/able/logs \
    /home/able/app/able/audit/logs

WORKDIR /home/able/app/able

EXPOSE 8080

ENV ABLE_HOME=/home/able/.able \
    PYTHONUNBUFFERED=1

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

CMD ["python", "start.py"]
