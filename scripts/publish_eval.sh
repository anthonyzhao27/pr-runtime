#!/usr/bin/env bash
# Publish eval/results/latest/{summary.json,rows.json,summary.md} to the cluster so the console's Eval page can show it.
set -euo pipefail
cd "$(dirname "$0")/.."
kubectl create configmap eval-results -n pr-runtime \
  --from-file=summary.json=eval/results/latest/summary.json \
  --from-file=rows.json=eval/results/latest/rows.json \
  --from-file=summary.md=eval/results/latest/summary.md \
  --dry-run=client -o yaml | kubectl apply -f -
echo "published; ConfigMap propagation to the pod takes up to ~60s"
