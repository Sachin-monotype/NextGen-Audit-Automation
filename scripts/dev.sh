#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# Fast local dev defaults — API up first; heavy work runs in background.
export INGEST_ENSURE_INDEXES_ON_START="${INGEST_ENSURE_INDEXES_ON_START:-false}"
export MONGO_RETENTION_SKIP_STARTUP_SWEEP="${MONGO_RETENTION_SKIP_STARTUP_SWEEP:-true}"
export DEV_SKIP_PIP="${DEV_SKIP_PIP:-auto}"

API_PORT="${API_PORT:-3200}"

# Stale uvicorn on :3200 means teammates keep talking to an old backend (no Mongo Results).
if command -v lsof >/dev/null 2>&1; then
  OLD_PIDS="$(lsof -tiTCP:"$API_PORT" -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -n "${OLD_PIDS}" ]]; then
    echo "Port :$API_PORT is busy (pids: $OLD_PIDS) — stopping stale backend so Results Mongo can load."
    # shellcheck disable=SC2086
    kill $OLD_PIDS 2>/dev/null || true
    sleep 0.5
    STILL="$(lsof -tiTCP:"$API_PORT" -sTCP:LISTEN 2>/dev/null || true)"
    if [[ -n "${STILL}" ]]; then
      # shellcheck disable=SC2086
      kill -9 $STILL 2>/dev/null || true
      sleep 0.3
    fi
  fi
fi

echo "Starting backend on :$API_PORT"
"$ROOT/backend/run.sh" &
BACK_PID=$!

echo "Waiting for backend /health…"
for _ in $(seq 1 120); do
  if curl -sf "http://127.0.0.1:${API_PORT}/health" >/dev/null 2>&1; then
    echo "Backend ready."
    # Surface Results Atlas status (separate from audit-log Mongo in the sidebar).
    if curl -sf "http://127.0.0.1:${API_PORT}/api/results/mongo/status" >/tmp/qa-results-mongo-status.json 2>/dev/null; then
      echo "QA Results Mongo: $(cat /tmp/qa-results-mongo-status.json)"
    else
      echo "QA Results Mongo: not reachable — set RESULTS_MONGO_URL in .env and restart." >&2
    fi
    break
  fi
  if ! kill -0 "$BACK_PID" 2>/dev/null; then
    echo "Backend exited during startup." >&2
    wait "$BACK_PID" || true
    exit 1
  fi
  sleep 0.25
done

echo "Starting frontend on :5174"
cd "$ROOT/frontend"
if [[ ! -d node_modules ]]; then npm install; fi
npm run dev &
UI_PID=$!

trap 'kill $BACK_PID $UI_PID 2>/dev/null || true' EXIT
wait
