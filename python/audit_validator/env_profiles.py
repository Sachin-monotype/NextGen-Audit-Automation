"""Audit environment profiles — PP preprod (default) vs Everest dev."""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse, urlunparse


@dataclass(frozen=True)
class OAuthProfile:
    token_url: str
    client_id: str
    client_secret: str
    audience: str
    organization: str = ""
    grant_type: str = "password"


PP_OAUTH = OAuthProfile(
    token_url="https://secure-pp.monotype.com/oauth/token",
    client_id="0bnAznyuRQfeaCg9qXxWKeoSZtqorUpD",
    client_secret="W-UEo0Zaa0bsNcTbYFtg-31U-8kzp9gyHiZQ2VeJU_9phYITuztKnxWJ0poxhUlc",
    audience="https://nextgen.monotype.com",
    organization="",
    grant_type="password",
)

QA_OAUTH = OAuthProfile(
    token_url="https://secure.monotype-pp.com/v2/oauth/token",
    client_id="LhfeUMNRvHeb0pbefR5YlvRUeV6Fk100",
    client_secret="I9ycmyvb4ztc_1s_9AgmjzGSOzAG2uEH5TzY2GtNKUiehXv3YjLZzX9h4W0UN07v",
    audience="https://api.monotype.com",
    organization="",
    grant_type="client_credentials",
)

# NextGen QA SPA client — password grant (same client as MTConnectAutomation QA).
# Browser SSO / Excel tokens use this azp; M2M QA_OAUTH rejects password grant.
QA_USER_OAUTH = OAuthProfile(
    token_url="https://secure.monotype-pp.com/oauth/token",
    client_id="s6k7gwI03MnW4EpGKirRF3VGZq5k9HLG",
    client_secret="BYyS6kCDXMSsOFzG9q_qZrFZhWO7rAHgLZfp0zKMbba8ZGLPifcWEFDRzLfP9LRx",
    audience="https://nextgen.monotype.com",
    organization="",
    grant_type="password",
)


@dataclass(frozen=True)
class AuditTargetProfile:
    name: str
    label: str
    nextgen_ui_url: str
    graphql_endpoint: str
    admin_graphql_endpoint: str
    nextgen_graphql_endpoint: str
    nextgen_origin: str
    nextgen_referer: str
    simulation_prefer_pp_bearer: bool
    rabbitmq_vhost: str  # path segment, e.g. %2F or mt-connect
    raw_events_queue: str
    enriched_events_queue: str
    consume_dead_letter_queue: bool
    purge_test_queues_on_e2e: bool
    ingress_api_url: str
    ingress_raw_queue: str
    ingress_enriched_queue: str
    ingress_rabbitmq_vhost: str
    seed_family_id: str
    seed_deactivate_family_id: str
    mongo_db_name: str = ""  # empty → derived as AuditLogs{NAME}
    oauth: OAuthProfile = PP_OAUTH
    oauth_username: str = ""
    # Password-grant OAuth for the Bearer credentials modal (QA uses PP user login).
    user_oauth: OAuthProfile | None = None


PP_PREPROD = AuditTargetProfile(
    name="pp",
    label="Monotype PP preprod",
    nextgen_ui_url="https://nextgen.monotype-pp.com",
    graphql_endpoint="https://nextgen.monotype-pp.com/graphql",
    admin_graphql_endpoint="https://nextgen.monotype-pp.com/graphql",
    nextgen_graphql_endpoint="https://nextgen.monotype-pp.com/graph",
    nextgen_origin="https://nextgen.monotype-pp.com",
    nextgen_referer="https://nextgen.monotype-pp.com/discover-fonts/all",
    simulation_prefer_pp_bearer=True,
    rabbitmq_vhost="mt-connect-preprod",
    # Automation taps on mt-connect-preprod. Platform resolver mains are
    # mt.platform.raw_events.resolver.queue + mt.platform.events.notification.queue.
    raw_events_queue="mtraw-automation(DO NOT DELETE)",
    enriched_events_queue="mtenrich-automation(DO NOT DELETE)",
    consume_dead_letter_queue=False,
    purge_test_queues_on_e2e=True,
    ingress_api_url="https://mt-audit-log-resolver-service-preprod.monotype-pp.com/v1/audit-events",
    ingress_raw_queue="mtraw-automation(DO NOT DELETE)",
    ingress_enriched_queue="mtenrich-automation(DO NOT DELETE)",
    ingress_rabbitmq_vhost="mt-connect-preprod",
    seed_family_id="794981",
    seed_deactivate_family_id="8kL8ZM64",
    mongo_db_name="AuditLogsPreprod",
    oauth_username="mem.auditpreprodtest@gmail.com",
)

UAT = AuditTargetProfile(
    name="uat",
    label="Monotype UAT",
    nextgen_ui_url="https://nextgen.monotype-uat.com",
    graphql_endpoint="https://nextgen.monotype-uat.com/graphql",
    admin_graphql_endpoint="https://nextgen.monotype-uat.com/graphql",
    nextgen_graphql_endpoint="https://nextgen.monotype-uat.com/graph",
    nextgen_origin="https://nextgen.monotype-uat.com",
    nextgen_referer="https://nextgen.monotype-uat.com/discover-fonts/all",
    simulation_prefer_pp_bearer=False,
    rabbitmq_vhost="mt-connect-preprod",
    raw_events_queue="mtraw-automation(DO NOT DELETE)",
    enriched_events_queue="mtenrich-automation(DO NOT DELETE)",
    consume_dead_letter_queue=False,
    purge_test_queues_on_e2e=True,
    ingress_api_url="https://mt-audit-log-resolver-service-uat.monotype-uat.com/v1/audit-events",
    ingress_raw_queue="mtraw-automation(DO NOT DELETE)",
    ingress_enriched_queue="mtenrich-automation(DO NOT DELETE)",
    ingress_rabbitmq_vhost="mt-connect-preprod",
    seed_family_id="794981",
    seed_deactivate_family_id="8kL8ZM64",
    mongo_db_name="AuditLogsUAT",
)

QA = AuditTargetProfile(
    name="qa",
    label="Monotype QA",
    nextgen_ui_url="https://nextgen-qa.monotype-pp.com",
    graphql_endpoint="https://nextgen-qa.monotype-pp.com/graphql",
    admin_graphql_endpoint="https://nextgen-qa.monotype-pp.com/graphql",
    nextgen_graphql_endpoint="https://nextgen-qa.monotype-pp.com/graph",
    nextgen_origin="https://nextgen-qa.monotype-pp.com",
    nextgen_referer="https://nextgen-qa.monotype-pp.com/discover-fonts/all",
    simulation_prefer_pp_bearer=True,
    rabbitmq_vhost="mt-connect-qa",
    raw_events_queue="mt_test_raw",
    enriched_events_queue="mt_test enrich",
    consume_dead_letter_queue=False,
    purge_test_queues_on_e2e=True,
    ingress_api_url="https://mt-audit-log-resolver-service-qa.monotype-pp.com/v1/audit-events",
    ingress_raw_queue="mt_test_raw",
    ingress_enriched_queue="mt_test enrich",
    ingress_rabbitmq_vhost="mt-connect-qa",
    seed_family_id="794981",
    seed_deactivate_family_id="8kL8ZM64",
    mongo_db_name="AuditLogsQA",
    oauth=QA_OAUTH,
    oauth_username="monotype.staging+testuser11new@gmail.com",
    user_oauth=QA_USER_OAUTH,
)

EVEREST_DEV = AuditTargetProfile(
    name="everest",
    label="Everest dev / mt-connect",
    nextgen_ui_url="https://nextgen-everest.monotype-dev.com",
    graphql_endpoint="https://mtconnectapi-everest.monotype-dev.com/graphql",
    admin_graphql_endpoint="https://mtconnectapi-everest.monotype-dev.com/graphql",
    nextgen_graphql_endpoint="https://nextgen-everest.monotype-dev.com/graph",
    nextgen_origin="https://nextgen-everest.monotype-dev.com",
    nextgen_referer="https://nextgen-everest.monotype-dev.com/discover-fonts/all",
    simulation_prefer_pp_bearer=False,
    rabbitmq_vhost="mt-connect",
    raw_events_queue="mt.platform,resolver.raw_events_test_queue",
    enriched_events_queue="mt.platform,resolver.enriched_events_test_queue",
    consume_dead_letter_queue=True,
    purge_test_queues_on_e2e=True,
    ingress_api_url="https://mt-audit-log-resolver-service-preprod.monotype-pp.com/v1/audit-events",
    ingress_raw_queue="mt.platform,resolver.raw_events_test_queue",
    ingress_enriched_queue="mt.platform,resolver.enriched_events_test_queue",
    ingress_rabbitmq_vhost="mt-connect-preprod",
    seed_family_id="794981",
    seed_deactivate_family_id="8kL8ZM64",
    mongo_db_name="AuditLogsEverest",
)

_PROFILES: dict[str, AuditTargetProfile] = {
    "pp": PP_PREPROD,
    "preprod": PP_PREPROD,
    "qa": QA,
    "uat": UAT,
    "everest": EVEREST_DEV,
    "dev": EVEREST_DEV,
    "everest-dev": EVEREST_DEV,
}

# Keys owned by the active profile (applied after .env load).
_PROFILE_KEYS: frozenset[str] = frozenset(
    {
        "NEXTGEN_UI_URL",
        "GRAPHQL_ENDPOINT",
        "GRAPHQL_API_ENDPOINT",
        "ADMIN_GRAPHQL_ENDPOINT",
        "NEXTGEN_GRAPHQL_ENDPOINT",
        "NEXTGEN_ORIGIN",
        "NEXTGEN_REFERER",
        "SIMULATION_PREFER_PP_BEARER",
        "RAW_EVENTS_QUEUE",
        "ENRICHED_EVENTS_QUEUE",
        "CONSUME_DEAD_LETTER_QUEUE",
        "PURGE_TEST_QUEUES_ON_E2E",
        "INGRESS_API_URL",
        "INGRESS_RAW_QUEUE",
        "INGRESS_ENRICHED_QUEUE",
        "SEED_FAMILY_ID",
        "SEED_DEACTIVATE_FAMILY_ID",
        "MONGO_DB_NAME",
        "OAUTH_TOKEN_URL",
        "OAUTH_CLIENT_ID",
        "OAUTH_CLIENT_SECRET",
        "OAUTH_AUDIENCE",
        "OAUTH_GRANT_TYPE",
        "OAUTH_ORG",
        "OAUTH_USERNAME",
        "AUTH0_TOKEN_URL",
        "AUTH0_CLIENT_ID",
        "AUTH0_CLIENT_SECRET",
        "AUTH0_AUDIENCE",
        "AUTH0_ORGANIZATION",
        "AUTH0_GRANT_TYPE",
    }
)


def user_oauth_for_profile(profile: AuditTargetProfile | None = None) -> OAuthProfile:
    """OAuth client used for user password grant (Bearer credentials modal)."""
    p = profile or get_audit_profile()
    return p.user_oauth if p.user_oauth is not None else p.oauth


def user_oauth_config_dict(profile: AuditTargetProfile | None = None) -> dict[str, str]:
    o = user_oauth_for_profile(profile)
    return {
        "token_url": o.token_url,
        "client_id": o.client_id,
        "client_secret": o.client_secret,
        "audience": o.audience,
        "grant_type": o.grant_type,
        "organization": o.organization,
    }


def mongo_db_for_profile(profile: AuditTargetProfile | None = None) -> str:
    """Mongo database name for the active audit target (created on first write)."""
    p = profile or get_audit_profile()
    if p.mongo_db_name:
        return p.mongo_db_name
    return f"AuditLogs{p.name.upper()}"


def audit_target_name() -> str:
    raw = (os.getenv("AUDIT_TARGET") or "qa").strip().lower()
    return raw if raw in _PROFILES else "qa"


def get_audit_profile(name: str | None = None) -> AuditTargetProfile:
    key = (name or audit_target_name()).strip().lower()
    return _PROFILES.get(key, PP_PREPROD)


def _rabbitmq_url_for_vhost(base_url: str, vhost: str) -> str:
    if not base_url.strip():
        return base_url
    parsed = urlparse(base_url)
    path = vhost if vhost.startswith("/") else f"/{vhost}"
    return urlunparse(parsed._replace(path=path))


def apply_audit_profile(*, project_root=None) -> AuditTargetProfile:
    """
    Apply AUDIT_TARGET profile defaults on top of .env.

    Profile-owned keys always win when AUDIT_TARGET is set (default qa).
    RABBITMQ_URL / INGRESS_RABBITMQ_URL keep credentials/host from .env but
    switch vhost to the profile value.
    """
    from pathlib import Path

    from dotenv import load_dotenv

    from .project_root import find_project_root

    root = Path(project_root) if project_root else find_project_root()
    load_dotenv(root / ".env")

    profile = get_audit_profile()
    os.environ.setdefault("AUDIT_TARGET", profile.name)

    # Honour explicit opt-out of profile overrides (local experiments).
    if os.getenv("AUDIT_TARGET_STRICT", "true").strip().lower() in {"0", "false", "no"}:
        return profile

    mapping = {
        "NEXTGEN_UI_URL": profile.nextgen_ui_url,
        "GRAPHQL_ENDPOINT": profile.graphql_endpoint,
        "GRAPHQL_API_ENDPOINT": profile.graphql_endpoint,
        "ADMIN_GRAPHQL_ENDPOINT": profile.admin_graphql_endpoint,
        "NEXTGEN_GRAPHQL_ENDPOINT": profile.nextgen_graphql_endpoint,
        "NEXTGEN_ORIGIN": profile.nextgen_origin,
        "NEXTGEN_REFERER": profile.nextgen_referer,
        "SIMULATION_PREFER_PP_BEARER": "true" if profile.simulation_prefer_pp_bearer else "false",
        "RAW_EVENTS_QUEUE": profile.raw_events_queue,
        "ENRICHED_EVENTS_QUEUE": profile.enriched_events_queue,
        "CONSUME_DEAD_LETTER_QUEUE": "true" if profile.consume_dead_letter_queue else "false",
        "PURGE_TEST_QUEUES_ON_E2E": "true" if profile.purge_test_queues_on_e2e else "false",
        "INGRESS_API_URL": profile.ingress_api_url,
        "INGRESS_RAW_QUEUE": profile.ingress_raw_queue,
        "INGRESS_ENRICHED_QUEUE": profile.ingress_enriched_queue,
        "SEED_FAMILY_ID": profile.seed_family_id,
        "SEED_DEACTIVATE_FAMILY_ID": profile.seed_deactivate_family_id,
        "MONGO_DB_NAME": mongo_db_for_profile(profile),
        "OAUTH_TOKEN_URL": profile.oauth.token_url,
        "OAUTH_CLIENT_ID": profile.oauth.client_id,
        "OAUTH_CLIENT_SECRET": profile.oauth.client_secret,
        "OAUTH_AUDIENCE": profile.oauth.audience,
        "OAUTH_GRANT_TYPE": profile.oauth.grant_type,
        "OAUTH_ORG": profile.oauth.organization,
        "OAUTH_USERNAME": profile.oauth_username,
        "AUTH0_TOKEN_URL": profile.oauth.token_url,
        "AUTH0_CLIENT_ID": profile.oauth.client_id,
        "AUTH0_CLIENT_SECRET": profile.oauth.client_secret,
        "AUTH0_AUDIENCE": profile.oauth.audience,
        "AUTH0_ORGANIZATION": profile.oauth.organization,
        "AUTH0_GRANT_TYPE": profile.oauth.grant_type,
    }
    for key, value in mapping.items():
        if key in _PROFILE_KEYS:
            os.environ[key] = value

    base_rmq = os.getenv("RABBITMQ_URL", "").strip()
    if base_rmq:
        os.environ["RABBITMQ_URL"] = _rabbitmq_url_for_vhost(base_rmq, profile.rabbitmq_vhost)

    ingress_base = (os.getenv("INGRESS_RABBITMQ_URL") or base_rmq).strip()
    if ingress_base:
        os.environ["INGRESS_RABBITMQ_URL"] = _rabbitmq_url_for_vhost(
            ingress_base, profile.ingress_rabbitmq_vhost
        )

    return profile
