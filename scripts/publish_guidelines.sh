#!/usr/bin/env bash
# Publish eval/guidelines/*.md (per-directory rules; slashes encoded as __) into the `guidelines` ConfigMap.
set -euo pipefail
cd "$(dirname "$0")/.."
args=()
for f in eval/guidelines/*.md; do args+=(--from-file="$(basename "$f")=$f"); done
kubectl create configmap guidelines -n pr-runtime "${args[@]}" --dry-run=client -o yaml | kubectl apply -f -
echo "published $(ls eval/guidelines/*.md | wc -l | tr -d ' ') guideline files"
