"""HTTP vs MySQL source clients must accept the same Compare kwargs."""

from __future__ import annotations

import inspect

from audit_validator.source_validation.db.factory import (
    _KwargsCompatClient,
    _filter_kwargs,
    http_db_kwonly_parity,
    wrap_source_client,
)


def test_http_db_kwonly_parity_has_no_mismatches() -> None:
    problems = http_db_kwonly_parity()
    assert problems == [], "HTTP/DB client kw-only drift:\n" + "\n".join(problems)


def test_ums_db_get_profile_by_id_accepts_user_type() -> None:
    from audit_validator.source_validation.db.clients import UmsDbClient

    sig = inspect.signature(UmsDbClient.get_profile_by_id)
    assert "user_type" in sig.parameters


def test_kwargs_compat_drops_unknown_without_typeerror() -> None:
    class Stub:
        def get_profile_by_id(self, profile_id: str, customer_id: str, *, correlation_id: str = ""):
            return {"id": profile_id, "customerId": customer_id, "cid": correlation_id}

    wrapped = wrap_source_client(Stub())
    assert isinstance(wrapped, _KwargsCompatClient)
    row = wrapped.get_profile_by_id(
        "pid-1",
        "cust-1",
        correlation_id="c",
        user_type="service",  # HTTP-only; must not TypeError
    )
    assert row == {"id": "pid-1", "customerId": "cust-1", "cid": "c"}


def test_filter_kwargs_keeps_known() -> None:
    def fn(*, correlation_id: str = "", user_type: str | None = None) -> None:
        return None

    assert _filter_kwargs(fn, {"correlation_id": "a", "user_type": "service", "nope": 1}) == {
        "correlation_id": "a",
        "user_type": "service",
    }
