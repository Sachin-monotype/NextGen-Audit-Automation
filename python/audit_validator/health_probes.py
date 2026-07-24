"""Connectivity / workability probes for the external systems this tool depends on.

Each probe answers one question: *can we reach and use this API right now?* Results
distinguish three states so the UI can tell a real outage from a network block:

- ``ok`` (green): reached the origin and it responded as expected.
- ``blocked`` (red): a Cloudflare/edge/VPN block — the request never reached the
  origin (e.g. Cloudflare ``error code: 1006``) or the TCP connect timed out. This is
  almost always "you are not on the corporate VPN".
- ``error`` (amber): reached the origin but it returned an unexpected error.

The source APIs (CMS / UMS / Discovery) and RabbitMQ live on the corporate network;
when off-VPN they return Cloudflare 1006 or time out — which is what turns source
validation rows into SKIP/N/A. This module makes that diagnosable at a glance.
"""

from __future__ import annotations

import os
import socket
import time
import uuid
from typing import Any
from urllib.parse import urlparse

import requests

from .curl_builder import _graphql_endpoint, _ingress_endpoint, _resolve_bearer
from .source_validation.config import load_source_validation_config

_TIMEOUT = float(os.getenv("HEALTH_PROBE_TIMEOUT_SEC", "8"))
_VPN_HINT = "Not reachable from this network — connect to the corporate VPN and retry."


def _now_ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)


def _is_cloudflare_block(resp: requests.Response) -> bool:
    server = (resp.headers.get("server") or "").lower()
    body = (resp.text or "")[:400].lower()
    if "cloudflare" in server and resp.status_code in (403, 503):
        return True
    return "error code: 10" in body  # cloudflare 1006/1007/1009/1010 access-denied family


def _base(id_: str, label: str, category: str, url: str, method: str, *, why: str = "") -> dict[str, Any]:
    return {
        "id": id_,
        "label": label,
        "category": category,
        "url": url,
        "method": method,
        "why": why,
        "state": "error",
        "ok": False,
        "reachable": False,
        "status_code": None,
        "latency_ms": None,
        "detail": "",
        "hint": "",
        "response_snippet": "",
        "sample": "",
        # Editable request the UI can tweak and re-send (Postman-like).
        "request": {
            "method": method,
            "url": url,
            "headers": {},
            "params": {},
            "body": None,
        },
    }


def _from_response(
    result: dict[str, Any], resp: requests.Response, start: float, *, ok_when
) -> dict[str, Any]:
    result["latency_ms"] = _now_ms(start)
    result["status_code"] = resp.status_code
    if _is_cloudflare_block(resp):
        result.update(
            state="blocked",
            reachable=False,
            ok=False,
            detail=f"Blocked by Cloudflare (HTTP {resp.status_code}, edge access denied).",
            hint=_VPN_HINT,
        )
        return result
    result["reachable"] = True
    snippet = (resp.text or "")[:600]
    result["response_snippet"] = snippet
    if ok_when(resp):
        result.update(state="ok", ok=True, detail=f"Reachable — HTTP {resp.status_code}.")
    else:
        result.update(
            state="error",
            ok=False,
            detail=f"Origin reachable but returned HTTP {resp.status_code}.",
        )
    return result


def _from_exception(result: dict[str, Any], exc: Exception, start: float) -> dict[str, Any]:
    result["latency_ms"] = _now_ms(start)
    is_timeout = isinstance(exc, (requests.Timeout, socket.timeout)) or "timed out" in str(exc).lower()
    result.update(
        state="blocked",
        reachable=False,
        ok=False,
        detail=("Connection timed out." if is_timeout else f"Connection failed: {exc}"),
        hint=_VPN_HINT,
    )
    return result


def _cid() -> str:
    return str(uuid.uuid4())


# --------------------------------------------------------------------------------------
# Sample data — probe the source APIs with REAL identifiers (pulled from a staged
# enriched sample + env seeds) so a healthy API returns 200 with data instead of a
# 400 "bad uuid". Cached for the process so we don't rescan on every probe.
# --------------------------------------------------------------------------------------
_SAMPLE_CACHE: dict[str, Any] | None = None


def _load_sample_context() -> dict[str, Any]:
    global _SAMPLE_CACHE
    if _SAMPLE_CACHE is not None:
        return _SAMPLE_CACHE

    import json
    from pathlib import Path

    from .auth import jwt_identity, resolve_nextgen_bearer_token, resolve_bearer_token
    from .source_validation.clients import UmsClient

    cfg = load_source_validation_config()
    identity = jwt_identity(resolve_nextgen_bearer_token() or resolve_bearer_token())
    ctx: dict[str, Any] = {
        "customer_id": identity.get("gcid") or cfg.gcid or "",
        "user_id": "",
        "role_id": "",
        "team_id": "",
        "family_id": os.getenv("SEED_FAMILY_ID", "").strip(),
        "style_id": os.getenv("SEED_STYLE_ID", "").strip(),
        "asset_id": "",
        "asset_type": "",
        "asset_user_id": "",
        "asset_customer_id": "",
        "idp_user_id": identity.get("idp_user_id") or "",
        "email": identity.get("email") or "",
        "invite_email": "",
        "private_tag_id": "",
        "from_bearer": bool(identity.get("gcid")),
    }

    # Resolve our profile UUID from UMS using the Bearer idpUserId (sub) — this is
    # the same identity that ends up on actor.globalUserId / enriched profiles.
    if ctx["idp_user_id"] and cfg.ums_ready:
        try:
            ums = UmsClient(cfg)
            user = ums.get_user_by_idp_user_id(
                ctx["idp_user_id"], correlation_id="health-bearer-lookup"
            )
            if isinstance(user, dict):
                profiles = user.get("profiles") or []
                if isinstance(profiles, list):
                    for pr in profiles:
                        if not isinstance(pr, dict):
                            continue
                        pid = pr.get("id") or (pr.get("profile") or {}).get("id")
                        if pid:
                            # Prefer profile under our gcid when available.
                            pcid = str(pr.get("customerId") or "")
                            if not ctx["user_id"] or pcid == ctx["customer_id"]:
                                ctx["user_id"] = str(pid)
                                rid = (pr.get("role") or {}).get("id")
                                if rid:
                                    ctx["role_id"] = str(rid)
                                if pcid == ctx["customer_id"]:
                                    break
            # Users API projection does not include role/team — hydrate from profiles.
            if ctx["user_id"] and ctx["customer_id"] and not ctx["role_id"]:
                try:
                    rows = ums.get_profiles_by_ids(
                        [ctx["user_id"]],
                        ctx["customer_id"],
                        correlation_id="health-bearer-profile",
                        user_type="",  # empty → omit userType filter (human profiles)
                    )
                    row = (rows or [None])[0] if rows else None
                    if isinstance(row, dict):
                        rid = (row.get("role") or {}).get("id") or row.get("roleId")
                        if rid:
                            ctx["role_id"] = str(rid)
                        tid = (row.get("team") or {}).get("id") or row.get("teamId")
                        if tid:
                            ctx["team_id"] = str(tid)
                except Exception:
                    pass
        except Exception:
            pass

    # Prefer a createAsset/Folder sample for AMS (reliably owned by the actor) over the
    # first alphabetical file (which may be a shared FontSet owned by someone else).
    enrich_dir = cfg.project_root / "payload" / "enrich"
    if enrich_dir.is_dir():
        files = sorted(enrich_dir.glob("*.json"))
        preferred = [p for p in files if p.stem in ("createAsset", "createProject")]
        for path in preferred + files:
            try:
                doc = json.loads(Path(path).read_text(encoding="utf-8"))
            except Exception:
                continue
            actor = doc.get("actor") or {}
            # Only adopt staged actor ids when Bearer didn't already give us identity —
            # AND when the staged actor matches our gcid (avoid another user's sample).
            actor_gcid = str(actor.get("globalCustomerId") or "")
            same_tenant = (not ctx["customer_id"]) or (actor_gcid == ctx["customer_id"])
            if same_tenant and not ctx["user_id"] and actor.get("globalUserId"):
                ctx["user_id"] = str(actor["globalUserId"])
                if not ctx["customer_id"]:
                    ctx["customer_id"] = actor_gcid
            snap = (doc.get("subject") or {}).get("enrichedSnapshot") or {}
            asset = snap.get("asset") or {}
            if not ctx["asset_id"] and isinstance(asset, dict) and asset.get("id") and same_tenant:
                ctx["asset_id"] = str(asset["id"])
                ctx["asset_type"] = str(asset.get("assetType") or "")
                ctx["asset_user_id"] = str(actor.get("globalUserId") or ctx["user_id"] or "")
                ctx["asset_customer_id"] = str(actor.get("globalCustomerId") or ctx["customer_id"] or "")
            role = snap.get("role") or {}
            if not ctx["role_id"] and isinstance(role, dict) and role.get("id") and same_tenant:
                ctx["role_id"] = str(role["id"])
            invs = snap.get("invitations") or []
            if not ctx["invite_email"] and isinstance(invs, list):
                for inv in invs:
                    if isinstance(inv, dict) and inv.get("email"):
                        ctx["invite_email"] = str(inv["email"])
                        break
            tags = snap.get("tags") or snap.get("privateTags") or []
            if not ctx["private_tag_id"] and isinstance(tags, list):
                for tag in tags:
                    if isinstance(tag, dict) and tag.get("id") not in (None, ""):
                        ctx["private_tag_id"] = str(tag["id"])
                        break
            fam = os.getenv("SEED_FAMILY_ID", "").strip()
            if not ctx["family_id"] and fam:
                ctx["family_id"] = fam
            if ctx["user_id"] and ctx["asset_id"] and ctx["role_id"]:
                break

    # When we still have no asset but we do have our profile, AMS headers can use them.
    if not ctx["asset_user_id"] and ctx["user_id"]:
        ctx["asset_user_id"] = ctx["user_id"]
    if not ctx["asset_customer_id"] and ctx["customer_id"]:
        ctx["asset_customer_id"] = ctx["customer_id"]

    _SAMPLE_CACHE = ctx
    return ctx


def _source_truth_db() -> bool:
    cfg = load_source_validation_config()
    return cfg.source_truth == "db" and cfg.mysql_source_ready


def _mysql_display() -> str:
    user = (os.getenv("MYSQL_USER") or "").strip()
    host = (os.getenv("MYSQL_HOST") or "").strip()
    port = int(os.getenv("MYSQL_PORT") or "3306")
    return f"{user}@{host}:{port}" if host else "(MYSQL_HOST unset)"


def _run_select_probe(
    result: dict[str, Any],
    sql: str,
    params: tuple[Any, ...],
    *,
    ok_when,
    start: float,
) -> dict[str, Any]:
    """Execute a read-only SQL probe and map to probe-shaped result."""
    try:
        from .source_validation.db.connection import connect, load_mysql_config

        conn = connect(load_mysql_config())
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                row = cur.fetchone()
        finally:
            conn.close()
        result["latency_ms"] = _now_ms(start)
        result["reachable"] = True
        result["status_code"] = 200
        snippet = str(dict(row) if row else {})[:600]
        result["response_snippet"] = snippet
        if ok_when(row):
            result.update(state="ok", ok=True, detail="Reachable — query returned data.")
        else:
            result.update(
                state="error",
                ok=False,
                detail="Connected but query returned no matching row.",
            )
        return result
    except Exception as exc:  # noqa: BLE001
        result["latency_ms"] = _now_ms(start)
        msg = str(exc)
        is_auth = "1045" in msg or "Access denied" in msg
        result.update(
            state="blocked" if is_auth else "error",
            reachable=False,
            ok=False,
            detail=f"MySQL query failed: {exc}",
            hint=(
                "Allowlist your IP on the RDS user grant or confirm MYSQL_PASSWORD."
                if is_auth
                else _VPN_HINT
            ),
        )
        return result


def probe_cms_db() -> dict[str, Any]:
    ctx = _load_sample_context()
    cfg = load_source_validation_config()
    gcid = ctx["customer_id"] or cfg.gcid or "00000000-0000-0000-0000-000000000000"
    display = _mysql_display()
    sql = (
        "SELECT id, name, display_name AS displayName "
        "FROM customer_management.customers WHERE id = %s LIMIT 1"
    )
    result = _base(
        "cms_customer",
        "CMS · SELECT customer by id",
        "source",
        display,
        "SELECT",
        why=(
            "Compares actor/subject customer fields against "
            "customer_management.customers (SOURCE_TRUTH=db)."
        ),
    )
    result["sample"] = f"customerId={gcid}"
    result["request"] = {
        "method": "SELECT",
        "url": display,
        "headers": {},
        "params": {},
        "body": sql.replace("%s", f"'{gcid}'"),
    }
    start = time.monotonic()
    return _run_select_probe(
        result,
        sql,
        (gcid,),
        ok_when=lambda row: bool(row and row.get("id")),
        start=start,
    )


def probe_ums_profiles_db() -> dict[str, Any]:
    ctx = _load_sample_context()
    gcid = ctx["customer_id"]
    uid = ctx["user_id"]
    display = _mysql_display()
    sql = (
        "SELECT profile_Id_uuid AS id, email, first_name AS firstName, "
        "last_name AS lastName, role_name AS roleName "
        "FROM user_management.vw_profile_details "
        "WHERE profile_Id_uuid = %s LIMIT 1"
    )
    result = _base(
        "ums_profiles",
        "UMS · SELECT profile by id",
        "source",
        display,
        "SELECT",
        why=(
            "Validates actor.enrichedSnapshot.user.profile.* from "
            "user_management.vw_profile_details (SOURCE_TRUTH=db)."
        ),
    )
    result["sample"] = f"profile.id={uid or '(none)'}"
    result["request"] = {
        "method": "SELECT",
        "url": display,
        "headers": {},
        "params": {},
        "body": sql.replace("%s", f"'{uid or '(profile_id)'}'"),
    }
    if not uid:
        result.update(detail="No sample profile id available.", hint="Generate an event first.")
        return result
    start = time.monotonic()
    return _run_select_probe(
        result,
        sql,
        (uid,),
        ok_when=lambda row: bool(row and row.get("id")),
        start=start,
    )


def probe_ums_roles_db() -> dict[str, Any]:
    ctx = _load_sample_context()
    role_id = ctx["role_id"]
    display = _mysql_display()
    sql = (
        "SELECT LOWER(BIN_TO_UUID(id)) AS id, display_name AS displayName, "
        "type_id AS typeId "
        "FROM user_management.roles WHERE id = UUID_TO_BIN(%s) LIMIT 1"
    )
    result = _base(
        "ums_roles",
        "UMS · SELECT role by id",
        "source",
        display,
        "SELECT",
        why="Validates role displayName / typeId from user_management.roles (SOURCE_TRUTH=db).",
    )
    result["sample"] = f"roleId={role_id or '(none)'}"
    result["request"] = {
        "method": "SELECT",
        "url": display,
        "headers": {},
        "params": {},
        "body": sql.replace("%s", f"'{role_id or '(role_id)'}'"),
    }
    if not role_id:
        result.update(detail="No sample role id available.")
        return result
    start = time.monotonic()
    return _run_select_probe(
        result,
        sql,
        (role_id,),
        ok_when=lambda row: bool(row and row.get("id")),
        start=start,
    )


def probe_ums_teams_db() -> dict[str, Any]:
    ctx = _load_sample_context()
    gcid = ctx["customer_id"]
    display = _mysql_display()
    sql = (
        "SELECT LOWER(BIN_TO_UUID(id)) AS id, name, description "
        "FROM user_management.teams "
        "WHERE customer_id = UUID_TO_BIN(%s) LIMIT 5"
    )
    result = _base(
        "ums_teams",
        "UMS · SELECT teams",
        "source",
        display,
        "SELECT",
        why="Validates team.name / description from user_management.teams (SOURCE_TRUTH=db).",
    )
    result["sample"] = f"customerId={gcid or '(none)'}"
    result["request"] = {
        "method": "SELECT",
        "url": display,
        "headers": {},
        "params": {},
        "body": sql.replace("%s", f"'{gcid or '(gcid)'}'"),
    }
    if not gcid:
        result.update(detail="No sample customer id available.")
        return result
    start = time.monotonic()
    return _run_select_probe(
        result,
        sql,
        (gcid,),
        ok_when=lambda row: bool(row),
        start=start,
    )


def probe_ums_users_db() -> dict[str, Any]:
    ctx = _load_sample_context()
    idp = ctx.get("idp_user_id") or ""
    display = _mysql_display()
    sql = (
        "SELECT idp_user_id AS idpUserId, first_name AS firstName, "
        "last_name AS lastName, email "
        "FROM user_management.users WHERE idp_user_id = %s LIMIT 1"
    )
    result = _base(
        "ums_users",
        "UMS · SELECT user by idpUserId",
        "source",
        display,
        "SELECT",
        why=(
            "Rehydrates deletedProfiles[].user via user_management.users "
            "(SOURCE_TRUTH=db)."
        ),
    )
    result["sample"] = f"idpUserId={idp or '(edit to a real idp)'}"
    result["request"] = {
        "method": "SELECT",
        "url": display,
        "headers": {},
        "params": {},
        "body": sql.replace("%s", f"'{idp or 'auth0|example'}'"),
    }
    if not idp:
        result.update(detail="No idpUserId from bearer — edit query to test.")
        return result
    start = time.monotonic()
    return _run_select_probe(
        result,
        sql,
        (idp,),
        ok_when=lambda row: bool(row and row.get("idpUserId")),
        start=start,
    )


def probe_ams_db() -> dict[str, Any]:
    ctx = _load_sample_context()
    asset_id = ctx["asset_id"]
    display = _mysql_display()
    sql = (
        "SELECT asset_id AS id, asset_type AS assetType, created_by AS createdBy, "
        "created_at AS createdAt "
        "FROM asset_management.assets WHERE asset_id = %s LIMIT 1"
    )
    result = _base(
        "ams_asset",
        "AMS · SELECT asset by id",
        "source",
        display,
        "SELECT",
        why=(
            "Validates subject.enrichedSnapshot.asset.* from "
            "asset_management.assets (+ projects join at compare time)."
        ),
    )
    result["sample"] = f"asset_id={asset_id or '(none)'}"
    result["request"] = {
        "method": "SELECT",
        "url": display,
        "headers": {},
        "params": {},
        "body": sql.replace("%s", f"'{asset_id or '(asset_id)'}'"),
    }
    if not asset_id:
        result.update(detail="No sample asset id available.", hint="Generate an asset event first.")
        return result
    start = time.monotonic()
    return _run_select_probe(
        result,
        sql,
        (asset_id,),
        ok_when=lambda row: bool(row and row.get("id")),
        start=start,
    )


def probe_rabbitmq() -> dict[str, Any]:
    url = os.getenv("INGEST_RABBITMQ_URL") or os.getenv("RABBITMQ_URL") or ""
    parsed = urlparse(url)
    host = parsed.hostname or ""
    port = parsed.port or (5671 if parsed.scheme in ("amqps", "amqp+ssl") else 5672)
    display = f"{parsed.scheme}://{host}:{port}" if host else url
    result = _base(
        "rabbitmq",
        "RabbitMQ (event queues)",
        "infra",
        display,
        "TCP",
        why=(
            "Raw + enriched audit events flow through RabbitMQ queues. Live ingestion and "
            "generate/validate capture both depend on this broker being reachable."
        ),
    )
    result["request"] = {"method": "TCP", "url": display, "headers": {}, "params": {}, "body": None}
    if not host:
        result.update(detail="No RABBITMQ_URL configured.")
        return result
    start = time.monotonic()
    sock = None
    try:
        # Resolve once and try only the first address so a multi-IP host doesn't
        # multiply the timeout (N addresses × _TIMEOUT) into a very long wait.
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
        family, socktype, proto, _canon, sockaddr = infos[0]
        sock = socket.socket(family, socktype, proto)
        sock.settimeout(_TIMEOUT)
        sock.connect(sockaddr)
        result.update(
            state="ok",
            ok=True,
            reachable=True,
            latency_ms=_now_ms(start),
            detail=f"TCP connect to {host}:{port} succeeded.",
        )
    except Exception as exc:  # noqa: BLE001
        _from_exception(result, exc, start)
        result["detail"] = f"Cannot reach {host}:{port} — {result['detail']}"
    finally:
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass
    return result


def probe_cms() -> dict[str, Any]:
    cfg = load_source_validation_config()
    ctx = _load_sample_context()
    gcid = ctx["customer_id"] or cfg.gcid or "00000000-0000-0000-0000-000000000000"
    url = f"{cfg.cms_base_url}/api/v2/customers/{gcid}"
    headers = {
        "accept": "application/json",
        "x-client-id": cfg.cms_client_id,
        "x-correlation-id": _cid(),
    }
    params = {"projection": "id,name,displayName", "application": "MTConnect"}
    result = _base(
        "cms_customer",
        "CMS · GET customer by id",
        "source",
        url,
        "GET",
        why=(
            "Compares actor/subject customer fields (name, displayName, subscription, source) "
            "in enriched snapshots against Customer Management — used on every event with a gcid."
        ),
    )
    result["sample"] = (
        f"customerId={gcid}"
        + (" · from our bearer" if ctx.get("from_bearer") else "")
    )
    result["request"] = {"method": "GET", "url": url, "headers": headers, "params": params, "body": None}
    start = time.monotonic()
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=_TIMEOUT)
        return _from_response(result, resp, start, ok_when=lambda r: r.status_code < 400)
    except Exception as exc:  # noqa: BLE001
        return _from_exception(result, exc, start)


def probe_ums_profiles() -> dict[str, Any]:
    """POST-as-GET /profiles with a real profile.id filter — should return 1 profile."""
    cfg = load_source_validation_config()
    ctx = _load_sample_context()
    gcid = ctx["customer_id"] or cfg.gcid
    uid = ctx["user_id"]
    url = f"{cfg.ums_base_url}/api/v3/customers/{gcid}/profiles"
    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "x-client-id": cfg.ums_client_id,
        "x-correlation-id": _cid(),
        "X-HTTP-Method-Override": "GET",
    }
    body = {
        "projection": "isActive,firstName,lastName,email,customerId,role.id,team.id,idpUserId",
        "filter": {"#and": {"isActive": {"eq": True}, "profile.id": {"in": [uid] if uid else []}}},
        "limit": 1,
        "offset": 0,
    }
    result = _base(
        "ums_profiles",
        "UMS · POST-as-GET profiles",
        "source",
        url,
        "POST→GET",
        why=(
            "Validates actor.enrichedSnapshot.user.profile.* (firstName, lastName, idpUserId, "
            "isActive, …). Plain GET returns id-only rows on preprod — we use the resolver's "
            "POST-as-GET form so Comparison can match real UMS values."
        ),
    )
    result["sample"] = (
        f"profile.id={uid or '(none)'}"
        + (" · from our bearer" if ctx.get("from_bearer") and uid else "")
    )
    result["request"] = {"method": "POST", "url": url, "headers": headers, "params": {}, "body": body}
    if not (gcid and uid):
        result.update(detail="No sample customer/user id available.", hint="Generate an event first.")
        return result
    start = time.monotonic()
    try:
        resp = requests.post(url, headers=headers, json=body, timeout=_TIMEOUT)

        def ok_when(r: requests.Response) -> bool:
            if r.status_code != 200:
                return False
            try:
                data = r.json()
            except ValueError:
                return False
            root = data.get("data") if isinstance(data, dict) else None
            profiles = (root or {}).get("profiles") if isinstance(root, dict) else None
            return bool(profiles)

        out = _from_response(result, resp, start, ok_when=ok_when)
        if out["state"] == "ok":
            out["detail"] = "Reachable — returned the profile with full fields."
        return out
    except Exception as exc:  # noqa: BLE001
        return _from_exception(result, exc, start)


def probe_ums_roles() -> dict[str, Any]:
    cfg = load_source_validation_config()
    ctx = _load_sample_context()
    gcid = ctx["customer_id"] or cfg.gcid
    role_id = ctx["role_id"]
    url = f"{cfg.ums_base_url}/api/v3/customers/{gcid}/roles"
    headers = {
        "accept": "application/json",
        "x-client-id": cfg.ums_client_id,
        "x-correlation-id": _cid(),
    }
    params: dict[str, Any] = {
        "projection": "id,displayName,typeId,permissions,description,profileCount",
        "filterType": "id",
    }
    if role_id:
        params["filter"] = role_id
    result = _base(
        "ums_roles",
        "UMS · GET role by id",
        "source",
        url,
        "GET",
        why=(
            "Validates actor/subject role.displayName, permissions, typeId against UMS. "
            "Used whenever the enricher attaches a role snapshot."
        ),
    )
    result["sample"] = (
        f"filter={role_id or '(none)'}"
        + (" · from our bearer profile" if ctx.get("from_bearer") and role_id else "")
    )
    result["request"] = {"method": "GET", "url": url, "headers": headers, "params": params, "body": None}
    start = time.monotonic()
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=_TIMEOUT)
        return _from_response(result, resp, start, ok_when=lambda r: r.status_code < 400)
    except Exception as exc:  # noqa: BLE001
        return _from_exception(result, exc, start)


def probe_ums_teams() -> dict[str, Any]:
    cfg = load_source_validation_config()
    ctx = _load_sample_context()
    gcid = ctx["customer_id"] or cfg.gcid
    url = f"{cfg.ums_base_url}/api/v3/customers/{gcid}/teams"
    headers = {
        "accept": "application/json",
        "x-client-id": cfg.ums_client_id,
        "x-correlation-id": _cid(),
    }
    params = {
        "projection": "name,description,customerId,profilesCount",
        "isProfileActive__eq": "true",
        "skip": 0,
        "limit": 5,
    }
    result = _base(
        "ums_teams",
        "UMS · GET teams",
        "source",
        url,
        "GET",
        why="Validates team.name / team.description on events that enrich team membership.",
    )
    result["sample"] = (
        f"customerId={gcid}"
        + (" · from our bearer" if ctx.get("from_bearer") else "")
    )
    result["request"] = {"method": "GET", "url": url, "headers": headers, "params": params, "body": None}
    start = time.monotonic()
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=_TIMEOUT)
        return _from_response(result, resp, start, ok_when=lambda r: r.status_code < 400)
    except Exception as exc:  # noqa: BLE001
        return _from_exception(result, exc, start)


def probe_ums_users() -> dict[str, Any]:
    """GET /api/v3/users?idpUserId=… — used after deleteProfiles (profile row is gone)."""
    cfg = load_source_validation_config()
    ctx = _load_sample_context()
    idp = ctx.get("idp_user_id") or ""
    enrich_dir = cfg.project_root / "payload" / "enrich"
    delete_sample = enrich_dir / "deleteProfiles.json"
    if not idp and delete_sample.is_file():
        try:
            import json as _json
            doc = _json.loads(delete_sample.read_text(encoding="utf-8"))
            snap = ((doc.get("subject") or {}).get("enrichedSnapshot") or {})
            deleted = snap.get("deletedProfiles") or []
            if deleted and isinstance(deleted[0], dict):
                idp = str(deleted[0].get("idpUserId") or (deleted[0].get("user") or {}).get("idpUserId") or "")
        except Exception:
            pass
    url = f"{cfg.ums_base_url}/api/v3/users"
    headers = {
        "accept": "application/json",
        "x-client-id": cfg.ums_client_id,
        "x-correlation-id": _cid(),
    }
    params = {
        "idpUserId": idp or "auth0|example",
        "projection": "idpUserId,firstName,lastName,email,profiles",
    }
    result = _base(
        "ums_users",
        "UMS · GET user by idpUserId",
        "source",
        url,
        "GET",
        why=(
            "After deleteProfiles the profile row is gone. The resolver rehydrates "
            "deletedProfiles[].user (firstName/lastName/email) via this endpoint "
            "(mtconnect-api #1005 / resolver PR #50). Also used by API Health to resolve "
            "our Bearer sub → profile UUID."
        ),
    )
    result["sample"] = (
        f"idpUserId={idp or '(edit to a real idp)'}"
        + (" · from our bearer" if ctx.get("idp_user_id") else "")
    )
    result["request"] = {"method": "GET", "url": url, "headers": headers, "params": params, "body": None}
    start = time.monotonic()
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=_TIMEOUT)
        return _from_response(result, resp, start, ok_when=lambda r: r.status_code < 400)
    except Exception as exc:  # noqa: BLE001
        return _from_exception(result, exc, start)


def probe_typesense_styles() -> dict[str, Any]:
    cfg = load_source_validation_config()
    ctx = _load_sample_context()
    fam = ctx["family_id"] or "794981"
    url = f"{cfg.discovery_base_url}/v1/styles?skipInventoryCheck=true"
    headers = {
        "accept": "application/json",
        "accept-language": "en",
        "content-type": "application/json",
        "x-correlation-id": _cid(),
    }
    auth = cfg.discovery_auth_header
    if auth:
        headers["Authorization"] = auth
    body = {"familyIds": [fam], "page": 1, "per_page": 5}
    result = _base(
        "typesense_styles",
        "Typesense · POST styles by familyId",
        "source",
        url,
        "POST",
        why=(
            "Validates subject.enrichedSnapshot.fontDetails.styles.* (name, density, "
            "foundry, …) for activation / favourite / list events."
        ),
    )
    result["sample"] = f"familyIds=[{fam}]"
    result["request"] = {"method": "POST", "url": url, "headers": headers, "params": {}, "body": body}
    start = time.monotonic()
    try:
        resp = requests.post(url, headers=headers, json=body, timeout=_TIMEOUT)
        return _from_response(result, resp, start, ok_when=lambda r: r.status_code < 400)
    except Exception as exc:  # noqa: BLE001
        return _from_exception(result, exc, start)


def probe_typesense_variations() -> dict[str, Any]:
    cfg = load_source_validation_config()
    ctx = _load_sample_context()
    fam = ctx["family_id"] or "794981"
    url = f"{cfg.discovery_base_url}/v1/variations"
    headers = {"accept": "application/json", "accept-language": "en", "x-correlation-id": _cid()}
    auth = cfg.discovery_auth_header
    if auth:
        headers["Authorization"] = auth
    params = {
        "familyIds": fam,
        "page": 1,
        "perPage": 5,
        "includeStyle": "false",
        "skipInventoryCheck": "true",
    }
    result = _base(
        "typesense_variations",
        "Typesense · GET variations by familyId",
        "source",
        url,
        "GET",
        why="Validates variation md5 / catalog fields on sync and activation events.",
    )
    result["sample"] = f"familyIds={fam}"
    result["request"] = {"method": "GET", "url": url, "headers": headers, "params": params, "body": None}
    start = time.monotonic()
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=_TIMEOUT)
        return _from_response(result, resp, start, ok_when=lambda r: r.status_code < 400)
    except Exception as exc:  # noqa: BLE001
        return _from_exception(result, exc, start)


def probe_ums_invitations_db() -> dict[str, Any]:
    ctx = _load_sample_context()
    invite_email = ctx.get("invite_email") or ctx.get("email") or ""
    sql = (
        "SELECT Id, Email, Status, CreatedOn, RoleId, GlobalCustomerId, EmailLocale "
        "FROM user_management.user_invitation WHERE email = %s LIMIT 1"
    )
    result = _base(
        "ums_invitations",
        "UMS · MySQL user_invitation by email",
        "source",
        _mysql_display(),
        "SELECT",
        why=(
            "Validates createUserInvitations subject.enrichedSnapshot.invitations[] "
            "(email, status, roleId, customerId) against user_management.user_invitation."
        ),
    )
    result["sample"] = f"email={invite_email or '(set invite_email from createUserInvitations sample)'}"
    result["request"] = {
        "method": "SELECT",
        "url": _mysql_display(),
        "headers": {},
        "params": {},
        "body": {"sql": sql, "params": [invite_email] if invite_email else []},
    }
    if not invite_email:
        result.update(
            detail="No invite email in staged samples — generate createUserInvitations first.",
            hint="Run createUserInvitations or set invite_email on a payload/enrich sample.",
        )
        return result
    start = time.monotonic()
    return _run_select_probe(
        result,
        sql,
        (invite_email,),
        ok_when=lambda row: bool(row),
        start=start,
    )


def probe_ums_invitations() -> dict[str, Any]:
    """HTTP-mode alias — invitation compare uses MySQL (same query as db probe)."""
    if _source_truth_db():
        return probe_ums_invitations_db()
    ctx = _load_sample_context()
    invite_email = ctx.get("invite_email") or ""
    from .source_validation.clients import UmsClient

    cfg = load_source_validation_config()
    result = _base(
        "ums_invitations",
        "UMS · user_invitation lookup (MySQL via compare helper)",
        "source",
        "mysql:user_management.user_invitation",
        "SELECT",
        why=(
            "createUserInvitations Compare reads invitation rows with "
            "SELECT * FROM user_management.user_invitation WHERE email = ?"
        ),
    )
    result["sample"] = f"email={invite_email or '(none)'}"
    if not invite_email:
        result.update(detail="No invite email in staged samples.", hint="Generate createUserInvitations.")
        return result
    start = time.monotonic()
    try:
        ums = UmsClient(cfg)
        row = ums.get_invitation_by_email(
            invite_email, ctx.get("customer_id") or cfg.gcid or "", correlation_id=_cid()
        )
        if row:
            result.update(
                ok=True,
                state="ok",
                detail=f"Found invitation id={row.get('id')} status={row.get('status')}",
                elapsed_ms=int((time.monotonic() - start) * 1000),
                response_preview=row,
            )
        else:
            result.update(
                ok=False,
                state="fail",
                detail=f"No row for email={invite_email}",
                elapsed_ms=int((time.monotonic() - start) * 1000),
            )
    except Exception as exc:  # noqa: BLE001
        return _from_exception(result, exc, start)
    return result


def probe_typesense_private_tag() -> dict[str, Any]:
    cfg = load_source_validation_config()
    ctx = _load_sample_context()
    tag_id = ctx.get("private_tag_id") or ""
    url = f"{cfg.discovery_base_url}/v1/privateTag/{tag_id or '(sample)'}"
    auth = cfg.discovery_auth_header
    headers = {
        "Authorization": auth,
        "accept": "application/json",
        "x-correlation-id": _cid(),
        "Content-Type": "application/json",
    }
    body = {"page": 1, "per_page": 10}
    result = _base(
        "typesense_private_tag",
        "Typesense · POST /v1/privateTag/{id}",
        "source",
        url,
        "POST",
        why=(
            "Validates updatePrivateTag / createPrivateTags subject.enrichedSnapshot.tags[] "
            "(id, name, customerId, stylesCount) from Discovery middleware."
        ),
    )
    result["sample"] = f"tagId={tag_id or '(none)'}"
    result["request"] = {"method": "POST", "url": url, "headers": headers, "params": {}, "body": body}
    if not tag_id:
        result.update(
            detail="No private tag id in staged samples — generate updatePrivateTag first.",
            hint="Run updatePrivateTag or createPrivateTags.",
        )
        return result
    if not auth:
        result.update(detail="Discovery token missing.", hint="Set BEARER_TOKEN / DISCOVERY auth in .env")
        return result
    start = time.monotonic()
    try:
        from .source_validation.clients import DiscoveryClient

        row = DiscoveryClient(cfg).fetch_private_tag_by_id(tag_id, correlation_id=_cid())
        if row:
            result.update(
                ok=True,
                state="ok",
                detail=f"Tag {row.get('id')} name={row.get('name')!r}",
                elapsed_ms=int((time.monotonic() - start) * 1000),
                response_preview=row,
            )
        else:
            result.update(
                ok=False,
                state="fail",
                detail=f"No tag document for id={tag_id}",
                elapsed_ms=int((time.monotonic() - start) * 1000),
            )
    except Exception as exc:  # noqa: BLE001
        return _from_exception(result, exc, start)
    return result


def probe_ams() -> dict[str, Any]:
    cfg = load_source_validation_config()
    ctx = _load_sample_context()
    asset_id = ctx["asset_id"]
    asset_type = ctx["asset_type"] or "Folder"
    ams_type = {"FontList": "FontSet"}.get(asset_type, asset_type) or "Folder"
    url = f"{cfg.ams_base_url}/v2/type/{ams_type}/asset/{asset_id or '(sample)'}"
    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "x-client-id": cfg.ams_client_id,
        "x-correlation-id": _cid(),
        "x-authorization-override": "true",
        "x-global-user-id": ctx["asset_user_id"] or ctx["user_id"],
        "x-global-customer-id": ctx["asset_customer_id"] or ctx["customer_id"],
    }
    params = {"projection": "name,assetType,createdBy,createdAt", "limit": -1, "offset": 0}
    result = _base(
        "ams_asset",
        "AMS · GET asset by id",
        "source",
        url,
        "GET",
        why=(
            "Validates subject.enrichedSnapshot.asset.* (name, assetType, metadata, "
            "accessIds, depth) for project / folder / list create-update-delete events."
        ),
    )
    result["sample"] = f"type={ams_type} id={asset_id or '(none)'}"
    result["request"] = {
        "method": "GET",
        "url": f"{cfg.ams_base_url}/v2/type/{ams_type}/asset/{asset_id or '(edit)'}",
        "headers": headers,
        "params": params,
        "body": None,
    }
    if not asset_id:
        result.update(detail="No sample asset id available.", hint="Generate an asset event first.")
        return result
    start = time.monotonic()
    try:
        resp = requests.get(
            f"{cfg.ams_base_url}/v2/type/{ams_type}/asset/{asset_id}",
            headers=headers,
            params=params,
            timeout=_TIMEOUT,
        )
        return _from_response(result, resp, start, ok_when=lambda r: r.status_code < 400)
    except Exception as exc:  # noqa: BLE001
        return _from_exception(result, exc, start)


def probe_graphql() -> dict[str, Any]:
    endpoint = os.getenv("NEXTGEN_GRAPHQL_ENDPOINT", "https://nextgen.monotype-pp.com/graph")
    authorization, real = _resolve_bearer()
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": authorization,
    }
    body = {"query": "query Health { __typename }"}
    result = _base(
        "graphql",
        "GraphQL API (NextGen /graph)",
        "api",
        endpoint,
        "POST",
        why=(
            "Primary trigger path for ~200 audit mutations (activateFamily, createProject, …). "
            "Generate & Edit-payload use this endpoint with the Bearer token."
        ),
    )
    result["request"] = {"method": "POST", "url": endpoint, "headers": headers, "params": {}, "body": body}
    if not real:
        result.update(detail="No BEARER_TOKEN configured — set it in .env.", hint="Set BEARER_TOKEN in .env")
        return result
    start = time.monotonic()
    try:
        resp = requests.post(endpoint, headers=headers, json=body, timeout=_TIMEOUT)

        def ok_when(r: requests.Response) -> bool:
            if r.status_code != 200:
                return False
            try:
                data = r.json()
            except ValueError:
                return False
            return isinstance(data, dict) and "data" in data and not data.get("errors")

        return _from_response(result, resp, start, ok_when=ok_when)
    except Exception as exc:  # noqa: BLE001
        return _from_exception(result, exc, start)


def probe_ingress() -> dict[str, Any]:
    endpoint = _ingress_endpoint()
    authorization, _real = _resolve_bearer()
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": authorization,
        "x-correlation-id": _cid(),
        "x-request-source": os.getenv("INGRESS_REQUEST_SOURCE", "MT_CONNECT_BS"),
        "x-os-platform": os.getenv("INGRESS_OS_PLATFORM", "MAC"),
    }
    body: list[Any] = []
    result = _base(
        "ingress",
        "Ingress API (desktop/plugin)",
        "api",
        endpoint,
        "POST",
        why=(
            "Desktop / plugin / FontBridge events are POSTed as audit envelopes here "
            "(~29 ingress cases). Empty array proves reachability; edit body to send a real event."
        ),
    )
    result["request"] = {"method": "POST", "url": endpoint, "headers": headers, "params": {}, "body": body}
    start = time.monotonic()
    try:
        # Empty batch: origin validates and rejects (4xx) but proves reachability.
        resp = requests.post(endpoint, headers=headers, json=body, timeout=_TIMEOUT)
        return _from_response(result, resp, start, ok_when=lambda r: r.status_code < 500)
    except Exception as exc:  # noqa: BLE001
        return _from_exception(result, exc, start)


def probe_mysql_source() -> dict[str, Any]:
    """SELECT-only connectivity to mosaic MySQL (CMS/UMS/AMS schemas)."""
    host = (os.getenv("MYSQL_HOST") or "").strip()
    port = int(os.getenv("MYSQL_PORT") or "3306")
    user = (os.getenv("MYSQL_USER") or "").strip()
    display = f"{user}@{host}:{port}" if host else "(MYSQL_HOST unset)"
    result = _base(
        "mysql_source",
        "MySQL · CMS/UMS/AMS source truth",
        "source",
        display,
        "SELECT",
        why=(
            "Independent ground truth for Compare when SOURCE_TRUTH=db. "
            "Reads customer_management.customers, user_management.profiles/roles, "
            "asset_management.assets (SELECT-only — never writes)."
        ),
    )
    result["sample"] = f"SOURCE_TRUTH={os.getenv('SOURCE_TRUTH') or 'api'}"
    gcid = (os.getenv("CUSTOMER_ID") or os.getenv("OAUTH_GCID") or "{gcid}").strip()
    result["request"] = {
        "method": "SELECT",
        "url": display,
        "headers": {},
        "params": {},
        "body": (
            "-- Sample ground-truth queries (replace placeholders)\n"
            f"SELECT id, name, display_name FROM customer_management.customers "
            f"WHERE id = '{gcid}' LIMIT 1;\n"
            "SELECT profile_Id_uuid, email, role_name "
            "FROM user_management.vw_profile_details "
            "WHERE profile_Id_uuid = '{profile_id}' LIMIT 1;\n"
            "SELECT CURRENT_USER() AS u, VERSION() AS v;"
        ),
    }
    if not host or not user:
        result.update(
            state="error",
            ok=False,
            detail="MYSQL_HOST / MYSQL_USER not configured.",
            hint="Set MYSQL_* in .env and SOURCE_TRUTH=db to use DB clients.",
        )
        return result
    start = time.monotonic()
    try:
        from .source_validation.db.connection import connect, load_mysql_config

        cfg = load_mysql_config()
        conn = connect(cfg)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT CURRENT_USER() AS u, "
                    "(SELECT COUNT(*) FROM information_schema.SCHEMATA "
                    " WHERE SCHEMA_NAME IN "
                    "('customer_management','user_management','asset_management')) AS schema_count"
                )
                row = dict(cur.fetchone() or {})
        finally:
            conn.close()
        result["latency_ms"] = _now_ms(start)
        result["reachable"] = True
        result["status_code"] = 200
        result.update(
            state="ok",
            ok=True,
            detail=f"Connected as {row.get('u')} — source schemas visible: {row.get('schema_count')}/3.",
        )
        result["response_snippet"] = str(row)[:600]
        return result
    except Exception as exc:  # noqa: BLE001
        result["latency_ms"] = _now_ms(start)
        msg = str(exc)
        is_auth = "1045" in msg or "Access denied" in msg
        result.update(
            state="blocked" if is_auth else "error",
            reachable=False,
            ok=False,
            detail=f"MySQL connect failed: {exc}",
            hint=(
                "TCP may work but MySQL rejected this client IP/password. "
                "Allowlist your current public IP on the RDS user grant (same path as Workbench), "
                "or confirm MYSQL_PASSWORD matches the Workbench keychain entry."
                if is_auth
                else _VPN_HINT
            ),
        )
        return result


_PROBES = {
    "rabbitmq": probe_rabbitmq,
    "graphql": probe_graphql,
    "ingress": probe_ingress,
    "mysql_source": probe_mysql_source,
    "cms_customer": probe_cms,
    "ums_profiles": probe_ums_profiles,
    "ums_roles": probe_ums_roles,
    "ums_teams": probe_ums_teams,
    "ums_users": probe_ums_users,
    "typesense_styles": probe_typesense_styles,
    "typesense_variations": probe_typesense_variations,
    "typesense_private_tag": probe_typesense_private_tag,
    "ums_invitations": probe_ums_invitations,
    "ams_asset": probe_ams,
}

_DB_SOURCE_PROBES = {
    "cms_customer": probe_cms_db,
    "ums_profiles": probe_ums_profiles_db,
    "ums_roles": probe_ums_roles_db,
    "ums_teams": probe_ums_teams_db,
    "ums_users": probe_ums_users_db,
    "ums_invitations": probe_ums_invitations_db,
    "ams_asset": probe_ams_db,
}

# Back-compat aliases so older UI calls (?target=cms/ums/discovery) still resolve.
_PROBE_ALIASES = {
    "cms": "cms_customer",
    "ums": "ums_profiles",
    "discovery": "typesense_styles",
}


def probe_one(target: str) -> dict[str, Any]:
    target = _PROBE_ALIASES.get(target, target)
    if _source_truth_db() and target in _DB_SOURCE_PROBES:
        return _DB_SOURCE_PROBES[target]()
    fn = _PROBES.get(target)
    if not fn:
        raise KeyError(target)
    return fn()


def probe_all() -> list[dict[str, Any]]:
    """Run every probe concurrently so one slow timeout doesn't serialize the rest."""
    from concurrent.futures import ThreadPoolExecutor

    global _SAMPLE_CACHE
    _SAMPLE_CACHE = None  # refresh Bearer-derived identity each Run all

    use_db = _source_truth_db()
    order = list(_PROBES.keys())

    def _run_probe(key: str) -> dict[str, Any]:
        if use_db and key in _DB_SOURCE_PROBES:
            return _DB_SOURCE_PROBES[key]()
        return _PROBES[key]()

    with ThreadPoolExecutor(max_workers=len(order)) as pool:
        results = list(pool.map(_run_probe, order))
    return results


def execute_custom_request(request: dict[str, Any]) -> dict[str, Any]:
    """Execute an edited probe request (Postman-like) and return a probe-shaped result."""
    method = str(request.get("method") or "GET").upper()
    if method in {"POST→GET", "POST-AS-GET"}:
        method = "POST"
    url = str(request.get("url") or "").strip()
    headers = dict(request.get("headers") or {})
    params = dict(request.get("params") or {})
    body = request.get("body")
    result = _base(
        "custom",
        "Custom request",
        "api",
        url or "(no url)",
        method,
        why="Ad-hoc request sent from the API Health editor.",
    )
    result["request"] = {
        "method": method,
        "url": url,
        "headers": headers,
        "params": params,
        "body": body,
    }
    if method == "TCP":
        result.update(detail="TCP probes are not editable — use Run all.", state="error")
        return result
    if not url or url.startswith("("):
        result.update(detail="Edit the URL to a real endpoint before sending.", state="error")
        return result
    start = time.monotonic()
    try:
        resp = requests.request(
            method,
            url,
            headers=headers,
            params=params or None,
            json=body if body is not None and method in {"POST", "PUT", "PATCH"} else None,
            timeout=_TIMEOUT,
        )
        return _from_response(result, resp, start, ok_when=lambda r: r.status_code < 500)
    except Exception as exc:  # noqa: BLE001
        return _from_exception(result, exc, start)
