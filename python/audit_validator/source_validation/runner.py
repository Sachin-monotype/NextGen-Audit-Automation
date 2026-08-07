"""Run source validation for enriched queue-pair samples."""

from __future__ import annotations

import json
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable

from ..auth import (
    _strip_bearer,
    ensure_discovery_user_token,
    resolve_discovery_base_url,
    resolve_excel_discovery_token,
)
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
        from ..case_keys import cron_staging_stem, parse_display_operation

        bases = (
            enriched_dir,
            cfg.project_root / "payload" / "ingress" / "enrich",
        )
        base_op, case_suffix = parse_display_operation(operation)
        stems = [operation]
        if case_suffix and base_op:
            stems.append(cron_staging_stem(base_op, case_suffix))
        if base_op and base_op not in stems:
            stems.append(base_op)
        for base in bases:
            if not base.is_dir():
                continue
            for stem in stems:
                canonical = base / f"{stem}.json"
                if canonical.is_file():
                    return json.loads(canonical.read_text(encoding="utf-8"))
                matches = sorted(base.glob(f"{stem}-*.json"))
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
        actor = enriched.get("actor") if isinstance(enriched.get("actor"), dict) else {}
        anonymous = str((actor or {}).get("authenticationState") or "").strip().lower() == "anonymous"
        if actor_snap:
            checks.append(FieldCheck("actor.enrichedSnapshot", "PASS", "structural", "Present"))
        elif anonymous:
            checks.append(
                FieldCheck(
                    "actor.enrichedSnapshot",
                    "PASS",
                    "structural",
                    "authenticationState=anonymous — no actor snapshot expected",
                )
            )
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


def _style_ids_in_hits(hits: list[dict]) -> set[str]:
    out: set[str] = set()
    for hit in hits:
        if not isinstance(hit, dict):
            continue
        sid = str(hit.get("id") or hit.get("style_id") or "").strip()
        if sid:
            out.add(sid)
    return out


def _missing_style_ids(style_hits: list[dict], style_id_list: list[str]) -> list[str]:
    covered = _style_ids_in_hits(style_hits)
    return [s for s in style_id_list if s not in covered]


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


def _note_discovery_failure(cache: dict[str, Any], exc: Exception) -> None:
    msg = str(exc).strip()
    if not msg:
        return
    label = f"Discovery/Typesense error: {msg}"
    prev = str(cache.get("discovery_error") or "")
    if not prev:
        cache["discovery_error"] = label
    elif msg not in prev:
        cache["discovery_error"] = f"{prev}; {msg}"


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
        cache["discovery_note"] = (
            "Discovery token missing — Typesense/middleware not queried "
            "(need a non-expired user SSO token in DISCOVERY_BEARER_TOKEN or "
            "NEXTGEN_BEARER_TOKEN; M2M BEARER_TOKEN is rejected with 401)"
        )
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
        from .discovery_resolver import synthesize_style_hits_from_variations

        style_hits = list(cached.get("style_hits") or [])
        # Ignore stale cache entries that saved zero style docs despite font ids to fetch.
        if not style_hits and (ids or style_id_list):
            cached = None
        else:
            synth = synthesize_style_hits_from_variations(cached.get("variation_hits") or [])
            if synth:
                style_hits = _merge_style_hits(style_hits, synth)
            missing = _missing_style_ids(style_hits, style_id_list)
            if missing and style_id_list:
                log.info(
                    "Discovery cache missing %d/%d style id(s) — refetching",
                    len(missing),
                    len(style_id_list),
                )
                cached = None
            else:
                cached["style_hits"] = style_hits
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
        # Resolver also uses POST /v1/family/{id}/styles when bulk familyIds returns empty.
        covered_families: set[str] = set()
        for hit in style_hits:
            fam = hit.get("mtc_families_data") if isinstance(hit, dict) else None
            if isinstance(fam, dict) and fam.get("id") is not None:
                covered_families.add(str(fam["id"]))
        for fid in ids:
            if fid in covered_families:
                continue
            if not budget.can_call():
                break
            route_fn = getattr(discovery, "fetch_styles_by_family_route", None)
            if not callable(route_fn):
                break
            try:
                budget.record(f"POST /v1/family/{fid}/styles")
                route_hits = route_fn(fid, correlation_id="source-validation-family-route")
                if route_hits:
                    style_hits = _merge_style_hits(style_hits, route_hits)
                    covered_families.add(fid)
            except Exception as exc:  # noqa: BLE001
                log.warning("Discovery family route %s failed: %s", fid, exc)
                _note_discovery_failure(cache, exc)
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
                        _note_discovery_failure(cache, exc)

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
                        _note_discovery_failure(cache, exc)
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
                        _note_discovery_failure(cache, exc)
        cache["variation_hits"] = _merge_variation_hits(variation_hits, *by_md5_groups)
        if not cache["variation_hits"] and not budget.can_call():
            cache["discovery_note"] = "Discovery budget exhausted before variations fetch"

        from .discovery_resolver import synthesize_style_hits_from_variations

        covered_styles = {
            str(h.get("id") or h.get("style_id") or "").strip()
            for h in style_hits
            if isinstance(h, dict) and str(h.get("id") or h.get("style_id") or "").strip()
        }
        synth = synthesize_style_hits_from_variations(cache.get("variation_hits") or [])
        if synth:
            missing_before = [s for s in style_id_list if s not in covered_styles]
            style_hits = _merge_style_hits(style_hits, synth)
            if missing_before:
                log.info(
                    "Discovery synthesized %d style doc(s) from variation mtc_styles_data",
                    len(synth),
                )
        cache["style_hits"] = style_hits
        if (ids or style_id_list) and not style_hits:
            cache["discovery_note"] = (
                "Typesense returned no style documents for requested family/style ids"
            )
        cache["cache_key"] = cache_key
        missing = _missing_style_ids(style_hits, style_id_list)
        if missing and style_id_list:
            cache["discovery_note"] = (
                f"Typesense missing {len(missing)}/{len(style_id_list)} requested style id(s)"
            )
        if style_hits and not missing:
            save_pickle(cfg.project_root, "discovery", key_parts, cache)
    except Exception as exc:
        cache["discovery_error"] = f"Discovery/Typesense error: {exc}"
        # Auth failures must not leave a stale empty pickle that masks future retries.
        err_l = str(exc).lower()
        if any(m in err_l for m in ("401", "unauthorized", "403", "forbidden")):
            try:
                from .source_cache import clear_pickle

                clear_pickle(cfg.project_root, "discovery", key_parts)
            except Exception:
                pass
        log.warning("Discovery prefetch failed: %s", exc)
    return cache


def _asset_ref_from_enriched(
    enriched: JsonDict,
    operation: str | None = None,
) -> tuple[str | None, str | None]:
    from .operation_rules import asset_ref_for_operation

    return asset_ref_for_operation(enriched, operation)


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
    for op_name, enriched in samples.items():
        actor = enriched.get("actor") or {}
        gcid = str(actor.get("globalCustomerId") or "").strip()
        if gcid:
            gcids.add(gcid)
        pid = str(actor.get("globalUserId") or "").strip()
        if pid:
            profiles.add(pid)
        snap = (actor.get("enrichedSnapshot") or {})
        subject = enriched.get("subject") or {}
        for role_obj in (
            ((snap.get("user") or {}).get("role") or {}),
            (snap.get("role") or {}),
        ):
            rid = str((role_obj or {}).get("id") or "").strip()
            if rid:
                roles.add(rid)
        subj_snap = subject.get("enrichedSnapshot") or {}
        subj_role = subj_snap.get("role") or {}
        if isinstance(subj_role, dict):
            srid = str(subj_role.get("id") or "").strip()
            if srid:
                roles.add(srid)
        subj_user = subj_snap.get("user") or {}
        subj_user_role = subj_user.get("role") if isinstance(subj_user, dict) else {}
        if isinstance(subj_user_role, dict):
            urid = str(subj_user_role.get("id") or "").strip()
            if urid:
                roles.add(urid)
        meta = subject.get("metadata") or {}
        result = meta.get("result") or {}
        role_from_result = None
        if isinstance(result, dict):
            role_from_result = (result.get("role") or {}).get("id")
        mrid = str(role_from_result or "").strip()
        if mrid:
            roles.add(mrid)
        for tid in _actor_team_ids_from_enriched(enriched):
            if gcid:
                teams.add(f"{gcid}|{tid}")
        subj_cid = _subject_customer_id_from_enriched(enriched)
        if subj_cid:
            gcids.add(str(subj_cid))
        subject_pid = _subject_profile_id_from_enriched(enriched)
        if subject_pid:
            profiles.add(str(subject_pid))
        op_name = str(op_name or enriched.get("source", {}).get("operation") or "")
        aid, atype = _asset_ref_from_enriched(enriched, op_name)
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
            import pymysql  # noqa: F401

            with shared_connection(load_mysql_config()):
                return _run()
        except ImportError:
            log.warning(
                "SOURCE_TRUTH=db but pymysql missing — identity prefetch via API/DB clients without shared connection"
            )
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
        bulk_only = getattr(ams, "get_assets_by_ids_only", None)
        asset_ids = sorted({a.split("|", 1)[0] for a in keys["assets"] if a})
        default_profile = next(iter(sorted(keys["profiles"])), "")
        default_gcid = next(iter(sorted(keys["gcids"])), "")
        if callable(bulk_only) and asset_ids:
            try:
                cache["ams_by_id"].update(
                    bulk_only(
                        asset_ids,
                        correlation_id="identity-prefetch",
                        global_user_id=default_profile,
                        global_customer_id=default_gcid,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                log.debug("AMS bulk-by-id prefetch failed: %s", exc)
        if callable(bulk_a) and asset_ids:
            try:
                try:
                    cache["ams_by_id"].update(
                        bulk_a(
                            asset_ids,
                            global_user_id=default_profile,
                            global_customer_id=default_gcid,
                        )
                    )
                except TypeError:
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


_INVITATION_OPS = frozenset({"createUserInvitations", "updateUserInvitations"})


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
    base_op = operation.split("(", 1)[0].strip() if "(" in operation else operation
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
                    ctx["ums_subject_role"] = ums_role_by.get(rid) or ums.get_role_by_id(
                        rid, customer_id, correlation_id=cid
                    )
                    subject_role_fetched = True
                    if not ctx.get("ums_subject_role"):
                        ctx["ums_role_missing"] = f"Role {rid} not found in UMS"
                except Exception as exc:  # noqa: BLE001
                    ctx["ums_role_error"] = f"UMS subject role lookup failed: {exc}"
        from .operation_rules import should_fetch_service_profile

        pid = _profile_id_from_enriched(enriched)
        if pid:
            try:
                service_actor = should_fetch_service_profile(base_op)
                if service_actor and pid not in ums_prof_by:
                    bulk_svc = getattr(ums, "get_profiles_by_ids", None)
                    if callable(bulk_svc):
                        rows = bulk_svc(
                            [pid],
                            customer_id,
                            correlation_id=cid,
                            user_type="service",
                        )
                        if rows and isinstance(rows[0], dict):
                            ums_prof_by[pid] = rows[0]
                profile = ums_prof_by.get(pid) or ums.get_profile_by_id(
                    pid,
                    customer_id,
                    correlation_id=cid,
                    user_type="service" if service_actor else None,
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
            if (
                role_id
                and not ctx.get("ums_role")
                and not subject_role_fetched
                and operation not in subject_role_ops
            ):
                try:
                    ctx["ums_role"] = ums_role_by.get(str(role_id)) or ums.get_role_by_id(
                        str(role_id), customer_id, correlation_id=cid
                    )
                except Exception as exc:  # noqa: BLE001
                    ctx["ums_role_error"] = f"UMS role lookup failed: {exc}"
        subject_pid = _subject_profile_id_from_enriched(enriched)
        if subject_pid and subject_pid != pid and base_op not in _INVITATION_OPS:
            try:
                ctx["ums_subject_profile"] = ums_prof_by.get(subject_pid) or ums.get_profile_by_id(
                    subject_pid, customer_id, correlation_id=cid
                )
            except Exception as exc:  # noqa: BLE001
                # Do NOT write ums_error — that pollutes unrelated actor UMS rows.
                ctx["ums_subject_error"] = f"UMS subject profile lookup failed: {exc}"
        if not ctx.get("ums_subject_role"):
            sub_role_id = _subject_user_role_id_from_enriched(enriched) or _role_id_from_enriched(
                enriched
            )
            if not sub_role_id:
                sub_prof = ctx.get("ums_subject_profile")
                if isinstance(sub_prof, dict):
                    sub_role_id = ((sub_prof.get("role") or {}).get("id"))
            if sub_role_id:
                try:
                    ctx["ums_subject_role"] = ums_role_by.get(str(sub_role_id)) or ums.get_role_by_id(
                        str(sub_role_id), customer_id, correlation_id=cid
                    )
                except Exception as exc:  # noqa: BLE001
                    ctx["ums_role_error"] = f"UMS subject role lookup failed: {exc}"

        # Subject team (createTeam / updateTeam) — numeric id, not a profile UUID.
        subject_team_id = _subject_team_id_from_enriched(enriched)
        if subject_team_id and not ctx.get("ums_team"):
            row = ums_team_by.get(subject_team_id)
            if not row:
                fetch_teams = getattr(ums, "get_teams_by_ids", None)
                if callable(fetch_teams):
                    try:
                        for trow in fetch_teams(
                            [subject_team_id], customer_id, correlation_id=cid
                        ) or []:
                            if isinstance(trow, dict) and trow.get("id") is not None:
                                ums_team_by[str(trow["id"])] = trow
                                row = trow
                    except Exception as exc:  # noqa: BLE001
                        ctx.setdefault(
                            "ums_team_error", f"UMS subject team lookup failed: {exc}"
                        )
            if isinstance(row, dict):
                ctx["ums_team"] = row

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
            # QA Auth0 gcid can disagree with CMS/UMS customer id. Prefer the
            # UMS profile's customerId when the JWT/actor gcid misses in CMS.
            if not ctx.get("cms_customer"):
                alt = ""
                for key in ("ums_profile", "ums_subject_profile"):
                    prof = ctx.get(key)
                    if isinstance(prof, dict):
                        alt = str(prof.get("customerId") or "").strip()
                        if alt:
                            break
                if alt and alt != str(customer_id):
                    ctx["cms_customer"] = cms_by.get(alt) or cms.get_customer_by_id(
                        alt, correlation_id=cid
                    )
                    if ctx.get("cms_customer"):
                        ctx["cms_customer_id_resolved"] = alt
                        ctx["cms_note"] = (
                            f"CMS miss for actor gcid {customer_id}; "
                            f"resolved via UMS profile.customerId={alt}"
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

    # Asset Management — resolver uses POST /v2/assets/bulk (type-agnostic), not only typed GET.
    if ams and cfg.ams_ready:
        asset_id, asset_type = _asset_ref_from_enriched(enriched, base_op)
        if asset_id:
            try:
                cached_ams = ams_by.get(asset_id)
                incomplete = bool(
                    cached_ams
                    and global_user_id
                    and (
                        cached_ams.get("name") is None
                        or not isinstance(cached_ams.get("accessIds"), list)
                        or not cached_ams.get("accessIds")
                    )
                )
                ctx["ams_asset"] = None if incomplete else cached_ams
                if not ctx.get("ams_asset"):
                    bulk_fn = getattr(ams, "get_assets_by_ids_only", None)
                    if callable(bulk_fn):
                        bulk_rows = bulk_fn(
                            [asset_id],
                            correlation_id=cid,
                            global_user_id=global_user_id,
                            global_customer_id=customer_id,
                        )
                        ctx["ams_asset"] = bulk_rows.get(asset_id)
                if not ctx.get("ams_asset"):
                    ams_type = asset_type or (
                        "WebProject" if base_op in {"downloadWebProject", "publishProject"} else "Folder"
                    )
                    ctx["ams_asset"] = ams.get_asset_by_id(
                        asset_id,
                        ams_type,
                        correlation_id=cid,
                        global_user_id=global_user_id,
                        global_customer_id=customer_id,
                    )
                if not ctx.get("ams_asset") and asset_type and asset_type != "Folder":
                    ctx["ams_asset"] = ams.get_asset_by_id(
                        asset_id,
                        "Folder",
                        correlation_id=cid,
                        global_user_id=global_user_id,
                        global_customer_id=customer_id,
                    )
                if not ctx.get("ams_asset"):
                    ctx["ams_error"] = f"AMS asset {asset_id} not found"
            except Exception as exc:
                ctx["ams_error"] = f"AMS lookup failed: {exc}"

    if base_op in _INVITATION_OPS:
        from .invitation_source import fetch_invitation_for_enriched

        inv, err = fetch_invitation_for_enriched(
            enriched, customer_id=customer_id, cfg=cfg
        )
        if inv:
            ctx["ums_invitation"] = inv
        if err:
            ctx["ums_invitation_error"] = err

    if base_op in {"updatePrivateTag", "createPrivateTags", "updatePrivateTagAssociations"}:
        tag_id = _private_tag_id_from_enriched(enriched)
        if tag_id and cfg.discovery_ready:
            try:
                disc = DiscoveryClient(cfg)
                ctx["discovery_private_tag"] = disc.fetch_private_tag_by_id(
                    tag_id, correlation_id=cid
                )
            except Exception as exc:  # noqa: BLE001
                ctx["discovery_error"] = f"Private tag lookup failed: {exc}"

    ctx["operation"] = operation
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
    rid = None
    if isinstance(result, dict):
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


def _looks_like_uuid(value: object) -> bool:
    s = str(value or "").strip()
    if len(s) != 36:
        return False
    import re

    return bool(
        re.fullmatch(
            r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
            s,
        )
    )


def _subject_profile_id_from_enriched(enriched: JsonDict) -> str | None:
    """Profile UUID for subject-user ops only — never team/asset/tag numeric ids.

    Falling back to ``subject.id`` for createTeam/updateTeam passed ``60284`` into
    ``UUID_TO_BIN`` and wiped UMS rows with a false ``subject profile lookup failed``.
    """
    subject = enriched.get("subject") or {}
    snap = subject.get("enrichedSnapshot") or {}
    if not isinstance(snap, dict):
        snap = {}
    if snap.get("invitations"):
        return None
    # Team / asset / tag subjects are not profiles.
    if snap.get("team") and not (isinstance(snap.get("user"), dict) and snap.get("user")):
        return None
    if snap.get("asset") and not (isinstance(snap.get("user"), dict) and snap.get("user")):
        return None
    if (snap.get("tags") or snap.get("privateTags")) and not (
        isinstance(snap.get("user"), dict) and snap.get("user")
    ):
        return None
    user = snap.get("user") or {}
    prof = user.get("profile") if isinstance(user, dict) else None
    if isinstance(prof, dict) and prof.get("id"):
        return str(prof["id"])
    # Only accept subject.id when it is a UUID (profile-targeted ops).
    ids = subject.get("id")
    candidates: list[object] = []
    if isinstance(ids, list):
        candidates.extend(ids)
    elif ids:
        candidates.append(ids)
    for cand in candidates:
        if _looks_like_uuid(cand):
            return str(cand).strip()
    return None


def _subject_team_id_from_enriched(enriched: JsonDict) -> str | None:
    subject = enriched.get("subject") or {}
    snap = subject.get("enrichedSnapshot") or {}
    team = snap.get("team") if isinstance(snap, dict) else None
    if isinstance(team, dict) and team.get("id") is not None:
        return str(team["id"]).strip()
    return None


def _subject_user_role_id_from_enriched(enriched: JsonDict) -> str | None:
    """Role on ``subject.enrichedSnapshot.user`` (bulkUpdateProfiles target user)."""
    subject = enriched.get("subject") or {}
    snap = subject.get("enrichedSnapshot") or {}
    user = snap.get("user") or {}
    if not isinstance(user, dict):
        return None
    role = user.get("role") or {}
    if isinstance(role, dict) and role.get("id"):
        return str(role["id"])
    return None


def _invitation_email_from_enriched(enriched: JsonDict) -> str | None:
    from .invitation_source import invitation_email_from_enriched

    return invitation_email_from_enriched(enriched)


def _private_tag_id_from_enriched(enriched: JsonDict) -> str | None:
    subject = enriched.get("subject") or {}
    snap = subject.get("enrichedSnapshot") or {}
    tags = snap.get("tags") or snap.get("privateTags") or []
    if isinstance(tags, list):
        for tag in tags:
            if isinstance(tag, dict) and tag.get("id") not in (None, ""):
                return str(tag["id"])
    meta = subject.get("metadata") or {}
    inp = meta.get("input") if isinstance(meta, dict) else {}
    if isinstance(inp, dict):
        for key in ("id", "tagId", "privateTagId"):
            val = inp.get(key)
            if val not in (None, "", []):
                return str(val)
    ids = subject.get("id")
    if isinstance(ids, list) and ids:
        return str(ids[0])
    if ids not in (None, ""):
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

    excel_tok = resolve_excel_discovery_token(cfg.project_root, ops=ops)
    minted = ensure_discovery_user_token(project_root=cfg.project_root)
    # Prefer auto-minted (or cached) user JWT; fall back to Excel auth_token.
    preferred = minted or excel_tok
    if not preferred:
        # Last chance — mint may have been skipped because env wasn't loaded yet.
        try:
            from audit_validator.auth import mint_user_password_token

            preferred = mint_user_password_token()
            if preferred:
                os.environ["DISCOVERY_BEARER_TOKEN"] = preferred
                os.environ["NEXTGEN_BEARER_TOKEN"] = preferred
                minted = preferred
                _emit("▸ Discovery/Typesense minted user JWT (last-chance password grant)")
        except Exception as exc:  # noqa: BLE001
            _emit(f"▸ Discovery/Typesense token unavailable: {exc}")
    if preferred and preferred != _strip_bearer(cfg.discovery_bearer_token):
        cfg = replace(
            cfg,
            discovery_bearer_token=preferred,
            discovery_base_url=resolve_discovery_base_url(),
        )
        src = "OAuth password grant" if minted else "Excel auth_token"
        _emit(
            f"▸ Discovery/Typesense using {src} via {cfg.discovery_base_url}"
        )
    elif cfg.discovery_ready:
        _emit(f"▸ Discovery/Typesense base={cfg.discovery_base_url}")
    else:
        _emit(
            "▸ Discovery/Typesense NOT ready — font catalog fields will FAIL auth. "
            "Set OAUTH_USERNAME/OAUTH_PASSWORD or a user SSO DISCOVERY_BEARER_TOKEN."
        )

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
        raw_ev = None
        # GraphQL mutation response + trigger context captured at generate time
        gql_path = cfg.project_root / "payload" / "graphql" / f"{op}.json"
        if gql_path.is_file():
            try:
                live["graphql_response"] = json.loads(gql_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        raw_path = cfg.project_root / "payload" / "raw" / f"{op}.json"
        if raw_path.is_file():
            try:
                raw_ev = json.loads(raw_path.read_text(encoding="utf-8"))
            except Exception:
                raw_ev = None
        try:
            from audit_validator.auth import (
                _identity_is_user,
                jwt_identity,
                jwt_identity_from_actor,
                resolve_our_profile_id,
            )
            from audit_validator.simulation.trigger_context import (
                build_trigger_from_captured_event,
                load_trigger_context,
            )

            saved_trigger = load_trigger_context(cfg.project_root, op)
            trigger = saved_trigger
            excel_note = (
                str((saved_trigger or {}).get("jwt_identity_note") or "")
                if isinstance(saved_trigger, dict)
                else ""
            )
            excel_jwt = bool(
                isinstance(saved_trigger, dict)
                and (
                    saved_trigger.get("jwt_from_excel")
                    or "Excel auth_token" in excel_note
                    or excel_note.startswith("JWT claims from Excel")
                    or bool(str(saved_trigger.get("auth_token") or "").strip())
                    or str(saved_trigger.get("capture_source") or "") == "playwright_script"
                )
                and (
                    (
                        isinstance(saved_trigger.get("jwt_identity"), dict)
                        and saved_trigger.get("jwt_identity")
                    )
                    or bool(str(saved_trigger.get("auth_token") or "").strip())
                )
            )
            ui_capture = bool(
                isinstance(trigger, dict)
                and (
                    str(trigger.get("replay_mode") or "")
                    in {"casepilot_ui", "playwright_script"}
                    or excel_jwt
                    or str(trigger.get("capture_source") or "")
                    in {"playwright_script", "casepilot_ui"}
                )
                and (
                    excel_jwt
                    or str(trigger.get("replay_mode") or "") == "playwright_script"
                    or (
                        isinstance(trigger.get("graphql_response"), dict)
                        and bool(trigger.get("graphql_response"))
                    )
                    or str(trigger.get("capture_source") or "") == "playwright_script"
                )
            )
            enrich_cid = str(enriched.get("xCorrelationId") or "").strip()
            raw_cid = str((raw_ev or {}).get("xCorrelationId") or "").strip()
            raw_mismatch = bool(
                enrich_cid and raw_cid and enrich_cid != raw_cid
            )
            if not ui_capture and isinstance(raw_ev, dict) and not raw_mismatch:
                trigger = build_trigger_from_captured_event(
                    op,
                    raw_ev,
                    enriched,
                    project_root=cfg.project_root,
                )
            elif not ui_capture and raw_mismatch:
                # Stale payload/raw on disk (common for UI runs: enrich updates, raw does not).
                trigger = build_trigger_from_captured_event(
                    op,
                    enriched,
                    enriched,
                    project_root=cfg.project_root,
                )
            elif not trigger:
                trigger = load_trigger_context(cfg.project_root, op)

            # Rebuild from raw/enrich drops Excel JWT — restore sheet identity when present.
            if excel_jwt and isinstance(saved_trigger, dict):
                if not isinstance(trigger, dict):
                    trigger = {}
                if isinstance(saved_trigger.get("jwt_identity"), dict) and saved_trigger.get(
                    "jwt_identity"
                ):
                    trigger["jwt_identity"] = saved_trigger["jwt_identity"]
                elif saved_trigger.get("auth_token"):
                    try:
                        trigger["jwt_identity"] = jwt_identity(
                            str(saved_trigger.get("auth_token"))
                        )
                    except Exception:
                        pass
                if saved_trigger.get("jwt_identity_note"):
                    trigger["jwt_identity_note"] = saved_trigger["jwt_identity_note"]
                trigger["jwt_from_excel"] = True
                if saved_trigger.get("our_profile_id"):
                    trigger["our_profile_id"] = saved_trigger["our_profile_id"]
                if saved_trigger.get("auth_token"):
                    trigger["auth_token"] = saved_trigger["auth_token"]
                saved_gql = saved_trigger.get("graphql_response")
                if isinstance(saved_gql, dict) and saved_gql:
                    trigger["graphql_response"] = saved_gql
                trigger["capture_source"] = saved_trigger.get("capture_source") or "playwright_script"
                if str(trigger.get("replay_mode") or "") in {"", "pending_raw", "None"}:
                    trigger["replay_mode"] = (
                        saved_trigger.get("replay_mode")
                        if str(saved_trigger.get("replay_mode") or "")
                        in {"casepilot_ui", "playwright_script"}
                        else "playwright_script"
                    )

            if trigger:
                live["trigger"] = trigger
                trigger_gql = (
                    trigger.get("graphql_response")
                    if isinstance(trigger.get("graphql_response"), dict)
                    else None
                )
                # Prefer Excel/Playwright Response over a stale payload/graphql file.
                if trigger_gql and (
                    trigger.get("jwt_from_excel")
                    or str(trigger.get("capture_source") or "") == "playwright_script"
                    or str(trigger.get("replay_mode") or "")
                    in {"casepilot_ui", "playwright_script"}
                ):
                    live["graphql_response"] = trigger_gql
                elif not live.get("graphql_response") and trigger_gql:
                    live["graphql_response"] = trigger_gql
                if isinstance(trigger.get("jwt_identity"), dict) and trigger["jwt_identity"]:
                    live["jwt_identity"] = trigger["jwt_identity"]
                if trigger.get("jwt_identity_note"):
                    live["jwt_identity_note"] = str(trigger.get("jwt_identity_note"))
                if trigger.get("jwt_from_excel") or "Excel auth_token" in str(
                    trigger.get("jwt_identity_note") or ""
                ):
                    live["jwt_from_excel"] = True
                if trigger.get("our_profile_id"):
                    live["our_profile_id"] = str(trigger.get("our_profile_id"))
            # Never fall back to project Bearer / BE seed JWT for Excel/UI captures.
            if "jwt_identity" not in live:
                if excel_jwt and isinstance(saved_trigger, dict):
                    if isinstance(saved_trigger.get("jwt_identity"), dict) and saved_trigger.get(
                        "jwt_identity"
                    ):
                        live["jwt_identity"] = saved_trigger["jwt_identity"]
                        live["jwt_from_excel"] = True
                    elif saved_trigger.get("auth_token"):
                        live["jwt_identity"] = jwt_identity(str(saved_trigger.get("auth_token")))
                        live["jwt_from_excel"] = True
                    else:
                        live["jwt_identity"] = {}
                        live["jwt_from_excel"] = True
                elif ui_capture:
                    # Prefer enriched actor over project seed Bearer for UI runs.
                    live["jwt_identity"] = jwt_identity_from_actor(
                        enriched.get("actor") if isinstance(enriched.get("actor"), dict) else {}
                    )
                    live["jwt_identity_note"] = (
                        "JWT claims taken from enriched actor (UI capture; no Excel token)"
                    )
                else:
                    live["jwt_identity"] = jwt_identity()
            # QA M2M tokens have no user claims — fall back to actor stamps on the event
            # so JWT Compare rows are not Source=none against a populated enriched actor.
            # Never do this when Excel auth_token supplied the identity — that would hide mismatches.
            ident = live.get("jwt_identity") if isinstance(live.get("jwt_identity"), dict) else {}
            if not live.get("jwt_from_excel") and not _identity_is_user(ident):
                from_actor = jwt_identity_from_actor(
                    enriched.get("actor") if isinstance(enriched.get("actor"), dict) else {}
                )
                if _identity_is_user(from_actor):
                    live["jwt_identity"] = from_actor
                    live["jwt_identity_note"] = (
                        "JWT claims taken from enriched actor "
                        "(active Bearer is M2M / has no user claims)"
                    )
            if not live.get("our_profile_id"):
                # Excel token path: never substitute the project logged-in profile.
                if not live.get("jwt_from_excel"):
                    pid = resolve_our_profile_id(project_root=cfg.project_root)
                    if pid:
                        live["our_profile_id"] = pid
                else:
                    # Best-effort resolve from Excel JWT idp/email only.
                    try:
                        from audit_validator.auth import jwt_identity as _jwt_ident_fn
                        from audit_validator.source_validation.clients import UmsClient
                        from audit_validator.source_validation.config import (
                            load_source_validation_config,
                        )

                        excel_ident = live.get("jwt_identity") if isinstance(live.get("jwt_identity"), dict) else {}
                        idp = str(excel_ident.get("idp_user_id") or "").strip()
                        token = ""
                        if isinstance(trigger, dict):
                            token = str(trigger.get("auth_token") or "").strip()
                        if token and not excel_ident:
                            excel_ident = _jwt_ident_fn(token)
                            live["jwt_identity"] = excel_ident
                            idp = str(excel_ident.get("idp_user_id") or "").strip()
                        if idp:
                            sv_cfg = load_source_validation_config(cfg.project_root)
                            if sv_cfg.ums_ready:
                                user = UmsClient(sv_cfg).get_user_by_idp_user_id(
                                    idp, correlation_id="compare-excel-auth-token-profile"
                                )
                                if isinstance(user, dict):
                                    gcid = str(excel_ident.get("gcid") or "")
                                    for pr in user.get("profiles") or []:
                                        if not isinstance(pr, dict):
                                            continue
                                        pid = pr.get("id") or (pr.get("profile") or {}).get("id")
                                        if not pid:
                                            continue
                                        if gcid and str(pr.get("customerId") or "") == gcid:
                                            live["our_profile_id"] = str(pid)
                                            break
                                        live.setdefault("our_profile_id", str(pid))
                    except Exception:
                        pass
        except Exception:
            pass
        # Raw envelope is for pairing only — comparison sources GraphQL trigger/response.
        live.pop("raw_event", None)
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
