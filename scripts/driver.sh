#!/usr/bin/env bash
# Run a command on the driver box via SSM (no SSH). Streams nothing; polls until done, then prints output.
#   scripts/driver.sh 'cd pr-runtime && git pull && make build'
#   scripts/driver.sh --bg 'long command'      # returns the CommandId immediately; fetch later with --get <id>
#   scripts/driver.sh --get <command-id>
# Runs as ec2-user in /home/ec2-user with the repo checked out at ~/pr-runtime.
set -euo pipefail
PROFILE=${AWS_PROFILE:-personal}
ID=$(cd "$(dirname "$0")/../infra" && terraform output -raw driver_instance_id)

get() {
  local st
  while :; do
    st=$(aws ssm get-command-invocation --profile "$PROFILE" --command-id "$1" --instance-id "$ID" --query Status --output text 2>/dev/null || echo Pending)
    case "$st" in Success|Failed|Cancelled|TimedOut) break;; esac
    sleep 5
  done
  aws ssm get-command-invocation --profile "$PROFILE" --command-id "$1" --instance-id "$ID" --query '[StandardOutputContent,StandardErrorContent]' --output text
  echo "[driver] status=$st"
  [ "$st" = Success ]
}

send() {
  # base64 the command so no quoting survives the SSM JSON round-trip; runs as ec2-user with a login shell.
  local b64 script
  b64=$(printf '%s' "$1" | base64 | tr -d '\n')
  script=$(python3 -c 'import json,sys; print(json.dumps(["echo " + sys.argv[1] + " | base64 -d | sudo -u ec2-user -i bash -s"]))' "$b64")
  aws ssm send-command --profile "$PROFILE" --instance-ids "$ID" --document-name AWS-RunShellScript \
    --timeout-seconds 7200 --parameters "{\"commands\":$script,\"executionTimeout\":[\"7200\"]}" \
    --query Command.CommandId --output text
}

case "${1:-}" in
  --get) get "$2" ;;
  --bg) send "$2" ;;
  *) CID=$(send "$1"); get "$CID" ;;
esac
