from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ValidationStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    SKIP = "SKIP"
    WARN = "WARN"


@dataclass
class CheckResult:
    layer: str
    check: str
    status: ValidationStatus
    message: str
    path: str | None = None


@dataclass
class ValidationResult:
    operation: str
    service: str
    template_id: str
    status: ValidationStatus
    checks: list[CheckResult] = field(default_factory=list)
    raw_path: str | None = None
    enriched_path: str | None = None

    def add(
        self,
        layer: str,
        check: str,
        status: ValidationStatus,
        message: str,
        path: str | None = None,
    ) -> None:
        self.checks.append(CheckResult(layer, check, status, message, path))
        if status == ValidationStatus.FAIL:
            self.status = ValidationStatus.FAIL
        elif status == ValidationStatus.WARN and self.status == ValidationStatus.PASS:
            self.status = ValidationStatus.WARN

    @property
    def failed_checks(self) -> list[CheckResult]:
        return [c for c in self.checks if c.status == ValidationStatus.FAIL]


JsonDict = dict[str, Any]
