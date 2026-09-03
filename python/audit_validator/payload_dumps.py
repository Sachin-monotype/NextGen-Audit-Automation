"""Fetch raw/enriched envelopes from mt-audit-log-resolver ``/v1/payload-dumps``.

Used as a fallback when local Mongo has no document for a correlation id
(e.g. Excel validation while RabbitMQ ingestion is off).
"""

from __future__ import annotations

import gzip
import json
import logging
import os
from typing import Any
from urllib.parse import urlparse, urlunparse

import requests

log = logging.getLogger(__name__)

# Display tab → resolver dump "type" (inbound ≈ raw, outbound ≈ enriched).
_TAB_DUMP_TYPE = {
    "raw": "inbound",
    "enriched": "outbound",
}


def payload_dumps_fallback_enabled() -> bool:
    raw = (os.getenv("PAYLOAD_DUMPS_FALLBACK") or "true").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def payload_dumps_base_url(*, target: str | None = None) -> str:
    """Resolve ``…/v1/payload-dumps`` for the active (or given) audit target.

    Order:
    1. ``PAYLOAD_DUMPS_URL_{TARGET}`` / ``PAYLOAD_DUMPS_URL``
    2. Derive from ``INGRESS_API_URL`` (swap path)
    3. Profile defaults (UAT host has no ``-uat`` infix)
    """
    from .env_profiles import audit_target_name, get_audit_profile

    name = (target or audit_target_name()).strip().lower()
    explicit = (
        (os.getenv(f"PAYLOAD_DUMPS_URL_{name.upper()}") or "").strip()
        or (os.getenv("PAYLOAD_DUMPS_URL") or "").strip()
    )
    if explicit:
        return explicit.rstrip("/")

    ingress = (os.getenv("INGRESS_API_URL") or "").strip()
    if not ingress and name:
        ingress = (get_audit_profile(name).ingress_api_url or "").strip()
    if ingress:
        parsed = urlparse(ingress)
        path = parsed.path or ""
        if path.endswith("/audit-events"):
            path = path[: -len("/audit-events")] + "/payload-dumps"
        elif path.rstrip("/").endswith("v1"):
            path = path.rstrip("/") + "/payload-dumps"
        else:
            path = "/v1/payload-dumps"
        return urlunparse(parsed._replace(path=path, query="", fragment="")).rstrip("/")

    defaults = {
        "uat": "https://mt-audit-log-resolver-service.monotype-uat.com/v1/payload-dumps",
        "qa": "https://mt-audit-log-resolver-service-qa.monotype-pp.com/v1/payload-dumps",
        "pp": "https://mt-audit-log-resolver-service-preprod.monotype-pp.com/v1/payload-dumps",
        "preprod": "https://mt-audit-log-resolver-service-preprod.monotype-pp.com/v1/payload-dumps",
    }
    return defaults.get(name, defaults["qa"])


def dump_type_for_tab(tab: str) -> str | None:
    return _TAB_DUMP_TYPE.get((tab or "").strip().lower())


def _decode_body(raw: bytes) -> Any:
    if not raw:
        return None
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    text = raw.decode("utf-8", errors="replace").strip()
    if not text:
        return None
    return json.loads(text)


def fetch_payload_dump(
    correlation_id: str,
    *,
    tab: str,
    base_url: str | None = None,
    timeout_sec: float | None = None,
) -> dict[str, Any] | None:
    """GET one envelope for ``correlation_id`` matching Display tab raw|enriched.

    Returns the JSON object or ``None`` when missing / unreachable.
    """
    cid = (correlation_id or "").strip()
    dump_type = dump_type_for_tab(tab)
    if not cid or not dump_type:
        return None

    url = (base_url or payload_dumps_base_url()).rstrip("/")
    timeout = timeout_sec
    if timeout is None:
        timeout = float(os.getenv("PAYLOAD_DUMPS_TIMEOUT_SEC") or "20")

    # ``dataType`` is accepted by the API but inbound/outbound already select
    # raw vs enriched; pass both spellings for compatibility.
    data_type = "raw" if dump_type == "inbound" else "enriched"
    params = {
        "type": dump_type,
        "correlation-id": cid,
        "dataType": data_type,
    }
    try:
        resp = requests.get(url, params=params, timeout=timeout)
    except requests.RequestException as exc:
        log.warning("payload-dumps request failed for %s (%s): %s", cid[:8], dump_type, exc)
        return None

    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        log.warning(
            "payload-dumps HTTP %s for %s (%s): %s",
            resp.status_code,
            cid[:8],
            dump_type,
            (resp.text or "")[:200],
        )
        return None

    try:
        data = _decode_body(resp.content)
    except (OSError, json.JSONDecodeError, UnicodeError) as exc:
        log.warning("payload-dumps decode failed for %s: %s", cid[:8], exc)
        return None

    if not isinstance(data, dict):
        return None
    return data
