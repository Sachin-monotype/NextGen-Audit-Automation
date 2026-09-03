"""Source-validation policy toggles (per audit target)."""

from __future__ import annotations

import os

from audit_validator.env_profiles import audit_target_name


def accept_enriched_on_source_miss(target: str | None = None) -> bool:
    """When False (default), never upgrade SKIP/FAIL → PASS because source was empty/unreachable.

    Opt in per target: ``SOURCE_VALIDATION_ACCEPT_ENRICHED_ON_MISS_UAT=true``
    or globally: ``SOURCE_VALIDATION_ACCEPT_ENRICHED_ON_MISS=true``.
    """
    t = (target or audit_target_name()).strip().lower()
    specific = (os.getenv(f"SOURCE_VALIDATION_ACCEPT_ENRICHED_ON_MISS_{t.upper()}") or "").strip()
    if specific:
        return specific.lower() in {"1", "true", "yes", "on"}
    raw = (os.getenv("SOURCE_VALIDATION_ACCEPT_ENRICHED_ON_MISS") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}
