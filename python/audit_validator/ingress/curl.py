"""Generate reproducible curl scripts for Ingress API test cases."""

from __future__ import annotations

import json
import re
from pathlib import Path

from ..models import JsonDict
from .client import load_ingress_client_config

_ENV_TOKEN = "${INGRESS_BEARER_TOKEN:-$BEARER_TOKEN_PP}"
_ENV_URL = "${INGRESS_API_URL:-https://mt-audit-log-resolver-service-preprod.monotype-pp.com/v1/audit-events}"
_ENV_MACHINE = "${INGRESS_MACHINE_ID}"
_ENV_UNIQUE = "${INGRESS_UNIQUE_ID}"


def _shell_escape(value: str) -> str:
    return value.replace("'", "'\"'\"'")


def build_ingress_curl(
    payload: JsonDict,
    *,
    payload_file: Path | None = None,
    use_env_placeholders: bool = True,
) -> str:
    """One-line-friendly curl with env placeholders for secrets and device ids."""
    cfg = load_ingress_client_config()
    url = _ENV_URL if use_env_placeholders else cfg.base_url
    token = _ENV_TOKEN if use_env_placeholders else cfg.bearer_token
    machine_id = _ENV_MACHINE if use_env_placeholders else (cfg.machine_id or "")
    unique_id = _ENV_UNIQUE if use_env_placeholders else (cfg.unique_id or "")

    actor = payload.get("actor") or {}
    ua = cfg.user_agent
    if machine_id and unique_id and "NGAPP-BS" not in ua and not use_env_placeholders:
        ua = (
            f"NGAPP-BS/{cfg.app_version}; (mac {cfg.os_version.lower()}; arm64 "
            f"{machine_id}; {unique_id})"
        )
    elif use_env_placeholders:
        ua = (
            f"NGAPP-BS/{cfg.app_version}; (mac {cfg.os_version.lower()}; arm64 "
            f"${{INGRESS_MACHINE_ID}}; ${{INGRESS_UNIQUE_ID}})"
        )

    headers = [
        "Accept: application/json",
        "Accept-Language: en",
        "Content-Type: application/json",
        f"Authorization: Bearer {token}",
        f"User-Agent: {ua}",
        f"x-dt-app-version: {cfg.app_version}",
        f"x-os-platform: {cfg.os_platform}",
        f"x-os-version: {cfg.os_version}",
        f"x-request-source: {cfg.request_source}",
        "x-unauthorized-redirect: false",
    ]
    if machine_id:
        headers.append(f"x-machine-id: {machine_id}")
    if unique_id:
        headers.append(f"x-unique-id: {unique_id}")
    cid = str(payload.get("xCorrelationId") or "")
    if cid:
        headers.append(f"x-correlation-id: {cid}")

    lines = [f"curl --location '{url}' \\"]
    for header in headers:
        lines.append(f"  --header '{_shell_escape(header)}' \\")
    if payload_file:
        lines.append(f"  --data-binary '@{payload_file.name}'")
    else:
        body = json.dumps([payload], ensure_ascii=False)
        lines.append(f"  --data-binary '{_shell_escape(body)}'")
    return "\n".join(lines)


def normalize_excel_curl(raw: object, *, payload_file: str = "payload.json") -> str | None:
    """Turn spreadsheet curl into a template with env vars and payload file reference."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text or text.casefold() in {"nan", "wip"}:
        return None
    if not text.lower().startswith("curl"):
        return None

    out = text
    out = re.sub(
        r"https?://[^\s']+/v1/audit-events",
        _ENV_URL,
        out,
        count=1,
    )
    out = re.sub(
        r"(Authorization:\s*Bearer\s+)[^\s\\']+",
        rf"\1{_ENV_TOKEN}",
        out,
        flags=re.IGNORECASE,
    )
    out = re.sub(
        r"(x-machine-id:\s*)[^\s\\']+",
        rf"\1{_ENV_MACHINE}",
        out,
        flags=re.IGNORECASE,
    )
    out = re.sub(
        r"(x-unique-id:\s*)[^\s\\']+",
        rf"\1{_ENV_UNIQUE}",
        out,
        flags=re.IGNORECASE,
    )
    out = re.sub(
        r"NGAPP-BS/[^;]+;\s*\([^)]+\)",
        (
            "NGAPP-BS/${INGRESS_APP_VERSION:-1.0.0.0}; "
            "(mac ${INGRESS_OS_VERSION:-26.5.0}; arm64 ${INGRESS_MACHINE_ID}; ${INGRESS_UNIQUE_ID})"
        ),
        out,
    )
    # Replace inline JSON body with payload file reference
    out = re.sub(r"\s\\?\n?\s*--data(-binary)?\s+('|\[)[\s\S]*", "", out, flags=re.IGNORECASE)
    out = out.rstrip().rstrip("\\").rstrip()
    out = f"{out} \\\n  --data-binary '@{payload_file}'"
    return out


def write_curl_script(
    path: Path,
    *,
    payload: JsonDict,
    payload_file: Path,
    excel_curl: str | None = None,
) -> None:
    """Write executable curl script next to payload JSON."""
    rel_payload = payload_file.name
    body = normalize_excel_curl(excel_curl, payload_file=rel_payload)
    if not body:
        body = build_ingress_curl(payload, payload_file=payload_file, use_env_placeholders=True)

    script = "\n".join(
        [
            "#!/usr/bin/env bash",
            "# Ingress API — POST audit event (requires INGRESS_BEARER_TOKEN, device ids).",
            "# Raw/enriched verification uses INGRESS_RAW_QUEUE / INGRESS_ENRICHED_QUEUE.",
            "set -euo pipefail",
            'ROOT="$(cd "$(dirname "$0")/.." && pwd)"',
            'cd "$ROOT"',
            "",
            body,
            "",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(script, encoding="utf-8")
    path.chmod(0o755)
