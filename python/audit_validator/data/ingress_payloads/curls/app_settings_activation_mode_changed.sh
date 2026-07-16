#!/usr/bin/env bash
# Ingress API — POST audit event (requires INGRESS_BEARER_TOKEN, device ids).
# Raw/enriched verification uses INGRESS_RAW_QUEUE / INGRESS_ENRICHED_QUEUE.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

curl --location '${INGRESS_API_URL:-https://mt-audit-log-resolver-service-preprod.monotype-pp.com/v1/audit-events}' \
--header 'Accept-Language: en' \
--header 'User-Agent: NGAPP-BS/${INGRESS_APP_VERSION:-1.0.0.0}; (mac ${INGRESS_OS_VERSION:-26.5.0}; arm64 ${INGRESS_MACHINE_ID}; ${INGRESS_UNIQUE_ID})' \
--header 'x-dt-app-version: 1.0.0.0' \
--header 'x-machine-id: ${INGRESS_MACHINE_ID}' \
--header 'x-os-platform: MAC' \
--header 'x-os-version: 26.5.1' \
--header 'x-request-source: MT_CONNECT_BS' \
--header 'x-unauthorized-redirect: false' \
--header 'x-unique-id: ${INGRESS_UNIQUE_ID}' \
--header 'Authorization: Bearer ${INGRESS_BEARER_TOKEN:-$BEARER_TOKEN_PP}' \
--header 'x-correlation-id: bff541bd-4a97-4818-b813-7fe1e813b334' \
--header 'Accept-Encoding: gzip; deflate; br' \
--header 'Content-Type: application/json; charset=utf-8' \
  --data-binary '@app_settings_activation_mode_changed.json'
