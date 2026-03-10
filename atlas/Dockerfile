# ATLAS v2 - Secure Multi-Tenant AI Agent System
# Build: docker build -t atlas-v2 .
# Run: docker run -d --env-file .env -p 8080:8080 atlas-v2

FROM python:3.14-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    git \
    curl \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Create atlas user (non-root for security)
RUN useradd -m -s /bin/bash atlas
USER atlas
WORKDIR /home/atlas

# Set up virtual environment
RUN python -m venv /home/atlas/venv
ENV PATH="/home/atlas/venv/bin:$PATH"

# Install Python dependencies
COPY --chown=atlas:atlas requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY --chown=atlas:atlas . /home/atlas/atlas-v2

# Create data directories
RUN mkdir -p /home/atlas/.atlas/memory \
    /home/atlas/.atlas/logs \
    /home/atlas/.atlas/.secrets \
    /home/atlas/.atlas/billing

WORKDIR /home/atlas/atlas-v2

# Expose port for health checks
EXPOSE 8080

# Environment variables (override at runtime)
ENV ATLAS_HOME=/home/atlas/.atlas
ENV PYTHONUNBUFFERED=1

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

# Default command - run via start.py (validates config before launching gateway)
CMD ["python", "start.py"]
