"""Pick the best raw↔enriched correlation per operation when multiple events exist."""

from __future__ import annotations

from datetime import datetime

from ..models import JsonDict, ValidationResult, ValidationStatus


def operation_from_raw(raw_payload: JsonDict) -> str:
    source = raw_payload.get("source") or {}
    return str(source.get("operation") or "unknown")


def operation_from_enriched(enriched_payload: JsonDict) -> str:
    return operation_from_raw(enriched_payload)


def _occurred_at_epoch(payload: JsonDict) -> float:
    raw = payload.get("occurredAt")
    if not isinstance(raw, str) or not raw:
        return 0.0
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def reconcile_correlation_pairs(
    raw_by_cid: dict[str, JsonDict],
    enriched_by_cid: dict[str, JsonDict],
) -> dict[str, tuple[JsonDict | None, JsonDict | None]]:
    """
    Pair raw ↔ enriched by xCorrelationId, then cross-pair unmatched events that share
    source.operation (closest occurredAt wins).
    """
    pairs: dict[str, tuple[JsonDict | None, JsonDict | None]] = {}

    for cid, raw_payload in raw_by_cid.items():
        pairs[cid] = (raw_payload, enriched_by_cid.get(cid))

    for cid, enriched_payload in enriched_by_cid.items():
        if cid not in pairs:
            pairs[cid] = (None, enriched_payload)

    unmatched_raw: list[tuple[str, JsonDict, str, float]] = []
    for cid, (raw_payload, enriched_payload) in pairs.items():
        if raw_payload and not enriched_payload:
            op = operation_from_raw(raw_payload)
            unmatched_raw.append((cid, raw_payload, op, _occurred_at_epoch(raw_payload)))

    unmatched_enriched: list[tuple[str, JsonDict, str, float]] = []
    used_enriched: set[str] = {
        cid
        for cid, (raw_payload, enriched_payload) in pairs.items()
        if raw_payload is not None and enriched_payload is not None
    }
    for cid, enriched_payload in enriched_by_cid.items():
        if cid in used_enriched:
            continue
        op = operation_from_enriched(enriched_payload)
        unmatched_enriched.append(
            (cid, enriched_payload, op, _occurred_at_epoch(enriched_payload))
        )

    by_op_enriched: dict[str, list[tuple[str, JsonDict, float]]] = {}
    for cid, payload, op, ts in unmatched_enriched:
        by_op_enriched.setdefault(op, []).append((cid, payload, ts))

    for raw_cid, raw_payload, op, raw_ts in sorted(
        unmatched_raw, key=lambda row: row[3]
    ):
        candidates = by_op_enriched.get(op) or []
        if not candidates:
            continue
        best_idx = min(
            range(len(candidates)),
            key=lambda i: abs(candidates[i][2] - raw_ts),
        )
        enr_cid, enr_payload, _ = candidates.pop(best_idx)
        if not candidates:
            del by_op_enriched[op]
        pairs[raw_cid] = (raw_payload, enr_payload)
        orphan = pairs.get(enr_cid)
        if orphan and orphan[0] is None:
            del pairs[enr_cid]

    return pairs


def _correlation_preference_rank(
    cid: str,
    *,
    order: list[str],
    reconciled: dict[str, tuple[JsonDict | None, JsonDict | None]],
    dl_by_cid: dict[str, JsonDict],
) -> tuple[int, int]:
    """Higher rank = better candidate for coverage (paired without DLQ wins)."""
    pair = reconciled.get(cid)
    has_enriched = bool(pair and pair[1] is not None)
    has_dl = cid in dl_by_cid
    if has_enriched and not has_dl:
        tier = 3
    elif has_enriched:
        tier = 2
    elif not has_dl:
        tier = 1
    else:
        tier = 0
    return tier, order.index(cid)


def best_correlation_per_operation(
    raw_by_cid: dict[str, JsonDict],
    enriched_by_cid: dict[str, JsonDict],
    *,
    dl_by_cid: dict[str, JsonDict] | None = None,
) -> dict[str, str]:
    """
    Map operation → xCorrelationId.

    Prefer raw+enriched pairs that did **not** dead-letter; then any paired
    correlation; then raw-only without DLQ; lastly raw-only that hit DLQ.
    """
    dead_letters = dl_by_cid or {}
    op_to_cids: dict[str, list[str]] = {}
    for cid, raw_payload in raw_by_cid.items():
        op = operation_from_raw(raw_payload)
        op_to_cids.setdefault(op, []).append(cid)

    reconciled = reconcile_correlation_pairs(raw_by_cid, enriched_by_cid)

    best: dict[str, str] = {}
    for op, cids in op_to_cids.items():
        best[op] = max(
            cids,
            key=lambda cid: _correlation_preference_rank(
                cid,
                order=cids,
                reconciled=reconciled,
                dl_by_cid=dead_letters,
            ),
        )
    return best


def _validation_score(result: ValidationResult) -> tuple[int, int, int]:
    status_rank = {
        ValidationStatus.PASS: 4,
        ValidationStatus.WARN: 3,
        ValidationStatus.FAIL: 2,
        ValidationStatus.SKIP: 1,
    }
    has_timeout = any(c.check == "enriched_timeout" for c in result.checks)
    has_dead_letter = any(c.check == "dead_letter" for c in result.checks)
    has_pair = not has_timeout and any(
        c.layer in {"layer2-outcome", "raw-vs-enriched", "layer3-domain"} for c in result.checks
    )
    return (
        status_rank.get(result.status, 0),
        1 if has_pair else 0,
        0 if has_dead_letter else 1,
    )


def best_validation_per_operation(
    validation_results: list[ValidationResult],
) -> dict[str, ValidationResult]:
    """When an operation appears in multiple correlation pairs, keep the best outcome."""
    best: dict[str, ValidationResult] = {}
    for result in validation_results:
        if result.service == "routing-key":
            continue
        prev = best.get(result.operation)
        if prev is None or _validation_score(result) > _validation_score(prev):
            best[result.operation] = result
    return best
