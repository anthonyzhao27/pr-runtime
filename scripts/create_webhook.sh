#!/usr/bin/env bash
# Create (or replace) the pull_request webhook on the Flask fork, pointing at API Gateway.
# Writes WEBHOOK_SECRET into .env. Uses the active `gh` login (must be anthonyzhao27).
set -euo pipefail
cd "$(dirname "$0")/.."
REPO=${REPO:-anthonyzhao27/flask}
URL=$(cd infra && terraform output -raw webhook_url)
touch .env
if ! grep -q '^WEBHOOK_SECRET=' .env; then
  echo "WEBHOOK_SECRET=$(openssl rand -hex 32)" >> .env
fi
SECRET=$(grep '^WEBHOOK_SECRET=' .env | cut -d= -f2)

# Remove any existing hook pointing at an execute-api URL so re-running is idempotent.
for id in $(gh api "repos/$REPO/hooks" --jq '.[] | select(.config.url | test("execute-api")) | .id'); do
  gh api -X DELETE "repos/$REPO/hooks/$id" >/dev/null && echo "deleted old hook $id"
done

gh api -X POST "repos/$REPO/hooks" \
  -f name=web -F active=true \
  -f 'events[]=pull_request' \
  -f config[url]="$URL" -f config[content_type]=json -f config[secret]="$SECRET" \
  --jq '{id, url: .config.url, events}'
