#!/usr/bin/env bash
# Start everything the Part 1 demo needs, one process each. Ctrl+C stops all.
#   carriers (5 uAgents)  -> logs/carrier_<id>.log
#   Exchange (Agentverse mailbox) -> logs/exchange.log
#   recovery worker       -> logs/recovery_worker.log   (recovers every withdrawal; finishes approvals)
#   carrier console       -> logs/console.log           (http://localhost:8800)
# Only ONE machine may run this with a given AGENT_URI (mailbox mode delivers each message once).
set -euo pipefail
cd "$(dirname "$0")/.."
unset VIRTUAL_ENV
mkdir -p logs
pids=()
./scripts/run_carriers.sh > logs/carriers.log 2>&1 &
pids+=($!)
uv run langgraph-av dev --no-browser > logs/exchange.log 2>&1 &
pids+=($!)
uv run python -u scripts/recovery_worker.py --interval 5 > logs/recovery_worker.log 2>&1 &
pids+=($!)
(cd console && uv run uvicorn carrier_console.web:app --port 8800) > logs/console.log 2>&1 &
pids+=($!)
echo "started carriers, Exchange, recovery worker, console (http://localhost:8800); logs in logs/"
trap 'kill "${pids[@]}" 2>/dev/null' INT TERM EXIT
wait
