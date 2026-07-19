"""Unified catalog of everything the pipeline can generate, tagged by source kind.

Three source kinds power the Generate-page Type filter and generation routing:

- ``graphql`` — driven by a GraphQL document (mtconnect-api / NextGen ``/graph``),
  triggered through the simulation flows.
- ``ingress`` — desktop / plugin / UI events replayed through the resolver Ingress
  API (``data/ingress_payloads/``).
- ``cron``    — scheduler payloads injected onto the raw queue
  (``data/cron_payloads/``): BYOF licence, subscription/token/account expiry, LMS…

Each catalog item has a stable ``id``:
  - graphql →  the operation name (e.g. ``activateFamily``)
  - ingress →  ``ingress:<case_id>``
  - cron    →  ``cron:<case_id>``

so the Generate page can list, filter, and select them, and the backend can route
each selection to the right injector.
"""

from __future__ import annotations

from functools import lru_cache

from .enricher_registry import enrichment_expected, should_simulate
from .operation_registry import tracked_operations
from .utility.operation_graphql import get_document_for_operation, is_mutation_operation

INGRESS_PREFIX = "ingress:"
CRON_PREFIX = "cron:"

# Ops that must not appear in Generate (broken / unsupported product APIs).
EXCLUDED_GENERATE_OPS = frozenset(
    {
        "deleteAssets",
    }
)


@lru_cache(maxsize=1)
def _ingress_cases() -> list:
    try:
        from .ingress.payloads import load_ingress_cases

        return list(load_ingress_cases())
    except Exception:  # noqa: BLE001 — ingress payloads are optional
        return []


@lru_cache(maxsize=1)
def _cron_cases() -> list:
    try:
        from .cron.payloads import load_cron_cases

        return list(load_cron_cases())
    except Exception:  # noqa: BLE001 — cron payloads are optional
        return []


def source_of(operation: str) -> str:
    """Kind for a plain GraphQL operation name (used elsewhere)."""
    if get_document_for_operation(operation):
        return "graphql"
    cron_ops = {c.operation for c in _cron_cases() if getattr(c, "operation", "")}
    if operation in cron_ops:
        return "cron"
    return "ingress"


@lru_cache(maxsize=1)
def operation_source_report() -> dict:
    """Full generatable catalog + accurate per-kind counts.

    Returns::

        {
          "catalog": [{"id","label","kind","operation"}...],
          "by_operation": {op: kind},         # graphql ops only (legacy filter)
          "counts": {"graphql": N, "ingress": 30, "cron": 22},
        }
    """
    catalog: list[dict] = []
    by_operation: dict[str, str] = {}

    # GraphQL — only ops that have a document, can simulate, and expect enrichment.
    # Excludes resolver-disabled ops, opt-out raw, and bare queries that never enrich.
    gql_ops = sorted(
        op
        for op in tracked_operations()
        if op not in EXCLUDED_GENERATE_OPS
        and get_document_for_operation(op)
        and should_simulate(op)
        and enrichment_expected(op)
        and is_mutation_operation(op)
    )
    for op in gql_ops:
        # Prefer touchpoint scenarios when FLOW_DEFS defines them (Generate multi-path).
        try:
            from audit_validator.touchpoint.scenarios import scenarios_for_operation

            scenarios = scenarios_for_operation(op)
        except Exception:  # noqa: BLE001
            scenarios = []
        if scenarios:
            for sc in scenarios:
                catalog.append(
                    {
                        "id": sc["id"],
                        "label": sc["label"],
                        "kind": "graphql",
                        "operation": op,
                        "touchpoint": sc["touchpoint"],
                        "steps": sc["steps"],
                    }
                )
        else:
            catalog.append({"id": op, "label": op, "kind": "graphql", "operation": op})
        by_operation[op] = "graphql"

    # Ingress — one item per desktop/plugin payload (event).
    for case in _ingress_cases():
        cid = getattr(case, "case_id", "")
        op = getattr(case, "operation", "") or cid
        label = getattr(case, "event_name", "") or op
        catalog.append(
            {
                "id": f"{INGRESS_PREFIX}{cid}",
                "label": label,
                "kind": "ingress",
                "operation": op,
            }
        )

    # Cron — one item per scheduler payload.
    for case in _cron_cases():
        cid = getattr(case, "case_id", "")
        op = getattr(case, "operation", "") or cid
        catalog.append(
            {
                "id": f"{CRON_PREFIX}{cid}",
                "label": f"{op} ({cid})" if op != cid else cid,
                "kind": "cron",
                "operation": op,
            }
        )

    counts = {
        "graphql": len(gql_ops),
        "ingress": len(_ingress_cases()),
        "cron": len(_cron_cases()),
    }
    return {"catalog": catalog, "by_operation": by_operation, "counts": counts}


def split_selection(ids: list[str]) -> dict[str, list[str]]:
    """Split selected catalog ids into per-kind buckets the backend can route.

    Returns ``{"graphql": [op...], "ingress_cases": [case_id...], "cron_cases": [case_id...]}``.

    Prefixed ids (``ingress:`` / ``cron:``) route directly. Bare names resolve via
    the catalog: GraphQL ops stay GraphQL; unknown bare names that match an
    ingress/cron ``operation`` are routed there (so a mistyped selection still
    triggers the payload injector instead of a silent GraphQL no-op / N/A).
    """
    graphql: list[str] = []
    ingress_cases: list[str] = []
    cron_cases: list[str] = []

    report = operation_source_report()
    gql_ops = {
        c["operation"]
        for c in report.get("catalog") or []
        if c.get("kind") == "graphql"
    }
    ingress_by_op: dict[str, list[str]] = {}
    cron_by_op: dict[str, list[str]] = {}
    for c in report.get("catalog") or []:
        op = str(c.get("operation") or "")
        cid = str(c.get("id") or "")
        if c.get("kind") == "ingress" and cid.startswith(INGRESS_PREFIX):
            ingress_by_op.setdefault(op, []).append(cid[len(INGRESS_PREFIX) :])
        elif c.get("kind") == "cron" and cid.startswith(CRON_PREFIX):
            cron_by_op.setdefault(op, []).append(cid[len(CRON_PREFIX) :])

    for raw in ids or []:
        if raw.startswith(INGRESS_PREFIX):
            ingress_cases.append(raw[len(INGRESS_PREFIX) :])
        elif raw.startswith(CRON_PREFIX):
            cron_cases.append(raw[len(CRON_PREFIX) :])
        elif "::" in raw:
            # Touchpoint scenario id — keep full id for the runner
            graphql.append(raw)
        elif raw in gql_ops:
            graphql.append(raw)
        elif raw in ingress_by_op:
            ingress_cases.extend(ingress_by_op[raw])
        elif raw in cron_by_op:
            cron_cases.extend(cron_by_op[raw])
        else:
            # Legacy / unknown — keep prior behaviour (treat as GraphQL name).
            graphql.append(raw)
    # De-dupe while preserving order
    def _uniq(xs: list[str]) -> list[str]:
        return list(dict.fromkeys(xs))

    return {
        "graphql": _uniq(graphql),
        "ingress_cases": _uniq(ingress_cases),
        "cron_cases": _uniq(cron_cases),
    }


def catalog_selection_ids(*, kinds: set[str] | None = None) -> list[str]:
    """All catalog ids, optionally filtered by kind (graphql/ingress/cron)."""
    report = operation_source_report()
    out: list[str] = []
    for c in report.get("catalog") or []:
        kind = str(c.get("kind") or "")
        if kinds and kind not in kinds:
            continue
        cid = str(c.get("id") or "").strip()
        if cid:
            out.append(cid)
    return out
