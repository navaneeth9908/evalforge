# syntax=docker/dockerfile:1.7
FROM python:3.11.15-slim-bookworm@sha256:d29f48a31a8b408ed19272ca1e7b10ebae13b240a27e862d3d4217c528e2e0c3 AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/evalforge
WORKDIR /build
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
RUN python -m pip install "uv==0.11.24" \
    && uv sync --frozen --no-dev --no-editable --no-cache

FROM python:3.11.15-slim-bookworm@sha256:d29f48a31a8b408ed19272ca1e7b10ebae13b240a27e862d3d4217c528e2e0c3 AS runtime

ENV PATH="/opt/evalforge/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    EVALFORGE_DATABASE_PATH=/var/lib/evalforge/evalforge.db
RUN groupadd --gid 10001 evalforge \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin evalforge \
    && install -d -o 10001 -g 10001 /var/lib/evalforge
COPY --from=builder --chown=10001:10001 /opt/evalforge /opt/evalforge

WORKDIR /var/lib/evalforge
USER 10001:10001
VOLUME ["/var/lib/evalforge"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["evalforge-smoke", "--health-only", "--timeout-seconds", "3"]
CMD ["uvicorn", "--factory", "evalforge.server:create_app_from_environment", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--no-server-header", "--no-proxy-headers", "--limit-concurrency", "100", "--timeout-keep-alive", "5"]
