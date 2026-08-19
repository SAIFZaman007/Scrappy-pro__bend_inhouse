# syntax=docker/dockerfile:1.7

# ---------- builder ----------
FROM python:3.12-slim-bookworm AS builder
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
WORKDIR /build

COPY pyproject.toml ./
RUN pip wheel --no-cache-dir --wheel-dir /build/wheels .

# ---------- runtime ----------
FROM mcr.microsoft.com/playwright/python:v1.49.1-noble AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH"

RUN useradd --create-home --uid 10001 scrappy || true

# Copy wheels from builder and install using Python's native pip in runtime
COPY --from=builder /build/wheels /tmp/wheels
RUN python -m pip install --upgrade pip \
 && python -m venv /opt/venv \
 && /opt/venv/bin/pip install /tmp/wheels/*.whl \
 && rm -rf /tmp/wheels

WORKDIR /app
COPY --chown=scrappy:scrappy . .
RUN mkdir -p /data/exports && chown -R scrappy:scrappy /data
RUN chmod +x /app/entrypoint.sh

USER scrappy
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD curl -fsS http://localhost:8000/api/v1/health || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["api"]