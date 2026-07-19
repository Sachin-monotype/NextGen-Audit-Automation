"""Correlation-id helpers for API + UI-triggered audit flows.

API / GraphQL simulation still mints ``x-correlation-id`` on the request.
Browser/UI flows go through Cloudflare, which overrides ``x-correlation-id``.
Dev added a separate response header ``correlation-id`` (no ``x-``) that survives
Cloudflare — use that to pair raw ↔ enriched when present.
"""

from __future__ import annotations

from typing import Any, Mapping


# Ordered preference when reading a response / envelope.
_RESPONSE_HEADER_KEYS = (
    "correlation-id",  # Cloudflare-safe (UI trigger)
    "Correlation-Id",
    "x-correlation-id",
    "X-Correlation-Id",
)

_PAYLOAD_KEYS = (
    "correlationId",  # camelCase sibling some emitters use
    "xCorrelationId",
    "correlation_id",
    "x_correlation_id",
)

_AMQP_HEADER_KEYS = (
    "correlation-id",
    "x-correlation-id",
    "xCorrelationId",
    "correlationId",
    "x_correlation_id",
)


def from_response_headers(headers: Mapping[str, Any] | None) -> str | None:
    """Prefer Cloudflare-safe ``correlation-id`` over ``x-correlation-id``."""
    if not headers:
        return None
    # Case-insensitive lookup
    lower = {str(k).lower(): v for k, v in headers.items()}
    for key in ("correlation-id", "x-correlation-id"):
        val = lower.get(key)
        if isinstance(val, bytes):
            val = val.decode("utf-8", errors="replace")
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def from_payload(payload: Mapping[str, Any] | None) -> str | None:
    if not payload:
        return None
    for key in _PAYLOAD_KEYS:
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def from_amqp_headers(headers: Mapping[str, Any] | None) -> str | None:
    if not headers:
        return None
    for key in _AMQP_HEADER_KEYS:
        val = headers.get(key)
        if isinstance(val, bytes):
            val = val.decode("utf-8", errors="replace")
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def extract_correlation(
    *,
    payload: Mapping[str, Any] | None = None,
    response_headers: Mapping[str, Any] | None = None,
    amqp_headers: Mapping[str, Any] | None = None,
    fallback: str | None = None,
) -> str | None:
    """Best-effort correlation for pairing raw ↔ enriched.

    Preference:
      1. Response header ``correlation-id`` (UI / Cloudflare-safe)
      2. Payload ``correlationId`` / ``xCorrelationId``
      3. AMQP headers
      4. Explicit fallback (e.g. the id we minted on the request)
    """
    return (
        from_response_headers(response_headers)
        or from_payload(payload)
        or from_amqp_headers(amqp_headers)
        or (fallback.strip() if isinstance(fallback, str) and fallback.strip() else None)
    )


def mongo_correlation_filter(cid: str, *, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Mongo filter matching either envelope field name, optionally AND-ed with ``extra``."""
    cid = (cid or "").strip()
    if not cid:
        return dict(extra or {})
    cid_or = {
        "$or": [
            {"xCorrelationId": cid},
            {"correlationId": cid},
        ]
    }
    if not extra:
        return cid_or
    return {"$and": [cid_or, extra]}
