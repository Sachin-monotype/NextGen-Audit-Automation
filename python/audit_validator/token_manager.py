"""Bearer-token lifecycle for simulation / source validation.

Goal (per QA workflow): paste a Bearer token **once**. From then on we:
  1. Track its expiry from the JWT ``exp`` claim (no manual checking).
  2. Auto-derive OAuth org / gcid from the token's claims so we can regenerate it.
  3. On each run, if the token is still valid we reuse it; if it is expired (or
     about to expire) we regenerate a fresh one via the password grant and write
     it back to ``.env`` — then run.
  4. Verify that a freshly generated token matches the pasted one (same subject),
     so we know the stored credentials produce the same identity.

This is a thin orchestration layer over :mod:`audit_validator.auth`, which already
implements the OAuth password grant and JWT decode helpers.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from dotenv import load_dotenv

from .auth import (
    DEFAULT_OAUTH,
    _set_env_var,
    _strip_bearer,
    fetch_oauth_token,
    jwt_expires_in_hours,
    jwt_is_expired,
    jwt_payload,
)
from .project_root import find_project_root

# JWT custom-claim keys that carry the OAuth org / gcid on NextGen tokens.
# We scan case-insensitively and also match any namespaced Auth0 claim that
# ends with these fragments (e.g. "https://monotype.com/gcid").
_GCID_CLAIM_HINTS = ("gcid", "globalcustomerid", "global_customer_id", "customerid")
_ORG_CLAIM_HINTS = ("t_organization", "org_id", "orgid", "organization", "identityprovider")
_EMAIL_CLAIM_HINTS = ("email", "preferred_username", "upn")

LogFn = Callable[[str], None]


@dataclass
class TokenStatus:
    present: bool = False
    expired: bool = True
    expires_in_hours: float | None = None
    email: str = ""
    org: str = ""
    gcid: str = ""
    source_var: str = "BEARER_TOKEN"
    regenerated: bool = False
    matches_provided: bool | None = None
    can_regenerate: bool = False
    message: str = ""
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "present": self.present,
            "expired": self.expired,
            "expires_in_hours": (
                round(self.expires_in_hours, 2) if self.expires_in_hours is not None else None
            ),
            "email": self.email,
            "org": self.org,
            "gcid": self.gcid,
            "source_var": self.source_var,
            "regenerated": self.regenerated,
            "matches_provided": self.matches_provided,
            "can_regenerate": self.can_regenerate,
            "message": self.message,
            "notes": self.notes,
        }


def _claim(payload: dict, hints: tuple[str, ...]) -> str:
    for key, value in payload.items():
        low = str(key).lower()
        if any(low == h or low.endswith(h) or low.endswith("/" + h) for h in hints):
            if value:
                return str(value)
    return ""


def token_metadata(token: str) -> dict[str, str]:
    """Extract email / org / gcid from a JWT's claims (best effort)."""
    payload = jwt_payload(token)
    if not payload:
        return {"email": "", "org": "", "gcid": ""}
    return {
        "email": _claim(payload, _EMAIL_CLAIM_HINTS),
        "org": _claim(payload, _ORG_CLAIM_HINTS),
        "gcid": _claim(payload, _GCID_CLAIM_HINTS),
    }


def _oauth_credentials(project_root: Path, token: str) -> dict[str, str]:
    """Resolve OAuth password-grant credentials.

    Explicit env vars win; org/gcid fall back to the pasted token's own claims so
    the user only ever has to supply username + password.
    """
    meta = token_metadata(token)
    return {
        "username": os.getenv("OAUTH_USERNAME", "").strip(),
        "password": os.getenv("OAUTH_PASSWORD", "").strip(),
        "org": os.getenv("OAUTH_ORG", "").strip() or meta["org"],
        "gcid": (
            os.getenv("OAUTH_GCID", "").strip()
            or os.getenv("GRAPHQL_CONTEXT_CUSTOMER_ID", "").strip()
            or meta["gcid"]
        ),
    }


def _oauth_common() -> dict[str, str]:
    return {
        "token_url": os.getenv("OAUTH_TOKEN_URL", DEFAULT_OAUTH["token_url"]),
        "client_id": os.getenv("OAUTH_CLIENT_ID", DEFAULT_OAUTH["client_id"]),
        "client_secret": os.getenv("OAUTH_CLIENT_SECRET", DEFAULT_OAUTH["client_secret"]),
        "audience": os.getenv("OAUTH_AUDIENCE", DEFAULT_OAUTH["audience"]),
    }


def _persist_bearer(project_root: Path, token: str, *, var: str = "BEARER_TOKEN") -> None:
    env_path = project_root / ".env"
    text = env_path.read_text(encoding="utf-8") if env_path.is_file() else ""
    text = _set_env_var(text, var, token)
    env_path.write_text(text, encoding="utf-8")
    os.environ[var] = token


def _generate(project_root: Path, token: str) -> str:
    creds = _oauth_credentials(project_root, token)
    missing = [k for k in ("username", "password", "org", "gcid") if not creds.get(k)]
    if missing:
        raise RuntimeError(
            "Cannot regenerate Bearer token — missing OAuth "
            f"{', '.join(missing)}. Set OAUTH_USERNAME / OAUTH_PASSWORD in .env "
            "(org & gcid are auto-read from the pasted token when possible)."
        )
    return fetch_oauth_token(
        username=creds["username"],
        password=creds["password"],
        org=creds["org"],
        gcid=creds["gcid"],
        **_oauth_common(),
    )


def bearer_status(project_root: Path | None = None, *, min_ttl_hours: float = 0.25) -> TokenStatus:
    """Report current BEARER_TOKEN health without side effects."""
    root = project_root or find_project_root()
    load_dotenv(root / ".env")
    raw = _strip_bearer(os.getenv("BEARER_TOKEN", ""))
    creds = _oauth_credentials(root, raw)
    can_regen = bool(creds.get("username") and creds.get("password") and creds.get("org") and creds.get("gcid"))

    st = TokenStatus(present=bool(raw), can_regenerate=can_regen)
    if not raw:
        st.message = "No BEARER_TOKEN set. Paste one into .env (it will be auto-refreshed thereafter)."
        return st

    meta = token_metadata(raw)
    st.email, st.org, st.gcid = meta["email"], meta["org"], meta["gcid"]
    st.expired = jwt_is_expired(raw, skew_sec=int(min_ttl_hours * 3600))
    st.expires_in_hours = jwt_expires_in_hours(raw)
    if st.expired:
        st.message = (
            "Bearer token expired — will regenerate on next run."
            if can_regen
            else "Bearer token expired and no OAuth credentials to regenerate. Paste a fresh token."
        )
    else:
        st.message = "Bearer token valid."
    return st


def ensure_fresh_bearer(
    project_root: Path | None = None,
    *,
    log: LogFn | None = None,
    min_ttl_hours: float = 0.25,
) -> TokenStatus:
    """Reuse the current token if valid; otherwise regenerate and persist it.

    Returns a :class:`TokenStatus` describing what happened so callers can surface
    it in job logs / the UI.
    """
    root = project_root or find_project_root()
    emit = log or (lambda _m: None)
    st = bearer_status(root, min_ttl_hours=min_ttl_hours)

    if st.present and not st.expired:
        ttl = f"{st.expires_in_hours:.1f}h" if st.expires_in_hours is not None else "unknown"
        emit(f"▸ Bearer token valid (subject={st.email or 'n/a'}, expires in {ttl}) — reusing")
        return st

    if not st.can_regenerate:
        emit(f"⚠ {st.message}")
        return st

    emit("▸ Bearer token missing/expired — generating a fresh one via OAuth password grant…")
    try:
        raw_before = _strip_bearer(os.getenv("BEARER_TOKEN", ""))
        fresh = _generate(root, raw_before)
    except Exception as exc:  # noqa: BLE001 — surfaced to the operator
        st.message = str(exc)
        emit(f"⚠ Token refresh failed: {exc}")
        return st

    # Compare the freshly generated token to the previously pasted one.
    if raw_before:
        before_sub = jwt_payload(raw_before).get("sub")
        after_sub = jwt_payload(fresh).get("sub")
        st.matches_provided = bool(before_sub) and before_sub == after_sub
        emit(
            "▸ Generated token subject "
            + ("matches" if st.matches_provided else "differs from")
            + " the previously stored token"
        )

    _persist_bearer(root, fresh)
    meta = token_metadata(fresh)
    st.present = True
    st.expired = False
    st.regenerated = True
    st.email, st.org, st.gcid = meta["email"], meta["org"], meta["gcid"]
    st.expires_in_hours = jwt_expires_in_hours(fresh)
    ttl = f"{st.expires_in_hours:.1f}h" if st.expires_in_hours is not None else "unknown"
    st.message = f"Bearer token regenerated (expires in {ttl})."
    emit(f"▸ {st.message}")
    return st


def apply_credentials(
    project_root: Path | None = None,
    *,
    username: str,
    password: str,
    org: str = "",
    gcid: str = "",
    persist: bool = True,
) -> TokenStatus:
    """Generate a Bearer from user credentials and optionally persist to .env.

    Used by the Generate UI "edit credentials" control so a teammate can paste
    their own username/password (and optional org/gcid) without hand-editing
    ``BEARER_TOKEN``.
    """
    root = project_root or find_project_root()
    load_dotenv(root / ".env")
    user = (username or "").strip()
    pwd = (password or "").strip()
    if not user or not pwd:
        st = TokenStatus(present=False, can_regenerate=False)
        st.message = "Username and password are required."
        return st

    # org / gcid are OPTIONAL — when omitted we let Auth0 resolve the user's default
    # organisation and read the real t_organization / gcid from the returned JWT.
    org_val = (org or "").strip()
    gcid_val = (gcid or "").strip()

    try:
        fresh = fetch_oauth_token(
            username=user,
            password=pwd,
            org=org_val,
            gcid=gcid_val,
            **_oauth_common(),
        )
    except Exception as exc:  # noqa: BLE001
        st = TokenStatus(present=False, can_regenerate=True)
        st.message = f"OAuth token request failed: {exc}"
        return st

    # Read the identity the token actually resolved to (source of truth).
    fresh_claims = token_metadata(fresh)
    org_val = org_val or fresh_claims.get("org", "")
    gcid_val = gcid_val or fresh_claims.get("gcid", "")

    if persist:
        env_path = root / ".env"
        text = env_path.read_text(encoding="utf-8") if env_path.is_file() else ""
        text = _set_env_var(text, "OAUTH_USERNAME", user)
        text = _set_env_var(text, "OAUTH_PASSWORD", pwd)
        text = _set_env_var(text, "OAUTH_ORG", org_val)
        text = _set_env_var(text, "OAUTH_GCID", gcid_val)
        text = _set_env_var(text, "BEARER_TOKEN", fresh)
        if "NEXTGEN_BEARER_TOKEN=" in text or os.getenv("NEXTGEN_BEARER_TOKEN"):
            text = _set_env_var(text, "NEXTGEN_BEARER_TOKEN", fresh)
        env_path.write_text(text, encoding="utf-8")
        os.environ["OAUTH_USERNAME"] = user
        os.environ["OAUTH_PASSWORD"] = pwd
        os.environ["OAUTH_ORG"] = org_val
        os.environ["OAUTH_GCID"] = gcid_val
        os.environ["BEARER_TOKEN"] = fresh
        os.environ["NEXTGEN_BEARER_TOKEN"] = fresh

    fresh_meta = token_metadata(fresh)
    return TokenStatus(
        present=True,
        expired=False,
        expires_in_hours=jwt_expires_in_hours(fresh),
        email=fresh_meta.get("email", ""),
        org=fresh_meta.get("org", "") or org_val,
        gcid=fresh_meta.get("gcid", "") or gcid_val,
        regenerated=True,
        can_regenerate=True,
        message="Bearer token generated from credentials.",
    )


def current_oauth_form_defaults(project_root: Path | None = None) -> dict[str, str]:
    """Non-secret defaults for the credentials editor (password never returned)."""
    root = project_root or find_project_root()
    load_dotenv(root / ".env")
    raw = _strip_bearer(os.getenv("BEARER_TOKEN", ""))
    meta = token_metadata(raw) if raw else {"email": "", "org": "", "gcid": ""}
    return {
        "username": os.getenv("OAUTH_USERNAME", "").strip() or meta.get("email", ""),
        "org": os.getenv("OAUTH_ORG", "").strip() or meta.get("org", ""),
        "gcid": (
            os.getenv("OAUTH_GCID", "").strip()
            or os.getenv("GRAPHQL_CONTEXT_CUSTOMER_ID", "").strip()
            or meta.get("gcid", "")
        ),
        "email": meta.get("email", ""),
        "has_password": "1" if os.getenv("OAUTH_PASSWORD", "").strip() else "0",
    }


def compare_provided_vs_generated(project_root: Path | None = None) -> dict[str, object]:
    """Explicitly regenerate a token and compare it to the pasted BEARER_TOKEN.

    Used by ``/api/token/verify`` to answer: "is the token in .env the same
    identity the stored credentials produce?" Does not persist anything.
    """
    root = project_root or find_project_root()
    load_dotenv(root / ".env")
    provided = _strip_bearer(os.getenv("BEARER_TOKEN", ""))
    result: dict[str, object] = {
        "provided_present": bool(provided),
        "provided_expired": jwt_is_expired(provided) if provided else True,
        "provided_meta": token_metadata(provided) if provided else {},
    }
    try:
        fresh = _generate(root, provided)
    except Exception as exc:  # noqa: BLE001
        result["error"] = str(exc)
        return result

    result["generated_meta"] = token_metadata(fresh)
    result["generated_expires_in_hours"] = jwt_expires_in_hours(fresh)
    if provided:
        result["same_subject"] = jwt_payload(provided).get("sub") == jwt_payload(fresh).get("sub")
    return result

