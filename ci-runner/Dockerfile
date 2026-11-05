# A self-hosted GitHub Actions runner for CI and Audit (compose.yml beside it): Ubuntu 24.04, as
# GitHub's ubuntu-24.04 runners, with what the workflows take from those runners' image rather
# than set up themselves (git, Python 3 for the stand-in model and the scripts, the Docker CLI
# with buildx and compose, sudo for the libraries Playwright installs, libatomic for pnpm's
# binary, and a Node with its npm on PATH, which pnpm's setup action runs before setup-node's).
# The Node a job builds with, pnpm, uv and OpenTofu come from the workflows' setup actions, at
# the versions they name.
#
#   docker compose -f ci-runner/compose.yml build

# The CLI and its plugins of the daemon each runner talks to, so that the two are one release.
FROM docker:29.9.2-dind@sha256:9e79b879b177fbd72c34376a3dd3d91747da5db49e1f560f76aea59561471977 AS docker

FROM ubuntu:24.04@sha256:68ee22004a9109b44973313b02ff7b1de250f1de0f5ecb628d73f579aaff33ac

# The runner's release, checked against the SHA-256 its release notes give. It updates itself
# when GitHub asks it to (into its volume), so this is the version a new runner starts from.
ARG RUNNER_VERSION=2.338.1
ARG RUNNER_SHA256=08e9ccb3604d2462c24b5e33604dc30810296ca15cc65f23cffd4317eb144c37
# The group the daemon's socket belongs to (compose.yml's --group).
ARG DOCKER_GID=121

ENV DEBIAN_FRONTEND=noninteractive LANG=C.UTF-8 TZ=Etc/UTC

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl git jq libatomic1 \
        python3 sudo tzdata unzip xz-utils zstd \
    && rm -rf /var/lib/apt/lists/*

COPY --from=docker /usr/local/bin/docker /usr/local/bin/docker
COPY --from=docker /usr/local/libexec/docker/cli-plugins /usr/local/libexec/docker/cli-plugins

# Node and npm on PATH, as on GitHub's image, checked against the release's SHASUMS256.txt.
# pnpm/action-setup runs `npm ci` by PATH, falling back to the runner's own Node, whose npm the
# runner's update to 2.338.2 unpacked as a copy of npm's script rather than its link, which
# then cannot find `../lib/cli.js` (every End to end job from 2026-11-03 to 2026-11-05).
ARG NODE_VERSION=24.22.2
ARG NODE_SHA256=48cc0e21e048790e54c0cf5749c2725005313d652651c40b7a21feb9697aaf75
RUN curl -fsSL -o /tmp/node.tar.xz \
        "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz" \
    && echo "${NODE_SHA256}  /tmp/node.tar.xz" | sha256sum -c - \
    && tar -xJf /tmp/node.tar.xz -C /usr/local --strip-components=1 --no-same-owner \
        "node-v${NODE_VERSION}-linux-x64/bin" "node-v${NODE_VERSION}-linux-x64/lib" \
    && rm /tmp/node.tar.xz \
    && node --version && npm --version

# Every job runs as this user, uid 1001 as on GitHub's runners, never as root: the suite checks
# what an unprivileged process may not write.
RUN groupadd --gid "$DOCKER_GID" docker \
    && useradd --create-home --uid 1001 --groups docker --shell /bin/bash runner \
    && echo 'runner ALL=(ALL) NOPASSWD:ALL' >/etc/sudoers.d/runner \
    && chmod 0440 /etc/sudoers.d/runner

WORKDIR /opt/actions-runner
RUN curl -fsSL -o /tmp/runner.tar.gz \
        "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz" \
    && echo "${RUNNER_SHA256}  /tmp/runner.tar.gz" | sha256sum -c - \
    && tar -xzf /tmp/runner.tar.gz \
    && rm /tmp/runner.tar.gz \
    && ./bin/installdependencies.sh \
    && rm -rf /var/lib/apt/lists/* \
    && chown -R runner:runner /opt/actions-runner

COPY --chmod=0755 start.sh clean.sh /usr/local/lib/runner/

USER runner
# Healthy while the runner listens for jobs (its process name, which /proc keeps to 15 letters).
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s \
    CMD ["bash", "-c", "grep -qsx Runner.Listener /proc/[0-9]*/comm"]
ENTRYPOINT ["/usr/local/lib/runner/start.sh"]
