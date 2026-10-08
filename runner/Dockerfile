# Runner image: Flask fork pre-cloned with test deps installed (the "warm" part).
# No secrets, no AWS or k8s credentials, runs as non-root, root filesystem read-only at runtime.
FROM python:3.12-slim-bookworm

RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv

ARG REPO_URL=https://github.com/anthonyzhao27/flask.git
ARG REF=main
ENV UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_CACHE_DIR=/work/.uv-cache HOME=/work

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
