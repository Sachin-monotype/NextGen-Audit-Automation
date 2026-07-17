"""Run source validation for enriched queue-pair samples."""

from __future__ import annotations

import json
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..models import JsonDict
from .clients import DiscoveryClient
from .db.factory import build_ums_cms_ams_clients
from .compare import (
    FieldCheck,
    check_paths_present,
    validate_activate_family_discovery,
    validate_actor_ums_cms,
    validate_create_role_ums,
)
from .comparison_rows import ComparisonRow, build_comparison_rows
from .config import SourceValidationConfig, load_source_validation_config
from .discovery_budget import DiscoveryCallBudget
from .audit_events_registry import DEFAULT_AUDIT_EVENTS_XLSX, events_by_operation
from .field_specs import ALL_SAMPLE_OPERATIONS, operations_for_iteration

log = logging.getLogger(__name__)


@dataclass
class OperationSourceResult:
    operation: str
    status: str  # PASS | FAIL | SKIP
    checks: list[FieldCheck] = field(default_factory=list)
    reason: str = ""


@dataclass
class SourceValidationReport:
    iteration: int
    operations: list[OperationSourceResult]
    discovery_calls: list[str]
    comparison_rows: list[ComparisonRow] = field(default_factory=list)
    local_enrichment: dict[str, Any] = field(default_factory=dict)
    pandas_summary: dict[str, int] = field(default_factory=dict)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.operations if r.status == "PASS")

    @property
    def failed(self) -> int:
        return sum(1 for r in self.operations if r.status == "FAIL")

    @property
    def skipped(self) -> int:
        return sum(1 for r in self.operations if r.status == "SKIP")


def _load_enriched_sample(
    cfg: SourceValidationConfig,
    operation: str,
    *,
    sample_source: str | None = None,
) -> JsonDict | None:
    """Load enriched JSON — fresh E2E capture preferred over static queue-pair fixtures."""
    mode = (sample_source or cfg.sample_source or "fresh").lower()
    enriched_dir = cfg.project_root / "payload" / "enrich"
    qp_path = (
        (cfg.queue_pairs_dir / f"{operation}EnrichedJson.json")
        if cfg.queue_pairs_dir
        else None
    )

    def _from_fresh() -> JsonDict | None:
        bases = (
            enriched_dir,
            cfg.project_root / "payload" / "ingress" / "enrich",
        )
        for base in bases:
            if not base.is_dir():
                continue
            canonical = base / f"{operation}.json"
            if canonical.is_file():
                return json.loads(canonical.read_text(encoding="utf-8"))
            matches = sorted(base.glob(f"{operation}-*.json"))
            if matches:
                return json.loads(matches[-1].read_text(encoding="utf-8"))
        return None

    def _from_queue_pairs() -> JsonDict | None:
        if qp_path and qp_path.is_file():
            return json.loads(qp_path.read_text(encoding="utf-8"))
        return None

    if mode == "queue-pairs":
        return _from_queue_pairs() or _from_fresh()
    if mode == "auto":
        return _from_fresh() or _from_queue_pairs()
    return _from_fresh()


def _font_ops() -> frozenset[str]:
    registry = events_by_operation(str(DEFAULT_AUDIT_EVENTS_XLSX))
    out: set[str] = set()
    for op, spec in registry.items():
        apis = (spec.subject_apis or "").upper()
        if "D:" in apis or "DISCOVERY" in apis:
            out.add(op)
    out.update({
        "activateFamily", "activateStyle", "deactivateStyle", "activateVariation",
        "bulkActivateStyles", "bulkDeactivateStyles", "addFavoriteStyles", "addFavoriteFamilies",
        "activateList", "deActivateList", "deactivateFamilies", "deactivateVariation",
        "bulkTagStyles", "bulkUntagStyles", "addFontListStyles", "removeFontListStyles",
    })
    return frozenset(out)


def _event_spec(operation: str):
    return events_by_operation(str(DEFAULT_AUDIT_EVENTS_XLSX)).get(operation)


def _generic_structural_checks(
    operation: str, enriched: JsonDict
) -> list[FieldCheck]:
    spec = _event_spec(operation)
    subject_snap = ((enriched.get("subject") or {}).get("enrichedSnapshot") or {})
    actor_snap = ((enriched.get("actor") or {}).get("enrichedSnapshot") or {})
    checks: list[FieldCheck] = []
    if not spec:
        if subject_snap or actor_snap:
            checks.append(FieldCheck("enrichedSnapshot", "PASS", "structural", "Present"))
        return checks

    is_query = "query" in spec.category.lower() or "read" in spec.category.lower()
    if spec.enriches_subject:
        if subject_snap:
            checks.append(FieldCheck("subject.enrichedSnapshot", "PASS", "structural", "Present"))
        elif is_query:
            checks.append(
                FieldCheck("subject.enrichedSnapshot", "SKIP", "structural", "Query sample — no subject snapshot")
            )
        else:
            checks.append(FieldCheck("subject.enrichedSnapshot", "FAIL", "structural", "Missing"))

    if spec.enriches_actor:
        if actor_snap:
            checks.append(FieldCheck("actor.enrichedSnapshot", "PASS", "structural", "Present"))
        elif is_query or spec.produces == "A":
            checks.append(
                FieldCheck("actor.enrichedSnapshot", "SKIP", "structural", "Actor-only/query sample — no actor snapshot")
            )
        else:
            checks.append(FieldCheck("actor.enrichedSnapshot", "FAIL", "structural", "Missing"))
    return checks


_DISCOVERY_ID_CHUNK = 50


def _chunk_ids(ids: list[str], size: int = _DISCOVERY_ID_CHUNK) -> list[list[str]]:
    return [ids[i : i + size] for i in range(0, len(ids), size)]


def _merge_style_hits(*groups: list[dict] | None) -> list[dict]:
    merged: list[dict] = []
    seen: set[str] = set()
    for group in groups:
        for hit in group or []:
            if not isinstance(hit, dict):
                continue
            key = str(hit.get("id") or hit.get("style_id") or "").strip()
            if not key or key in seen:
                continue
            seen.add(key)
            merged.append(hit)
    return merged


def _variation_md5s_in_hits(hits: list[dict]) -> set[str]:
    return {str(h.get("md5")).strip() for h in hits if isinstance(h, dict) and h.get("md5")}


def _prefetch_discovery(
    ops: list[str],
    samples: dict[str, JsonDict],
    *,
    discovery: DiscoveryClient | None,
    cfg: SourceValidationConfig,
    budget: DiscoveryCallBudget,
) -> dict[str, Any]:
    """Batched Discovery fetch for all font family/style IDs (parallel chunks + disk cache)."""
    from .source_cache import load_pickle, save_pickle

    discovery_workers = max(1, int(os.getenv("SOURCE_VALIDATION_DISCOVERY_WORKERS", "12")))

    cache: dict[str, Any] = {}
    if not discovery or not cfg.discovery_ready:
        cache["discovery_note"] = "Discovery token missing — Typesense/middleware not queried"
        return cache

    family_ids: set[str] = set()
    style_ids: set[str] = set()
    md5s: set[str] = set()
    for op in ops:
        enriched = samples.get(op)
        if not enriched:
            continue
        # Collect font IDs from ANY operation whose enriched snapshot carries
        # fontDetails — not just the hardcoded _font_ops() set. Otherwise ops like
        # fontActivationTypeSwitched / bulkMarkAsProductionFontsRequest never get
        # their Discovery documents fetched and every font field falsely FAILs.
        snap = (enriched.get("subject") or {}).get("enrichedSnapshot") or {}
        if op not in _font_ops() and not snap.get("fontDetails"):
            continue
        family_ids.update(_family_ids_from_enriched(enriched))
        style_ids.update(_style_ids_from_enriched(enriched))
        md5s.update(_variation_md5s_from_enriched(enriched))

    if not family_ids and not style_ids:
        return cache

    ids = sorted(family_ids)
    style_id_list = sorted(style_ids)
    md5_list = sorted(md5s)
    cache_key = ",".join(ids + style_id_list + md5_list)
    key_parts = ["discovery", cache_key]
    cached = load_pickle(cfg.project_root, "discovery", key_parts)
    if isinstance(cached, dict) and cached.get("style_hits") is not None:
        cached["cache_key"] = cache_key
        cached["from_disk_cache"] = True
        return cached

    try:
        style_hits: list[dict] = []
        if budget.can_call() and ids:
            budget.record(f"POST /v1/styles familyIds=[{len(ids)}]")
            style_hits = discovery.fetch_styles_by_family_ids(
                ids,
                correlation_id="source-validation-batch",
            )
        style_batches = _chunk_ids(style_id_list)

        def _styles_batch(batch: list[str]) -> list[dict]:
            return discovery.fetch_styles_by_family_ids(
                [],
                style_ids=batch,
                correlation_id="source-validation-batch-styles",
            )

        def _vars_batch(batch: list[str]) -> list[dict]:
            return discovery.fetch_variations_by_style_ids(
                batch, correlation_id="source-validation-batch-variations"
            )

        by_style_groups: list[list[dict]] = []
        if style_batches and budget.can_call():
            # Parallel Typesense chunks — biggest win on 170+ ops.
            workers = min(discovery_workers, len(style_batches))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futs = []
                for batch in style_batches:
                    if not budget.can_call():
                        break
                    budget.record(f"POST /v1/styles styleIds=[{len(batch)}]")
                    futs.append(pool.submit(_styles_batch, batch))
                for fut in as_completed(futs):
                    try:
                        by_style_groups.append(fut.result())
                    except Exception as exc:  # noqa: BLE001
                        log.warning("Discovery style batch failed: %s", exc)

        style_hits = _merge_style_hits(style_hits, *by_style_groups)
        cache["style_hits"] = style_hits
        by_family: list[dict] = []
        by_style_var_groups: list[list[dict]] = []
        if style_batches and budget.can_call():
            workers = min(discovery_workers, len(style_batches))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futs = []
                for batch in style_batches:
                    if not budget.can_call():
                        break
                    budget.record(f"GET /v1/variations styleIds=[{len(batch)}]")
                    futs.append(pool.submit(_vars_batch, batch))
                for fut in as_completed(futs):
                    try:
                        by_style_var_groups.append(fut.result())
                    except Exception as exc:  # noqa: BLE001
                        log.warning("Discovery variation batch failed: %s", exc)
        if not by_style_var_groups and budget.can_call() and ids:
            budget.record(f"GET /v1/variations familyIds=[{len(ids)} ids]")
            by_family = discovery.fetch_variations_by_family_ids(
                ids, correlation_id="source-validation-batch"
            )
        by_style_var = _merge_variation_hits(*by_style_var_groups)
        variation_hits = _merge_variation_hits(by_family, by_style_var)
        covered_md5s = _variation_md5s_in_hits(variation_hits)
        missing_md5s = [m for m in md5_list if m not in covered_md5s]
        by_md5_groups: list[list[dict]] = []
        md5_batches = _chunk_ids(missing_md5s)
        if md5_batches and budget.can_call():
            with ThreadPoolExecutor(
                max_workers=min(discovery_workers, max(4, len(md5_batches)), len(md5_batches))
            ) as pool:
                futs = []
                for batch in md5_batches:
                    if not budget.can_call():
                        break
                    budget.record(f"GET /v1/variations md5s=[{len(batch)}]")
                    futs.append(
                        pool.submit(
                            discovery.fetch_variations_by_md5s,
                            batch,
                            correlation_id="source-validation-batch-md5",
                        )
                    )
                for fut in as_completed(futs):
                    try:
                        by_md5_groups.append(fut.result())
                    except Exception as exc:  # noqa: BLE001
                        log.warning("Discovery md5 batch failed: %s", exc)
        cache["variation_hits"] = _merge_variation_hits(variation_hits, *by_md5_groups)
        if not cache["variation_hits"] and not budget.can_call():
            cache["discovery_note"] = "Discovery budget exhausted before variations fetch"
        cache["cache_key"] = cache_key
        save_pickle(cfg.project_root, "discovery", key_parts, cache)
    except Exception as exc:
        cache["discovery_error"] = f"Discovery/Typesense error: {exc}"
        log.warning("Discovery prefetch failed: %s", exc)
    return cache


def _asset_ref_from_enriched(enriched: JsonDict) -> tuple[str | None, str | None]:
    """Asset id + type for the AMS lookup (subject snapshot → subject id fallback)."""
    subject = enriched.get("subject") or {}
    snap = subject.get("enrichedSnapshot") or {}
    asset = snap.get("asset") or {}
    if isinstance(asset, dict):
        aid = asset.get("id")
        atype = asset.get("assetType")
        if aid:
            return str(aid), (str(atype) if atype else None)
    # delete-* / no-snapshot ops carry the id on the subject envelope.
    ids = subject.get("id")
    if isinstance(ids, list) and ids:
        return str(ids[0]), (str(asset.get("assetType")) if isinstance(asset, dict) and asset.get("assetType") else None)
    if isinstance(ids, str) and ids:
        return ids, None
    return None, None


def _actor_team_ids_from_enriched(enriched: JsonDict) -> list[str]:
    """Numeric team ids from ``actor.enrichedSnapshot.user.teams[*].id``."""
    actor = enriched.get("actor") or {}
    snap = actor.get("enrichedSnapshot") or {}
    user = snap.get("user") or {}
    teams = user.get("teams") if isinstance(user, dict) else None
    out: list[str] = []
    if isinstance(teams, list):
        for t in teams:
            if isinstance(t, dict) and t.get("id") is not None:
                tid = str(t.get("id")).strip()
                if tid:
                    out.append(tid)
    return list(dict.fromkeys(out))


def _collect_identity_keys(samples: dict[str, JsonDict]) -> dict[str, set[str]]:
    """Distinct CMS/UMS/AMS ids across samples — input for bulk prefetch."""
    gcids: set[str] = set()
    profiles: set[str] = set()
    roles: set[str] = set()
    assets: set[str] = set()  # "assetId|assetType|gcid"
    # "gcid|teamId" — actor teams need UMS GET /teams (not profile.team UUID)
    teams: set[str] = set()
    for enriched in samples.values():
        actor = enriched.get("actor") or {}
        gcid = str(actor.get("globalCustomerId") or "").strip()
        if gcid:
            gcids.add(gcid)
        pid = str(actor.get("globalUserId") or "").strip()
        if pid:
            profiles.add(pid)
        snap = (actor.get("enrichedSnapshot") or {})
        for role_obj in (
            ((snap.get("user") or {}).get("role") or {}),
            (snap.get("role") or {}),
        ):
            rid = str((role_obj or {}).get("id") or "").strip()
            if rid:
                roles.add(rid)
        for tid in _actor_team_ids_from_enriched(enriched):
            if gcid:
                teams.add(f"{gcid}|{tid}")
        subj_cid = _subject_customer_id_from_enriched(enriched)
        if subj_cid:
            gcids.add(str(subj_cid))
        subject_pid = _subject_profile_id_from_enriched(enriched)
        if subject_pid:
            profiles.add(str(subject_pid))
        aid, atype = _asset_ref_from_enriched(enriched)
        if aid:
            assets.add(f"{aid}|{atype or ''}|{gcid}")
    return {
        "gcids": gcids,
        "profiles": profiles,
        "roles": roles,
        "assets": assets,
        "teams": teams,
    }


def _prefetch_identity_sources(
    samples: dict[str, JsonDict],
    *,
    ums: Any | None,
    cms: Any | None,
    ams: Any | None,
    cfg: SourceValidationConfig,
) -> dict[str, Any]:
    """One fetch per unique gcid/profile/role/asset across the whole Compare run.

    Discovery already batches in ``_prefetch_discovery``. Without this, 250 events
    would re-hit CMS/UMS/AMS ~250 times for the same Everest Admin actor.

    DB mode reuses a single MySQL connection for the batch (SSL handshake is ~3s
    each otherwise).
    """
    from .db.connection import load_mysql_config, shared_connection
    from .db.factory import source_truth_mode

    def _run() -> dict[str, Any]:
        return _prefetch_identity_sources_inner(
            samples, ums=ums, cms=cms, ams=ams, cfg=cfg
        )

    if source_truth_mode() == "db":
        try:
            with shared_connection(load_mysql_config()):
                return _run()
        except Exception as exc:  # noqa: BLE001
            log.warning("Shared MySQL prefetch failed (%s) — retrying without reuse", exc)
    return _run()


def _prefetch_identity_sources_inner(
    samples: dict[str, JsonDict],
    *,
    ums: Any | None,
    cms: Any | None,
    ams: Any | None,
    cfg: SourceValidationConfig,
) -> dict[str, Any]:
    """Actual prefetch body (may run under ``shared_connection``)."""
    from .source_cache import load_pickle, save_pickle

    cache: dict[str, Any] = {
        "cms_by_id": {},
        "ums_profile_by_id": {},
        "ums_role_by_id": {},
        "ums_team_by_id": {},
        "ams_by_id": {},
        "identity_prefetch": {},
    }
    keys = _collect_identity_keys(samples)
    cache["identity_prefetch"] = {k: sorted(v) for k, v in keys.items()}
    key_parts = [
        "identity",
        ",".join(sorted(keys["gcids"])),
        ",".join(sorted(keys["profiles"])),
        ",".join(sorted(keys["roles"])),
        ",".join(sorted(keys["assets"])),
        ",".join(sorted(keys.get("teams") or [])),
    ]
    hit = load_pickle(cfg.project_root, "identity", key_parts)
    if isinstance(hit, dict) and (
        hit.get("cms_by_id") is not None or hit.get("ums_profile_by_id") is not None
    ):
        hit["identity_prefetch"] = cache["identity_prefetch"]
        hit["from_disk_cache"] = True
        return hit

    # --- CMS ---
    if cms and cfg.cms_ready and keys["gcids"]:
        bulk = getattr(cms, "get_customers_by_ids", None)
        if callable(bulk):
            try:
                cache["cms_by_id"].update(bulk(sorted(keys["gcids"])))
            except Exception as exc:  # noqa: BLE001
                cache["cms_prefetch_error"] = str(exc)
                log.warning("CMS bulk prefetch failed: %s", exc)
        if not cache["cms_by_id"]:
            for gcid in sorted(keys["gcids"]):
                try:
                    row = cms.get_customer_by_id(gcid, correlation_id="identity-prefetch")
                    if row:
                        cache["cms_by_id"][gcid] = row
                except Exception as exc:  # noqa: BLE001
                    log.debug("CMS prefetch %s failed: %s", gcid, exc)

    # --- UMS profiles (need a customer id; use first known gcid as hint) ---
    default_gcid = next(iter(sorted(keys["gcids"])), cfg.gcid or "")
    if ums and cfg.ums_ready and keys["profiles"]:
        # Group profiles: get_profiles_by_ids exists on both HTTP and DB clients.
        bulk_p = getattr(ums, "get_profiles_by_ids", None)
        loaded: dict[str, dict] = {}
        if callable(bulk_p) and keys["profiles"]:
            try:
                rows = bulk_p(
                    sorted(keys["profiles"]),
                    default_gcid,
                    correlation_id="identity-prefetch",
                    user_type="",
                )
                for row in rows or []:
                    if isinstance(row, dict) and row.get("id"):
                        loaded[str(row["id"])] = row
            except TypeError:
                # HTTP client requires user_type=service default — retry without empty.
                try:
                    rows = bulk_p(
                        sorted(keys["profiles"]),
                        default_gcid,
                        correlation_id="identity-prefetch",
                    )
                    for row in rows or []:
                        if isinstance(row, dict) and row.get("id"):
                            loaded[str(row["id"])] = row
                except Exception as exc:  # noqa: BLE001
                    cache["ums_prefetch_error"] = str(exc)
            except Exception as exc:  # noqa: BLE001
                cache["ums_prefetch_error"] = str(exc)
                log.warning("UMS bulk profile prefetch failed: %s", exc)
        for pid in sorted(keys["profiles"]):
            if pid in loaded:
                continue
            try:
                row = ums.get_profile_by_id(pid, default_gcid, correlation_id="identity-prefetch")
                if row:
                    loaded[pid] = row
            except Exception as exc:  # noqa: BLE001
                log.debug("UMS profile prefetch %s failed: %s", pid, exc)
        cache["ums_profile_by_id"] = loaded
        for row in loaded.values():
            rid = ((row.get("role") or {}) if isinstance(row.get("role"), dict) else {}).get("id")
            if rid:
                keys["roles"].add(str(rid))

    # --- UMS roles ---
    if ums and cfg.ums_ready and keys["roles"]:
        bulk_r = getattr(ums, "get_roles_by_ids", None)
        if callable(bulk_r):
            try:
                cache["ums_role_by_id"].update(
                    bulk_r(sorted(keys["roles"]), default_gcid, correlation_id="identity-prefetch")
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("UMS bulk role prefetch failed: %s", exc)
        for rid in sorted(keys["roles"]):
            if rid in cache["ums_role_by_id"]:
                continue
            try:
                row = ums.get_role_by_id(rid, default_gcid, correlation_id="identity-prefetch")
                if row:
                    cache["ums_role_by_id"][rid] = row
            except Exception as exc:  # noqa: BLE001
                log.debug("UMS role prefetch %s failed: %s", rid, exc)

    # --- UMS teams (actor.enrichedSnapshot.user.teams[*] — numeric id + name/description)
    team_keys = keys.get("teams") or set()
    if ums and cfg.ums_ready and team_keys:
        by_gcid: dict[str, list[str]] = {}
        for key in team_keys:
            parts = str(key).split("|", 1)
            if len(parts) != 2:
                continue
            gcid_t, tid = parts[0].strip(), parts[1].strip()
            if gcid_t and tid:
                by_gcid.setdefault(gcid_t, []).append(tid)
        fetch_teams = getattr(ums, "get_teams_by_ids", None)
        if callable(fetch_teams):
            for gcid_t, tids in by_gcid.items():
                uniq = list(dict.fromkeys(tids))
                try:
                    rows = fetch_teams(
                        uniq, gcid_t, correlation_id="identity-prefetch"
                    )
                    for row in rows or []:
                        if isinstance(row, dict) and row.get("id") is not None:
                            cache["ums_team_by_id"][str(row["id"])] = row
                except Exception as exc:  # noqa: BLE001
                    log.warning("UMS teams prefetch failed for %s: %s", gcid_t, exc)

    # --- AMS assets ---
    if ams and cfg.ams_ready and keys["assets"]:
        bulk_a = getattr(ams, "get_assets_by_ids", None)
        asset_ids = sorted({a.split("|", 1)[0] for a in keys["assets"] if a})
        # Prefer a known profile id so DB ACL + AMS API accessIds resolve the same.
        default_profile = next(iter(sorted(keys["profiles"])), "")
        if callable(bulk_a) and asset_ids:
            try:
                try:
                    cache["ams_by_id"].update(
                        bulk_a(asset_ids, global_user_id=default_profile)
                    )
                except TypeError:
                    cache["ams_by_id"].update(bulk_a(asset_ids))
            except Exception as exc:  # noqa: BLE001
                log.warning("AMS bulk prefetch failed: %s", exc)
        for key in sorted(keys["assets"]):
            aid, atype, gcid = (key.split("|") + ["", ""])[:3]
            cached = cache["ams_by_id"].get(aid)
            # Re-fetch when bulk row lacks name/accessIds (needs user / projects join).
            if cached and (cached.get("name") is not None or cached.get("accessIds")):
                continue
            try:
                row = ams.get_asset_by_id(
                    aid,
                    atype or "Folder",
                    correlation_id="identity-prefetch",
                    global_customer_id=gcid,
                    global_user_id=default_profile,
                )
                if row:
                    cache["ams_by_id"][aid] = row
            except Exception as exc:  # noqa: BLE001
                log.debug("AMS prefetch %s failed: %s", aid, exc)

    log.info(
        "Identity prefetch: cms=%d profiles=%d roles=%d teams=%d assets=%d (from %d samples)",
        len(cache["cms_by_id"]),
        len(cache["ums_profile_by_id"]),
        len(cache["ums_role_by_id"]),
        len(cache.get("ums_team_by_id") or {}),
        len(cache["ams_by_id"]),
        len(samples),
    )
    try:
        save_pickle(cfg.project_root, "identity", key_parts, cache)
    except Exception:  # noqa: BLE001
        pass
    return cache


def _live_context_for_operation(
    operation: str,
    enriched: JsonDict,
    *,
    cfg: SourceValidationConfig,
    discovery_cache: dict[str, Any],
    ums: Any | None,
    cms: Any | None,
    ams: Any | None = None,
    identity_cache: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ctx = dict(discovery_cache)
    ident = identity_cache or {}
    cms_by = ident.get("cms_by_id") or {}
    ums_prof_by = ident.get("ums_profile_by_id") or {}
    ums_role_by = ident.get("ums_role_by_id") or {}
    ums_team_by = ident.get("ums_team_by_id") or {}
    ams_by = ident.get("ams_by_id") or {}
    cid = str(enriched.get("xCorrelationId") or "source-validation")
    actor = enriched.get("actor") or {}
    customer_id = str(actor.get("globalCustomerId") or cfg.gcid or "")
    global_user_id = str(actor.get("globalUserId") or "")

    if ums and cfg.ums_ready and customer_id:
        # Isolate each UMS call so a role/team failure does not wipe a successful
        # profile fetch (and leave a false "UMS lookup failed" note on PASS rows).
        subject_role_ops = {"createRole", "updateRole", "deleteRoles"}
        subject_role_fetched = False
        if operation in subject_role_ops:
            rid = _role_id_from_enriched(enriched)
            if rid:
                try:
                    ctx["ums_role"] = ums_role_by.get(rid) or ums.get_role_by_id(
                        rid, customer_id, correlation_id=cid
                    )
                    subject_role_fetched = True
                    if not ctx["ums_role"]:
                        ctx["ums_role_missing"] = f"Role {rid} not found in UMS"
                except Exception as exc:  # noqa: BLE001
                    ctx["ums_role_error"] = f"UMS role lookup failed: {exc}"
        pid = _profile_id_from_enriched(enriched)
        if pid:
            try:
                profile = ums_prof_by.get(pid) or ums.get_profile_by_id(
                    pid, customer_id, correlation_id=cid
                )
                ctx["ums_profile"] = profile
            except Exception as exc:  # noqa: BLE001
                ctx["ums_error"] = f"UMS profile lookup failed: {exc}"
                profile = None
            role_id = None
            if isinstance(profile, dict):
                role = profile.get("role") or {}
                if isinstance(role, dict):
                    role_id = role.get("id")
            if role_id and not ctx.get("ums_role") and not subject_role_fetched:
                try:
                    ctx["ums_role"] = ums_role_by.get(str(role_id)) or ums.get_role_by_id(
                        str(role_id), customer_id, correlation_id=cid
                    )
                except Exception as exc:  # noqa: BLE001
                    ctx["ums_role_error"] = f"UMS role lookup failed: {exc}"
        subject_pid = _subject_profile_id_from_enriched(enriched)
        if subject_pid and subject_pid != pid:
            try:
                ctx["ums_subject_profile"] = ums_prof_by.get(subject_pid) or ums.get_profile_by_id(
                    subject_pid, customer_id, correlation_id=cid
                )
            except Exception as exc:  # noqa: BLE001
                # Keep actor profile results — only note subject-profile failure.
                ctx.setdefault("ums_error", f"UMS subject profile lookup failed: {exc}")
        sub_prof = ctx.get("ums_subject_profile") or ctx.get("ums_profile")
        if isinstance(sub_prof, dict):
            sub_role_id = (sub_prof.get("role") or {}).get("id")
            if sub_role_id and not ctx.get("ums_subject_role"):
                try:
                    ctx["ums_subject_role"] = ums_role_by.get(str(sub_role_id)) or ums.get_role_by_id(
                        str(sub_role_id), customer_id, correlation_id=cid
                    )
                except Exception as exc:  # noqa: BLE001
                    ctx["ums_role_error"] = f"UMS subject role lookup failed: {exc}"

        # Actor teams — UMS GET /customers/{gcid}/teams (id/name/description).
        # Profile nested team.id is a UUID and must not be used for teams[i].*
        team_ids = _actor_team_ids_from_enriched(enriched)
        if team_ids:
            missing = [t for t in team_ids if t not in ums_team_by]
            if missing:
                fetch_teams = getattr(ums, "get_teams_by_ids", None)
                if callable(fetch_teams):
                    try:
                        for row in fetch_teams(
                            missing, customer_id, correlation_id=cid
                        ) or []:
                            if isinstance(row, dict) and row.get("id") is not None:
                                ums_team_by[str(row["id"])] = row
                    except Exception as exc:  # noqa: BLE001
                        ctx.setdefault(
                            "ums_teams_error", f"UMS teams lookup failed: {exc}"
                        )
            ordered: list[dict[str, Any]] = []
            for tid in team_ids:
                row = ums_team_by.get(tid)
                if isinstance(row, dict):
                    ordered.append(row)
            if ordered:
                ctx["ums_actor_teams"] = ordered

        # deleteProfiles: profile is already deleted — resolve user via idpUserId
        # from the mutation result / enriched deletedProfiles entry (resolver PR #50).
        if operation == "deleteProfiles":
            idp = _deleted_profile_idp_from_enriched(enriched)
            if idp:
                try:
                    ctx["ums_user"] = ums.get_user_by_idp_user_id(
                        idp, correlation_id=cid
                    )
                except Exception as exc:  # noqa: BLE001
                    ctx.setdefault("ums_error", f"UMS user-by-idp lookup failed: {exc}")
    elif ums and cfg.ums_ready and not customer_id:
        ctx["ums_error"] = "UMS skipped: no globalCustomerId on actor"

    if cms and cfg.cms_ready and customer_id:
        try:
            ctx["cms_customer"] = cms_by.get(customer_id) or cms.get_customer_by_id(
                customer_id, correlation_id=cid
            )
        except Exception as exc:
            ctx["cms_error"] = f"CMS lookup failed: {exc}"
    elif cms and cfg.cms_ready and not customer_id:
        ctx["cms_error"] = "CMS skipped: no globalCustomerId on actor"

    # Subject customer (create/updateCustomer target) — different from the actor's customer.
    if cms and cfg.cms_ready:
        subject_cid = _subject_customer_id_from_enriched(enriched)
        if subject_cid and subject_cid != customer_id:
            try:
                ctx["cms_subject_customer"] = cms_by.get(subject_cid) or cms.get_customer_by_id(
                    subject_cid, correlation_id=cid
                )
            except Exception as exc:
                ctx["cms_subject_error"] = f"CMS subject lookup failed: {exc}"

    # Asset Management — fetch the asset the resolver enriched from, so every
    # subject.enrichedSnapshot.asset.* field can be compared against AMS (not SKIP).
    if ams and cfg.ams_ready:
        asset_id, asset_type = _asset_ref_from_enriched(enriched)
        if asset_id:
            try:
                cached_ams = ams_by.get(asset_id)
                # Prefer a live fetch when the cache miss / incomplete ACL projection.
                incomplete = bool(
                    cached_ams
                    and global_user_id
                    and (
                        cached_ams.get("name") is None
                        or not isinstance(cached_ams.get("accessIds"), list)
                        or not cached_ams.get("accessIds")
                    )
                )
                ctx["ams_asset"] = (
                    None if incomplete else cached_ams
                ) or ams.get_asset_by_id(
                    asset_id,
                    asset_type or "Folder",
                    correlation_id=cid,
                    global_user_id=global_user_id,
                    global_customer_id=customer_id,
                )
                if not ctx.get("ams_asset"):
                    ctx["ams_error"] = f"AMS asset {asset_id} not found"
            except Exception as exc:
                ctx["ams_error"] = f"AMS lookup failed: {exc}"
    return ctx


def _valid_discovery_family_id(value: object) -> bool:
    fid = str(value or "").strip()
    if not fid or fid.upper() in {"N/A", "NA", "NULL", "NONE"}:
        return False
    return True


def _valid_style_id(value: object) -> bool:
    sid = str(value or "").strip()
    if not sid or sid.upper() in {"N/A", "NA", "NULL", "NONE"}:
        return False
    return True


def _family_ids_from_enriched(enriched: JsonDict) -> list[str]:
    from .discovery_resolver import font_context

    ctx = font_context(enriched)
    subject = enriched.get("subject") or {}
    meta = subject.get("metadata") or {}
    inp = meta.get("input") or {}
    ids = inp.get("familyIds") or subject.get("id") or []
    out: list[str] = []
    for x in ids if isinstance(ids, list) else [ids]:
        if _valid_discovery_family_id(x):
            out.append(str(x))
    if ctx.get("family_id") and _valid_discovery_family_id(ctx["family_id"]):
        out.append(str(ctx["family_id"]))
    # Also scan enrichedSnapshot fontDetails
    snap = subject.get("enrichedSnapshot") or {}
    for fd in snap.get("fontDetails") or []:
        if isinstance(fd, dict):
            fam = fd.get("family") or {}
            if isinstance(fam, dict) and _valid_discovery_family_id(fam.get("id")):
                out.append(str(fam["id"]))
    return list(dict.fromkeys(out))


def _style_ids_from_enriched(enriched: JsonDict) -> list[str]:
    from .discovery_resolver import font_context

    ctx = font_context(enriched)
    out: list[str] = []
    if ctx.get("style_id") and _valid_style_id(ctx["style_id"]):
        out.append(str(ctx["style_id"]))
    subject = enriched.get("subject") or {}
    for sid in subject.get("styleIds") or []:
        if _valid_style_id(sid):
            out.append(str(sid))
    snap = subject.get("enrichedSnapshot") or {}
    for fd in snap.get("fontDetails") or []:
        if not isinstance(fd, dict):
            continue
        for st in fd.get("styles") or []:
            if isinstance(st, dict):
                sid = str(st.get("id") or "").strip()
                if _valid_style_id(sid):
                    out.append(sid)
    meta = subject.get("metadata") or {}
    inp = meta.get("input") or {}
    for item in inp.get("styles") or []:
        if isinstance(item, dict):
            sid = str(item.get("styleId") or item.get("id") or "").strip()
            if _valid_style_id(sid):
                out.append(sid)
    for item in inp.get("variations") or []:
        if isinstance(item, dict):
            sid = str(item.get("styleId") or item.get("id") or "").strip()
            if _valid_style_id(sid):
                out.append(sid)
    return list(dict.fromkeys(out))


def _variation_md5s_from_enriched(enriched: JsonDict) -> list[str]:
    out: list[str] = []
    subject = enriched.get("subject") or {}
    for md5 in subject.get("md5s") or []:
        m = str(md5 or "").strip()
        if m:
            out.append(m)
    meta = subject.get("metadata") or {}
    for item in (meta.get("input") or {}).get("variations") or []:
        if isinstance(item, dict):
            m = str(item.get("md5") or "").strip()
            if m:
                out.append(m)
    snap = subject.get("enrichedSnapshot") or {}
    for fd in snap.get("fontDetails") or []:
        if not isinstance(fd, dict):
            continue
        for st in fd.get("styles") or []:
            if not isinstance(st, dict):
                continue
            for var in st.get("variations") or []:
                if not isinstance(var, dict):
                    continue
                cat = var.get("catalog") if isinstance(var.get("catalog"), dict) else var
                md5 = str((cat or {}).get("md5") or var.get("md5") or "").strip()
                if md5:
                    out.append(md5)
    return list(dict.fromkeys(out))


def _merge_variation_hits(*groups: list[dict] | None) -> list[dict]:
    merged: list[dict] = []
    seen: set[str] = set()
    for group in groups:
        for hit in group or []:
            if not isinstance(hit, dict):
                continue
            key = str(hit.get("md5") or hit.get("id") or hit.get("variation_id") or "").strip()
            if not key or key in seen:
                continue
            seen.add(key)
            merged.append(hit)
    return merged


def _role_id_from_enriched(enriched: JsonDict) -> str | None:
    subject = enriched.get("subject") or {}
    snap = subject.get("enrichedSnapshot") or {}
    role = snap.get("role") or {}
    if role.get("id"):
        return str(role["id"])
    meta = subject.get("metadata") or {}
    result = meta.get("result") or {}
    rid = ((result.get("role") or {}).get("id"))
    if rid:
        return str(rid)
    ids = subject.get("id") or []
    return str(ids[0]) if ids else None


def _profile_id_from_enriched(enriched: JsonDict) -> str | None:
    actor = enriched.get("actor") or {}
    gid = actor.get("globalUserId")
    return str(gid) if gid else None


def _subject_customer_id_from_enriched(enriched: JsonDict) -> str | None:
    """Customer id of the subject (target) for customer create/update ops."""
    subject = enriched.get("subject") or {}
    snap = subject.get("enrichedSnapshot") or {}
    cust = snap.get("customer") or {}
    if isinstance(cust, dict) and cust.get("id"):
        return str(cust["id"])
    ids = subject.get("id")
    if isinstance(ids, list) and ids:
        return str(ids[0])
    if isinstance(ids, str) and ids:
        return ids
    return None


def _subject_profile_id_from_enriched(enriched: JsonDict) -> str | None:
    subject = enriched.get("subject") or {}
    snap = subject.get("enrichedSnapshot") or {}
    user = snap.get("user") or {}
    prof = user.get("profile") or {}
    if isinstance(prof, dict) and prof.get("id"):
        return str(prof["id"])
    ids = subject.get("id")
    if isinstance(ids, list) and ids:
        return str(ids[0])
    if ids:
        return str(ids)
    return None


def _deleted_profile_idp_from_enriched(enriched: JsonDict) -> str | None:
    """idpUserId captured before deleteProfiles — used to re-fetch the user from UMS.

    Resolver enricher (PR #50 / mtconnect-api #1005) looks up
    ``GET /api/v3/users?idpUserId=…`` because the profile row is already gone.
    """
    subject = enriched.get("subject") or {}
    snap = subject.get("enrichedSnapshot") or {}
    deleted = snap.get("deletedProfiles") or []
    if isinstance(deleted, list):
        for entry in deleted:
            if not isinstance(entry, dict):
                continue
            idp = entry.get("idpUserId")
            if idp:
                return str(idp)
            user = entry.get("user") or {}
            if isinstance(user, dict) and user.get("idpUserId"):
                return str(user["idpUserId"])
    meta = subject.get("metadata") or {}
    result = meta.get("result")
    if isinstance(result, list):
        for item in result:
            if not isinstance(item, dict):
                continue
            user = item.get("user") or {}
            if isinstance(user, dict) and user.get("idpUserId"):
                return str(user["idpUserId"])
            if item.get("idpUserId"):
                return str(item["idpUserId"])
    return None


def _summarize(checks: list[FieldCheck]) -> str:
    fails = [c for c in checks if c.status == "FAIL"]
    if not fails:
        return ""
    return "; ".join(f"{c.path}: {c.message}" for c in fails[:3])


def _status_from_checks(checks: list[FieldCheck]) -> str:
    if any(c.status == "FAIL" for c in checks):
        return "FAIL"
    if checks and all(c.status in {"PASS", "SKIP"} for c in checks):
        return "PASS"
    return "SKIP"


def validate_operation(
    operation: str,
    enriched: JsonDict,
    *,
    cfg: SourceValidationConfig,
    discovery: DiscoveryClient | None,
    ums: Any | None,
    cms: Any | None,
    budget: DiscoveryCallBudget,
    discovery_cache: dict[str, Any],
) -> OperationSourceResult:
    cid = str(enriched.get("xCorrelationId") or "source-validation")
    spec = _event_spec(operation)
    checks: list[FieldCheck] = []

    if operation == "activateFamily" and cfg.discovery_ready:
        style_hits = discovery_cache.get("style_hits") or []
        var_hits = discovery_cache.get("variation_hits") or []
        if style_hits or var_hits:
            checks.extend(
                validate_activate_family_discovery(
                    enriched, style_hits=style_hits, variation_hits=var_hits
                )
            )
        elif discovery_cache.get("discovery_note"):
            checks.append(
                FieldCheck(
                    "subject.enrichedSnapshot.fontDetails",
                    "SKIP",
                    "Discovery/Typesense",
                    str(discovery_cache.get("discovery_note")),
                )
            )
        else:
            checks.append(
                FieldCheck(
                    "subject.enrichedSnapshot.fontDetails",
                    "SKIP",
                    "Discovery/Typesense",
                    "No Discovery data (token missing or no family IDs in sample)",
                )
            )

    elif operation in _font_ops():
        # Structural only in iteration 1 — Discovery budget reserved for activateFamily
        snap = ((enriched.get("subject") or {}).get("enrichedSnapshot") or {})
        if snap.get("fontDetails") or snap.get("source") or snap.get("asset"):
            checks.append(
                FieldCheck("subject.enrichedSnapshot", "PASS", "structural", "Snapshot present")
            )
        else:
            checks.append(
                FieldCheck("subject.enrichedSnapshot", "FAIL", "structural", "Missing snapshot")
            )

    elif operation in {"updateRole", "deleteRoles"} and ums and cfg.ums_ready:
        customer_id = str((enriched.get("actor") or {}).get("globalCustomerId") or cfg.gcid)
        role_id = _role_id_from_enriched(enriched)
        if role_id and customer_id:
            try:
                ums_role = ums.get_role_by_id(role_id, customer_id, correlation_id=cid)
                if ums_role:
                    checks.extend(validate_create_role_ums(enriched, ums_role=ums_role))
                else:
                    checks.append(
                        FieldCheck(
                            "subject.enrichedSnapshot.role",
                            "SKIP",
                            "UMS",
                            f"Role {role_id} not found",
                        )
                    )
            except Exception as exc:
                checks.append(
                    FieldCheck("subject.enrichedSnapshot.role", "SKIP", "UMS", str(exc))
                )

    elif operation in {
        "createProject",
        "publishProject",
        "createAsset",
        "updateAsset",
        "createWebProject",
        "activateList",
        "updateProfile",
    }:
        snap = ((enriched.get("subject") or {}).get("enrichedSnapshot") or {})
        asset = snap.get("asset") or {}
        user = snap.get("user") or {}
        if asset.get("id") or user.get("profile") or snap.get("fontDetails"):
            checks.append(FieldCheck("subject.enrichedSnapshot", "PASS", "structural", "Present"))
        else:
            checks.append(FieldCheck("subject.enrichedSnapshot", "FAIL", "structural", "Missing snapshot"))

    elif operation == "createRole" and ums and cfg.ums_ready:
        customer_id = str((enriched.get("actor") or {}).get("globalCustomerId") or cfg.gcid)
        role_id = _role_id_from_enriched(enriched)
        if role_id and customer_id:
            try:
                ums_role = ums.get_role_by_id(role_id, customer_id, correlation_id=cid)
                if ums_role:
                    checks.extend(validate_create_role_ums(enriched, ums_role=ums_role))
                else:
                    checks.append(
                        FieldCheck(
                            "subject.enrichedSnapshot.role",
                            "SKIP",
                            "UMS",
                            f"Role {role_id} not found",
                        )
                    )
            except Exception as exc:
                checks.append(
                    FieldCheck("subject.enrichedSnapshot.role", "SKIP", "UMS", str(exc))
                )

    elif operation == "createTeam":
        snap = ((enriched.get("subject") or {}).get("enrichedSnapshot") or {})
        team = snap.get("team") or {}
        if team.get("id") and team.get("name"):
            checks.append(FieldCheck("subject.enrichedSnapshot.team", "PASS", "structural", "team block present"))
        else:
            checks.append(FieldCheck("subject.enrichedSnapshot.team", "FAIL", "structural", "Missing team"))

    elif operation in {"addFavoriteStyles", "addFavoriteFamilies", "createPrivateTags"}:
        snap = ((enriched.get("subject") or {}).get("enrichedSnapshot") or {})
        if snap:
            checks.append(FieldCheck("subject.enrichedSnapshot", "PASS", "structural", "Present"))
        else:
            checks.append(FieldCheck("subject.enrichedSnapshot", "SKIP", "structural", "No snapshot in sample"))

    else:
        checks.extend(_generic_structural_checks(operation, enriched))

    # Actor cross-check (UMS + CMS) when actor snapshot exists in sample
    actor = enriched.get("actor") or {}
    customer_id = str(actor.get("globalCustomerId") or cfg.gcid or "")
    actor_snap = (actor.get("enrichedSnapshot") or {})
    if (
        (spec is None or spec.enriches_actor)
        and actor_snap
        and ums
        and cms
        and cfg.ums_ready
        and customer_id
    ):
        pid = _profile_id_from_enriched(enriched)
        try:
            profile = (
                ums.get_profile_by_id(pid, customer_id, correlation_id=cid) if pid else None
            )
            customer = (
                cms.get_customer_by_id(customer_id, correlation_id=cid)
                if cfg.cms_ready
                else None
            )
            checks.extend(validate_actor_ums_cms(enriched, ums_profile=profile, cms_customer=customer))
        except Exception as exc:
            checks.append(
                FieldCheck("actor.enrichedSnapshot", "SKIP", "UMS/CMS", f"Live lookup failed: {exc}")
            )

    status = _status_from_checks(checks)
    return OperationSourceResult(operation, status, checks, _summarize(checks))


def _customer_id_from_enriched(enriched: JsonDict, cfg: SourceValidationConfig) -> str:
    actor = enriched.get("actor") or {}
    return str(actor.get("globalCustomerId") or cfg.gcid or "")


def run_local_enrichment_validation(
    *,
    cfg: SourceValidationConfig,
    operations: list[str],
    samples: dict[str, JsonDict],
) -> dict[str, Any]:
    """Build snapshots locally (resolver parity) and compare to queue-pair enriched JSON."""
    from .local_enrichment import enrich_event
    from .local_enrichment.types import EnrichmentClients
    from .pandas_compare import compare_enriched_snapshots

    discovery = DiscoveryClient(cfg) if cfg.discovery_ready else None
    ums, cms, _ams_unused, truth = build_ums_cms_ams_clients(cfg)
    # Local enrichment only needs discovery + ums + cms (no AMS today).
    del _ams_unused
    clients = EnrichmentClients(discovery=discovery, ums=ums, cms=cms)
    _ = truth

    out: dict[str, Any] = {"operations": {}}
    # Local enrichment parity — font + role ops only (Discovery budget)
    font_ops = {
        op for op in operations
        if _event_spec(op) and (
            "D:" in (_event_spec(op).subject_apis or "").upper()
            or op in {"activateFamily", "createRole", "createTeam"}
        )
    }
    for op in operations:
        if op not in font_ops:
            continue
        enriched = samples.get(op)
        if not enriched:
            continue
        local = enrich_event(op, enriched, clients=clients)
        op_report: dict[str, Any] = {"errors": local.errors}
        if local.subject_snapshot:
            expected_sub = ((enriched.get("subject") or {}).get("enrichedSnapshot")) or {}
            op_report["subject_mismatches"] = compare_enriched_snapshots(
                expected=expected_sub, actual=local.subject_snapshot, prefix="subject"
            )
        if local.actor_snapshot:
            expected_act = ((enriched.get("actor") or {}).get("enrichedSnapshot")) or {}
            op_report["actor_mismatches"] = compare_enriched_snapshots(
                expected=expected_act, actual=local.actor_snapshot, prefix="actor"
            )
        out["operations"][op] = op_report
    return out


def run_source_validation(
    *,
    project_root: Path | None = None,
    operations: list[str] | None = None,
    iteration: int = 1,
    sample_source: str | None = None,
    progress: Callable[[str], None] | None = None,
    on_operation_rows: Callable[[str, list[ComparisonRow]], None] | None = None,
    field_paths_by_op: dict[str, list[str]] | None = None,
) -> SourceValidationReport:
    cfg = load_source_validation_config(project_root)
    src_mode = (sample_source or cfg.sample_source or "fresh").lower()
    ops = operations or list(operations_for_iteration(iteration, project_root=cfg.project_root))

    def _emit(msg: str) -> None:
        if progress:
            try:
                progress(msg)
            except Exception:  # noqa: BLE001 — progress must never break validation
                pass

    discovery = DiscoveryClient(cfg) if cfg.discovery_ready else None
    ums, cms, ams, truth_mode = build_ums_cms_ams_clients(cfg)
    _emit(f"Source truth: {truth_mode} (UMS/CMS/AMS); Typesense stays on HTTP")
    budget = DiscoveryCallBudget(cfg.max_discovery_calls_per_iteration)
    discovery_cache: dict[str, Any] = {}

    samples: dict[str, JsonDict] = {}
    for op in ops:
        enriched = _load_enriched_sample(cfg, op, sample_source=src_mode)
        if enriched:
            samples[op] = enriched

    _emit(f"▸ Prefetching Discovery/Typesense for {len(samples)} sample(s)…")
    discovery_cache.update(
        _prefetch_discovery(ops, samples, discovery=discovery, cfg=cfg, budget=budget)
    )
    _emit(f"▸ Prefetching unique CMS/UMS/AMS identities…")
    identity_cache = _prefetch_identity_sources(
        samples, ums=ums, cms=cms, ams=ams, cfg=cfg
    )
    stats = identity_cache.get("identity_prefetch") or {}
    _emit(
        "  … unique "
        f"gcids={len(stats.get('gcids') or [])} "
        f"profiles={len(stats.get('profiles') or [])} "
        f"roles={len(stats.get('roles') or [])} "
        f"assets={len(stats.get('assets') or [])}"
    )

    results: list[OperationSourceResult] = []
    all_rows: list[ComparisonRow] = []
    live_by_op: dict[str, dict[str, Any]] = {}
    with_samples = [op for op in ops if samples.get(op)]
    total = len(with_samples)
    compare_workers = max(1, int(os.getenv("SOURCE_VALIDATION_COMPARE_WORKERS", "8")))
    progress_lock = threading.Lock()

    _emit(
        f"▸ Building live context for {total} operation(s) "
        f"(workers={compare_workers})…"
    )
    done = 0

    def _build_live(op: str) -> tuple[str, dict[str, Any]]:
        return op, _live_context_for_operation(
            op,
            samples[op],
            cfg=cfg,
            discovery_cache=discovery_cache,
            ums=ums,
            cms=cms,
            ams=ams,
            identity_cache=identity_cache,
        )

    if with_samples:
        with ThreadPoolExecutor(max_workers=min(compare_workers, len(with_samples))) as pool:
            futs = [pool.submit(_build_live, op) for op in with_samples]
            for fut in as_completed(futs):
                op, live = fut.result()
                live_by_op[op] = live
                with progress_lock:
                    done += 1
                    if done == 1 or done % 10 == 0 or done == total:
                        _emit(f"  … source context {done}/{total} ({op})")

    _emit(f"▸ Comparing fields for {total} operation(s) (workers={compare_workers})…")
    done = 0
    compare_results: dict[str, OperationSourceResult] = {}
    rows_by_op: dict[str, list[ComparisonRow]] = {}

    def _compare_one(op: str) -> tuple[str, list[ComparisonRow], OperationSourceResult]:
        enriched = samples[op]
        live = dict(live_by_op.get(op, {}))
        # GraphQL mutation response + trigger context captured at generate time
        gql_path = cfg.project_root / "payload" / "graphql" / f"{op}.json"
        if gql_path.is_file():
            try:
                live["graphql_response"] = json.loads(gql_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        try:
            from audit_validator.simulation.trigger_context import load_trigger_context
            from audit_validator.auth import jwt_identity, resolve_our_profile_id

            trigger = load_trigger_context(cfg.project_root, op)
            if trigger:
                live["trigger"] = trigger
                if not live.get("graphql_response") and isinstance(trigger.get("graphql_response"), dict):
                    live["graphql_response"] = trigger["graphql_response"]
                if isinstance(trigger.get("jwt_identity"), dict) and trigger["jwt_identity"]:
                    live["jwt_identity"] = trigger["jwt_identity"]
            if "jwt_identity" not in live:
                live["jwt_identity"] = jwt_identity()
            pid = resolve_our_profile_id(project_root=cfg.project_root)
            if pid:
                live["our_profile_id"] = pid
        except Exception:
            pass
        paths = (field_paths_by_op or {}).get(op) if field_paths_by_op else None
        rows = build_comparison_rows(
            op, enriched, live=live, field_paths=paths
        )
        op_result = validate_operation(
            op,
            enriched,
            cfg=cfg,
            discovery=discovery,
            ums=ums,
            cms=cms,
            budget=budget,
            discovery_cache=discovery_cache,
        )
        return op, rows, op_result

    if with_samples:
        with ThreadPoolExecutor(max_workers=min(compare_workers, len(with_samples))) as pool:
            futs = [pool.submit(_compare_one, op) for op in with_samples]
            for fut in as_completed(futs):
                op, rows, op_result = fut.result()
                rows_by_op[op] = rows
                compare_results[op] = op_result
                if on_operation_rows:
                    try:
                        on_operation_rows(op, rows)
                    except Exception as exc:  # noqa: BLE001
                        log.warning("on_operation_rows(%s) failed: %s", op, exc)
                with progress_lock:
                    done += 1
                    if done == 1 or done % 10 == 0 or done == total:
                        _emit(f"  … compared {done}/{total} ({op})")

    # Preserve input operation order for stable reports / UI.
    for op in ops:
        if op in rows_by_op:
            all_rows.extend(rows_by_op[op])
        if op in compare_results:
            results.append(compare_results[op])
        elif op not in samples:
            results.append(
                OperationSourceResult(op, "SKIP", [], f"No enriched sample for {op}")
            )

    local_enrichment: dict[str, Any] = {}
    try:
        local_enrichment = run_local_enrichment_validation(
            cfg=cfg, operations=ops, samples=samples
        )
    except Exception as exc:  # noqa: BLE001 — parity check must never fail Compare
        log.warning("Local enrichment parity skipped: %s", exc)
        local_enrichment = {"error": str(exc)}
    pandas_summary: dict[str, int] = {}
    try:
        from .pandas_compare import export_comparison_frame, summarize_dataframe

        temp_dir = cfg.project_root / "reports" / "source-validation" / "temp"
        exported = export_comparison_frame(all_rows, out_dir=temp_dir)
        pandas_summary = summarize_dataframe(exported["dataframe"])
    except Exception as exc:
        log.warning("Pandas comparison export skipped: %s", exc)

    return SourceValidationReport(
        iteration=iteration,
        operations=results,
        discovery_calls=budget.log,
        comparison_rows=all_rows,
        local_enrichment=local_enrichment,
        pandas_summary=pandas_summary,
    )


def write_source_validation_report(report: SourceValidationReport, path: Path) -> None:
    payload = {
        "iteration": report.iteration,
        "summary": {
            "pass": report.passed,
            "fail": report.failed,
            "skip": report.skipped,
            "discovery_calls": report.discovery_calls,
            "pandas": report.pandas_summary,
        },
        "local_enrichment": report.local_enrichment,
        "operations": [
            {
                "operation": r.operation,
                "status": r.status,
                "reason": r.reason,
                "checks": [
                    {"path": c.path, "status": c.status, "source": c.expected_source, "message": c.message}
                    for c in r.checks
                ],
            }
            for r in report.operations
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
