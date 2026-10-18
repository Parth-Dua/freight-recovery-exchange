#!/usr/bin/env bash
# Start every carrier in fixtures/demo_carriers.json (one process each). Ctrl+C stops all.
# Logs: logs/carrier_<id>.log
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
carriers=$(python3 -c "import json;print(' '.join(c['carrier_id'] for c in json.load(open('fixtures/demo_carriers.json'))['carriers']))")
pids=()
for c in $carriers; do
  (cd carriers && uv run python -m carrier_agent --carrier "$c") > "logs/carrier_$c.log" 2>&1 &
  pids+=($!)
  echo "started $c (pid $!) -> logs/carrier_$c.log"
done
trap 'kill "${pids[@]}" 2>/dev/null' INT TERM EXIT
wait
