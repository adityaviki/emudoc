# syntax=docker/dockerfile:1
# Emudoc's image: the API, the worker, the release step and the maintenance commands, one image
# with a command each (deploying.md, "The image"). Build it here, tagged with the commit:
#
#   docker build -t emudoc:$(git rev-parse --short HEAD) .
#
# It keeps the checkout's layout under /app, so the backend finds the frontend's build and its
# migrations where it would in a checkout. It holds no settings: a deployment passes them in
# the environment, and .dockerignore keeps .env and the uploads out of the build.

ARG PYTHON_IMAGE=python:3.13-slim-trixie

FROM node:24-slim AS frontend
RUN npm install --global pnpm@11.2.2
WORKDIR /app/frontend
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm build

FROM ${PYTHON_IMAGE} AS backend
COPY --from=ghcr.io/astral-sh/uv:0.11.15 /uv /bin/uv
# The image's own Python, never one uv downloads, so the environment's interpreter is the one
# the final stage has at the same path. Compiled now, since the running image cannot write.
ENV UV_PYTHON_DOWNLOADS=never UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock backend/.python-version ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev

FROM ${PYTHON_IMAGE}
# A user of its own with a fixed id, for volumes and for runAsNonRoot, and the directory a
# file:// storage keeps its files in, which a named volume mounted there takes over with its
# owner and mode: the group may list it, as the restore drill does (deploying.md, "The drill").
RUN groupadd --system --gid 10001 emudoc \
  && useradd --system --uid 10001 --gid emudoc --home-dir /nonexistent --no-create-home \
      --shell /usr/sbin/nologin emudoc \
  && install -d -o emudoc -g emudoc -m 750 /var/lib/emudoc
WORKDIR /app/backend
COPY --from=backend /app/backend/.venv .venv
COPY backend/alembic.ini ./
COPY backend/migrations migrations
COPY backend/app app
RUN .venv/bin/python -m compileall -q app migrations
COPY --from=frontend /app/frontend/dist /app/frontend/dist
# What a release on AWS runs on the instance (deploy/aws/host/release, taken out of the image by
# emudoc-release): the compose files, Caddy's, the drill's scripts and the host's own, so that
# a tag carries the deployment it was tested with. The app reads none of it.
COPY deploy /app/deploy
# Production unless told otherwise: a container started without its settings refuses to run
# with development's, rather than running with them. psycopg's binary package brings an OpenSSL
# that looks for the system's certificates where it was built, so a database reached with
# sslrootcert=system is checked against none unless SSL_CERT_FILE names them.
ENV PATH=/app/backend/.venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    ENVIRONMENT=production
USER 10001:10001
EXPOSE 8000
# A stop ends the answer streams at once and gives the other requests in progress 8 seconds,
# within the 10 Docker waits before it kills the process (deploying.md, "The image").
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--timeout-graceful-shutdown", "8"]
