"""Run all GraphQL simulation flows in Python (replaces npm run flows-only)."""

from __future__ import annotations

import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .client import DualEndpointGraphQLClient, GraphQLClient
from .config import GraphQLSimulationConfig, load_simulation_config
from ..auth import customer_context_header_id
from .flows import FLOW_REGISTRY

SERIAL_FLOW_NAMES = frozenset({"notificationRecipient"})
ENRICHMENT_SERIAL_FLOW_NAMES = frozenset({
    "fontActivation",
    "favorites",
    "fontList",
    "tags",
    "fontProject",
    "webProject",
    "fontAccess",
})
SERIAL_MUTATION_FLOW_NAMES = frozenset({"roles", "teams"})
from .graphql_loader import load_graphql_documents
from .operation_runner import FlowContext, OperationResult

log = logging.getLogger(__name__)

GET_PROFILE = """
query GetProfile {
  getProfile {
    id
    customer { id }
  }
}
"""


@dataclass
class FlowSummary:
    name: str
    status: str
    results: list[OperationResult]
    error: str | None = None


def _build_context(cfg: GraphQLSimulationConfig, flow_name: str) -> FlowContext:
    if cfg.route_mutations_to_bff:
        from ..auth import jwt_is_expired, nextgen_bearer_diagnostics, resolve_nextgen_bearer_token

        ng_raw = (cfg.nextgen_bearer_token or "").strip()
        resolved = resolve_nextgen_bearer_token()
        if ng_raw and jwt_is_expired(ng_raw) and resolved and not jwt_is_expired(resolved):
            log.warning(
                "[%s] NEXTGEN_BEARER_TOKEN expired — using valid BEARER_TOKEN for /graph mutations. "
                "Paste fresh browser SSO into NEXTGEN_BEARER_TOKEN for font enrichment parity. %s",
                flow_name,
                nextgen_bearer_diagnostics(),
            )
        elif not resolved or jwt_is_expired(resolved):
            log.error(
                "[%s] No valid bearer for NextGen /graph — mutations will 401. %s",
                flow_name,
                nextgen_bearer_diagnostics(),
            )
        client = DualEndpointGraphQLClient(cfg)
        admin = DualEndpointGraphQLClient(cfg, admin=True)
    else:
        client = GraphQLClient(cfg)
        admin = GraphQLClient(cfg, admin=True)
    profile = client.request(GET_PROFILE.strip())
    profile_id = (profile.get("getProfile") or {}).get("id") or ""
    customer_id = ((profile.get("getProfile") or {}).get("customer") or {}).get("id") or ""
    context_id = customer_context_header_id(
        use_customer_context=cfg.use_customer_context,
        customer_context_id=cfg.customer_context_id,
        profile_customer_id=customer_id,
    )
    if context_id:
        client.set_customer_id(context_id)
        admin.set_customer_id(context_id)
    nextgen = client.nextgen_client if isinstance(client, DualEndpointGraphQLClient) else GraphQLClient(
        cfg, endpoint=cfg.nextgen_endpoint
    )
    if context_id and not isinstance(client, DualEndpointGraphQLClient):
        nextgen.set_customer_id(context_id)
    log.info(
        "[%s] Profile: %s Customer: %s (context header: %s)",
        flow_name,
        profile_id or "(unknown)",
        customer_id or "(unknown)",
        context_id or "off",
    )
    return FlowContext(
        client=client,
        admin_client=admin,
        nextgen_client=nextgen,
        profile_id=profile_id,
        customer_id=customer_id,
        project_root=getattr(cfg, "project_root", None),
    )


def _run_flow(name: str, fn, cfg: GraphQLSimulationConfig) -> FlowSummary:
    try:
        ctx = _build_context(cfg, name)
    except Exception as exc:
        return FlowSummary(name=name, status="failed", results=[], error=f"Bootstrap failed: {exc}")

    try:
        fn(ctx, cfg)
        status = "completed"
    except Exception as exc:
        status = "failed"
        error = str(exc)
    else:
        error = None

    if name in ENRICHMENT_SERIAL_FLOW_NAMES or name in SERIAL_MUTATION_FLOW_NAMES:
        gap = float(os.getenv("SIMULATION_FLOW_GAP_SEC", "2"))
        if gap > 0:
            time.sleep(gap)

    if status == "failed":
        return FlowSummary(name=name, status="failed", results=ctx.results, error=error)
    return FlowSummary(name=name, status="completed", results=ctx.results)


def _filtered_registry(cfg: GraphQLSimulationConfig) -> list[tuple[str, Any]]:
    registry = [
        (name, fn) for name, fn in FLOW_REGISTRY if name not in cfg.skip_flows
    ]
    if cfg.flow_filter:
        registry = [(name, fn) for name, fn in registry if name in cfg.flow_filter]
    return registry


def run_all_flows(cfg: GraphQLSimulationConfig) -> list[FlowSummary]:
    from .seed_catalog import apply_dynamic_seeds

    cfg = apply_dynamic_seeds(cfg)
    load_graphql_documents(str(cfg.project_root))  # warm cache
    registry = _filtered_registry(cfg)
    if not registry:
        log.warning("No flows selected — check SKIP_E2E_FLOWS / flow_filter")
        return []
    summaries: list[FlowSummary] = []
    serial_first = [
        (name, fn)
        for name, fn in registry
        if name in ENRICHMENT_SERIAL_FLOW_NAMES or name in SERIAL_MUTATION_FLOW_NAMES
    ]
    parallel = [
        (name, fn)
        for name, fn in registry
        if name not in SERIAL_FLOW_NAMES
        and name not in ENRICHMENT_SERIAL_FLOW_NAMES
        and name not in SERIAL_MUTATION_FLOW_NAMES
    ]
    serial_last = [(name, fn) for name, fn in registry if name in SERIAL_FLOW_NAMES]

    for name, fn in serial_first:
        log.info("Running enrichment-serial flow %s (resolver-sensitive)", name)
        summaries.append(_run_flow(name, fn, cfg))

    max_workers = max(cfg.max_parallel_flows, 1)
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_run_flow, name, fn, cfg): name for name, fn in parallel}
        for fut in as_completed(futures):
            summaries.append(fut.result())

    for name, fn in serial_last:
        log.info("Running serial flow %s (depends on primary user context)", name)
        summaries.append(_run_flow(name, fn, cfg))

    summaries.sort(key=lambda s: s.name)
    return summaries


def write_flows_results(path: Path, summaries: list[FlowSummary]) -> None:
    payload = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "simulator": "python",
        "flows": [
            {
                "name": s.name,
                "status": s.status,
                "error": s.error,
                "results": [
                    {
                        "operation": r.operation,
                        "status": r.status,
                        "durationMs": r.duration_ms,
                        "error": r.error,
                    }
                    for r in s.results
                ],
            }
            for s in summaries
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def run_flows_simulation(
    project_root: Path,
    *,
    results_path: Path | None = None,
    flow_filter: frozenset[str] | None = None,
) -> int:
    from dataclasses import replace

    cfg = load_simulation_config(project_root)
    if flow_filter:
        cfg = replace(cfg, flow_filter=flow_filter)
    log.info(
        "Starting Python GraphQL simulation (%d flows, skip=%s, filter=%s)",
        len(_filtered_registry(cfg)),
        sorted(cfg.skip_flows),
        sorted(flow_filter) if flow_filter else None,
    )
    log.info(
        "GraphQL targets: api=%s bff=%s route_mutations_to_bff=%s",
        cfg.api_endpoint,
        cfg.nextgen_endpoint,
        cfg.route_mutations_to_bff,
    )
    summaries = run_all_flows(cfg)

    from ..report_paths import gql_flows_json

    out = results_path or gql_flows_json(project_root)
    write_flows_results(out, summaries)
    log.info("Wrote flow results to %s", out)

    any_fail = any(
        s.status == "failed" or any(r.status == "FAIL" for r in s.results)
        for s in summaries
    )
    return 1 if any_fail else 0
