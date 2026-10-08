#!/usr/bin/env bash
# On the driver box: materialize ~/pr-runtime/.env from Secrets Manager and log gh in with the bot token.
# Secrets never leave AWS; nothing is typed on a laptop.
set -euo pipefail
cd "$(dirname "$0")/.."
SECRET=$(aws secretsmanager get-secret-value --region us-east-1 --secret-id pr-runtime/app --query SecretString --output text)
python3 - "$SECRET" <<'PY' > .env
import json, sys
d = json.loads(sys.argv[1])
for k in ("OPENAI_API_KEY", "GITHUB_BOT_TOKEN", "POSTGRES_PASSWORD", "JUDGE_MODEL"):
    if d.get(k): print(f"{k}={d[k]}")
PY
chmod 600 .env
grep -q '^JUDGE_MODEL=' .env || echo "JUDGE_MODEL=gpt-6-luna" >> .env
grep '^GITHUB_BOT_TOKEN=' .env | cut -d= -f2- | gh auth login --with-token 2>/dev/null || true
gh auth status 2>&1 | grep -E "Logged in|account" | head -1
echo ".env ready: $(cut -d= -f1 .env | tr '\n' ' ')"
