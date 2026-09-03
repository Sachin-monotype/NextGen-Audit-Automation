#!/usr/bin/env bash
# Start local MongoDB for UAT raw/enrich audit logs (mongodb://localhost:27017).
#
# Tries Docker first (docker-compose.yml). If Docker Hub is unreachable (common on
# corporate VPN), falls back to Homebrew mongodb-community@7.0.
#
# Usage:
#   ./scripts/start-audit-mongo.sh          # auto (docker → brew)
#   ./scripts/start-audit-mongo.sh --brew   # skip docker, use Homebrew only
#   ./scripts/start-audit-mongo.sh --docker # docker only, no brew fallback

set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

MODE="${1:-auto}"
MONGO_PORT="${MONGO_PORT:-27017}"
BREW_MONGO="mongodb-community@7.0"

DOCKER_DESKTOP_BIN="/Applications/Docker.app/Contents/Resources/bin"
if [[ -d "$DOCKER_DESKTOP_BIN" ]]; then
  export PATH="$DOCKER_DESKTOP_BIN:$PATH"
fi

mongo_ready() {
  if command -v nc >/dev/null 2>&1; then
    nc -z 127.0.0.1 "$MONGO_PORT" 2>/dev/null
    return
  fi
  # bash /dev/tcp fallback
  (echo >/dev/tcp/127.0.0.1/"$MONGO_PORT") >/dev/null 2>&1
}

wait_for_mongo() {
  local i
  for i in $(seq 1 30); do
    if mongo_ready; then
      return 0
    fi
    sleep 1
  done
  return 1
}

start_docker() {
  if ! command -v docker >/dev/null 2>&1; then
    return 1
  fi
  if ! docker info >/dev/null 2>&1; then
    echo "Docker daemon not reachable." >&2
    return 1
  fi

  if ! command -v docker-credential-desktop >/dev/null 2>&1; then
    export DOCKER_CONFIG="${TMPDIR:-/tmp}/nextgen-audit-docker-$$"
    mkdir -p "$DOCKER_CONFIG"
    printf '%s\n' '{"auths":{}}' >"$DOCKER_CONFIG/config.json"
  fi

  if docker image inspect mongo:7 >/dev/null 2>&1; then
    echo "Using local Docker image mongo:7"
  else
    echo "Pulling mongo:7 from Docker Hub (may fail on VPN/corporate network)..."
    if ! docker pull mongo:7; then
      echo "Docker pull failed (registry EOF / blocked)." >&2
      return 1
    fi
  fi

  docker compose up -d audit-mongo
  wait_for_mongo
  echo "Audit Mongo (Docker) ready at mongodb://localhost:${MONGO_PORT}"
  echo "  container: nextgen-audit-mongo  DB: AuditLogsUAT when AUDIT_TARGET=uat"
}

start_brew() {
  if ! command -v brew >/dev/null 2>&1; then
    echo "Homebrew not found — install from https://brew.sh or fix Docker Hub access." >&2
    return 1
  fi

  if ! brew tap | grep -q '^mongodb/brew$'; then
    echo "Adding mongodb/brew tap..."
    brew tap mongodb/brew
  fi

  if ! brew list "$BREW_MONGO" >/dev/null 2>&1; then
    echo "Installing $BREW_MONGO (no Docker Hub needed)..."
    brew install "$BREW_MONGO"
  fi

  # Link binaries if not on PATH
  BREW_PREFIX="$(brew --prefix)"
  MONGO_BIN="$BREW_PREFIX/opt/$BREW_MONGO/bin"
  if [[ -d "$MONGO_BIN" ]]; then
    export PATH="$MONGO_BIN:$PATH"
  fi

  if mongo_ready; then
    echo "MongoDB already listening on :${MONGO_PORT}"
  else
    echo "Starting $BREW_MONGO via brew services..."
    brew services start "$BREW_MONGO"
    if ! wait_for_mongo; then
      echo "Mongo did not become ready on :${MONGO_PORT} within 30s." >&2
      echo "Check: brew services list && tail /opt/homebrew/var/log/mongodb/mongo.log" >&2
      return 1
    fi
  fi

  echo "Audit Mongo (Homebrew) ready at mongodb://localhost:${MONGO_PORT}"
  echo "  service: $BREW_MONGO  DB: AuditLogsUAT when AUDIT_TARGET=uat"
  echo "  stop:  brew services stop $BREW_MONGO"
}

if mongo_ready; then
  echo "MongoDB already running on mongodb://localhost:${MONGO_PORT}"
  exit 0
fi

case "$MODE" in
  --brew|brew)
    start_brew
    ;;
  --docker|docker)
    start_docker
    ;;
  auto|"")
    if start_docker; then
      exit 0
    fi
    echo ""
    echo "Docker path failed — trying Homebrew MongoDB instead..."
    echo ""
    start_brew
    ;;
  *)
    echo "Usage: $0 [--brew|--docker]" >&2
    exit 1
    ;;
esac
