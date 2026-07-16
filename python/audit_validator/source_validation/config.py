"""Source-validation API config from .env (mirrors resolver Postman env)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from ..project_root import find_project_root
from ..auth import _strip_bearer, resolve_discovery_bearer_token


def _bearer_header(token: str) -> str:
    raw = _strip_bearer(token)
    return f"Bearer {raw}" if raw else ""


@dataclass(frozen=True)
class SourceValidationConfig:
    project_root: Path
    discovery_base_url: str
    discovery_bearer_token: str
    ums_base_url: str
    ums_client_id: str
    cms_base_url: str
    cms_client_id: str
    ams_base_url: str
    ams_client_id: str
    ams_api_key: str
    gcid: str
    audit_events_xlsx: Path | None = None
    max_discovery_calls_per_iteration: int = 12
    queue_pairs_dir: Path | None = None
    sample_source: str = "fresh"  # fresh | queue-pairs | auto

    @property
    def discovery_ready(self) -> bool:
        return bool(self.discovery_base_url and _strip_bearer(self.discovery_bearer_token))

    @property
    def discovery_auth_header(self) -> str:
        return _bearer_header(self.discovery_bearer_token)

    @property
    def source_truth(self) -> str:
        """``api`` (HTTP) or ``db`` (MySQL). Env: SOURCE_TRUTH / SOURCE_VALIDATION_TRUTH."""
        raw = (
            os.getenv("SOURCE_TRUTH")
            or os.getenv("SOURCE_VALIDATION_TRUTH")
            or "api"
        ).strip().lower()
        return "db" if raw in {"db", "mysql", "sql"} else "api"

    @property
    def mysql_source_ready(self) -> bool:
        return bool(
            (os.getenv("MYSQL_HOST") or "").strip()
            and (os.getenv("MYSQL_USER") or "").strip()
            and (os.getenv("MYSQL_PASSWORD") or "").strip()
        )

    @property
    def ums_ready(self) -> bool:
        # gcid is resolved per-event from the enriched actor.globalCustomerId, so we
        # must NOT require the global cfg.gcid here — that would disable UMS lookups
        # (and skip every actor role/profile attribute) whenever GRAPHQL_CONTEXT_CUSTOMER_ID
        # is blank, which is the common case.
        if self.source_truth == "db" and self.mysql_source_ready:
            return True
        return bool(self.ums_base_url and self.ums_client_id)

    @property
    def cms_ready(self) -> bool:
        if self.source_truth == "db" and self.mysql_source_ready:
            return True
        return bool(self.cms_base_url and self.cms_client_id)

    @property
    def ams_ready(self) -> bool:
        # The single-asset GET only needs the base URL + client id (header auth);
        # the API key is required solely for the sharing/bulk endpoints.
        if self.source_truth == "db" and self.mysql_source_ready:
            return True
        return bool(self.ams_base_url and self.ams_client_id)


def load_source_validation_config(project_root: Path | None = None) -> SourceValidationConfig:
    root = project_root or find_project_root()
    load_dotenv(root / ".env")

    qp = root / "reports" / "queue-pairs" / "enriched"
    audit_xlsx = Path(
        os.getenv(
            "AUDIT_EVENTS_XLSX",
            str(Path.home() / "Downloads" / "MT Connect NextGen" / "audit-events.xlsx"),
        )
    )
    return SourceValidationConfig(
        project_root=root,
        discovery_base_url=os.getenv(
            "DISCOVERY_BASE_URL", "https://mtc-middleware-discovery.monotype-pp.com"
        ).rstrip("/"),
        discovery_bearer_token=resolve_discovery_bearer_token(),
        ums_base_url=os.getenv("UMS_BASE_URL", "https://usermanagement-pp.monotype.com").rstrip(
            "/"
        ),
        ums_client_id=os.getenv("UMS_CLIENT_ID", "mt-events-resolver-service"),
        cms_base_url=os.getenv(
            "CMS_BASE_URL", "https://customermanagement-preprod.monotype.com"
        ).rstrip("/"),
        cms_client_id=os.getenv("CMS_CLIENT_ID", "mt-events-resolver-service"),
        ams_base_url=os.getenv(
            "AMS_BASE_URL", "http://assetmanagement-asteria.enterprisenonprod.com/api"
        ).rstrip("/"),
        ams_client_id=os.getenv("AMS_CLIENT_ID", "mt-audit-log-resolver-service"),
        ams_api_key=os.getenv("AMS_API_KEY", "").strip(),
        gcid=os.getenv("GRAPHQL_CONTEXT_CUSTOMER_ID", "").strip(),
        audit_events_xlsx=audit_xlsx if audit_xlsx.is_file() else None,
        # Default high enough that a full multi-operation run fetches every font
        # document. A low cap silently truncates Discovery and turns un-fetched
        # font fields into false FAIL/SKIP rows. Lower it only for quick single-op runs.
        max_discovery_calls_per_iteration=int(os.getenv("SOURCE_VALIDATION_MAX_DISCOVERY_CALLS", "500")),
        queue_pairs_dir=qp if qp.is_dir() else None,
        sample_source=os.getenv("SOURCE_VALIDATION_SAMPLE_SOURCE", "fresh").strip().lower(),
    )
