# ABLE v2 - Secure Multi-Tenant AI Agent System
# Build: docker build -t able-v2 .
# Run: docker run -d --env-file .env -p 8080:8080 able-v2

FROM python:3.14-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    git \
    curl \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Create able user (non-root for security)
RUN useradd -m -s /bin/bash able
USER able
WORKDIR /home/able

# Set up virtual environment
RUN python -m venv /home/able/venv
ENV PATH="/home/able/venv/bin:$PATH"

# Install Python dependencies
COPY --chown=able:able requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY --chown=able:able . /home/able/able-v2

# Create data directories
RUN mkdir -p /home/able/.able/memory \
    /home/able/.able/logs \
    /home/able/.able/.secrets \
    /home/able/.able/billing

WORKDIR /home/able/able-v2

# Expose port for health checks
EXPOSE 8080

# Environment variables (override at runtime)
ENV ABLE_HOME=/home/able/.able
ENV PYTHONUNBUFFERED=1

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

# Default command - run via start.py (validates config before launching gateway)
CMD ["python", "start.py"]
