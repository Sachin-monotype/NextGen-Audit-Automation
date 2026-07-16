"""OAuth token refresh (same flow as MTConnectAutomation TokenProvider)."""

from __future__ import annotations

import os
import re
import time
import urllib.parse
from pathlib import Path

import requests
from dotenv import load_dotenv

DEFAULT_OAUTH = {
    "token_url": "https://secure-pp.monotype.com/oauth/token",
    "client_id": "0bnAznyuRQfeaCg9qXxWKeoSZtqorUpD",
    "client_secret": "W-UEo0Zaa0bsNcTbYFtg-31U-8kzp9gyHiZQ2VeJU_9phYITuztKnxWJ0poxhUlc",
    "audience": "https://nextgen.monotype.com",
}


def _strip_bearer(value: str) -> str:
    token = value.strip()
    if token.lower().startswith("bearer "):
        return token[7:].strip()
    return token


def jwt_payload(token: str) -> dict:
    """Decode JWT payload without verification (expiry / email checks only)."""
    import base64
    import json

    raw = _strip_bearer(token)
    parts = raw.split(".")
    if len(parts) != 3:
        return {}
    pad = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(pad))
    except Exception:
        return {}


def jwt_identity(token: str | None = None) -> dict[str, str]:
    """Extract the identity claims we use for CMS/UMS/AMS and actor assertions.

    Notes:
    - ``xCorrelationId`` is **not** here — it is per-request, not per-user.
    - ``gcid`` / ``org_id`` / ``email`` / ``idp_user_id`` (sub) stay stable for the Bearer.
    - Profile UUID is usually NOT in the JWT; callers look it up via UMS using
      ``idp_user_id`` when needed.
    """
    tok = token or resolve_nextgen_bearer_token() or resolve_bearer_token()
    if not tok:
        return {}
    p = jwt_payload(tok)
    gcid = str(p.get("https://api.monotype.com/gcid") or "").strip()
    org = str(p.get("https://api.monotype.com/org_id") or "").strip()
    email = str(p.get("https://api.monotype.com/email") or "").strip()
    idp = str(p.get("sub") or "").strip()
    info = p.get("https://secure.monotype.com/info") or {}
    parent = ""
    if isinstance(info, dict):
        parent = str(info.get("parentCustomerId") or "").strip()
    return {
        "gcid": gcid or parent,
        "org_id": org,
        "email": email,
        "idp_user_id": idp,
        "parent_customer_id": parent or gcid,
    }


def resolve_our_profile_id(*, project_root: Path | None = None) -> str | None:
    """Profile UUID for the current Bearer (actor.globalUserId on our events).

    JWT carries Auth0 ``sub`` (idpUserId), not the UMS profile UUID. We resolve
    it once via UMS ``GET /users?idpUserId=…`` (or env fallbacks).
    """
    import os

    for key in ("NOTIFICATION_CLEANUP_USER_ID", "INGRESS_DEFAULT_USER_ID", "OAUTH_PROFILE_ID"):
        val = (os.getenv(key) or "").strip()
        if val:
            return val
    ident = jwt_identity()
    idp = ident.get("idp_user_id") or ""
    if not idp:
        return None
    try:
        from .source_validation.clients import UmsClient
        from .source_validation.config import load_source_validation_config

        cfg = load_source_validation_config(project_root)
        if not cfg.ums_ready:
            return None
        user = UmsClient(cfg).get_user_by_idp_user_id(idp, correlation_id="resolve-our-profile")
        if not isinstance(user, dict):
            return None
        gcid = ident.get("gcid") or ""
        profiles = user.get("profiles") or []
        if isinstance(profiles, list):
            for pr in profiles:
                if not isinstance(pr, dict):
                    continue
                pid = pr.get("id") or (pr.get("profile") or {}).get("id")
                if not pid:
                    continue
                if gcid and str(pr.get("customerId") or "") == gcid:
                    return str(pid)
            for pr in profiles:
                if isinstance(pr, dict):
                    pid = pr.get("id") or (pr.get("profile") or {}).get("id")
                    if pid:
                        return str(pid)
    except Exception:
        return None
    return None


def jwt_is_expired(token: str, *, skew_sec: int = 120) -> bool:
    """True when JWT exp is in the past (or token is not a JWT)."""
    if not token:
        return True
    exp = jwt_payload(token).get("exp")
    if not exp:
        return False
    return float(exp) <= time.time() + skew_sec


def jwt_expires_in_hours(token: str) -> float | None:
    exp = jwt_payload(token).get("exp")
    if not exp:
        return None
    return (float(exp) - time.time()) / 3600.0


def resolve_bearer_token(*, prefer_pp: bool | None = None) -> str:
    """
    Default: BEARER_TOKEN (Everest OAuth).
    PP: set SIMULATION_PREFER_PP_BEARER=true and BEARER_TOKEN_PP (Monotype SSO).
    """
    if prefer_pp is None:
        prefer_pp = os.getenv("SIMULATION_PREFER_PP_BEARER", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
    pp = _strip_bearer(os.getenv("BEARER_TOKEN_PP", ""))
    primary = _strip_bearer(os.getenv("BEARER_TOKEN", ""))
    if prefer_pp and pp:
        return pp
    return primary


def resolve_discovery_bearer_token() -> str:
    """
    Bearer for Discovery middleware (POST /v1/styles, GET /v1/variations).

  PP/browser SSO tokens work; Everest OAuth ``BEARER_TOKEN`` often returns 401 here.
    Prefer ``DISCOVERY_BEARER_TOKEN``, then ``BEARER_TOKEN_PP`` / ``NEXTGEN_BEARER_TOKEN``.
    """
    explicit = _strip_bearer(os.getenv("DISCOVERY_BEARER_TOKEN", ""))
    if explicit and not jwt_is_expired(explicit):
        return explicit
    for key in ("BEARER_TOKEN_PP", "NEXTGEN_BEARER_TOKEN"):
        token = _strip_bearer(os.getenv(key, ""))
        if token and not jwt_is_expired(token):
            return token
    oauth = _strip_bearer(os.getenv("BEARER_TOKEN", ""))
    if oauth and not jwt_is_expired(oauth):
        return oauth
    for key in ("BEARER_TOKEN_PP", "NEXTGEN_BEARER_TOKEN", "BEARER_TOKEN"):
        token = _strip_bearer(os.getenv(key, ""))
        if token:
            return token
    return ""


def resolve_graphql_bearer_token() -> str:
    """
    Bearer for Everest ``/graphql`` (bootstrap queries, cleanup).

    OAuth ``BEARER_TOKEN`` is often rejected before JWT ``exp``; browser SSO works.
    Prefer ``NEXTGEN_BEARER_TOKEN`` / ``BEARER_TOKEN_PP``, then OAuth.
    """
    for key in ("NEXTGEN_BEARER_TOKEN", "BEARER_TOKEN_PP"):
        token = _strip_bearer(os.getenv(key, ""))
        if token and not jwt_is_expired(token):
            return token
    oauth = _strip_bearer(os.getenv("BEARER_TOKEN", ""))
    if oauth and not jwt_is_expired(oauth):
        return oauth
    for key in ("NEXTGEN_BEARER_TOKEN", "BEARER_TOKEN_PP", "BEARER_TOKEN"):
        token = _strip_bearer(os.getenv(key, ""))
        if token:
            return token
    return ""


def resolve_nextgen_bearer_token(*, allow_oauth_fallback: bool = True) -> str:
    """
    Bearer for NextGen /graph mutations (font activation audit events).

    Prefer browser SSO in NEXTGEN_BEARER_TOKEN. When it is missing or expired,
    fall back to fresh OAuth BEARER_TOKEN (mutations succeed; some font enrichments
    may still dead-letter without browser SSO).
    """
    candidates: list[tuple[str, str]] = []
    for key in ("NEXTGEN_BEARER_TOKEN", "BEARER_TOKEN_PP", "BEARER_TOKEN"):
        token = _strip_bearer(os.getenv(key, ""))
        if token:
            candidates.append((key, token))

    for key, token in candidates:
        if not jwt_is_expired(token):
            return token

    if allow_oauth_fallback:
        oauth = _strip_bearer(os.getenv("BEARER_TOKEN", ""))
        if oauth and not jwt_is_expired(oauth):
            return oauth

    # Last resort: first configured token (caller may surface 401)
    return candidates[0][1] if candidates else ""


def nextgen_bearer_diagnostics() -> dict[str, str | float | bool]:
    """Human-readable auth state for /graph mutations."""
    keys = ("NEXTGEN_BEARER_TOKEN", "BEARER_TOKEN_PP", "BEARER_TOKEN")
    out: dict[str, str | float | bool] = {}
    for key in keys:
        tok = _strip_bearer(os.getenv(key, ""))
        if not tok:
            continue
        out[f"{key}_present"] = True
        out[f"{key}_expired"] = jwt_is_expired(tok)
        hrs = jwt_expires_in_hours(tok)
        if hrs is not None:
            out[f"{key}_expires_in_h"] = round(hrs, 1)
    resolved = resolve_nextgen_bearer_token()
    out["resolved_source"] = next(
        (k for k in keys if _strip_bearer(os.getenv(k, "")) == resolved),
        "BEARER_TOKEN" if resolved == _strip_bearer(os.getenv("BEARER_TOKEN", "")) else "unknown",
    )
    out["resolved_expired"] = jwt_is_expired(resolved) if resolved else True
    return out


def assert_nextgen_bearer_usable(*, min_ttl_hours: float = 0.05) -> str:
    """Return resolved /graph token or raise with fix instructions."""
    token = resolve_nextgen_bearer_token()
    if not token:
        raise RuntimeError(
            "No bearer token for NextGen /graph. Set NEXTGEN_BEARER_TOKEN (browser SSO) "
            "or BEARER_TOKEN (run ./run.sh refresh-tokens)."
        )
    if jwt_is_expired(token):
        diag = nextgen_bearer_diagnostics()
        raise RuntimeError(
            "All configured NextGen /graph bearer tokens are expired. "
            f"Diagnostics: {diag}. "
            "Paste a fresh browser Bearer from DevTools into NEXTGEN_BEARER_TOKEN, "
            "or run: cd python && ./run.sh refresh-tokens"
        )
    hrs = jwt_expires_in_hours(token)
    if hrs is not None and hrs < min_ttl_hours:
        raise RuntimeError(
            f"NextGen bearer expires in {hrs:.2f}h — refresh NEXTGEN_BEARER_TOKEN before E2E."
        )
    return token


def customer_context_header_id(
    *,
    use_customer_context: bool,
    customer_context_id: str,
    profile_customer_id: str,
) -> str:
    """Return the ``x-context-customerid`` value, or empty when it must not be sent.

    IMPORTANT — matching the browser: the NextGen web app does NOT send
    ``x-context-customerid`` when a user works inside their own company. Echoing
    the caller's own customer id here makes the resolver treat the request as a
    cross-company "manage companies" admin action, which requires the
    ``MANAGE_COMPANIES`` permission — so favorites / private-tags / activation
    mutations come back ``FORBIDDEN`` even though they work from the UI.

    We therefore only send the header for a *genuine* cross-company (admin) call,
    i.e. when an explicit ``GRAPHQL_CONTEXT_CUSTOMER_ID`` is set to a customer that
    differs from the token's own. Set ``GRAPHQL_SEND_OWN_CONTEXT_HEADER=true`` to
    restore the previous (own-id-echoing) behaviour.
    """
    if not use_customer_context:
        return ""
    explicit = (customer_context_id or "").strip()
    own = (profile_customer_id or "").strip()
    if explicit and explicit != own:
        return explicit
    import os

    if os.getenv("GRAPHQL_SEND_OWN_CONTEXT_HEADER", "").strip().lower() in {"1", "true", "yes", "on"}:
        return explicit or own
    return ""


def fetch_oauth_token(
    *,
    username: str,
    password: str,
    org: str,
    gcid: str,
    token_url: str | None = None,
    client_id: str | None = None,
    client_secret: str | None = None,
    audience: str | None = None,
) -> str:
    url = token_url or DEFAULT_OAUTH["token_url"]
    fields = [
        ("grant_type", "password"),
        ("client_id", client_id or DEFAULT_OAUTH["client_id"]),
        ("client_secret", client_secret or DEFAULT_OAUTH["client_secret"]),
        ("audience", audience or DEFAULT_OAUTH["audience"]),
        ("username", username),
        ("password", password),
        ("scope", "openid profile email offline_access"),
        ("t_organization", org),
        ("gcid", gcid),
    ]
    body = urllib.parse.urlencode(fields, encoding="utf-8")
    resp = requests.post(
        url,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=60,
    )
    resp.raise_for_status()
    token = resp.json().get("access_token")
    if not token or len(str(token).split(".")) != 3:
        raise RuntimeError(f"OAuth token request to {url} did not return a JWT")
    return str(token)


def _set_env_var(text: str, name: str, value: str) -> str:
    line = f"{name}={value}"
    if re.search(rf"^{re.escape(name)}=.*$", text, flags=re.M):
        return re.sub(rf"^{re.escape(name)}=.*$", line, text, flags=re.M)
    return text.rstrip() + "\n" + line + "\n"


def refresh_env_tokens(project_root: Path) -> dict[str, str]:
    """Fetch fresh OAuth bearer tokens and write them to repo-root .env.

    NEXTGEN_BEARER_TOKEN is left unchanged — paste browser SSO Bearer manually.
    """
    env_path = project_root / ".env"
    load_dotenv(env_path)

    username = os.getenv("OAUTH_USERNAME", "").strip()
    password = os.getenv("OAUTH_PASSWORD", "").strip()
    org = os.getenv("OAUTH_ORG", "").strip()
    gcid = os.getenv("OAUTH_GCID", "").strip()
    if not all([username, password, org, gcid]):
        raise RuntimeError(
            "Set OAUTH_USERNAME, OAUTH_PASSWORD, OAUTH_ORG, and OAUTH_GCID in .env"
        )

    common = {
        "token_url": os.getenv("OAUTH_TOKEN_URL", DEFAULT_OAUTH["token_url"]),
        "client_id": os.getenv("OAUTH_CLIENT_ID", DEFAULT_OAUTH["client_id"]),
        "client_secret": os.getenv("OAUTH_CLIENT_SECRET", DEFAULT_OAUTH["client_secret"]),
        "audience": os.getenv("OAUTH_AUDIENCE", DEFAULT_OAUTH["audience"]),
    }

    primary = fetch_oauth_token(username=username, password=password, org=org, gcid=gcid, **common)
    time.sleep(1)

    secondary_user = os.getenv("OAUTH_SECONDARY_USERNAME", "").strip()
    secondary = ""
    if secondary_user:
        secondary = fetch_oauth_token(
            username=secondary_user,
            password=os.getenv("OAUTH_SECONDARY_PASSWORD", password),
            org=org,
            gcid=gcid,
            **common,
        )

    text = env_path.read_text(encoding="utf-8") if env_path.is_file() else ""
    text = _set_env_var(text, "BEARER_TOKEN", primary)
    if secondary:
        text = _set_env_var(text, "BEARER_TOKEN_SECONDARY", secondary)
    env_path.write_text(text, encoding="utf-8")

    nextgen = _strip_bearer(os.getenv("NEXTGEN_BEARER_TOKEN", ""))
    return {
        "BEARER_TOKEN": primary,
        "NEXTGEN_BEARER_TOKEN": nextgen,
        "BEARER_TOKEN_SECONDARY": secondary,
    }
