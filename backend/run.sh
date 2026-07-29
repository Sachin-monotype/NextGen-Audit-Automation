#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/backend"

if [[ ! -d .venv ]]; then
  python -m venv .venv || python3 -m venv .venv
fi

# Windows venv uses Scripts/; Unix uses bin/
if [[ -x .venv/Scripts/python ]]; then
  PY=".venv/Scripts/python"
elif [[ -x .venv/bin/python ]]; then
  PY=".venv/bin/python"
else
  echo "No python found in .venv" >&2
  exit 1
fi

"$PY" -m pip install -r requirements.txt
if [[ -f "$ROOT/python/requirements.txt" ]]; then
  "$PY" -m pip install -r "$ROOT/python/requirements.txt"
fi

export PYTHONPATH="$ROOT/backend"
exec "$PY" -m uvicorn app.main:app --host 0.0.0.0 --port "${API_PORT:-3200}" --reload
