"""Cap Discovery middleware calls (Typesense-backed) per validation iteration."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DiscoveryCallBudget:
    max_calls: int
    used: int = 0
    log: list[str] = field(default_factory=list)

    def can_call(self) -> bool:
        return self.used < self.max_calls

    def record(self, label: str) -> None:
        self.used += 1
        self.log.append(label)

    def deny(self, label: str) -> str:
        return f"Discovery budget exhausted ({self.used}/{self.max_calls}); skipped {label}"
