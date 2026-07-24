"""HTTP clients for enrichment source APIs (Discovery, UMS, CMS)."""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any

import requests

from .config import SourceValidationConfig

log = logging.getLogger(__name__)

_GUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.I,
)

# Projection for POST-as-GET /api/v3/customers/{gcid}/profiles — copied verbatim from
# the resolver (getProfilesByIds). A plain GET (even with this as a query param) returns
# id-only rows on preprod; the POST-as-GET body form returns the fully-populated profile.
_UMS_PROFILE_GET_PROJECTION = (
    "isActive,firstName,lastName,email,meta,userId,externalUserId,customerId,"
    "tempUserExpiryDate,idpUserId,activity.lastActivityTimestamp,role.id,team.id,"
    "team.teamAdminIds,createdAt,profile.metaData"
)

_UMS_TEAM_PROJECTION = (
    "name,description,customerId,profile.id,profilesCount,adminProfile.id"
)

_CMS_PROJECTION = (
    "id,name,displayName,source,parentId,identityProviderId,externalId,metaData,"
    "isPreDeliveryEnabled,isTestDemo,subscription,createdAt"
)
_CMS_SUBSCRIPTION_FIELDS = (
    "planDefinition,productType,seatsAvailable,terminationDate,isTrial,isActive,createdAt"
)


def _correlation_id(value: str) -> str:
    if _GUID_RE.match(value.strip()):
        return value.strip()
    return str(uuid.uuid4())


def _unwrap_discovery_hits(data: object) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if not isinstance(data, dict):
        return []
    if isinstance(data.get("results"), dict):
        hits = data["results"].get("hits")
        if isinstance(hits, list):
            return [x for x in hits if isinstance(x, dict)]
    for key in ("hits", "data"):
        val = data.get(key)
        if isinstance(val, list):
            return [x for x in val if isinstance(x, dict)]
    return []


def _extract_ums_profiles(payload: object) -> list[dict[str, Any]]:
    """Pull the profile rows out of a UMS profiles response.

    Handles ``{"data": {"count", "profiles": [...]}}`` (the shape returned by the
    plain GET) as well as ``{"profiles": [...]}`` and bare-list responses.
    """
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if not isinstance(payload, dict):
        return []
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    profiles = data.get("profiles") if isinstance(data, dict) else None
    if not isinstance(profiles, list):
        profiles = payload.get("profiles")
    return [r for r in profiles if isinstance(r, dict)] if isinstance(profiles, list) else []


def _normalize_ums_profile(row: dict[str, Any]) -> dict[str, Any]:
    if "profile" in row and isinstance(row["profile"], dict):
        merged = dict(row["profile"])
        if row.get("role"):
            merged["role"] = row["role"]
        if row.get("team"):
            merged["teams"] = [row["team"]] if isinstance(row["team"], dict) else row["team"]
        return merged
    return row


class DiscoveryClient:
    def __init__(self, cfg: SourceValidationConfig) -> None:
        self._cfg = cfg
        self._session = requests.Session()

    def fetch_styles_by_family_ids(
        self,
        family_ids: list[str],
        *,
        style_ids: list[str] | None = None,
        correlation_id: str,
    ) -> list[dict[str, Any]]:
        # skipInventoryCheck bypasses customer-inventory scoping so validation sees the
        # same catalog the resolver's M2M token resolved (avoids false "missing" fonts).
        url = f"{self._cfg.discovery_base_url}/v1/styles?skipInventoryCheck=true"
        cid = _correlation_id(correlation_id)
        headers = {
            "Authorization": self._cfg.discovery_auth_header,
            "accept": "application/json",
            "accept-language": "en",
            "x-correlation-id": cid,
            "Content-Type": "application/json",
        }
        body: dict[str, Any] = {"page": 1, "per_page": 250}
        if family_ids:
            body["familyIds"] = family_ids[:50]
        if style_ids:
            body["styleIds"] = style_ids[:50]
        if not family_ids and not style_ids:
            return []
        resp = self._session.post(url, json=body, headers=headers, timeout=60)
        resp.raise_for_status()
        return _unwrap_discovery_hits(resp.json())

    def fetch_variations_by_family_ids(
        self,
        family_ids: list[str],
        *,
        correlation_id: str,
    ) -> list[dict[str, Any]]:
        url = f"{self._cfg.discovery_base_url}/v1/variations"
        cid = _correlation_id(correlation_id)
        headers = {
            "Authorization": self._cfg.discovery_auth_header,
            "accept": "application/json",
            "accept-language": "en",
            "x-correlation-id": cid,
        }
        params = {
            "familyIds": ",".join(family_ids[:10]),
            "includeStyle": "false",
            "skipInventoryCheck": "true",
            "page": 1,
            "perPage": 250,
        }
        resp = self._session.get(url, headers=headers, params=params, timeout=60)
        resp.raise_for_status()
        return _unwrap_discovery_hits(resp.json())

    def fetch_variations_by_style_ids(
        self,
        style_ids: list[str],
        *,
        correlation_id: str,
    ) -> list[dict[str, Any]]:
        if not style_ids:
            return []
        url = f"{self._cfg.discovery_base_url}/v1/variations"
        cid = _correlation_id(correlation_id)
        headers = {
            "Authorization": self._cfg.discovery_auth_header,
            "accept": "application/json",
            "accept-language": "en",
            "x-correlation-id": cid,
        }
        params = {
            "stylesIds": ",".join(style_ids[:50]),
            "includeStyle": "true",
            "skipInventoryCheck": "true",
            "page": 1,
            "perPage": 250,
        }
        resp = self._session.get(url, headers=headers, params=params, timeout=60)
        resp.raise_for_status()
        return _unwrap_discovery_hits(resp.json())

    def fetch_variations_by_md5s(
        self,
        md5s: list[str],
        *,
        correlation_id: str,
    ) -> list[dict[str, Any]]:
        if not md5s:
            return []
        url = f"{self._cfg.discovery_base_url}/v1/variations"
        cid = _correlation_id(correlation_id)
        headers = {
            "Authorization": self._cfg.discovery_auth_header,
            "accept": "application/json",
            "accept-language": "en",
            "x-correlation-id": cid,
        }
        params = {
            "md5s": ",".join(md5s[:50]),
            "includeStyle": "true",
            "skipInventoryCheck": "true",
            "page": 1,
            "perPage": 250,
        }
        resp = self._session.get(url, headers=headers, params=params, timeout=60)
        resp.raise_for_status()
        return _unwrap_discovery_hits(resp.json())

    def fetch_private_tag_by_id(
        self,
        tag_id: str,
        *,
        correlation_id: str,
    ) -> dict[str, Any] | None:
        """GET/POST ``/v1/privateTag/{id}`` — private tag document (Typesense middleware)."""
        tid = str(tag_id or "").strip()
        if not tid:
            return None
        url = f"{self._cfg.discovery_base_url}/v1/privateTag/{tid}"
        cid = _correlation_id(correlation_id)
        headers = {
            "Authorization": self._cfg.discovery_auth_header,
            "accept": "application/json",
            "accept-language": "en",
            "x-correlation-id": cid,
            "Content-Type": "application/json",
        }
        resp = self._session.post(
            url,
            json={"page": 1, "per_page": 10},
            headers=headers,
            timeout=60,
        )
        resp.raise_for_status()
        payload = resp.json()
        if not isinstance(payload, dict):
            return None
        results = payload.get("results")
        if isinstance(results, dict):
            data = results.get("data")
            if isinstance(data, list) and data and isinstance(data[0], dict):
                return data[0]
        data = payload.get("data")
        if isinstance(data, list) and data and isinstance(data[0], dict):
            return data[0]
        if payload.get("id"):
            return payload
        return None


class UmsClient:
    def __init__(self, cfg: SourceValidationConfig) -> None:
        self._cfg = cfg
        self._session = requests.Session()

    def _headers(self, correlation_id: str, *, post: bool = False) -> dict[str, str]:
        h = {
            "accept": "application/json",
            "x-client-id": self._cfg.ums_client_id,
            "x-correlation-id": _correlation_id(correlation_id),
        }
        if post:
            h["Content-Type"] = "application/json"
        return h

    def get_role_by_id(
        self,
        role_id: str,
        customer_id: str,
        *,
        correlation_id: str,
    ) -> dict[str, Any] | None:
        gcid = customer_id or self._cfg.gcid
        url = f"{self._cfg.ums_base_url}/api/v3/customers/{gcid}/roles"
        params = {
            "projection": "id,displayName,typeId,permissions,description,profileCount",
            "filter": role_id,
            "filterType": "id",
        }
        resp = self._session.get(
            url, headers=self._headers(correlation_id), params=params, timeout=30
        )
        resp.raise_for_status()
        data = resp.json()
        items = data if isinstance(data, list) else data.get("data") or data.get("roles") or []
        if not items:
            return None
        return items[0] if isinstance(items[0], dict) else None

    def _profiles_post_as_get(
        self,
        customer_id: str,
        profile_ids: list[str],
        *,
        correlation_id: str,
        user_type: str | None = None,
        limit: int = -1,
    ) -> list[dict[str, Any]]:
        """POST-as-GET /profiles — mirrors the resolver's ``getProfilesByIds``.

        The plain GET (even with ``projection`` as a query param) returns id-only
        rows on preprod, which is why every profile field falsely FAILed. The
        resolver uses POST + ``X-HTTP-Method-Override: GET`` with a JSON body that
        carries the ``projection`` and a ``filter`` on ``profile.id`` — that returns
        the fully-populated profile. We copy that request verbatim.
        """
        ids = [str(p).strip() for p in profile_ids if str(p or "").strip()]
        if not ids:
            return []
        gcid = customer_id or self._cfg.gcid
        url = f"{self._cfg.ums_base_url}/api/v3/customers/{gcid}/profiles"
        and_filter: dict[str, Any] = {
            "isActive": {"eq": True},
            "profile.id": {"in": ids},
        }
        if user_type:
            and_filter["userType"] = {"eq": user_type}
        body = {
            "projection": _UMS_PROFILE_GET_PROJECTION,
            "filter": {"#and": and_filter},
            "limit": limit,
            "offset": 0,
        }
        headers = {**self._headers(correlation_id, post=True), "X-HTTP-Method-Override": "GET"}
        resp = self._session.post(url, headers=headers, json=body, timeout=30)
        resp.raise_for_status()
        return _extract_ums_profiles(resp.json())

    def get_profile_by_id(
        self,
        profile_id: str,
        customer_id: str,
        *,
        correlation_id: str,
    ) -> dict[str, Any] | None:
        """Fetch a single profile via POST-as-GET with a ``profile.id`` filter."""
        if not profile_id:
            return None
        profiles = self._profiles_post_as_get(
            customer_id, [profile_id], correlation_id=correlation_id, limit=1
        )
        pid = profile_id.strip().lower()
        for row in profiles:
            rid = str(row.get("id") or (row.get("profile") or {}).get("id") or "").lower()
            if rid == pid:
                return _normalize_ums_profile(row)
        return _normalize_ums_profile(profiles[0]) if profiles else None

    def get_user_by_idp_user_id(
        self,
        idp_user_id: str,
        *,
        correlation_id: str,
    ) -> dict[str, Any] | None:
        """Fetch a UMS user by Auth0 idpUserId (used after deleteProfiles).

        Mirrors the resolver's ``getUserByIdpUserId`` — when a profile has already
        been deleted the enricher rehydrates ``deletedProfiles[].user`` from this
        endpoint instead of the profiles API.
        """
        if not idp_user_id:
            return None
        url = f"{self._cfg.ums_base_url}/api/v3/users"
        params = {
            "idpUserId": idp_user_id,
            # UMS rejects ``userId`` in projection — allowed: idpUserId, email,
            # firstName, lastName, profiles, meta.*, …
            "projection": "idpUserId,firstName,lastName,email,profiles",
        }
        resp = self._session.get(
            url, headers=self._headers(correlation_id), params=params, timeout=30
        )
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            return data[0] if data and isinstance(data[0], dict) else None
        if isinstance(data, dict):
            inner = data.get("data") if isinstance(data.get("data"), dict) else data
            for key in ("users", "user"):
                val = inner.get(key) if isinstance(inner, dict) else None
                if isinstance(val, list) and val and isinstance(val[0], dict):
                    return val[0]
                if isinstance(val, dict):
                    return val
            if inner.get("idpUserId") or inner.get("userId"):
                return inner
        return None

    def get_invitation_by_email(
        self,
        email: str,
        customer_id: str = "",
        *,
        correlation_id: str = "",
    ) -> dict[str, Any] | None:
        """Lookup invitation row by email (MySQL when configured)."""
        del correlation_id
        em = str(email or "").strip()
        if not em:
            return None
        try:
            from .db.connection import load_mysql_config, mysql_ready
            from .db.clients import UmsDbClient

            mysql = load_mysql_config()
            if mysql_ready(mysql):
                return UmsDbClient(mysql).get_invitation_by_email(
                    em, customer_id, correlation_id=correlation_id
                )
        except Exception:
            pass
        return None

    def get_profiles_by_ids(
        self,
        profile_ids: list[str],
        customer_id: str,
        *,
        correlation_id: str,
        user_type: str = "service",
    ) -> list[dict[str, Any]]:
        """Fetch multiple profiles (service-account snapshots use userType=service)."""
        rows = self._profiles_post_as_get(
            customer_id, profile_ids, correlation_id=correlation_id, user_type=user_type
        )
        return [_normalize_ums_profile(r) for r in rows]

    def get_teams_by_ids(
        self,
        team_ids: list[str],
        customer_id: str,
        *,
        correlation_id: str,
    ) -> list[dict[str, Any]]:
        """Fetch teams via the proven plain GET (curl #2). Returns [] on empty input."""
        ids = [str(t).strip() for t in team_ids if str(t or "").strip()]
        if not ids:
            return []
        gcid = customer_id or self._cfg.gcid
        url = f"{self._cfg.ums_base_url}/api/v3/customers/{gcid}/teams"
        params = {
            "projection": _UMS_TEAM_PROJECTION,
            "teamIds__in": ",".join(ids),
            "isProfileActive__eq": "true",
            "skip": 0,
            "limit": -1,
        }
        resp = self._session.get(
            url, headers=self._headers(correlation_id), params=params, timeout=30
        )
        resp.raise_for_status()
        data = resp.json()
        rows = data.get("data") if isinstance(data, dict) else data
        if isinstance(rows, dict):
            rows = rows.get("teams") or rows.get("data")
        return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


class CmsClient:
    def __init__(self, cfg: SourceValidationConfig) -> None:
        self._cfg = cfg
        self._session = requests.Session()

    def get_customer_by_id(
        self,
        customer_id: str,
        *,
        correlation_id: str,
    ) -> dict[str, Any] | None:
        gcid = customer_id or self._cfg.gcid
        url = f"{self._cfg.cms_base_url}/api/v2/customers/{gcid}"
        headers = {
            "accept": "application/json",
            "x-client-id": self._cfg.cms_client_id,
            "x-correlation-id": _correlation_id(correlation_id),
        }
        params = {
            "projection": _CMS_PROJECTION,
            "subscriptionFields": _CMS_SUBSCRIPTION_FIELDS,
            "application": "MTConnect",
        }
        resp = self._session.get(url, headers=headers, params=params, timeout=30)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict):
            return data.get("data") or data
        return None

    def get_customer(self, *, correlation_id: str) -> dict[str, Any] | None:
        return self.get_customer_by_id(self._cfg.gcid, correlation_id=correlation_id)


# Asset-type remap + per-type projections — copied from the resolver
# (ams-resolution.util.ts) so we fetch exactly what the enricher fetched.
_AMS_TYPE_MAP = {
    "FontList": "FontSet",
    "FontProject": "FontProject",
    "WebProject": "WebProject",
    "DigitalAd": "DigitalAd",
    "Folder": "Folder",
}

_AMS_PROJECTIONS = {
    "FontSet": (
        "accessIds,assetPath,assetType,children.accessIds,children.assetType,"
        "children.id,createdAt,lastAccessedAt,createdBy,depth,description,"
        "metaData.fontFormatSelection,metaData.isProduction,metaData.remotelyActivated,"
        "name,parentId,updatedAt"
    ),
    "FontProject": (
        "name,assetType,description,createdAt,lastAccessedAt,accessIds,createdBy,parentId,"
        "assetPath,depth,hasChildren,updatedAt,metaData.status,metaData.expiryDate,"
        "metaData.publishedAt,metaData.archivedAt,metaData.allowFontAdditionsByCollaborators,"
        "metaData.allowFontDownloadsByCollaborators,metaData.allowFontImportsByCollaborators,"
        "metaData.enableProjectLevelImportedFonts,metaData.autoActivateFontsForMembers"
    ),
    "WebProject": (
        "accessIds,assetPath,assetType,createdAt,lastAccessedAt,createdBy,depth,domains,"
        "kitPath,metaData.contactId,metaData.isProduction,metaData.markedAutomatically,"
        "metaData.subsetInfo,metaData.v2,name,parentId,publishedCSSPath,"
        "publishedCSSPathEnhanced,publishedJSEnhanced,publishStatus,size,updatedAt,isArchived"
    ),
    "Folder": (
        "name,assetType,description,createdAt,lastAccessedAt,accessIds,createdBy,parentId,"
        "assetPath,depth,hasChildren,updatedAt"
    ),
}
_AMS_DEFAULT_PROJECTION = _AMS_PROJECTIONS["Folder"]


class AmsClient:
    """Asset-Management client — mirrors the resolver's ams.client.ts.

    Auth is header-based (no Bearer): x-client-id + x-authorization-override +
    x-global-user-id / x-global-customer-id. The single-asset GET does NOT need the
    AMS API key (only the sharing/bulk endpoints do).
    """

    def __init__(self, cfg: SourceValidationConfig) -> None:
        self._cfg = cfg
        self._session = requests.Session()

    @staticmethod
    def ams_asset_type(asset_type: str) -> str:
        return _AMS_TYPE_MAP.get(asset_type, asset_type or "")

    def _headers(
        self, correlation_id: str, *, global_user_id: str = "", global_customer_id: str = ""
    ) -> dict[str, str]:
        h = {
            "accept": "application/json",
            "content-type": "application/json",
            "x-client-id": self._cfg.ams_client_id,
            "x-correlation-id": _correlation_id(correlation_id),
            "x-authorization-override": "true",
        }
        if global_user_id:
            h["x-global-user-id"] = global_user_id
        if global_customer_id:
            h["x-global-customer-id"] = global_customer_id
        return h

    def get_assets_by_ids_only(
        self,
        asset_ids: list[str],
        *,
        correlation_id: str,
        global_user_id: str = "",
        global_customer_id: str = "",
    ) -> dict[str, dict[str, Any]]:
        """Bulk fetch by id only — mirrors resolver ``getAssetsByIdsOnly``."""
        ids = [str(a).strip() for a in asset_ids if str(a or "").strip()]
        if not ids:
            return {}
        url = f"{self._cfg.ams_base_url}/v2/assets/bulk"
        headers = {
            **self._headers(
                correlation_id,
                global_user_id=global_user_id,
                global_customer_id=global_customer_id,
            ),
            "X-HTTP-Method-Override": "GET",
            "x-method-overrides": "GET",
        }
        body = {
            "ids": ids,
            "fields": (
                "name,depth,createdAt,updatedAt,lastAccessedAt,accessIds,accessRight,"
                "parentId,assetType,assetPath,metaData.isProduction,metaData.v2,"
                "hasChildren,metaData.markedAutomatically,createdBy,children"
            ),
        }
        import time as _time

        last_exc: Exception | None = None
        for attempt in range(4):
            try:
                resp = self._session.post(url, headers=headers, json=body, timeout=30)
                if resp.status_code == 404:
                    return {}
                if resp.status_code in (400, 403, 429, 500, 502, 503, 504) and attempt < 3:
                    _time.sleep(0.6 * (2 ** attempt))
                    continue
                resp.raise_for_status()
                data = resp.json()
                rows = data.get("data") if isinstance(data, dict) else data
                out: dict[str, dict[str, Any]] = {}
                if isinstance(rows, list):
                    for item in rows:
                        if not isinstance(item, dict):
                            continue
                        inner = item.get("data") if isinstance(item.get("data"), dict) else item
                        aid = str(inner.get("id") or item.get("id") or "").strip()
                        if aid:
                            out[aid] = inner
                return out
            except requests.RequestException as exc:
                last_exc = exc
                if attempt < 3:
                    _time.sleep(0.6 * (2 ** attempt))
                    continue
                raise
        if last_exc:
            raise last_exc
        return {}

    def get_asset_by_id(
        self,
        asset_id: str,
        asset_type: str,
        *,
        correlation_id: str,
        global_user_id: str = "",
        global_customer_id: str = "",
    ) -> dict[str, Any] | None:
        if not asset_id:
            return None
        bulk = self.get_assets_by_ids_only(
            [asset_id],
            correlation_id=correlation_id,
            global_user_id=global_user_id,
            global_customer_id=global_customer_id,
        )
        if bulk.get(asset_id):
            return bulk[asset_id]
        ams_type = self.ams_asset_type(asset_type) or "Folder"
        type_chain = [ams_type]
        for alt in ("FontSet", "Folder", "FontProject", "WebProject", "DigitalAd"):
            if alt not in type_chain:
                type_chain.append(alt)
        import time as _time

        last_exc: Exception | None = None
        for try_type in type_chain:
            projection = _AMS_PROJECTIONS.get(try_type, _AMS_DEFAULT_PROJECTION)
            url = (
                f"{self._cfg.ams_base_url}/v2/type/{try_type}/asset/{asset_id}"
                f"?projection={projection}&limit=-1&offset=0"
            )
            headers = self._headers(
                correlation_id,
                global_user_id=global_user_id,
                global_customer_id=global_customer_id,
            )
            for attempt in range(4):
                try:
                    resp = self._session.get(url, headers=headers, timeout=30)
                    if resp.status_code == 404:
                        break
                    if resp.status_code in (400, 403, 429, 500, 502, 503, 504) and attempt < 3:
                        _time.sleep(0.6 * (2 ** attempt))
                        continue
                    resp.raise_for_status()
                    data = resp.json()
                    if isinstance(data, dict):
                        inner = data.get("data")
                        return inner if isinstance(inner, dict) else data
                    return None
                except requests.RequestException as exc:
                    last_exc = exc
                    if attempt < 3:
                        _time.sleep(0.6 * (2 ** attempt))
                        continue
                    break
        if last_exc:
            raise last_exc
        return None
