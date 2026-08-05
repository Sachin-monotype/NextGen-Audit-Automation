#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# Fast local dev defaults — API up first; heavy work runs in background.
export INGEST_ENSURE_INDEXES_ON_START="${INGEST_ENSURE_INDEXES_ON_START:-false}"
export MONGO_RETENTION_SKIP_STARTUP_SWEEP="${MONGO_RETENTION_SKIP_STARTUP_SWEEP:-true}"
export DEV_SKIP_PIP="${DEV_SKIP_PIP:-auto}"

echo "Starting backend on :3200"
"$ROOT/backend/run.sh" &
BACK_PID=$!

echo "Waiting for backend /health…"
for _ in $(seq 1 120); do
  if curl -sf "http://127.0.0.1:${API_PORT:-3200}/health" >/dev/null 2>&1; then
    echo "Backend ready."
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
