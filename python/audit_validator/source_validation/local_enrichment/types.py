"""Shared types for local enrichment."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from ...models import JsonDict


class DiscoveryLike(Protocol):
    def fetch_styles_by_family_ids(
        self, family_ids: list[str], *, correlation_id: str
    ) -> list[dict[str, Any]]: ...

    def fetch_variations_by_family_ids(
        self, family_ids: list[str], *, correlation_id: str
    ) -> list[dict[str, Any]]: ...


class UmsLike(Protocol):
    def get_profile_by_id(
        self, profile_id: str, customer_id: str, *, correlation_id: str
    ) -> dict[str, Any] | None: ...

    def get_role_by_id(
        self, role_id: str, customer_id: str, *, correlation_id: str
    ) -> dict[str, Any] | None: ...


class CmsLike(Protocol):
    def get_customer_by_id(
        self, customer_id: str, *, correlation_id: str
    ) -> dict[str, Any] | None: ...


@dataclass
class EnrichmentClients:
    discovery: DiscoveryLike | None = None
    ums: UmsLike | None = None
    cms: CmsLike | None = None


@dataclass
class EnrichmentResult:
    operation: str
    subject_snapshot: JsonDict | None = None
    actor_snapshot: JsonDict | None = None
    errors: list[str] = field(default_factory=list)
