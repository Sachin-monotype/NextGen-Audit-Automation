"""DB-backed CMS / UMS / AMS clients — same method surface as HTTP clients.

Shapes returned match what ``comparison_rows`` expects from the HTTP APIs
(camelCase leaves: displayName, assetType, metaData, role.id, …).

UMS note
--------
``user_management.profiles`` / ``roles`` store UUIDs as ``binary(16)``. Raw
``SELECT id`` looks like NULL in Workbench. Prefer ``vw_profile_details``
(varchar UUID columns) like MTConnectAutomation's
``UserManagementProfilesDBHelper``, or ``BIN_TO_UUID(id)``.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from .connection import MysqlConfig, load_mysql_config, select_all, select_one

log = logging.getLogger(__name__)


def _parse_json(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return {}
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            return value
    return value


def _pick(row: dict[str, Any], *names: str, default: Any = None) -> Any:
    if not row:
        return default
    lower = {str(k).lower(): k for k in row}
    for name in names:
        key = lower.get(name.lower())
        if key is not None and row.get(key) is not None:
            return row[key]
    return default


def _str(value: Any) -> str:
    return "" if value is None else str(value)


_UUID_RE = None


def _is_uuid(value: Any) -> bool:
    global _UUID_RE
    if _UUID_RE is None:
        import re

        _UUID_RE = re.compile(
            r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
        )
    s = _str(value).strip()
    return bool(s and _UUID_RE.match(s))


def _iso(value: Any) -> Any:
    """Serialize datetime-like values so JSON / Compare never choke.

    Mirror CMS/AMS API style: ``2026-06-29T06:34:38.855Z`` (ms, not µs).
    """
    if value is None:
        return None
    if hasattr(value, "isoformat") and callable(getattr(value, "isoformat")):
        try:
            # Prefer UTC wall-clock when tz-aware
            v = value
            if hasattr(v, "tzinfo") and v.tzinfo is not None and hasattr(v, "astimezone"):
                from datetime import timezone

                v = v.astimezone(timezone.utc).replace(tzinfo=None)
            s = v.isoformat(sep="T", timespec="milliseconds")
            # isoformat milliseconds may still omit 'Z' for naive datetimes
            if "T" in s:
                # Strip trailing +00:00 if present
                if s.endswith("+00:00"):
                    s = s[:-6]
                if not s.endswith("Z") and "+" not in s[10:] and not s.endswith("Z"):
                    s = s + "Z"
            return s
        except Exception:
            return str(value)
    if isinstance(value, str):
        s = value.strip()
        # Normalize bare MySQL/ISO strings that already look like datetimes
        if len(s) >= 19 and "T" in s.replace(" ", "T"):
            s = s.replace(" ", "T")
            # Drop timezone offsets → Z
            if s.endswith("+00:00"):
                s = s[:-6] + "Z"
            # Truncate/pad fractional seconds to 3 digits
            if "." in s:
                head, frac = s.split(".", 1)
                frac_digits = "".join(c for c in frac if c.isdigit())
                suffix = "Z" if frac.endswith("Z") or "Z" in frac or s.endswith("Z") else ""
                if not suffix and not s.endswith("Z"):
                    suffix = "Z"
                s = f"{head}.{frac_digits.ljust(3, '0')[:3]}{suffix or 'Z'}"
            elif not s.endswith("Z"):
                s = s + ".000Z"
            return s
    return value


def _as_bool(value: Any) -> Any:
    """Match CMS/UMS API booleans (True/False) instead of MySQL tinyint 0/1."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(int(value))
    if isinstance(value, str) and value.strip().isdigit():
        return bool(int(value.strip()))
    return value


def _subscription_is_active(row: dict[str, Any]) -> bool:
    """Mirror CMS HTTP ``subscription.isActive``.

    There is no ``is_active`` column — the API treats a non-deleted subscription
    as active only while ``termination_date`` is in the future (or null).
    Using ``NOT is_deleted`` alone falsely marks expired rows as active.
    """
    if _as_bool(_pick(row, "is_deleted", default=0)):
        return False
    term = _pick(row, "termination_date", "terminationDate")
    if term is None or term == "":
        return True
    if isinstance(term, datetime):
        if term.tzinfo is None:
            term = term.replace(tzinfo=timezone.utc)
        return term > datetime.now(timezone.utc)
    if isinstance(term, str):
        s = term.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(s)
        except ValueError:
            return True
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed > datetime.now(timezone.utc)
    return True


def _table_exists(schema: str, table: str, *, cfg: MysqlConfig) -> bool:
    row = select_one(
        """
        SELECT 1 AS ok
        FROM information_schema.TABLES
        WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
        LIMIT 1
        """,
        (schema, table),
        cfg=cfg,
    )
    return bool(row)


class CmsDbClient:
    """``customer_management.customers`` (+ subscription) — mirrors CmsClient."""

    def __init__(self, mysql: MysqlConfig | None = None) -> None:
        self._mysql = mysql or load_mysql_config()

    def get_customer_by_id(self, customer_id: str, *, correlation_id: str = "") -> dict[str, Any] | None:
        del correlation_id
        if not customer_id:
            return None
        row = select_one(
            """
            SELECT *
            FROM customer_management.customers
            WHERE id = %s
            LIMIT 1
            """,
            (customer_id,),
            cfg=self._mysql,
        )
        if not row:
            return None
        return self._to_api(row, self._subscription_for(customer_id))

    def get_customer(self, *, correlation_id: str = "") -> dict[str, Any] | None:
        del correlation_id
        return None

    def get_customers_by_ids(self, customer_ids: list[str]) -> dict[str, dict[str, Any]]:
        ids = [str(i).strip() for i in customer_ids if str(i or "").strip()]
        if not ids:
            return {}
        placeholders = ",".join(["%s"] * len(ids))
        rows = select_all(
            f"""
            SELECT *
            FROM customer_management.customers
            WHERE id IN ({placeholders})
            """,
            tuple(ids),
            cfg=self._mysql,
        )
        subs = self._subscriptions_for(ids)
        out: dict[str, dict[str, Any]] = {}
        for row in rows:
            mapped = self._to_api(row, subs.get(_str(_pick(row, "id"))))
            cid = _str(mapped.get("id"))
            if cid:
                out[cid] = mapped
        return out

    def _subscription_for(self, customer_id: str) -> dict[str, Any] | None:
        return self._subscriptions_for([customer_id]).get(customer_id)

    def _subscriptions_for(self, customer_ids: list[str]) -> dict[str, dict[str, Any]]:
        ids = [str(i).strip() for i in customer_ids if str(i or "").strip()]
        if not ids:
            return {}
        placeholders = ",".join(["%s"] * len(ids))
        try:
            rows = select_all(
                f"""
                SELECT *
                FROM customer_management.customer_subscription
                WHERE customer_id IN ({placeholders})
                  AND (is_deleted = 0 OR is_deleted IS NULL)
                ORDER BY id DESC
                """,
                tuple(ids),
                cfg=self._mysql,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("CMS subscription lookup failed: %s", exc)
            return {}
        out: dict[str, dict[str, Any]] = {}
        for row in rows:
            cid = _str(_pick(row, "customer_id", "customerId"))
            if not cid or cid in out:
                # ORDER BY id DESC → first row is newest
                continue
            plan = _parse_json(_pick(row, "plan_definition", "planDefinition"))
            out[cid] = {
                "customerId": cid,
                "planDefinition": plan if isinstance(plan, dict) else {},
                "productType": _pick(row, "product_type", "productType"),
                "seatsAvailable": _pick(row, "seats_available", "seatsAvailable"),
                "terminationDate": _iso(_pick(row, "termination_date", "terminationDate")),
                "isTrial": _as_bool(_pick(row, "is_trial", "isTrial")),
                # CMS API: not deleted AND terminationDate still in the future
                "isActive": _subscription_is_active(row),
                "createdAt": _iso(_pick(row, "created_on", "createdAt")),
            }
        return out

    @staticmethod
    def _to_api(row: dict[str, Any], subscription: dict[str, Any] | None = None) -> dict[str, Any]:
        meta = _parse_json(_pick(row, "metaData", "metadata", "meta_data"))
        out = {
            "id": _str(_pick(row, "id")),
            "name": _pick(row, "name"),
            "displayName": _pick(row, "display_name", "displayName", "displayname"),
            "source": _pick(row, "source"),
            "parentId": _pick(row, "parent_id", "parentId"),
            "identityProviderId": _pick(row, "identity_provider_id", "identityProviderId"),
            "externalId": _pick(row, "external_id", "externalId"),
            "metaData": meta if isinstance(meta, dict) else {},
            "isPreDeliveryEnabled": _as_bool(
                _pick(row, "is_predelivery_enabled", "isPreDeliveryEnabled")
            ),
            "isTestDemo": _as_bool(_pick(row, "is_test_demo", "isTestDemo")),
            "createdAt": _iso(_pick(row, "created_on", "created_at", "createdAt")),
            "modifiedAt": _iso(_pick(row, "modified_on", "modified_at", "modifiedAt")),
            "_source": "mysql:customer_management.customers",
        }
        if subscription:
            out["subscription"] = subscription
            out["_source"] = "mysql:customer_management.customers+customer_subscription"
        return out


class UmsDbClient:
    """``user_management`` via ``vw_profile_details`` + ``roles`` (binary UUID aware)."""

    def __init__(self, mysql: MysqlConfig | None = None) -> None:
        self._mysql = mysql or load_mysql_config()
        self._has_view: bool | None = None

    def _uses_view(self) -> bool:
        if self._has_view is None:
            self._has_view = _table_exists("user_management", "vw_profile_details", cfg=self._mysql)
        return self._has_view

    def get_profile_by_id(
        self,
        profile_id: str,
        customer_id: str,
        *,
        correlation_id: str = "",
    ) -> dict[str, Any] | None:
        del correlation_id
        if not profile_id:
            return None

        row: dict[str, Any] | None = None
        if self._uses_view():
            sql = """
                SELECT *
                FROM user_management.vw_profile_details
                WHERE profile_Id_uuid = %s
                  AND (is_deleted = 0 OR is_deleted IS NULL)
            """
            params: list[Any] = [profile_id]
            if customer_id:
                sql += " AND customer_id_uuid = %s"
                params.append(customer_id)
            sql += " LIMIT 1"
            row = select_one(sql, tuple(params), cfg=self._mysql)
            if not row and customer_id:
                row = select_one(
                    """
                    SELECT *
                    FROM user_management.vw_profile_details
                    WHERE profile_Id_uuid = %s
                      AND (is_deleted = 0 OR is_deleted IS NULL)
                    LIMIT 1
                    """,
                    (profile_id,),
                    cfg=self._mysql,
                )
        if not row:
            # Legacy binary(16) profiles table — same pattern as MTConnectAutomation helper.
            row = select_one(
                """
                SELECT
                  LOWER(BIN_TO_UUID(id)) AS profile_Id_uuid,
                  LOWER(BIN_TO_UUID(user_id)) AS user_id_uuid,
                  LOWER(BIN_TO_UUID(customer_id)) AS customer_id_uuid,
                  LOWER(BIN_TO_UUID(role_id)) AS role_id_uuid,
                  is_active,
                  temp_user_expiry_date,
                  created_on,
                  modified_on,
                  meta_data AS meta
                FROM user_management.profiles
                WHERE id = UUID_TO_BIN(%s)
                LIMIT 1
                """,
                (profile_id,),
                cfg=self._mysql,
            )
        if not row:
            return None
        mapped = self._profile_to_api(row)
        # View already embeds role_name — only hydrate when missing.
        role = mapped.get("role") if isinstance(mapped.get("role"), dict) else {}
        role_id = role.get("id") if role else None
        if role_id and not role.get("displayName"):
            hydrated = self.get_role_by_id(
                str(role_id), customer_id or mapped.get("customerId") or "", correlation_id="db"
            )
            if hydrated:
                mapped["role"] = {
                    "id": hydrated.get("id"),
                    "displayName": hydrated.get("displayName"),
                    "typeId": hydrated.get("typeId"),
                    "description": hydrated.get("description"),
                    "permissions": hydrated.get("permissions"),
                }
        return mapped

    def get_profiles_by_ids(
        self,
        profile_ids: list[str],
        customer_id: str,
        *,
        correlation_id: str = "",
        user_type: str = "service",
    ) -> list[dict[str, Any]]:
        """Bulk profile fetch — one SQL ``IN`` (critical for Compare prefetch)."""
        del correlation_id, user_type, customer_id
        ids = [str(p).strip() for p in profile_ids if str(p or "").strip()]
        if not ids:
            return []
        if self._uses_view():
            placeholders = ",".join(["%s"] * len(ids))
            rows = select_all(
                f"""
                SELECT *
                FROM user_management.vw_profile_details
                WHERE profile_Id_uuid IN ({placeholders})
                  AND (is_deleted = 0 OR is_deleted IS NULL)
                """,
                tuple(ids),
                cfg=self._mysql,
            )
            return [self._profile_to_api(r) for r in rows]
        # Fallback: legacy binary ids one-by-one (rare)
        out: list[dict[str, Any]] = []
        for pid in ids:
            row = self.get_profile_by_id(pid, "", correlation_id="db-bulk")
            if row:
                out.append(row)
        return out

    def get_roles_by_ids(
        self,
        role_ids: list[str],
        customer_id: str = "",
        *,
        correlation_id: str = "",
    ) -> dict[str, dict[str, Any]]:
        """Bulk roles — one SQL ``IN`` → id → role dict (with permissions)."""
        del correlation_id, customer_id
        ids = [str(r).strip() for r in role_ids if str(r or "").strip()]
        if not ids:
            return {}
        # Prefer UUID_TO_BIN(...) so the binary(16) PK/index is usable.
        ids = [i for i in ids if _is_uuid(i)]
        if not ids:
            return {}
        placeholders = ",".join(["UUID_TO_BIN(%s)"] * len(ids))
        rows = select_all(
            f"""
            SELECT
              LOWER(BIN_TO_UUID(id)) AS id,
              display_name,
              type_id,
              description,
              LOWER(BIN_TO_UUID(customer_id)) AS customer_id
            FROM user_management.roles
            WHERE id IN ({placeholders})
            """,
            tuple(ids),
            cfg=self._mysql,
        )
        perms_by_role = self._permissions_for_roles(ids)
        out: dict[str, dict[str, Any]] = {}
        for row in rows:
            rid = _str(_pick(row, "id"))
            if not rid:
                continue
            out[rid] = {
                "id": rid,
                "displayName": _pick(row, "display_name", "displayName"),
                "typeId": _pick(row, "type_id", "typeId"),
                "description": _pick(row, "description"),
                "permissions": perms_by_role.get(rid) or [],
                "_source": "mysql:user_management.roles",
            }
        return out

    def _permissions_for_roles(self, role_ids: list[str]) -> dict[str, list[dict[str, int]]]:
        """Mirror CMS API shape: permissions: [{id: 1}, {id: 2}, …]."""
        ids = [str(r).strip() for r in role_ids if _is_uuid(r)]
        if not ids:
            return {}
        placeholders = ",".join(["UUID_TO_BIN(%s)"] * len(ids))
        try:
            rows = select_all(
                f"""
                SELECT
                  LOWER(BIN_TO_UUID(role_id)) AS role_id,
                  permission_id
                FROM user_management.role_permissions_mapping
                WHERE role_id IN ({placeholders})
                ORDER BY permission_id
                """,
                tuple(ids),
                cfg=self._mysql,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("UMS role permissions lookup failed: %s", exc)
            return {}
        out: dict[str, list[dict[str, int]]] = {}
        for row in rows:
            rid = _str(_pick(row, "role_id"))
            pid = _pick(row, "permission_id")
            if not rid or pid is None:
                continue
            try:
                out.setdefault(rid, []).append({"id": int(pid)})
            except (TypeError, ValueError):
                continue
        return out

    def get_role_by_id(
        self,
        role_id: str,
        customer_id: str,
        *,
        correlation_id: str = "",
    ) -> dict[str, Any] | None:
        bulk = self.get_roles_by_ids([role_id], customer_id, correlation_id=correlation_id)
        return bulk.get(role_id)

    def get_user_by_idp_user_id(
        self,
        idp_user_id: str,
        *,
        correlation_id: str = "",
    ) -> dict[str, Any] | None:
        del correlation_id
        if not idp_user_id:
            return None
        if self._uses_view():
            rows = select_all(
                """
                SELECT *
                FROM user_management.vw_profile_details
                WHERE idp_user_id = %s
                  AND (is_deleted = 0 OR is_deleted IS NULL)
                LIMIT 20
                """,
                (idp_user_id,),
                cfg=self._mysql,
            )
            if rows:
                profiles = [self._profile_to_api(r) for r in rows]
                first = profiles[0]
                return {
                    "idpUserId": idp_user_id,
                    "firstName": first.get("firstName"),
                    "lastName": first.get("lastName"),
                    "email": first.get("email"),
                    "profiles": [
                        {"id": p.get("id"), "customerId": p.get("customerId")} for p in profiles
                    ],
                    "_source": "mysql:user_management.vw_profile_details",
                }
        try:
            row = select_one(
                """
                SELECT *
                FROM user_management.deleted_profiles
                WHERE idp_user_id = %s
                LIMIT 1
                """,
                (idp_user_id,),
                cfg=self._mysql,
            )
        except Exception:
            row = None
        if row:
            return {
                "idpUserId": idp_user_id,
                "firstName": _pick(row, "first_name", "firstName"),
                "lastName": _pick(row, "last_name", "lastName"),
                "email": _pick(row, "email"),
                "profiles": [],
                "_source": "mysql:user_management.deleted_profiles",
            }
        return None

    @staticmethod
    def _invitation_to_api(row: dict[str, Any]) -> dict[str, Any]:
        role_id = _pick(row, "RoleId", "role_id", "roleId")
        gcid = _pick(row, "GlobalCustomerId", "global_customer_id", "globalCustomerId")
        inv_id = _pick(row, "Id", "id", "invitationId")
        role_obj: dict[str, Any] = {}
        if role_id:
            role_obj = {"id": _str(role_id)}
        gcid_str = _str(gcid) if gcid else None
        return {
            "invitationId": inv_id,
            "id": inv_id,
            "email": _pick(row, "Email", "email"),
            "status": _pick(row, "Status", "status"),
            "roleId": _str(role_id) if role_id else None,
            "role": role_obj,
            "globalCustomerId": gcid_str,
            "customerId": gcid_str,
            "createdAt": _iso(_pick(row, "CreatedOn", "created_on", "createdAt")),
            "emailLocale": _pick(row, "EmailLocale", "email_locale", "emailLocale"),
            "teamIds": _parse_json(_pick(row, "TeamIds", "team_ids", "teamIds")),
            "_source": "mysql:user_management.user_invitation",
        }

    def get_invitation_by_email(
        self,
        email: str,
        customer_id: str = "",
        *,
        correlation_id: str = "",
    ) -> dict[str, Any] | None:
        """``user_management.user_invitation`` row for the invited email."""
        del correlation_id
        em = str(email or "").strip()
        if not em:
            return None
        row = select_one(
            """
            SELECT *
            FROM user_management.user_invitation
            WHERE email = %s
            LIMIT 1
            """,
            (em,),
            cfg=self._mysql,
        )
        if not row:
            return None
        mapped = self._invitation_to_api(row)
        role_id = mapped.get("roleId")
        gcid = customer_id or mapped.get("globalCustomerId") or mapped.get("customerId") or ""
        if role_id and gcid:
            hydrated = self.get_role_by_id(str(role_id), str(gcid), correlation_id="db")
            if hydrated:
                mapped["role"] = {
                    "id": hydrated.get("id"),
                    "displayName": hydrated.get("displayName"),
                    "name": hydrated.get("displayName"),
                    "permissions": hydrated.get("permissions"),
                }
        return mapped

    @staticmethod
    def _profile_to_api(row: dict[str, Any]) -> dict[str, Any]:
        role_id = _pick(row, "role_id_uuid", "role_id", "roleId")
        role_name = _pick(row, "role_name", "roleName")
        role_desc = _pick(row, "role_description", "roleDescription")
        role_obj: dict[str, Any] = {}
        if role_id:
            role_obj = {"id": _str(role_id)}
            if role_name:
                role_obj["displayName"] = role_name
            if role_desc:
                role_obj["description"] = role_desc
        meta = _parse_json(_pick(row, "meta", "meta_data", "metaData"))
        return {
            "id": _str(_pick(row, "profile_Id_uuid", "id", "profile_id")),
            "customerId": _str(_pick(row, "customer_id_uuid", "customer_id", "customerId")),
            "isActive": _as_bool(_pick(row, "is_active", "isActive")),
            "firstName": _pick(row, "first_name", "firstName"),
            "lastName": _pick(row, "last_name", "lastName"),
            "email": _pick(row, "email"),
            "idpUserId": _pick(row, "idp_user_id", "idpUserId"),
            "userId": _pick(row, "user_id_uuid", "user_id", "userId"),
            "externalUserId": _pick(row, "externaluser_id", "externalUserId"),
            "createdAt": _iso(_pick(row, "created_on", "createdAt")),
            "role": role_obj,
            "team": {},
            "meta": meta if isinstance(meta, dict) else {},
            "_source": "mysql:user_management.vw_profile_details",
        }

    def get_teams_by_ids(
        self,
        team_ids: list[str],
        customer_id: str,
        *,
        correlation_id: str = "",
    ) -> list[dict[str, Any]]:
        """``user_management.teams`` — numeric id + name/description (HTTP GET /teams)."""
        del correlation_id
        ids: list[int] = []
        for t in team_ids:
            s = str(t or "").strip()
            if s.isdigit():
                ids.append(int(s))
        if not ids:
            return []
        placeholders = ",".join(["%s"] * len(ids))
        params: list[Any] = list(ids)
        sql = f"""
            SELECT
              id,
              name,
              description,
              LOWER(BIN_TO_UUID(customer_id)) AS customerId
            FROM user_management.teams
            WHERE id IN ({placeholders})
        """
        if customer_id:
            sql += " AND customer_id = UUID_TO_BIN(%s)"
            params.append(customer_id)
        try:
            rows = select_all(sql, tuple(params), cfg=self._mysql)
        except Exception as exc:  # noqa: BLE001
            log.warning("UMS teams DB lookup failed: %s", exc)
            return []
        out: list[dict[str, Any]] = []
        for row in rows or []:
            out.append(
                {
                    "id": _pick(row, "id"),
                    "name": _pick(row, "name"),
                    "description": _pick(row, "description"),
                    "customerId": _str(_pick(row, "customerId", "customer_id")),
                    "_source": "mysql:user_management.teams",
                }
            )
        return out


class AmsDbClient:
    """``asset_management.assets`` (+ ``projects`` / access) — mirrors AmsClient.

    The ``assets`` table only stores path / created_at / meta. API fields
    ``name``, ``parentId``, ``updatedAt``, ``description`` live on
    ``projects`` (binary UUID ``id``). ``accessIds`` are effective ACL ids
    from ``asset_user_access`` on the asset + ancestors (+ SuperAdmin for
    Company Admin profiles).
    """

    def __init__(self, mysql: MysqlConfig | None = None) -> None:
        self._mysql = mysql or load_mysql_config()
        self._super_admin_by_type: dict[str, int] | None = None

    @staticmethod
    def ams_asset_type(asset_type: str) -> str | None:
        mapping = {
            "FontList": "FontSet",
            "FontProject": "FontProject",
            "WebProject": "WebProject",
            "DigitalAd": "DigitalAd",
            "Folder": "Folder",
            "FontSet": "FontSet",
            "Project": "Project",
        }
        return mapping.get(asset_type) or asset_type or None

    def get_asset_by_id(
        self,
        asset_id: str,
        asset_type: str,
        *,
        correlation_id: str = "",
        global_user_id: str = "",
        global_customer_id: str = "",
    ) -> dict[str, Any] | None:
        del correlation_id
        if not asset_id:
            return None
        sql = """
            SELECT *
            FROM asset_management.assets
            WHERE asset_id = %s
        """
        params: list[Any] = [asset_id]
        ams_type = self.ams_asset_type(asset_type or "") or ""
        if ams_type:
            sql += " AND asset_type = %s"
            params.append(ams_type)
        if global_customer_id:
            sql += " AND global_customer_id = %s"
            params.append(global_customer_id)
        sql += " LIMIT 1"
        row = select_one(sql, tuple(params), cfg=self._mysql)
        if not row and (ams_type or global_customer_id):
            row = select_one(
                "SELECT * FROM asset_management.assets WHERE asset_id = %s LIMIT 1",
                (asset_id,),
                cfg=self._mysql,
            )
        if not row:
            return None
        return self._hydrate(row, global_user_id=global_user_id)

    def get_assets_by_ids_only(
        self,
        asset_ids: list[str],
        *,
        correlation_id: str = "",
        global_user_id: str = "",
        global_customer_id: str = "",
    ) -> dict[str, dict[str, Any]]:
        """Type-agnostic bulk lookup — mirrors HTTP ``POST /v2/assets/bulk``."""
        del correlation_id, global_customer_id
        return self.get_assets_by_ids(asset_ids, global_user_id=global_user_id)

    def get_assets_by_ids(
        self,
        asset_ids: list[str],
        *,
        global_user_id: str = "",
    ) -> dict[str, dict[str, Any]]:
        ids = [str(a).strip() for a in asset_ids if str(a or "").strip()]
        if not ids:
            return {}
        placeholders = ",".join(["%s"] * len(ids))
        rows = select_all(
            f"""
            SELECT *
            FROM asset_management.assets
            WHERE asset_id IN ({placeholders})
            """,
            tuple(ids),
            cfg=self._mysql,
        )
        proj = self._projects_for(ids)
        access = self._access_ids_for(ids, global_user_id=global_user_id)
        out: dict[str, dict[str, Any]] = {}
        for row in rows:
            mapped = self._to_api(
                row,
                project=proj.get(_str(_pick(row, "asset_id"))),
                access_ids=access.get(_str(_pick(row, "asset_id"))),
            )
            aid = _str(mapped.get("id"))
            if aid:
                out[aid] = mapped
        return out

    def _hydrate(self, row: dict[str, Any], *, global_user_id: str = "") -> dict[str, Any]:
        aid = _str(_pick(row, "asset_id", "id"))
        proj = self._projects_for([aid]).get(aid) if aid else None
        access = self._access_ids_for([aid], global_user_id=global_user_id).get(aid) if aid else None
        return self._to_api(row, project=proj, access_ids=access)

    def _projects_for(self, asset_ids: list[str]) -> dict[str, dict[str, Any]]:
        ids = [str(i).strip() for i in asset_ids if _is_uuid(i)]
        if not ids:
            return {}
        placeholders = ",".join(["UUID_TO_BIN(%s)"] * len(ids))
        try:
            rows = select_all(
                f"""
                SELECT
                  LOWER(BIN_TO_UUID(id)) AS id,
                  name,
                  description,
                  CASE
                    WHEN parent_id IS NULL THEN NULL
                    ELSE LOWER(BIN_TO_UUID(parent_id))
                  END AS parent_id,
                  created_at,
                  updated_at
                FROM asset_management.projects
                WHERE id IN ({placeholders})
                """,
                tuple(ids),
                cfg=self._mysql,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("AMS projects lookup failed: %s", exc)
            return {}
        return {_str(_pick(r, "id")): r for r in rows if _pick(r, "id")}

    def _access_ids_for(
        self,
        asset_ids: list[str],
        *,
        global_user_id: str = "",
    ) -> dict[str, list[int]]:
        """Effective accessIds ≈ grants on asset + ancestors (+ SuperAdmin)."""
        ids = [str(i).strip() for i in asset_ids if str(i or "").strip()]
        if not ids:
            return {}

        placeholders = ",".join(["%s"] * len(ids))
        path_rows = select_all(
            f"""
            SELECT asset_id, asset_path, asset_type
            FROM asset_management.assets
            WHERE asset_id IN ({placeholders})
            """,
            tuple(ids),
            cfg=self._mysql,
        )
        path_by: dict[str, list[str]] = {}
        type_by: dict[str, str] = {}
        all_nodes: set[str] = set(ids)
        for row in path_rows:
            aid = _str(_pick(row, "asset_id"))
            type_by[aid] = _str(_pick(row, "asset_type"))
            raw_path = _str(_pick(row, "asset_path"))
            ancestors = [p for p in raw_path.split("|") if p]
            path_by[aid] = ancestors
            all_nodes.update(ancestors)

        grants: dict[str, list[int]] = {n: [] for n in all_nodes}
        if all_nodes and global_user_id:
            node_list = sorted(all_nodes)
            ph = ",".join(["%s"] * len(node_list))
            try:
                acc_rows = select_all(
                    f"""
                    SELECT asset_id, access_id
                    FROM asset_management.asset_user_access
                    WHERE user_id = %s
                      AND asset_id IN ({ph})
                    """,
                    (global_user_id, *node_list),
                    cfg=self._mysql,
                )
                for r in acc_rows:
                    nid = _str(_pick(r, "asset_id"))
                    aid_val = _pick(r, "access_id")
                    if nid and aid_val is not None:
                        try:
                            grants.setdefault(nid, []).append(int(aid_val))
                        except (TypeError, ValueError):
                            pass
            except Exception as exc:  # noqa: BLE001
                log.warning("AMS access lookup failed: %s", exc)

        is_company_admin = self._is_company_admin(global_user_id) if global_user_id else False
        out: dict[str, list[int]] = {}
        for aid in ids:
            ordered: list[int] = []
            seen: set[int] = set()
            for node in [aid, *path_by.get(aid, [])]:
                for acc in grants.get(node) or []:
                    if acc not in seen:
                        seen.add(acc)
                        ordered.append(acc)
            if is_company_admin:
                sa = self._super_admin_id(type_by.get(aid) or "")
                if sa is not None and sa not in seen:
                    ordered.append(sa)
            out[aid] = ordered
        return out

    def _super_admin_id(self, asset_type: str) -> int | None:
        if self._super_admin_by_type is None:
            self._super_admin_by_type = {}
            try:
                rows = select_all(
                    """
                    SELECT id, asset_type
                    FROM asset_management.access
                    WHERE name = 'SuperAdmin'
                    """,
                    cfg=self._mysql,
                )
                for r in rows:
                    self._super_admin_by_type[_str(_pick(r, "asset_type"))] = int(_pick(r, "id"))
            except Exception as exc:  # noqa: BLE001
                log.warning("AMS SuperAdmin lookup failed: %s", exc)
        return self._super_admin_by_type.get(asset_type)

    def _is_company_admin(self, profile_id: str) -> bool:
        if not profile_id:
            return False
        try:
            row = select_one(
                """
                SELECT role_name
                FROM user_management.vw_profile_details
                WHERE profile_Id_uuid = %s
                LIMIT 1
                """,
                (profile_id,),
                cfg=self._mysql,
            )
        except Exception:
            row = None
        name = _str(_pick(row or {}, "role_name")).casefold()
        return "company admin" in name or name in {"admin", "super admin"}

    def _to_api(
        self,
        row: dict[str, Any],
        *,
        project: dict[str, Any] | None = None,
        access_ids: list[int] | None = None,
    ) -> dict[str, Any]:
        meta = _parse_json(_pick(row, "meta_data", "metaData", "metadata"))
        asset_path = _pick(row, "asset_path", "assetPath")
        parent_from_path = None
        if isinstance(asset_path, str) and asset_path.strip("|"):
            parts = [p for p in asset_path.split("|") if p]
            if parts:
                parent_from_path = parts[-1]

        name = _pick(project or {}, "name") if project else None
        parent_id = _pick(project or {}, "parent_id", "parentId") if project else None
        updated = _pick(project or {}, "updated_at", "updatedAt") if project else None
        description = _pick(project or {}, "description") if project else None

        out = {
            "id": _str(_pick(row, "asset_id", "id")),
            "assetType": _pick(row, "asset_type", "assetType"),
            "createdBy": _pick(row, "created_by", "createdBy"),
            "globalCustomerId": _pick(row, "global_customer_id", "globalCustomerId"),
            "createdAt": _iso(_pick(row, "created_at", "createdAt")),
            "updatedAt": _iso(updated),
            "name": name,
            "description": description,
            "parentId": _str(parent_id) if parent_id else parent_from_path,
            "assetPath": asset_path,
            "depth": _pick(row, "asset_level", "depth", "assetLevel"),
            "accessIds": list(access_ids or []),
            "metaData": meta if isinstance(meta, dict) else {},
            "_source": "mysql:asset_management.assets+projects",
        }
        return out

