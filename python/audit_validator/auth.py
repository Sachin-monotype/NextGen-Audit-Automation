"""OAuth token refresh (same flow as MTConnectAutomation TokenProvider)."""

from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

DEFAULT_OAUTH = {
    "token_url": "https://secure-pp.monotype.com/oauth/token",
    "client_id": "0bnAznyuRQfeaCg9qXxWKeoSZtqorUpD",
    "client_secret": "W-UEo0Zaa0bsNcTbYFtg-31U-8kzp9gyHiZQ2VeJU_9phYITuztKnxWJ0poxhUlc",
    "audience": "https://nextgen.monotype.com",
}


def resolve_oauth_config() -> dict[str, str]:
    """Active OAuth client settings — follows AUDIT_TARGET profile when strict (default)."""
    from audit_validator.env_profiles import get_audit_profile

    strict = os.getenv("AUDIT_TARGET_STRICT", "true").strip().lower() not in {"0", "false", "no"}
    if strict:
        o = get_audit_profile().oauth
        return {
            "token_url": o.token_url,
            "client_id": o.client_id,
            "client_secret": o.client_secret,
            "audience": o.audience,
            "grant_type": o.grant_type.strip().lower(),
            "organization": (o.organization or "").strip(),
        }

    return {
        "token_url": (
            os.getenv("OAUTH_TOKEN_URL")
            or os.getenv("AUTH0_TOKEN_URL")
            or DEFAULT_OAUTH["token_url"]
        ),
        "client_id": (
            os.getenv("OAUTH_CLIENT_ID")
            or os.getenv("AUTH0_CLIENT_ID")
            or DEFAULT_OAUTH["client_id"]
        ),
        "client_secret": (
            os.getenv("OAUTH_CLIENT_SECRET")
            or os.getenv("AUTH0_CLIENT_SECRET")
            or DEFAULT_OAUTH["client_secret"]
        ),
        "audience": (
            os.getenv("OAUTH_AUDIENCE")
            or os.getenv("AUTH0_AUDIENCE")
            or DEFAULT_OAUTH["audience"]
        ),
        "grant_type": (
            os.getenv("OAUTH_GRANT_TYPE")
            or os.getenv("AUTH0_GRANT_TYPE")
            or "password"
        ).strip().lower(),
        "organization": (
            os.getenv("OAUTH_ORG")
            or os.getenv("AUTH0_ORGANIZATION")
            or ""
        ).strip(),
    }


def oauth_grant_type() -> str:
    return resolve_oauth_config()["grant_type"]


def oauth_token_kwargs(cfg: dict[str, Any] | None = None) -> dict[str, str]:
    """OAuth client fields safe for ``fetch_oauth_token*`` (excludes grant_type)."""
    c = cfg or resolve_oauth_config()
    out: dict[str, str] = {}
    for key in ("token_url", "client_id", "client_secret", "audience"):
        val = c.get(key)
        if val:
            out[key] = str(val)
    return out


def oauth_organization(cfg: dict[str, Any] | None = None) -> str:
    """Auth0 organization id for client_credentials grant (not used in password grant)."""
    c = cfg or resolve_oauth_config()
    return str(c.get("organization") or "").strip()


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


def _identity_from_payload(p: dict[str, Any]) -> dict[str, Any]:
    gcid = str(p.get("https://api.monotype.com/gcid") or "").strip()
    org = str(
        p.get("https://api.monotype.com/org_id")
        or p.get("org_id")
        or p.get("https://api.monotype.com/t_organization")
        or ""
    ).strip()
    email = str(
        p.get("https://api.monotype.com/email")
        or p.get("email")
        or ""
    ).strip()
    idp = str(p.get("sub") or "").strip()
    info = p.get("https://secure.monotype.com/info") or {}
    parent = ""
    if isinstance(info, dict):
        parent = str(info.get("parentCustomerId") or "").strip()
    inventories = p.get("https://api.monotype.com/inventories")
    if inventories is None and isinstance(info, dict):
        inventories = info.get("inventories")
    return {
        "gcid": gcid or parent,
        "org_id": org,
        "email": email,
        "idp_user_id": idp,
        "parent_customer_id": parent or gcid,
        "inventories": inventories if inventories is not None else [],
        "org_name": str(p.get("https://api.monotype.com/org_name") or "").strip(),
        "paying_customer": str(p.get("https://api.monotype.com/paying_customer") or "").strip(),
    }


def _identity_is_user(ident: dict[str, Any]) -> bool:
    """True when claims look like a user JWT (not M2M client_credentials)."""
    if ident.get("gcid") or ident.get("email"):
        return True
    idp = str(ident.get("idp_user_id") or "")
    return bool(idp) and not idp.endswith("@clients")


def jwt_identity(token: str | None = None) -> dict[str, Any]:
    """Extract the identity claims we use for CMS/UMS/AMS and actor assertions.

    Mirrors the ActivateFamily QA sheet:
    - ``gcid`` ← ``https://api.monotype.com/gcid``
    - ``org_id`` ← ``org_id`` / namespaced claim
    - ``email`` ← ``https://api.monotype.com/email`` (UMS lookups use this)
    - ``parent_customer_id`` ← ``https://secure.monotype.com/info.parentCustomerId``
    - ``inventories`` ← ``https://api.monotype.com/inventories``
    - ``xCorrelationId`` is **not** here — minted per request from this user.

    Prefer a user JWT over M2M ``client_credentials`` tokens (common on QA),
    which lack gcid/email and make JWT Compare rows look empty.
    """
    if token:
        return _identity_from_payload(jwt_payload(token))

    candidates: list[str] = []
    for key in (
        "NEXTGEN_BEARER_TOKEN",
        "BEARER_TOKEN_PP",
        "DISCOVERY_BEARER_TOKEN",
        "BEARER_TOKEN",
    ):
        tok = _strip_bearer(os.getenv(key, ""))
        if tok and tok not in candidates:
            candidates.append(tok)
    # Fresh OAuth last — on QA this is often M2M without user claims.
    for resolver in (resolve_nextgen_bearer_token, resolve_bearer_token):
        try:
            tok = resolver()
        except Exception:
            tok = ""
        if tok and tok not in candidates:
            candidates.append(tok)

    best: dict[str, Any] = {}
    for tok in candidates:
        ident = _identity_from_payload(jwt_payload(tok))
        if _identity_is_user(ident):
            return ident
        if not best:
            best = ident
    return best


def jwt_identity_from_actor(actor: dict[str, Any] | None) -> dict[str, Any]:
    """Build JWT-shaped identity from an enriched ``actor`` (event already JWT-stamped).

    Used on QA when only an M2M ``client_credentials`` token is available — that
    token has no gcid/email/org claims, so Compare would otherwise show Source=none.
    """
    if not isinstance(actor, dict):
        return {}
    snap = actor.get("enrichedSnapshot") if isinstance(actor.get("enrichedSnapshot"), dict) else {}
    user = snap.get("user") if isinstance(snap.get("user"), dict) else {}
    customer = snap.get("customer") if isinstance(snap.get("customer"), dict) else {}
    gcid = str(actor.get("globalCustomerId") or "").strip()
    parent = str(actor.get("parentCustomerId") or "").strip()
    inventories = actor.get("inventories")
    if inventories is None and isinstance(actor.get("info"), dict):
        inventories = actor["info"].get("inventories")
    org_name = str(
        customer.get("displayName")
        or customer.get("name")
        or actor.get("orgName")
        or ""
    ).strip()
    return {
        "gcid": gcid,
        "org_id": str(actor.get("orgId") or "").strip(),
        "email": str(user.get("email") or actor.get("email") or "").strip(),
        "idp_user_id": str(user.get("idpUserId") or actor.get("idpUserId") or "").strip(),
        "parent_customer_id": parent or gcid,
        "inventories": inventories if inventories is not None else [],
        "org_name": org_name,
        "paying_customer": "",
        "_source": "enriched-actor",
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


def resolve_discovery_base_url() -> str:
    """Base URL for Typesense/Discovery HTTP calls.

    Browser / Excel user JWTs are accepted by the NextGen BFF search proxy
    (``{NEXTGEN_UI_URL}/api/search/...``) but rejected with 401 by the bare
    ``mtc-middleware-discovery`` host (that host expects resolver M2M).

    Override with ``DISCOVERY_BASE_URL``. Set ``DISCOVERY_USE_MIDDLEWARE=true`` to
    force the bare middleware host (only with a Discovery M2M token).
    """
    explicit = (os.getenv("DISCOVERY_BASE_URL") or "").strip().rstrip("/")
    force_mw = (os.getenv("DISCOVERY_USE_MIDDLEWARE") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    ui = (os.getenv("NEXTGEN_UI_URL") or "").strip().rstrip("/")
    if not ui:
        try:
            from audit_validator.env_profiles import get_audit_profile

            ui = get_audit_profile().nextgen_ui_url.rstrip("/")
        except Exception:
            ui = "https://nextgen-qa.monotype-pp.com"
    bff = f"{ui}/api/search"
    if not explicit:
        return bff
    if "mtc-middleware-discovery" in explicit and not force_mw:
        return bff
    return explicit


def resolve_excel_discovery_token(
    project_root: Path | None = None,
    *,
    ops: list[str] | None = None,
) -> str:
    """Pick a non-expired Excel ``auth_token`` (browser SSO) for Discovery/Typesense.

    Looks at saved UI-script trigger contexts first, then ``datasource-latest.xlsx``.
    """
    del ops  # reserved for future per-op scoping
    root = project_root
    if root is None:
        try:
            from .project_root import find_project_root

            root = find_project_root()
        except Exception:
            return ""

    candidates: list[str] = []

    def _consider(raw: object) -> None:
        tok = _strip_bearer(str(raw or ""))
        if not tok or jwt_is_expired(tok):
            return
        ident = _identity_from_payload(jwt_payload(tok))
        if _identity_is_user(ident) and tok not in candidates:
            candidates.append(tok)

    trig_dir = Path(root) / "payload" / "trigger"
    if trig_dir.is_dir():
        for path in trig_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(data, dict):
                _consider(data.get("auth_token"))

    try:
        from audit_validator.ui_script_import import (
            load_ui_script_rows,
            resolve_ui_script_datasource_path,
        )

        ds = resolve_ui_script_datasource_path()
        for target in ("web", "app"):
            for row in load_ui_script_rows(target=target, path=ds):
                _consider(row.get("auth_token"))
    except Exception:
        pass

    return candidates[0] if candidates else ""


def resolve_discovery_bearer_token() -> str:
    """
    Bearer for Discovery middleware (POST /v1/styles, GET /v1/variations).

    Discovery rejects Everest M2M ``client_credentials`` tokens (401 Unauthorized).
    Prefer a **non-expired user** JWT: ``DISCOVERY_BEARER_TOKEN``, then
    ``BEARER_TOKEN_PP`` / ``NEXTGEN_BEARER_TOKEN``. Never return an ``@clients``
    M2M token or an expired JWT (both produce silent empty Typesense results).
    """
    for key in (
        "DISCOVERY_BEARER_TOKEN",
        "BEARER_TOKEN_PP",
        "NEXTGEN_BEARER_TOKEN",
        "BEARER_TOKEN",
    ):
        token = _strip_bearer(os.getenv(key, ""))
        if not token or jwt_is_expired(token):
            continue
        ident = _identity_from_payload(jwt_payload(token))
        if _identity_is_user(ident):
            return token
    return ""


def mint_user_password_token(
    *,
    username: str = "",
    password: str = "",
    org: str = "",
    gcid: str = "",
) -> str:
    """Mint a user JWT via the profile's password-grant client (``user_oauth``).

    QA's main OAuth client is M2M-only; user/Discovery tokens must use the NextGen
    SPA client (same as MTConnectAutomation TokenProvider).
    """
    from audit_validator.env_profiles import get_audit_profile, user_oauth_config_dict

    profile = get_audit_profile()
    uo = user_oauth_config_dict(profile)
    user = (username or os.getenv("OAUTH_USERNAME") or profile.oauth_username or "").strip()
    pwd = (password or os.getenv("OAUTH_PASSWORD") or "").strip()
    if not user or not pwd:
        raise RuntimeError(
            "OAUTH_USERNAME / OAUTH_PASSWORD required to mint a user JWT "
            "(set once in .env — no Excel paste needed)."
        )
    org_val = (
        (org or "").strip()
        or (os.getenv("OAUTH_ORG") or "").strip()
        or (os.getenv("OAUTH_ORGANIZATION") or "").strip()
        or (uo.get("organization") or "").strip()
    )
    gcid_val = (
        (gcid or "").strip()
        or (os.getenv("OAUTH_GCID") or "").strip()
        or (os.getenv("GRAPHQL_CONTEXT_CUSTOMER_ID") or "").strip()
    )
    return fetch_oauth_token(
        username=user,
        password=pwd,
        org=org_val,
        gcid=gcid_val,
        token_url=uo["token_url"],
        client_id=uo["client_id"],
        client_secret=uo["client_secret"],
        audience=uo["audience"],
    )


def ensure_discovery_user_token(*, project_root: Path | None = None) -> str:
    """Return a non-expired user JWT usable by Discovery/Typesense.

    Prefer a cached ``DISCOVERY_BEARER_TOKEN`` / browser JWT when still valid;
    otherwise mint via ``OAUTH_USERNAME`` / ``OAUTH_PASSWORD`` on the profile
    ``user_oauth`` client (QA SPA password grant).
    """
    existing = resolve_discovery_bearer_token()
    if existing:
        return existing

    username = (os.getenv("OAUTH_USERNAME") or "").strip()
    password = (os.getenv("OAUTH_PASSWORD") or "").strip()
    if not username or not password:
        return ""

    try:
        fresh = mint_user_password_token(username=username, password=password)
    except Exception:
        return ""

    ident = _identity_from_payload(jwt_payload(fresh))
    if not _identity_is_user(ident) or jwt_is_expired(fresh):
        return ""

    os.environ["DISCOVERY_BEARER_TOKEN"] = fresh
    os.environ["NEXTGEN_BEARER_TOKEN"] = fresh
    root = project_root
    if root is None:
        try:
            from .project_root import find_project_root

            root = find_project_root()
        except Exception:
            root = None
    if root is not None:
        try:
            env_path = Path(root) / ".env"
            text = env_path.read_text(encoding="utf-8") if env_path.is_file() else ""
            text = _set_env_var(text, "DISCOVERY_BEARER_TOKEN", fresh)
            text = _set_env_var(text, "NEXTGEN_BEARER_TOKEN", fresh)
            env_path.write_text(text, encoding="utf-8")
        except Exception:
            pass
    return fresh


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



def fetch_oauth_token_client_credentials(
    *,
    token_url: str | None = None,
    client_id: str | None = None,
    client_secret: str | None = None,
    audience: str | None = None,
    organization: str = "",
) -> str:
    """Machine-to-machine token (QA Auth0 client_credentials grant)."""
    cfg = resolve_oauth_config()
    url = token_url or cfg["token_url"]
    fields = [
        ("grant_type", "client_credentials"),
        ("client_id", client_id or cfg["client_id"]),
        ("client_secret", client_secret or cfg["client_secret"]),
        ("audience", audience or cfg["audience"]),
    ]
    org = (organization or "").strip()
    if not org and os.getenv("OAUTH_CLIENT_CREDENTIALS_INCLUDE_ORG", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        org = cfg["organization"]
    if org:
        fields.append(("organization", org))
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
        raise RuntimeError(f"OAuth client_credentials request to {url} did not return a JWT")
    return str(token)


def oauth_token_form_fields(
    *,
    grant_type: str | None = None,
    username: str = "",
    password: str = "",
    org: str = "",
    gcid: str = "",
) -> tuple[str, list[tuple[str, str]]]:
    """Build OAuth token POST body fields for the active AUDIT_TARGET profile."""
    cfg = resolve_oauth_config()
    url = cfg["token_url"]
    gt = (grant_type or cfg["grant_type"] or "password").strip().lower()
    if gt == "client_credentials":
        fields: list[tuple[str, str]] = [
            ("grant_type", "client_credentials"),
            ("client_id", cfg["client_id"]),
            ("client_secret", cfg["client_secret"]),
            ("audience", cfg["audience"]),
        ]
        org_val = (cfg.get("organization") or "").strip()
        if org_val and os.getenv("OAUTH_CLIENT_CREDENTIALS_INCLUDE_ORG", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }:
            fields.append(("organization", org_val))
        return url, fields

    user = (username or os.getenv("OAUTH_USERNAME", "")).strip()
    pwd = (password or os.getenv("OAUTH_PASSWORD", "")).strip()
    merged_org = (org or os.getenv("OAUTH_ORG", "")).strip()
    merged_gcid = (gcid or os.getenv("OAUTH_GCID", "")).strip()
    fields = [
        ("grant_type", "password"),
        ("client_id", cfg["client_id"]),
        ("client_secret", cfg["client_secret"]),
        ("audience", cfg["audience"]),
        ("username", user),
        ("password", pwd),
        ("scope", "openid profile email offline_access"),
    ]
    if merged_org:
        fields.append(("t_organization", merged_org))
    if merged_gcid:
        fields.append(("gcid", merged_gcid))
    return url, fields


def oauth_token_request_curl(
    *,
    grant_type: str | None = None,
    username: str = "",
    password: str = "",
    org: str = "",
    gcid: str = "",
    redact_secrets: bool = True,
) -> str:
    """Runnable curl for the Auth0 token endpoint (secrets redacted by default)."""
    url, fields = oauth_token_form_fields(
        grant_type=grant_type,
        username=username,
        password=password,
        org=org,
        gcid=gcid,
    )
    encoded_fields: list[tuple[str, str]] = []
    for key, val in fields:
        if redact_secrets and key in {"client_secret", "password"}:
            val = "***"
        encoded_fields.append((key, val))
    body = urllib.parse.urlencode(encoded_fields, encoding="utf-8")
    return (
        f"curl -X POST '{url}' \\\n"
        f"  -H 'Content-Type: application/x-www-form-urlencoded' \\\n"
        f"  -d '{body}'"
    )


def audit_app_token_credentials_curl(
    *,
    base_url: str = "http://localhost:3200",
    username: str = "",
    password: str = "",
    org: str = "",
    gcid: str = "",
) -> str:
    """Runnable curl for POST /api/token/credentials (Generate UI Bearer modal)."""
    import json

    payload = {
        "username": username or os.getenv("OAUTH_USERNAME", ""),
        "password": "***" if (password or os.getenv("OAUTH_PASSWORD", "")) else "",
        "org": org or os.getenv("OAUTH_ORG", ""),
        "gcid": gcid or os.getenv("OAUTH_GCID", ""),
    }
    body = json.dumps(payload)
    return (
        f"curl -X POST '{base_url.rstrip('/')}/api/token/credentials' \\\n"
        f"  -H 'Content-Type: application/json' \\\n"
        f"  -d '{body}'"
    )


def fetch_oauth_token(
    *,
    username: str,
    password: str,
    org: str = "",
    organization: str = "",
    gcid: str = "",
    token_url: str | None = None,
    client_id: str | None = None,
    client_secret: str | None = None,
    audience: str | None = None,
) -> str:
    cfg = resolve_oauth_config()
    url = token_url or cfg["token_url"]
    merged_org = (org or organization or "").strip()
    fields = [
        ("grant_type", "password"),
        ("client_id", client_id or cfg["client_id"]),
        ("client_secret", client_secret or cfg["client_secret"]),
        ("audience", audience or cfg["audience"]),
        ("username", username),
        ("password", password),
        ("scope", "openid profile email offline_access"),
    ]
    # org / gcid are optional — when the caller supplies only username + password,
    # Auth0 resolves the user's default org and the resulting JWT carries the real
    # t_organization / gcid claims (which we then read back).
    if merged_org:
        fields.append(("t_organization", merged_org))
    if gcid:
        fields.append(("gcid", gcid))
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

    common = resolve_oauth_config()
    oauth_kw = oauth_token_kwargs(common)
    org_cfg = oauth_organization(common)
    if common["grant_type"] == "client_credentials":
        m2m_org = org_cfg if os.getenv(
            "OAUTH_CLIENT_CREDENTIALS_INCLUDE_ORG", ""
        ).strip().lower() in {"1", "true", "yes", "on"} else ""
        primary = fetch_oauth_token_client_credentials(**oauth_kw, organization=m2m_org)
        secondary = ""
    else:
        if not all([username, password, org, gcid]):
            raise RuntimeError(
                "Set OAUTH_USERNAME, OAUTH_PASSWORD, OAUTH_ORG, and OAUTH_GCID in .env"
            )
        primary = fetch_oauth_token(
            username=username, password=password, org=org or org_cfg, gcid=gcid, **oauth_kw
        )
        time.sleep(1)
        secondary_user = os.getenv("OAUTH_SECONDARY_USERNAME", "").strip()
        secondary = ""
        if secondary_user:
            secondary = fetch_oauth_token(
                username=secondary_user,
                password=os.getenv("OAUTH_SECONDARY_PASSWORD", password),
                org=org,
                gcid=gcid,
                **oauth_kw,
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
