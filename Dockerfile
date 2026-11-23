# syntax=docker/dockerfile:1
# Emudoc's image: the API, the worker, the release step and the maintenance commands, one image
# with a command each (deploying.md, "The image"), for amd64 and arm64. Build it here, tagged
# with the commit and naming it:
#
#   commit=$(git rev-parse HEAD)
#   docker build -t emudoc:$commit --build-arg VERSION=$commit .
#
# A release's is built for both platforms and published to GHCR by .github/workflows/publish.yml.
#
# It keeps the checkout's layout under /app, so the backend finds the frontend's build and its
# migrations where it would in a checkout. It holds no settings: a deployment passes them in
# the environment, and .dockerignore keeps .env and the uploads out of the build.

# The bases are pinned by digest, so a tag moved on the registry changes nothing until this file
# does; Dependabot proposes the new digest when the tag is rebuilt (.github/dependabot.yml), and
# the Audit workflow builds the image every day and fails on a fixed vulnerability in it. One
# Python for the two stages below, since the environment one builds runs on the other's.
FROM python:3.13-slim-trixie@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90 AS base

# The frontend's build is the same files on every platform, so it runs on the builder's own
# (BUILDPLATFORM), never emulated, when the image is built for amd64 and arm64 together.
FROM --platform=$BUILDPLATFORM node:24-slim@sha256:dc26ae92c9fb15cf6b1d63e20f0944b2efe0f115b20e00be48fb23fceb061c54 AS frontend
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
# LibreOffice without its windows or Java, for the converter (app/converter.py): this image run
# as a container of its own that turns a Word, PowerPoint or Excel file into the PDF the
# document pane shows it by (deploy/compose.yml, `converter`). With the fonts documents name
# most, so a page breaks where it did where it was written: Carlito and Caladea measure as
# Calibri and Cambria, Liberation as Arial, Times New Roman and Courier New, and Noto draws the
# other scripts, Chinese, Japanese and Korean among them. Debian's security updates reach it as
# the image is built again, the base's own packages too: the upgrade takes those Debian fixed
# after the base's digest was built (zlib1g's +deb13u1 of November 2026, which no python:3.13
# digest held yet). The font packages build fontconfig's cache as they install, since the
# running image cannot write one. And PostgreSQL 17's pg_dump and pg_restore, for the backup and
# restore commands (app/backup.py), which handle a server of 17 or earlier: Debian's, with the
# Perl their wrapper is written in, about 40 MB.
RUN apt-get update \
  && apt-get upgrade --yes --no-install-recommends \
  && apt-get install --yes --no-install-recommends \
      libreoffice-writer-nogui libreoffice-calc-nogui libreoffice-impress-nogui \
      fonts-liberation fonts-crosextra-carlito fonts-crosextra-caladea fonts-dejavu-core \
      fonts-noto-core fonts-noto-cjk postgresql-client-17 \
  && rm -rf /var/lib/apt/lists/*
# A user of its own with a fixed id, for volumes and for runAsNonRoot, and the directory a
# file:// storage keeps its files in, which a named volume mounted there takes over with its
# owner and mode: the group may list it, as the restore drill does (deploying.md, "The drill").
# /backups likewise, for the compose file's backups volume, which nobody else reads.
# Without the base's pip, which nothing here runs (uv built the environment) and whose own
# copies of urllib3 and msgpack would be the image's to keep patched. Without emSign's ECC root
# G3 in the system's bundle, which SSL_CERT_FILE names below for the mail relay and every other
# connection Python's ssl makes: eight intermediates under it lost their keys (October 2026),
# certifi 2026.10.19 dropped it and Go's x509 refuses the intermediates, while Debian's
# ca-certificates 20250419 still trusts it. Once Debian's package leaves it out, the sed finds
# nothing and the check still holds.
RUN python -m pip uninstall --yes --quiet --root-user-action ignore pip \
  && sed -i 's|^mozilla/emSign_ECC_Root_CA_-_G3\.crt$|!&|' /etc/ca-certificates.conf \
  && update-ca-certificates --fresh > /dev/null \
  && test ! -e /etc/ssl/certs/emSign_ECC_Root_CA_-_G3.pem \
  && groupadd --system --gid 10001 emudoc \
  && useradd --system --uid 10001 --gid emudoc --home-dir /nonexistent --no-create-home \
      --shell /usr/sbin/nologin emudoc \
  && install -d -o emudoc -g emudoc -m 750 /var/lib/emudoc \
  && install -d -o emudoc -g emudoc -m 700 /backups
WORKDIR /app/backend
COPY --from=backend /app/backend/.venv .venv
COPY backend/alembic.ini ./
COPY backend/migrations migrations
COPY backend/app app
RUN .venv/bin/python -m compileall -q app migrations
COPY --from=frontend /app/frontend/dist /app/frontend/dist
# The version the image is, which /api/health and the user menu show (app/version.py): a
# release's number for an image published on a version tag (.github/workflows/publish.yml),
# else the commit's full SHA its tag repeats; "unknown" when the build was not told. REVISION
# is the commit, the SHA again unless told. Declared after the last RUN and COPY: they differ
# with every commit, and a layer after them would be built again each time.
ARG VERSION=unknown
ARG REVISION=$VERSION
LABEL org.opencontainers.image.version=$VERSION \
      org.opencontainers.image.revision=$REVISION
# Production unless told otherwise: a container started without its settings refuses to run
# with development's, rather than running with them. psycopg's binary package brings an OpenSSL
# that looks for the system's certificates where it was built, so a database reached with
# sslrootcert=system is checked against none unless SSL_CERT_FILE names them. The API and the
# worker serve their metrics on 9464 of the container (deploying.md, "Metrics").
ENV PATH=/app/backend/.venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    ENVIRONMENT=production \
    METRICS_ADDRESS=0.0.0.0:9464 \
    EMUDOC_VERSION=$VERSION
USER 10001:10001
# The metrics' port is not exposed, so that `docker run -P` does not publish it with the API's.
EXPOSE 8000
# A stop ends the answer streams at once and gives the other requests in progress 8 seconds,
# within the 10 Docker waits before it kills the process (deploying.md, "The image").
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--timeout-graceful-shutdown", "8"]
