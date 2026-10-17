# syntax=docker/dockerfile:1
# Emudoc's image: the API, the worker, the release step and the maintenance commands, one image
# with a command each (deploying.md, "The image"). Build it here, tagged with the commit and
# naming it:
#
#   commit=$(git rev-parse HEAD)
#   docker build -t emudoc:$commit --build-arg VERSION=$commit .
#
# It keeps the checkout's layout under /app, so the backend finds the frontend's build and its
# migrations where it would in a checkout. It holds no settings: a deployment passes them in
# the environment, and .dockerignore keeps .env and the uploads out of the build.

# The bases are pinned by digest, so a tag moved on the registry changes nothing until this file
# does; Dependabot proposes the new digest when the tag is rebuilt (.github/dependabot.yml), and
# the Audit workflow builds the image every day and fails on a fixed vulnerability in it. One
# Python for the two stages below, since the environment one builds runs on the other's.
FROM python:3.13-slim-trixie@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f AS base

FROM node:24-slim@sha256:a5f4366e834695d5f363b920c104953a535d3a9864d1bdf74566a0ebe01daee5 AS frontend
RUN npm install --global pnpm@11.2.2
WORKDIR /app/frontend
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm build

FROM base AS backend
COPY --from=ghcr.io/astral-sh/uv:0.11.15@sha256:e590846f4776907b254ac0f44b5b380347af5d90d668138ca7938d1b0c2f98d3 /uv /bin/uv
# The image's own Python, never one uv downloads, so the environment's interpreter is the one
# the final stage has at the same path. Compiled now, since the running image cannot write.
ENV UV_PYTHON_DOWNLOADS=never UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock backend/.python-version ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev

FROM base
# A user of its own with a fixed id, for volumes and for runAsNonRoot, and the directory a
# file:// storage keeps its files in, which a named volume mounted there takes over with its
# owner and mode: the group may list it, as the restore drill does (deploying.md, "The drill").
# Without the base's pip, which nothing here runs (uv built the environment) and whose own
# copies of urllib3 and msgpack would be the image's to keep patched.
RUN python -m pip uninstall --yes --quiet --root-user-action ignore pip \
  && groupadd --system --gid 10001 emudoc \
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
# The commit the image is built from, the full SHA its tag repeats, which /api/health and the
# user menu show (app/version.py) and deploy/aws/release.sh waits for; "unknown" when the build
# was not told. Declared after the last RUN and COPY: it differs with every commit, and a layer
# after it would be built again each time.
ARG VERSION=unknown
LABEL org.opencontainers.image.revision=$VERSION
# Production unless told otherwise: a container started without its settings refuses to run
# with development's, rather than running with them. psycopg's binary package brings an OpenSSL
# that looks for the system's certificates where it was built, so a database reached with
# sslrootcert=system is checked against none unless SSL_CERT_FILE names them.
ENV PATH=/app/backend/.venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    ENVIRONMENT=production \
    EMUDOC_VERSION=$VERSION
USER 10001:10001
EXPOSE 8000
# A stop ends the answer streams at once and gives the other requests in progress 8 seconds,
# within the 10 Docker waits before it kills the process (deploying.md, "The image").
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--timeout-graceful-shutdown", "8"]
