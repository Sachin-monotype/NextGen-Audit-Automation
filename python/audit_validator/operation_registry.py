"""Unified operation catalog for coverage, reports, and E2E expectations."""

from __future__ import annotations

from .utility.operation_graphql import all_graphql_operations
from .rabbitmq.resolver_routing_map import RESOLVER_MAPPED_OPERATIONS
from .simulation.operation_registry import simulated_operations
from .template_registry import OPERATION_TEMPLATE_MAP


def tracked_operations() -> list[str]:
    """
    All operations the automation pipeline knows about.

    Union of:
    - operations run in simulation flows
    - resolver outbound-routing-map entries
    - template-registry entries (domain validation families)
    - GraphQL documents with a resolvable audit operation name
    """
    known = (
        set(simulated_operations())
        | set(RESOLVER_MAPPED_OPERATIONS)
        | set(OPERATION_TEMPLATE_MAP.keys())
        | set(all_graphql_operations())
    )
    return sorted(known)


def e2e_expected_operations() -> frozenset[str]:
    """Operations we expect raw audit events for after a full flows simulation."""
    return simulated_operations() | frozenset(OPERATION_TEMPLATE_MAP.keys())
