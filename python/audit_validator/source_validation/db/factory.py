"""Choose HTTP vs MySQL clients for UMS / CMS / AMS."""

from __future__ import annotations

import inspect
import logging
import os
from typing import Any, Callable

from ..clients import AmsClient, CmsClient, UmsClient
from ..config import SourceValidationConfig
from .clients import AmsDbClient, CmsDbClient, UmsDbClient
from .connection import load_mysql_config, mysql_ready

log = logging.getLogger(__name__)


def source_truth_mode() -> str:
    """``api`` (default) | ``db`` — also accepts SOURCE_VALIDATION_TRUTH."""
    raw = (
        os.getenv("SOURCE_TRUTH")
        or os.getenv("SOURCE_VALIDATION_TRUTH")
        or "api"
    ).strip().lower()
    return "db" if raw in {"db", "mysql", "sql"} else "api"


def _filter_kwargs(fn: Callable[..., Any], kwargs: dict[str, Any]) -> dict[str, Any]:
    """Drop kwargs the callable does not accept (HTTP vs DB signature drift)."""
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return kwargs
    if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()):
        return kwargs
    allowed = {
        name
        for name, p in sig.parameters.items()
        if p.kind
        in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        )
    }
    kept = {k: v for k, v in kwargs.items() if k in allowed}
    dropped = sorted(set(kwargs) - set(kept))
    if dropped:
        log.debug(
            "%s ignored unexpected kwargs %s (HTTP/DB client parity)",
            getattr(fn, "__qualname__", getattr(fn, "__name__", repr(fn))),
            dropped,
        )
    return kept


class _KwargsCompatClient:
    """Proxy so DB clients never TypeError on HTTP-only keyword args.

    Compare always calls the same kwargs against whichever source-truth client
    ``build_ums_cms_ams_clients`` returns. A missing ``user_type`` (etc.) on the
    MySQL client used to wipe entire UMS profile blocks into SKIP — never again.
    """

    __slots__ = ("_inner",)

    def __init__(self, inner: Any) -> None:
        object.__setattr__(self, "_inner", inner)

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._inner, name)
        if not callable(attr) or isinstance(attr, type):
            return attr

        def _call(*args: Any, **kwargs: Any) -> Any:
            return attr(*args, **_filter_kwargs(attr, kwargs))

        return _call

    def __repr__(self) -> str:
        return f"_KwargsCompatClient({self._inner!r})"


def wrap_source_client(client: Any | None) -> Any | None:
    """Wrap a DB/HTTP client for kwargs-safe Compare calls."""
    if client is None:
        return None
    if isinstance(client, _KwargsCompatClient):
        return client
    return _KwargsCompatClient(client)


# Shared method names that Compare / prefetch call on both HTTP and DB clients.
SOURCE_CLIENT_SHARED_METHODS: dict[str, tuple[str, ...]] = {
    "ums": (
        "get_profile_by_id",
        "get_profiles_by_ids",
        "get_role_by_id",
        "get_user_by_idp_user_id",
        "get_teams_by_ids",
        "get_invitation_by_email",
    ),
    "cms": ("get_customer_by_id", "get_customer"),
    "ams": ("get_asset_by_id", "get_assets_by_ids_only"),
}


def http_db_kwonly_parity() -> list[str]:
    """Return human-readable mismatches: HTTP kw-only params missing on DB clients.

    Used by unit tests so Compare cannot regress into
    ``unexpected keyword argument`` SKIP storms again.
    """
    pairs = (
        ("ums", UmsClient, UmsDbClient),
        ("cms", CmsClient, CmsDbClient),
        ("ams", AmsClient, AmsDbClient),
    )
    problems: list[str] = []
    for kind, http_cls, db_cls in pairs:
        for name in SOURCE_CLIENT_SHARED_METHODS.get(kind, ()):
            http_fn = getattr(http_cls, name, None)
            db_fn = getattr(db_cls, name, None)
            if http_fn is None or db_fn is None:
                problems.append(f"{kind}.{name}: missing on {'HTTP' if http_fn is None else 'DB'}")
                continue
            try:
                http_sig = inspect.signature(http_fn)
                db_sig = inspect.signature(db_fn)
            except (TypeError, ValueError) as exc:
                problems.append(f"{kind}.{name}: cannot inspect ({exc})")
                continue
            http_kw = {
                n
                for n, p in http_sig.parameters.items()
                if p.kind == inspect.Parameter.KEYWORD_ONLY and n != "self"
            }
            db_params = db_sig.parameters
            db_accepts_var = any(
                p.kind == inspect.Parameter.VAR_KEYWORD for p in db_params.values()
            )
            if db_accepts_var:
                continue
            for kw in sorted(http_kw):
                if kw not in db_params:
                    problems.append(
                        f"{kind}.{name}: DB missing keyword-only param {kw!r} "
                        f"(HTTP has it; Compare will TypeError without kwargs compat)"
                    )
    return problems


def build_ums_cms_ams_clients(
    cfg: SourceValidationConfig,
) -> tuple[Any | None, Any | None, Any | None, str]:
    """Return ``(ums, cms, ams, truth_mode)``.

    When ``SOURCE_TRUTH=db`` and MySQL env is set, swap UMS/CMS/AMS to DB clients.
    Discovery/Typesense stays on HTTP either way.
    Falls back to HTTP API clients when pymysql is missing or MySQL is unreachable.
    """
    mode = source_truth_mode()
    if mode == "db":
        try:
            import pymysql  # noqa: F401
        except ImportError:
            msg = (
                "SOURCE_TRUTH=db but pymysql is not installed — falling back to API clients. "
                "QA customers often missing on PP CMS/UMS HTTP → mass SKIP/FAIL. "
                "Install with: backend/.venv/bin/pip install pymysql "
                "(and run Compare with that venv's python)."
            )
            log.warning(msg)
            print(msg)
            mode = "api"
        else:
            mysql = load_mysql_config()
            if not mysql_ready(mysql):
                log.warning(
                    "SOURCE_TRUTH=db but MYSQL_* not configured — falling back to API clients"
                )
                mode = "api"
            else:
                log.info(
                    "Source truth: MySQL (%s) for UMS/CMS/AMS — Typesense still HTTP",
                    mysql.host,
                )
                cms = wrap_source_client(CmsDbClient(mysql))
                ams = wrap_source_client(AmsDbClient(mysql))
                # Opt out of MySQL UMS only when explicitly requested (e.g. SOURCE_TRUTH_UMS=api).
                force_ums_api = (os.getenv("SOURCE_TRUTH_UMS") or "").strip().lower() in {
                    "api",
                    "http",
                }
                if force_ums_api:
                    log.info("Source truth: UMS via HTTP API (SOURCE_TRUTH_UMS=api)")
                    ums = wrap_source_client(UmsClient(cfg) if cfg.ums_ready else None)
                else:
                    ums = wrap_source_client(UmsDbClient(mysql))
                return ums, cms, ams, "db"

    ums = wrap_source_client(UmsClient(cfg) if cfg.ums_ready else None)
    cms = wrap_source_client(CmsClient(cfg) if cfg.cms_ready else None)
    ams = wrap_source_client(AmsClient(cfg) if cfg.ams_ready else None)
    return ums, cms, ams, "api"
