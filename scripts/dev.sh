#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "Starting backend on :3200"
"$ROOT/backend/run.sh" &
BACK_PID=$!

echo "Starting frontend on :5174"
cd "$ROOT/frontend"
if [[ ! -d node_modules ]]; then npm install; fi
npm run dev &
UI_PID=$!

trap 'kill $BACK_PID $UI_PID 2>/dev/null || true' EXIT
wait
