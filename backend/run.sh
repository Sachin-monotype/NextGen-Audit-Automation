#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/backend"
if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
.venv/bin/pip install -r requirements.txt
if [[ -f "$ROOT/python/requirements.txt" ]]; then
  .venv/bin/pip install -r "$ROOT/python/requirements.txt"
fi
export PYTHONPATH="$ROOT/backend"
exec .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port "${API_PORT:-3200}" --reload
