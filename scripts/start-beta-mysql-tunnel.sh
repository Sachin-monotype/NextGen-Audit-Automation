#!/usr/bin/env bash
# SSH tunnel to Beta MySQL source databases (AMS / CMS / UMS) — READ-ONLY.
#
# Usage:
#   ./scripts/start-beta-mysql-tunnel.sh          # start in background
#   ./scripts/start-beta-mysql-tunnel.sh --stop   # stop
#   ./scripts/start-beta-mysql-tunnel.sh --fg     # foreground

set -euo pipefail

LOCAL_PORT="${MYSQL_BETA_TUNNEL_LOCAL_PORT:-13307}"
SSH_HOST="${MYSQL_BETA_SSH_HOST:-100.59.16.174}"
SSH_USER="${MYSQL_BETA_SSH_USER:-ssh_user_shared}"
SSH_KEY="${MYSQL_BETA_SSH_KEY:-$HOME/Downloads/beta_nextgen}"
REMOTE_MYSQL_HOST="${MYSQL_BETA_REMOTE_HOST:-mosaic-nextgen.monotype-beta-r53.com}"
REMOTE_MYSQL_PORT="${MYSQL_BETA_REMOTE_PORT:-3306}"
PID_FILE="${TMPDIR:-/tmp}/nextgen-beta-mysql-tunnel.pid"

is_listening() {
  if command -v nc >/dev/null 2>&1; then
    nc -z 127.0.0.1 "$LOCAL_PORT" 2>/dev/null
    return
  fi
  (echo >/dev/tcp/127.0.0.1/"$LOCAL_PORT") >/dev/null 2>&1
}

stop_tunnel() {
  if [[ -f "$PID_FILE" ]]; then
    local pid
    pid="$(cat "$PID_FILE" 2>/dev/null || true)"
    if [[ -n "${pid:-}" ]] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
      echo "Stopped beta MySQL tunnel pid=$pid"
    fi
    rm -f "$PID_FILE"
  fi
  pkill -f "${LOCAL_PORT}:${REMOTE_MYSQL_HOST}:${REMOTE_MYSQL_PORT}" 2>/dev/null || true
}

case "${1:-}" in
  --stop|stop)
    stop_tunnel
    exit 0
    ;;
esac

if [[ ! -r "$SSH_KEY" ]]; then
  echo "SSH key not readable: $SSH_KEY" >&2
  exit 1
fi

if is_listening; then
  echo "Beta MySQL tunnel already listening on 127.0.0.1:${LOCAL_PORT}"
  exit 0
fi

SSH_OPTS=(
  -o BatchMode=yes
  -o ExitOnForwardFailure=yes
  -o ServerAliveInterval=30
  -o ServerAliveCountMax=3
  -o ConnectTimeout=15
  -i "$SSH_KEY"
  -L "${LOCAL_PORT}:${REMOTE_MYSQL_HOST}:${REMOTE_MYSQL_PORT}"
  "${SSH_USER}@${SSH_HOST}"
)

if [[ "${1:-}" == "--fg" || "${1:-}" == "fg" ]]; then
  echo "Foreground tunnel 127.0.0.1:${LOCAL_PORT} → ${REMOTE_MYSQL_HOST}:${REMOTE_MYSQL_PORT}"
  exec ssh -N "${SSH_OPTS[@]}"
fi

ssh -f -N "${SSH_OPTS[@]}"
pgrep -f "${LOCAL_PORT}:${REMOTE_MYSQL_HOST}:${REMOTE_MYSQL_PORT}" | head -1 >"$PID_FILE" || true

for _ in $(seq 1 20); do
  if is_listening; then
    echo "Beta MySQL tunnel ready: 127.0.0.1:${LOCAL_PORT} → ${REMOTE_MYSQL_HOST}:${REMOTE_MYSQL_PORT}"
    echo "  stop: ./scripts/start-beta-mysql-tunnel.sh --stop"
    exit 0
  fi
  sleep 1
done

echo "Beta MySQL tunnel failed to listen on :${LOCAL_PORT}" >&2
exit 1