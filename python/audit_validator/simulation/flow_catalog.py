"""Map simulation flows to the GraphQL operation labels they invoke."""

from __future__ import annotations

import inspect
import re
from dataclasses import dataclass
from functools import lru_cache

from .flows import FLOW_REGISTRY

_AUDIT_OP = re.compile(r"\s*\([^)]+\)")


def audit_operation(label: str) -> str:
    """Strip flow labels like 'createAsset (FontList)' → 'createAsset'."""
    return _AUDIT_OP.sub("", label).strip()


@dataclass(frozen=True)
class FlowOperation:
    flow: str
    label: str
    graphql_operation: str
    uses_secondary_token: bool = False
    skipped_by_default: bool = False


def _uses_secondary(label: str, flow: str) -> bool:
    if flow == "notificationRecipient":
        return label != "notificationRecipient (secondary grant)"
    return "(secondary" in label.lower()


def _skipped_by_default(label: str) -> bool:
    return label in {
        "deleteAllPrivateTags",
        "resetPassword",
        "notificationRecipient (secondary grant)",
    }


@lru_cache(maxsize=1)
def flow_operations() -> tuple[FlowOperation, ...]:
    out: list[FlowOperation] = []
    seen: set[tuple[str, str]] = set()

    for flow_name, fn in FLOW_REGISTRY:
        src = inspect.getsource(fn)
        labels: list[str] = []
        labels.extend(re.findall(r'run_operation\(\s*ctx,\s*"([^"]+)"', src))
        labels.extend(re.findall(r'_append\(\s*"([^"]+)"', src))
        labels.extend(re.findall(r'label="([^"]+)"', src))

        for label in labels:
            key = (flow_name, label)
            if key in seen:
                continue
            seen.add(key)
            out.append(
                FlowOperation(
                    flow=flow_name,
                    label=label,
                    graphql_operation=audit_operation(label),
                    uses_secondary_token=_uses_secondary(label, flow_name),
                    skipped_by_default=_skipped_by_default(label),
                )
            )
    return tuple(out)
