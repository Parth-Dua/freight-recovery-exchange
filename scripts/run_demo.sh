#!/usr/bin/env bash
# Start everything the Part 1 demo needs, one process each. Ctrl+C stops all.
#   carriers (5 uAgents)  -> logs/carrier_<id>.log
#   Exchange (Agentverse mailbox) -> logs/exchange.log
#   recovery worker       -> logs/recovery_worker.log   (recovers every withdrawal; finishes approvals)
#   carrier console       -> logs/console.log           (http://localhost:8800)
#   Exchange API (internal, for the gateway) -> logs/exchange_api.log (127.0.0.1:8900)
#   buyer gateway uAgent (ASI:One entrypoint, mailbox) -> logs/gateway.log
#   Mission Control read-only dashboard + observer -> logs/ops_api.log (http://127.0.0.1:8820; build: cd dashboard && npm run build)
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
uv run uvicorn freight_exchange.exchange_api:app --app-dir src --host 127.0.0.1 --port 8900 > logs/exchange_api.log 2>&1 &
pids+=($!)
(cd gateway && uv run python -m buyer_gateway) > logs/gateway.log 2>&1 &
pids+=($!)
uv run uvicorn freight_exchange.ops.api:app --app-dir src --host 127.0.0.1 --port 8820 > logs/ops_api.log 2>&1 &
pids+=($!)
echo "started carriers, Exchange, recovery worker, console (http://localhost:8800), Exchange API, buyer gateway, Mission Control (http://127.0.0.1:8820); logs in logs/"
trap 'kill "${pids[@]}" 2>/dev/null' INT TERM EXIT
wait
