# Runner image: Flask fork pre-cloned with test deps installed (the "warm" part).
# No secrets, no AWS or k8s credentials, runs as non-root, root filesystem read-only at runtime.
FROM python:3.12-slim-bookworm

RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /uvx /usr/local/bin/

ARG REPO_URL=https://github.com/anthonyzhao27/flask.git
ARG REF=main
# SEED_REPO is the one repo baked in for the warm path; any other repo is partial-cloned on demand.
ENV UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_CACHE_DIR=/work/.uv-cache HOME=/work \
    SEED_DIR=/opt/seed/flask SEED_REPO=anthonyzhao27/flask WORK_ROOT=/work \
    UV_TOOL_DIR=/opt/uv-tools UV_TOOL_BIN_DIR=/opt/uv-tools/bin PATH=/opt/uv-tools/bin:$PATH

# ruff as a uv tool so `uvx ruff` / `ruff` work offline for any repo.
RUN mkdir -p /opt/uv-tools && chown -R 1000:1000 /opt/uv-tools
RUN UV_CACHE_DIR=/tmp/uv-cache uv tool install --quiet ruff && rm -rf /tmp/uv-cache

RUN mkdir -p /opt/seed /work && chown -R 1000:1000 /opt/seed /work
USER 1000

RUN git clone --quiet ${REPO_URL} /opt/seed/flask \
 && cd /opt/seed/flask && git checkout --quiet ${REF} \
 && git config user.email runner@pr-runtime.local && git config user.name runner \
 && UV_CACHE_DIR=/tmp/uv-cache uv sync --group tests --group dev --quiet \
 && rm -rf /tmp/uv-cache

COPY --chown=1000:1000 server.py /app/server.py
WORKDIR /work
EXPOSE 8080
ENTRYPOINT ["python", "/app/server.py"]
