#!/usr/bin/env bash
# Rerun the newest task for a PR (default #13) through the live controller and print the result. Works on the driver or a laptop.
set -euo pipefail
PR=${1:-13}; CFG=${2:-full}
(kubectl port-forward -n pr-runtime svc/controller 18000:8000 >/dev/null 2>&1 &)
until curl -s localhost:18000/healthz >/dev/null; do sleep 1; done
TID=$(curl -s "localhost:18000/api/tasks?pr=$PR&limit=1" | python3 -c 'import sys,json; print(json.load(sys.stdin)[0]["id"])')
NEW=$(curl -s -X POST "localhost:18000/api/tasks/$TID/rerun" -H 'Content-Type: application/json' -d "{\"config\":\"$CFG\"}" | python3 -c 'import sys,json; print(json.load(sys.stdin)["id"])')
until curl -s "localhost:18000/api/tasks/$NEW" | grep -qE '"state": *"(posted|failed)"'; do sleep 3; done
curl -s "localhost:18000/api/tasks/$NEW" | python3 -c 'import sys,json; t=json.load(sys.stdin); print({k:t[k] for k in ("id","state","verdict","findings_count","cost_tokens_usd","review_url","error")}); print("timings:", t["timings"])'
pkill -f "port-forward -n pr-runtime svc/controller 18000" || true
