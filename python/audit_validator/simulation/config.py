"""GraphQL simulation config from .env."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from ..env_profiles import apply_audit_profile


def _bool_env(key: str, default: bool) -> bool:
    val = os.getenv(key)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


def _parse_bulk_style_entries(env_key: str, default: str) -> tuple[dict, ...]:
    """Parse 'id:Style Name,id:Style Name' into UI bulk style objects."""
    raw = os.getenv(env_key, default).strip()
    out: list[dict] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            style_id, style_name = part.split(":", 1)
            out.append(
                {
                    "id": style_id.strip(),
                    "metadata": {"styleName": style_name.strip()},
                }
            )
        else:
            out.append({"id": part})
    return tuple(out)


@dataclass(frozen=True)
class SeedConfig:
    family_id: str
    deactivate_family_id: str
    favorite_family_id: str
    family_ids: tuple[str, ...]
    style_id: str
    deactivate_style_id: str
    favorite_style_id: str
    headline_style_id: str
    body_style_id: str
    variation_style_id: str
    variation_id: str
    variation_md5: str
    deactivate_variation_md5: str
    imported_style_id: str
    font_list_id: str
    upload_session_id: str
    byof_batch_id: str
    sharee_id: str
    fontproject_contributor_access_id: int
    project_id: str
    remove_project_family_id: str
    tag_id: str
    shopify_customer_gid: str
    bulk_activate_styles: tuple[dict, ...]
    bulk_favourite_styles: tuple[dict, ...]


@dataclass(frozen=True)
class AccountsApiConfig:
    graphql_endpoint: str
    rest_base_url: str
    bearer_token: str
    id_token: str
    reset_password_email: str


@dataclass(frozen=True)
class GraphQLSimulationConfig:
    project_root: Path
    endpoint: str
    api_endpoint: str
    admin_endpoint: str
    nextgen_endpoint: str
    nextgen_origin: str
    nextgen_referer: str
    nextgen_user_agent: str
    route_mutations_to_bff: bool
    bearer_token: str
    nextgen_bearer_token: str
    secondary_bearer_token: str
    accept_language: str
    use_customer_context: bool
    customer_context_id: str
    accounts: AccountsApiConfig | None
    seed: SeedConfig
    max_parallel_flows: int
    skip_flows: frozenset[str]
    flow_filter: frozenset[str] | None = None


def _parse_flow_list(raw: str) -> frozenset[str]:
    names = {part.strip() for part in raw.split(",") if part.strip()}
    return frozenset(names)


def load_simulation_config(project_root: Path) -> GraphQLSimulationConfig:
    apply_audit_profile(project_root=project_root)
    from ..auth import (
        resolve_bearer_token,
        resolve_graphql_bearer_token,
        resolve_nextgen_bearer_token,
    )

    oauth_token = resolve_bearer_token()
    token = resolve_graphql_bearer_token() or oauth_token
    nextgen_token = resolve_nextgen_bearer_token() or token
    if not token:
        raise RuntimeError(
            "NEXTGEN_BEARER_TOKEN, BEARER_TOKEN_PP, or BEARER_TOKEN is required in .env "
            "for GraphQL simulation"
        )

    use_ctx = os.getenv("GRAPHQL_USE_CUSTOMER_CONTEXT", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }

    secondary = os.getenv("BEARER_TOKEN_SECONDARY", "").strip()
    if secondary.lower().startswith("bearer "):
        secondary = secondary[7:].strip()

    nextgen_ui = os.getenv(
        "NEXTGEN_UI_URL", "https://nextgen.monotype-pp.com"
    ).rstrip("/")
    nextgen_endpoint = os.getenv("NEXTGEN_GRAPHQL_ENDPOINT", f"{nextgen_ui}/graph").strip()
    nextgen_origin = os.getenv("NEXTGEN_ORIGIN", nextgen_ui).rstrip("/")
    nextgen_referer = os.getenv(
        "NEXTGEN_REFERER", f"{nextgen_origin}/discover-fonts/all"
    ).strip()
    nextgen_user_agent = os.getenv(
        "NEXTGEN_USER_AGENT",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
    ).strip()

    # PP preprod: mutations via /graph (audit); queries via /graphql on nextgen.monotype-pp.com.
    route_mutations_to_bff = _bool_env("SIMULATION_ROUTE_MUTATIONS_TO_BFF", True)
    explicit_api = os.getenv("GRAPHQL_API_ENDPOINT", "").strip()
    explicit_endpoint = os.getenv("GRAPHQL_ENDPOINT", "").strip()
    explicit_admin = os.getenv("ADMIN_GRAPHQL_ENDPOINT", "").strip()
    api_endpoint = explicit_api or explicit_endpoint or f"{nextgen_ui}/graphql"
    admin_endpoint = explicit_admin or api_endpoint

    family_ids_raw = os.getenv("SEED_FAMILY_IDS", "").strip()
    if family_ids_raw:
        family_ids = tuple(x.strip() for x in family_ids_raw.split(",") if x.strip())
    else:
        primary = os.getenv("SEED_FAMILY_ID", "").strip()
        family_ids = (primary,) if primary else ()

    accounts_token = os.getenv("ACCOUNTS_API_TOKEN", "").strip()
    if accounts_token.lower().startswith("bearer "):
        accounts_token = accounts_token[7:].strip()
    accounts_graphql = os.getenv(
        "ACCOUNTS_GRAPHQL_ENDPOINT", "https://pp-accounts-api.monotype.com/graphql"
    ).strip()
    accounts_rest = os.getenv(
        "ACCOUNTS_REST_BASE_URL", "https://pp-accounts.monotype.com"
    ).strip()
    accounts_id_token = os.getenv("ACCOUNTS_ID_TOKEN", "").strip()
    accounts_email = os.getenv("ACCOUNTS_RESET_PASSWORD_EMAIL", os.getenv("OAUTH_USERNAME", "")).strip()
    shopify_gid = os.getenv("SHOPIFY_CUSTOMER_GID", "").strip()
    accounts = None
    if accounts_graphql and shopify_gid:
        if not accounts_token:
            accounts_token = token
        accounts = AccountsApiConfig(
            graphql_endpoint=accounts_graphql,
            rest_base_url=accounts_rest,
            bearer_token=accounts_token,
            id_token=accounts_id_token,
            reset_password_email=accounts_email,
        )

    return GraphQLSimulationConfig(
        project_root=project_root,
        endpoint=api_endpoint,
        api_endpoint=api_endpoint,
        admin_endpoint=admin_endpoint,
        nextgen_endpoint=nextgen_endpoint,
        nextgen_origin=nextgen_origin,
        nextgen_referer=nextgen_referer,
        nextgen_user_agent=nextgen_user_agent,
        route_mutations_to_bff=route_mutations_to_bff,
        bearer_token=token,
        nextgen_bearer_token=nextgen_token,
        secondary_bearer_token=secondary,
        accept_language=os.getenv("ACCEPT_LANGUAGE", "en"),
        use_customer_context=use_ctx,
        customer_context_id=os.getenv("GRAPHQL_CONTEXT_CUSTOMER_ID", "").strip(),
        accounts=accounts,
        seed=SeedConfig(
            family_id=os.getenv("SEED_FAMILY_ID", "910042901").strip(),
            deactivate_family_id=os.getenv("SEED_DEACTIVATE_FAMILY_ID", "8kL8ZM64").strip(),
            favorite_family_id=os.getenv(
                "SEED_FAVORITE_FAMILY_ID",
                os.getenv("SEED_FAMILY_ID", "910042901"),
            ).strip(),
            family_ids=family_ids,
            style_id=os.getenv("SEED_STYLE_ID", "920374778").strip(),
            deactivate_style_id=os.getenv("SEED_DEACTIVATE_STYLE_ID", "920374778").strip(),
            favorite_style_id=os.getenv(
                "SEED_FAVORITE_STYLE_ID",
                os.getenv("SEED_STYLE_ID", "920374778"),
            ).strip(),
            headline_style_id=os.getenv("SEED_HEADLINE_STYLE_ID", "920142132").strip(),
            body_style_id=os.getenv("SEED_BODY_STYLE_ID", "920233774").strip(),
            variation_style_id=os.getenv(
                "SEED_VARIATION_STYLE_ID", "e7z4R6sG"
            ).strip(),
            variation_id=os.getenv("SEED_VARIATION_ID", ""),
            variation_md5=os.getenv(
                "SEED_VARIATION_MD5", "b783215634650cf0a55e0d723123d5e0"
            ).strip(),
            deactivate_variation_md5=os.getenv(
                "SEED_DEACTIVATE_VARIATION_MD5",
                "394675e814698b1c770e81582907a9eb",
            ).strip(),
            imported_style_id=os.getenv("SEED_IMPORTED_STYLE_ID", "").strip(),
            font_list_id=os.getenv("FONT_LIST_ID", os.getenv("SEED_FONT_LIST_ID", "")),
            upload_session_id=os.getenv("SEED_UPLOAD_SESSION_ID", ""),
            byof_batch_id=os.getenv("SEED_BYOF_BATCH_ID", ""),
            sharee_id=os.getenv("SEED_SHARING_SHAREE_ID", ""),
            fontproject_contributor_access_id=int(
                os.getenv("SEED_FONTPROJECT_CONTRIBUTOR_ACCESS_ID", "34")
            ),
            project_id=os.getenv("PROJECT_ID", "").strip(),
            remove_project_family_id=os.getenv(
                "SEED_REMOVE_PROJECT_FAMILY_ID",
                os.getenv("SEED_DEACTIVATE_FAMILY_ID", "90672"),
            ).strip(),
            tag_id=os.getenv("TAG_ID", "").strip(),
            shopify_customer_gid=os.getenv("SHOPIFY_CUSTOMER_GID", "").strip(),
            bulk_activate_styles=_parse_bulk_style_entries(
                "SEED_BULK_ACTIVATE_STYLES",
                "920374778:Regular,920374779:Soft",
            ),
            bulk_favourite_styles=_parse_bulk_style_entries(
                "SEED_BULK_FAVOURITE_STYLES",
                "920374778:Regular,920374779:Soft",
            ),
        ),
        max_parallel_flows=max(1, int(os.getenv("SIMULATION_MAX_PARALLEL_FLOWS", "4"))),
        skip_flows=_parse_flow_list(os.getenv("SKIP_E2E_FLOWS", "")),
        flow_filter=None,
    )
