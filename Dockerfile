# syntax=docker/dockerfile:1.7

# ---------- builder ----------
FROM mcr.microsoft.com/playwright/python:v1.62.0-noble AS builder
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
WORKDIR /build

COPY pyproject.toml ./
RUN --mount=type=cache,target=/root/.cache/pip \
    pip wheel --wheel-dir /build/wheels .

# ---------- runtime ----------
FROM mcr.microsoft.com/playwright/python:v1.62.0-noble AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Install curl (required for Docker HEALTHCHECK)
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 10001 scrappy || true

COPY --from=builder /build/wheels /tmp/wheels
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install --upgrade pip \
 && pip install --break-system-packages /tmp/wheels/*.whl \
 && rm -rf /tmp/wheels

WORKDIR /app
COPY --chown=scrappy:scrappy . .
RUN mkdir -p /data/exports && chown -R scrappy:scrappy /data \
 && chmod +x /app/entrypoint.sh

USER scrappy
EXPOSE 8000

# Extended start-period to 60s to allow DB wait-scripts & migrations to complete before checking health
HEALTHCHECK --interval=15s --timeout=5s --start-period=60s --retries=5 \
  CMD curl -fsS http://localhost:8000/api/v1/health || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["api"]