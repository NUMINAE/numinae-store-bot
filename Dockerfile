# syntax=docker/dockerfile:1

# ---- builder: turn the source into a wheel ------------------------------------
FROM python:3.15-slim AS builder
WORKDIR /build
COPY pyproject.toml README.md LICENSE ./
COPY bot ./bot
RUN pip wheel --no-cache-dir --no-deps --wheel-dir /dist .

# ---- runtime: install only the wheel ------------------------------------------
FROM python:3.15-slim AS runtime

# PYTHONUNBUFFERED: logs appear immediately.
# PYTHONDONTWRITEBYTECODE: the root filesystem is read-only in docker-compose.yml.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# A fixed, unprivileged user: the bot never needs root.
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid 10001 --no-create-home \
        --shell /usr/sbin/nologin app

WORKDIR /app

COPY --from=builder /dist/*.whl /tmp/wheels/
RUN pip install /tmp/wheels/*.whl && rm -rf /tmp/wheels

# The data directory exists and is owned by the app user, so a named volume mounted
# here inherits that ownership. The database lives at /app/data/shop.db.
RUN mkdir -p /app/data && chown app:app /app/data && chmod 700 /app/data
ENV DB_PATH=data/shop.db

USER 10001:10001

# The sweep touches a heartbeat file every minute; this fails if it goes stale.
# It proves the event loop and job queue are running, not that Telegram is
# reachable or the token is valid.
HEALTHCHECK --interval=60s --timeout=10s --start-period=90s --retries=3 \
    CMD ["python", "-m", "bot.main", "--healthcheck"]

# Exec form, so SIGTERM from `docker stop` reaches Python and starts a graceful stop.
CMD ["python", "-m", "bot.main"]
