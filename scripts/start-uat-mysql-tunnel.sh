#!/usr/bin/env bash
# SSH tunnel to Nextgen UAT MySQL (AMS / CMS) — READ-ONLY app access.
#
# Bastion: sachin@3.81.215.108
# MySQL:   nextgen-mosaic.monotype-uat-r53.com:3306 (Uba_uat)
#
# Usage:
#   ./scripts/start-uat-mysql-tunnel.sh          # start (background)
#   ./scripts/start-uat-mysql-tunnel.sh --stop   # stop
#   ./scripts/start-uat-mysql-tunnel.sh --fg     # foreground
#
# Then set (see .env.example):
#   MYSQL_HOST_UAT=127.0.0.1
#   MYSQL_PORT_UAT=13306
#   MYSQL_USER_UAT=Uba_uat
#   MYSQL_PASSWORD_UAT=…   # from MySQL Workbench / Keychain
#
# NOTE: This UAT host exposes unsuffixed schemas for source validation:
#   user_management / customer_management / asset_management
# (QA mirrors use *_nextgenqa — do not use those on AUDIT_TARGET=uat.)
# App access is SELECT-only (shared_user_read).

set -euo pipefail

LOCAL_PORT="${MYSQL_TUNNEL_LOCAL_PORT:-13306}"
SSH_HOST="${MYSQL_SSH_HOST:-3.81.215.108}"
SSH_USER="${MYSQL_SSH_USER:-sachin}"
SSH_KEY="${MYSQL_SSH_KEY:-$HOME/.ssh/id_rsa}"
REMOTE_MYSQL_HOST="${MYSQL_REMOTE_HOST:-nextgen-mosaic.monotype-uat-r53.com}"
REMOTE_MYSQL_PORT="${MYSQL_REMOTE_PORT:-3306}"
PID_FILE="${TMPDIR:-/tmp}/nextgen-uat-mysql-tunnel.pid"

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
      echo "Stopped tunnel pid=$pid"
    fi
    rm -f "$PID_FILE"
  fi
  # Also clear any leftover forward on this port
  pkill -f "${LOCAL_PORT}:${REMOTE_MYSQL_HOST}:${REMOTE_MYSQL_PORT}" 2>/dev/null || true
}

case "${1:-}" in
  --stop|stop)
    stop_tunnel
    exit 0
    ;;
esac

if is_listening; then
  echo "Tunnel already listening on 127.0.0.1:${LOCAL_PORT}"
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
# Record ssh pid (best-effort)
pgrep -f "${LOCAL_PORT}:${REMOTE_MYSQL_HOST}:${REMOTE_MYSQL_PORT}" | head -1 >"$PID_FILE" || true
sleep 0.5
if is_listening; then
  echo "UAT MySQL tunnel ready: 127.0.0.1:${LOCAL_PORT} → ${REMOTE_MYSQL_HOST}:${REMOTE_MYSQL_PORT}"
  echo "  stop: ./scripts/start-uat-mysql-tunnel.sh --stop"
else
  echo "Tunnel failed to listen on :${LOCAL_PORT}" >&2
  exit 1
fi
