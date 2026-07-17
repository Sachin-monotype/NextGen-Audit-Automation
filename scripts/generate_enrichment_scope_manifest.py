#!/usr/bin/env python3
"""Regenerate enrichment_scope_manifest.json from mt-audit-log-resolver-service.

Usage::

    python scripts/generate_enrichment_scope_manifest.py \\
      --resolver-root "/path/to/mt-audit-log-resolver-service"
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


def _extract_handler_list(handler_text: str, fn_name: str) -> set[str]:
    m = re.search(
        rf"function {fn_name}\(operation: string\): boolean \{{[\s\S]*?"
        rf"return \[([\s\S]*?)\]\.includes\(operation\);",
        handler_text,
    )
    if not m:
        return set()
    return set(re.findall(r'"([a-zA-Z][a-zA-Z0-9_]*)"', m.group(1)))


def build_manifest(resolver_root: Path) -> dict:
    handler = (
        resolver_root
        / "src/audit-resolver-consumer/consumers/auditResolverMessage.handler.ts"
    ).read_text(encoding="utf-8")
    req_subj = _extract_handler_list(handler, "requiresSubjectEnrichedSnapshot")
    req_actor = _extract_handler_list(handler, "requiresActorEnrichedSnapshot")

    produce: dict[str, dict] = {}
    for f in (resolver_root / "src/enrichment/enrichers").rglob("*.enricher.ts"):
        text = f.read_text(encoding="utf-8")
        ops = re.findall(
            r"(?:readonly\s+)?operation(?:\??)?\s*[:=]\s*['\"]([a-zA-Z0-9_]+)['\"]",
            text,
        )
        if not ops:
            ops = re.findall(r"operation\s*===\s*['\"]([a-zA-Z0-9_]+)['\"]", text)
        if not ops:
            ops = re.findall(
                r"source\.operation\s*===\s*['\"]([a-zA-Z0-9_]+)['\"]", text
            )
        has_subj = "subjectUpdates" in text
        has_actor = "actorUpdates" in text
        for op in set(ops):
            produce[op] = {
                "file": str(f.relative_to(resolver_root)),
                "produces_subject": has_subj,
                "produces_actor": has_actor,
            }

    all_ops = sorted(set(produce) | req_subj | req_actor)
    operations: dict[str, dict] = {}
    for op in all_ops:
        p = produce.get(op, {})
        ps, pa = bool(p.get("produces_subject")), bool(p.get("produces_actor"))
        rs, ra = op in req_subj, op in req_actor
        impl = "both" if ps and pa else "subject-only" if ps else "actor-only" if pa else "none"
        enf = "both" if rs and ra else "subject-only" if rs else "actor-only" if ra else "none"
        operations[op] = {
            "implementation": {"subject": ps, "actor": pa, "scope": impl},
            "enforced": {"subject": rs, "actor": ra, "scope": enf},
            "gap": impl != enf and (rs or ra or ps or pa),
            "source_file": p.get("file", ""),
        }
    return {
        "generated_from": str(resolver_root.resolve()),
        "operations": operations,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--resolver-root",
        type=Path,
        default=Path(
            "/Users/sachinkoirala/Documents/CodeBases/MT Connect NextGen/"
            "mt-audit-log-resolver-service"
        ),
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("python/audit_validator/data/enrichment_scope_manifest.json"),
    )
    args = parser.parse_args()
    data = build_manifest(args.resolver_root)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    n = len(data["operations"])
    gaps = sum(1 for v in data["operations"].values() if v["gap"])
    print(f"Wrote {args.out} — {n} operations, {gaps} produce≠require gaps")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
