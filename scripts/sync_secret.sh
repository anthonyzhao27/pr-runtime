#!/usr/bin/env bash
# Turn .env into the k8s Secret the chart references. Generates POSTGRES_PASSWORD on first run.
# Only the controller and postgres mount it; the runner never does.
set -euo pipefail
cd "$(dirname "$0")/.."
touch .env
grep -q '^POSTGRES_PASSWORD=' .env || echo "POSTGRES_PASSWORD=$(openssl rand -hex 24)" >> .env
for k in OPENAI_API_KEY GITHUB_BOT_TOKEN; do
  grep -q "^$k=" .env || echo "warning: $k not in .env yet (needed by the controller)" >&2
done
kubectl create secret generic pr-runtime-env --namespace pr-runtime \
  --from-env-file=<(grep -vE '^\s*(#|$)' .env) \
  --dry-run=client -o yaml | kubectl apply -f -
