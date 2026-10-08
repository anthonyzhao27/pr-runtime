# Build context = repo root:  docker build -f controller/Dockerfile .
# Stage 1: console (React/Vite/TS) -> static assets
FROM node:22-alpine AS console
WORKDIR /console
COPY console/package.json console/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY console/ ./
RUN npx vite build --outDir /out --emptyOutDir

# Stage 2: controller
FROM python:3.12-slim-bookworm
# git: read-only bare mirror for the reviewer's grep/read_file/list_dir tools (S2). Nothing from a PR is executed here.
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv
WORKDIR /app
COPY controller/pyproject.toml ./
RUN uv pip install --system --no-cache -r pyproject.toml
COPY controller/app ./app
COPY --from=console /out ./static
USER 1000
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
