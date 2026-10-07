# syntax=docker/dockerfile:1
# One image for three uses: the API (default command), the worker and the one-shot migration.
#   API:        uvicorn qarz.interface.asgi:build --factory        (one process: rate-limit counters and
#               metrics live in that process's memory by design)
#   worker:     python -m qarz.interface.worker
#   migrations: alembic upgrade head                               (with QD_MIGRATION_URL, the owner)
# Build context: backend/

FROM python:3.12-slim AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
COPY requirements.lock pyproject.toml scripts/runtime_requirements.py ./
# Only the runtime part of the lock, every package with its hashes and as a wheel: no compiler is needed
# and nothing unpinned can be installed. pip itself does not go into the final image.
RUN python runtime_requirements.py requirements.lock pyproject.toml > runtime.lock \
    && python -m venv /opt/venv \
    && /opt/venv/bin/pip install --require-hashes --only-binary=:all: -r runtime.lock \
    && /opt/venv/bin/pip uninstall --yes pip

FROM python:3.12-slim
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONPATH=/app/src \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
RUN groupadd --system --gid 10001 qarz \
    && useradd --system --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent \
       --shell /usr/sbin/nologin qarz \
    && python -m pip uninstall --yes pip \
    && install -d -o 10001 -g 10001 /var/lib/qarz/files
WORKDIR /app
COPY --from=build /opt/venv /opt/venv
COPY alembic.ini ./
COPY migrations ./migrations
COPY src ./src
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).status == 200 else 1)"]
# The API is reachable only from the proxy's network (nothing is published), so the forwarded headers it
# is sent can be trusted from any peer on that network.
CMD ["uvicorn", "qarz.interface.asgi:build", "--factory", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*", "--no-access-log", "--no-server-header", \
     "--timeout-graceful-shutdown", "20"]
