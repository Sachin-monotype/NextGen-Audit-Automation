"""
Operation classification for E2E validation expectations.

Resolver behavior (mt-audit-log-resolver-service):
- Mapped operations (outbound-routing-map) → enriched event expected
- Unmapped mutations → raw ack'd, no enriched publish
- Query/read operations → may emit raw audit events but never enriched
  (unless explicitly mapped in outbound-routing-map, e.g. getPackageId)
"""

from __future__ import annotations

from .enricher_registry import enrichment_expected as _enricher_expects_enrichment
from .utility.operation_graphql import (
    all_graphql_operations,
    is_mutation_operation,
    is_query_operation,
    load_operation_index,
)
from .rabbitmq.resolver_routing_map import RESOLVER_MAPPED_OPERATIONS


def query_ops_no_enrichment() -> frozenset[str]:
    """GraphQL queries not mapped in the resolver outbound-routing-map."""
    return frozenset(
        op
        for op in all_graphql_operations()
        if is_query_operation(op) and op not in RESOLVER_MAPPED_OPERATIONS
    )


def unmapped_mutation_ops() -> frozenset[str]:
    """GraphQL mutations present in the bundle but not in outbound-routing-map."""
    return frozenset(
        op
        for op in all_graphql_operations()
        if is_mutation_operation(op) and op not in RESOLVER_MAPPED_OPERATIONS
    )


# Backward-compatible aliases (computed from GraphQL bundle + routing map).
QUERY_OPS_NO_ENRICHMENT: frozenset[str] = query_ops_no_enrichment()
UNMAPPED_MUTATION_OPS: frozenset[str] = unmapped_mutation_ops()


def expects_enriched_event(operation: str) -> bool:
    """True when resolver enricher is registered and enrichment is expected (dev-confirmed)."""
    if _enricher_expects_enrichment(operation):
        return True
    if operation in RESOLVER_MAPPED_OPERATIONS and is_query_operation(operation):
        return operation in RESOLVER_MAPPED_OPERATIONS
    return False


# Ops where pipeline events work but domain templates / validators are not wired yet.
VALIDATOR_PENDING_OPS: frozenset[str] = frozenset(
    {
        "createBYOFBatchAndCheckDuplicates",
        "createCompanyLogoUploadUrl",
        "createContract",
        "deleteCompanyLogo",
        "markCompanyLogoUploadSuccess",
        "markOnboardingCompleted",
    }
)


def is_validator_pending(operation: str) -> bool:
    return operation in VALIDATOR_PENDING_OPS


def enrichment_expectation_label(operation: str) -> str:
    if _enricher_expects_enrichment(operation):
        return "enricher_registered"
    if operation in RESOLVER_MAPPED_OPERATIONS and is_query_operation(operation):
        return "query_mapped"
    if is_query_operation(operation):
        return "query_no_enrichment"
    if is_mutation_operation(operation):
        return "unmapped_mutation"
    if operation in load_operation_index():
        return "unknown_graphql"
    return "unknown"
