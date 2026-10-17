#!/usr/bin/env bash
# Start the three fictional carrier agents (one process each). Ctrl+C stops all.
# Logs: logs/carrier_<id>.log
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
pids=()
for c in atlas beacon cove; do
  (cd carriers && uv run python -m carrier_agent --carrier "$c") > "logs/carrier_$c.log" 2>&1 &
  pids+=($!)
  echo "started $c (pid $!) -> logs/carrier_$c.log"
done
trap 'kill "${pids[@]}" 2>/dev/null' INT TERM EXIT
wait
