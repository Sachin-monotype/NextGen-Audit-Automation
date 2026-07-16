#!/usr/bin/env bash
# Ingress API — POST audit event (requires INGRESS_BEARER_TOKEN, device ids).
# Raw/enriched verification uses INGRESS_RAW_QUEUE / INGRESS_ENRICHED_QUEUE.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

curl --location '${INGRESS_API_URL:-https://mt-audit-log-resolver-service-preprod.monotype-pp.com/v1/audit-events}' \
  --header 'Accept: application/json' \
  --header 'Accept-Language: en' \
  --header 'Content-Type: application/json' \
  --header 'Authorization: Bearer ${INGRESS_BEARER_TOKEN:-$BEARER_TOKEN_PP}' \
  --header 'User-Agent: NGAPP-BS/1.0.0.0; (mac 26.5.0; arm64 ${INGRESS_MACHINE_ID}; ${INGRESS_UNIQUE_ID})' \
  --header 'x-dt-app-version: 1.0.0.0' \
  --header 'x-os-platform: MAC' \
  --header 'x-os-version: 26.5.0' \
  --header 'x-request-source: MT_CONNECT_BS' \
  --header 'x-unauthorized-redirect: false' \
  --header 'x-machine-id: ${INGRESS_MACHINE_ID}' \
  --header 'x-unique-id: ${INGRESS_UNIQUE_ID}' \
  --header 'x-correlation-id: 6dbd2806-08e0-45c1-8af4-7b5b2eda6619' \
  --data-binary '@userloginfailureapp.json'
