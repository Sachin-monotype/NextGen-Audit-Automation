#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/backend"
if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi

_pip_stamp() {
  local stamp="$ROOT/backend/.venv/.pip-stamp"
  local hash
  hash=$( (cat requirements.txt; [[ -f "$ROOT/python/requirements.txt" ]] && cat "$ROOT/python/requirements.txt") | shasum -a 256 | awk '{print $1}')
  if [[ "${DEV_SKIP_PIP:-}" == "1" || "${DEV_SKIP_PIP:-}" == "true" ]]; then
    return 0
  fi
  if [[ "${DEV_SKIP_PIP:-}" == "auto" && -f "$stamp" && "$(cat "$stamp")" == "$hash" ]]; then
    return 0
  fi
  echo "Installing Python deps…"
  .venv/bin/pip install -q -r requirements.txt
  if [[ -f "$ROOT/python/requirements.txt" ]]; then
    .venv/bin/pip install -q -r "$ROOT/python/requirements.txt"
  fi
  echo "$hash" > "$stamp"
}

_pip_stamp

export PYTHONPATH="$ROOT/backend"
exec .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port "${API_PORT:-3200}" --reload
