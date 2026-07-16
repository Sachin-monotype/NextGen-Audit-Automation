#!/usr/bin/env bash
# Run the RabbitMQ → Mongo ingestion service standalone (audit-sense replacement).
# Continuously drains the platform subscription queues into Mongo until Ctrl-C.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# Reuse the backend venv (has pika + pymongo + python-dotenv installed).
PY="$ROOT/backend/.venv/bin/python"
if [[ ! -x "$PY" ]]; then
  echo "Backend venv not found — run scripts/dev.sh once (or backend/run.sh) to create it." >&2
  exit 1
fi

export PYTHONPATH="$ROOT/python:${PYTHONPATH:-}"
cd "$ROOT"
exec "$PY" -m audit_validator.ingestion
