"""Dynamic GraphQL operation index — built from data/graphql_documents.json."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from ..rabbitmq.resolver_routing_map import RESOLVER_MAPPED_OPERATIONS

_PKG_ROOT = Path(__file__).resolve().parent.parent
_DATA_FILE = _PKG_ROOT / "data" / "graphql_documents.json"
_MANIFEST_FILE = _PKG_ROOT / "data" / "operation_manifest.json"

_ROOT_FIELD = re.compile(
    r"(?:query|mutation|subscription)\s+[^{]+\{\s*(\w+)\s*(?:\(|{)",
    re.IGNORECASE,
)
_KIND = re.compile(r"^(query|mutation|subscription)\s", re.IGNORECASE)

# Rare cases where flow/report label or export-name camelCase must win over GraphQL root field.
_OPERATION_EXPORT_OVERRIDES: dict[str, str] = {
    "deActivateList": "DEACTIVATE_LIST",
    "deActivateFontProject": "DEACTIVATE_FONT_PROJECT",
    "markProductionFonts": "MARK_AS_PRODUCTION_FONT",
    "markAsProductionFont": "MARK_AS_PRODUCTION_FONT",
    "createBYOFBatchAndCheckDuplicates": "CREATE_BYOF_BATCH_AND_CHECK_DUPLICATES",
    "syncUnSyncVariations": "SYNC_UNSYNC_VARIATIONS",
    "getCustomerById": "GET_CUSTOMER_BY_ID",
    "getAssetsFolderSummary": "GET_ASSETS_FOLDER_SUMMARY",
}


@dataclass(frozen=True)
class GraphQLOperationEntry:
    export_name: str
    root_field: str
    audit_operation: str
    kind: str  # query | mutation | subscription


def export_name_to_camel(export_name: str) -> str:
    parts = export_name.lower().split("_")
    return parts[0] + "".join(part.capitalize() for part in parts[1:])


def _resolve_audit_operation(export_name: str, root_field: str) -> str:
    from_export = export_name_to_camel(export_name)
    if from_export in RESOLVER_MAPPED_OPERATIONS:
        return from_export
    return root_field


def _parse_entry(export_name: str, document: str) -> GraphQLOperationEntry | None:
    stripped = document.strip()
    kind_match = _KIND.search(stripped)
    root_match = _ROOT_FIELD.search(stripped)
    if not kind_match or not root_match:
        return None
    root_field = root_match.group(1)
    return GraphQLOperationEntry(
        export_name=export_name,
        root_field=root_field,
        audit_operation=_resolve_audit_operation(export_name, root_field),
        kind=kind_match.group(1).lower(),
    )


@lru_cache(maxsize=1)
def load_operation_index() -> dict[str, GraphQLOperationEntry]:
    """Map audit operation name → GraphQL metadata entry."""
    if not _DATA_FILE.is_file():
        raise FileNotFoundError(
            f"GraphQL document bundle not found: {_DATA_FILE}. "
            "Run scripts/export_graphql_documents.py to regenerate."
        )
    raw = json.loads(_DATA_FILE.read_text(encoding="utf-8"))
    by_operation: dict[str, GraphQLOperationEntry] = {}
    for export_name, document in raw.items():
        entry = _parse_entry(str(export_name), str(document))
        if entry is None:
            continue
        by_operation.setdefault(entry.audit_operation, entry)
        if entry.root_field != entry.audit_operation:
            by_operation.setdefault(entry.root_field, entry)
    for operation, export_name in _OPERATION_EXPORT_OVERRIDES.items():
        entry = _parse_entry(export_name, str(raw[export_name]))
        if entry:
            by_operation[operation] = GraphQLOperationEntry(
                export_name=export_name,
                root_field=entry.root_field,
                audit_operation=operation,
                kind=entry.kind,
            )
    return by_operation


def get_operation_entry(operation: str) -> GraphQLOperationEntry | None:
    return load_operation_index().get(operation)


def get_export_for_operation(operation: str) -> str | None:
    entry = get_operation_entry(operation)
    return entry.export_name if entry else None


def get_document_for_operation(operation: str) -> str | None:
    export = get_export_for_operation(operation)
    if not export:
        return None
    from ..simulation.graphql_loader import load_graphql_documents

    return load_graphql_documents().get(export)


def all_graphql_operations() -> frozenset[str]:
    return frozenset(load_operation_index().keys())


def graphql_operation_kind(operation: str) -> str | None:
    entry = get_operation_entry(operation)
    return entry.kind if entry else None


def is_query_operation(operation: str) -> bool:
    return graphql_operation_kind(operation) == "query"


def is_mutation_operation(operation: str) -> bool:
    return graphql_operation_kind(operation) == "mutation"


def operation_graphql_export_map() -> dict[str, str]:
    """Audit operation name → GraphQL export const (replaces static OPERATION_GRAPHQL_EXPORT)."""
    index = load_operation_index()
    return {op: entry.export_name for op, entry in sorted(index.items())}


def write_operation_manifest(path: Path | None = None) -> Path:
    """Write a human-readable manifest of all parsed GraphQL operations."""
    out = path or _MANIFEST_FILE
    index = load_operation_index()
    entries = sorted(
        {
            entry.audit_operation: entry
            for entry in index.values()
        }.values(),
        key=lambda e: e.audit_operation,
    )
    payload = {
        "generatedFrom": _DATA_FILE.name,
        "operationCount": len(entries),
        "operations": [
            {
                "auditOperation": e.audit_operation,
                "exportName": e.export_name,
                "rootField": e.root_field,
                "kind": e.kind,
                "resolverMapped": e.audit_operation in RESOLVER_MAPPED_OPERATIONS,
            }
            for e in entries
        ],
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return out


# Invoked via NextGen /graph (Apollo Client), not mtconnect-api /graphql.
NEXTGEN_UI_OPERATIONS = frozenset(
    {
        "activateFamily",
        "deactivateFamilies",
        "activateStyle",
        "deactivateStyle",
        "activateVariation",
        "deactivateVariation",
        "bulkActivateStyles",
        "bulkDeactivateStyles",
        "addFontProjectFamilies",
        "removeFontProjectFamilies",
        "addFontProjectStyles",
        "updateFontProjectStyles",
        "addFontListFamilies",
        "removeFontListFamilies",
        "addFontListStyles",
        "removeFontListStyles",
        "addFavoriteFamilies",
        "removeFavoriteFamilies",
        "addFavoriteStyles",
        "removeFavoriteStyles",
        "bulkAddStylesToFavourites",
        "bulkRemoveStylesFromFavourites",
    }
)


def is_nextgen_ui_operation(operation: str) -> bool:
    return operation in NEXTGEN_UI_OPERATIONS


def clear_operation_index_cache() -> None:
    load_operation_index.cache_clear()
    from ..simulation.graphql_loader import load_graphql_documents

    load_graphql_documents.cache_clear()
