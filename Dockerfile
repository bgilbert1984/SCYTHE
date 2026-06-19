# ─── RF SCYTHE / NerfEngine — Dockerfile ────────────────────────────────────
# Multi-stage build: builder installs Python wheels; runtime image is lean.
# Base: python:3.12-slim-bookworm  (matches production Python version)

# ┌─────────────────────────────────────────────────────────────────────────┐
# │  Stage 1 — dependency builder                                           │
# └─────────────────────────────────────────────────────────────────────────┘
FROM python:3.12-slim-bookworm AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc g++ libpcap-dev libffi-dev libssl-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-docker.txt .
RUN pip install --no-cache-dir --user -r requirements-docker.txt


# ┌─────────────────────────────────────────────────────────────────────────┐
# │  Stage 2 — runtime image                                                │
# └─────────────────────────────────────────────────────────────────────────┘
FROM python:3.12-slim-bookworm AS runtime

LABEL org.opencontainers.image.title="RF SCYTHE NerfEngine" \
      org.opencontainers.image.description="Multi-instance intelligence orchestrator with hypergraph analytics, Gemma inference pipeline, and Threat Gravity Map" \
      org.opencontainers.image.version="1.0.0"

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH=/root/.local/bin:$PATH

# Runtime system packages
# - libpcap0.8  : scapy / PCAP capture
# - nmap        : traceroute + host discovery (/api/timing/*)
# - traceroute  : fallback for timing geolocation
# - iproute2    : ip / ss (used by some diagnostics)
# - curl        : healthcheck + ollama readiness probe
# - tshark      : optional deep PCAP inspection
RUN echo "wireshark-common wireshark-common/install-setuid boolean false" \
        | debconf-set-selections \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        libpcap0.8 \
        nmap \
        traceroute \
        iproute2 \
        curl \
        tshark \
    && rm -rf /var/lib/apt/lists/*

# Copy installed Python packages from builder stage
COPY --from=builder /root/.local /root/.local

WORKDIR /app

# Copy all application source (respects .dockerignore — large asset dirs excluded)
COPY . .

# Ensure runtime directories exist (instances/ and logs/ will be volume-mounted)
RUN mkdir -p instances data logs

# Entrypoint script
RUN chmod +x /app/docker-entrypoint.sh

# ── Exposed ports ────────────────────────────────────────────────────────────
# 5001  : SCYTHE Orchestrator (HTTP API + UI)
# 8765  : ws_ingest WebSocket relay (stream_relay)
# 8766  : rf_voxel_processor MCP WebSocket
# NOTE  : Dynamic instance ports (40000-50000) are only accessible with
#         network_mode: host  (Linux) or explicit port-range publishing.
EXPOSE 5001 8765 8766

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD curl -sf http://localhost:5001/api/scythe/health || exit 1

ENTRYPOINT ["/app/docker-entrypoint.sh"]
