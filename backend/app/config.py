"""Application configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass
class Settings:
    mongo_url: str
    mongo_db: str
    mongo_raw: str
    mongo_enriched: str
    mongo_dlq: str
    audit_project_root: Path
    default_limit: int
    max_limit: int
    page_size_options: list[int]
    cors_origins: list[str]
    retention_max_docs: int
    retention_interval_sec: int


def load_settings() -> Settings:
    root = Path(__file__).resolve().parents[2]
    load_dotenv(root / ".env")

    audit_root = os.getenv("AUDIT_PROJECT_ROOT", "").strip()
    if not audit_root or audit_root == ".":
        audit_project_root = root
    else:
        candidate = Path(audit_root)
        audit_project_root = candidate if candidate.is_absolute() else (root / candidate)

    cors = os.getenv("CORS_ORIGINS", "http://localhost:5173,http://localhost:5174")
    page_sizes = [20, 50, 100, 200]
    return Settings(
        mongo_url=os.getenv("MONGO_DB_URL", "mongodb://localhost:27017"),
        mongo_db=os.getenv("MONGO_DB_NAME", "AuditLogsPreprod"),
        mongo_raw=os.getenv("MONGO_COLLECTION_RAW", "raw"),
        mongo_enriched=os.getenv("MONGO_COLLECTION_ENRICHED", "enriched"),
        mongo_dlq=os.getenv("MONGO_COLLECTION_DLQ", "dlq"),
        audit_project_root=audit_project_root.resolve(),
        default_limit=int(os.getenv("API_DEFAULT_PAGE_SIZE", "20")),
        max_limit=int(os.getenv("API_MAX_PAGE_SIZE", "200")),
        page_size_options=page_sizes,
        cors_origins=[x.strip() for x in cors.split(",") if x.strip()],
        retention_max_docs=int(os.getenv("MONGO_RETENTION_MAX_DOCS_PER_OPERATION", "20")),
        retention_interval_sec=int(os.getenv("MONGO_RETENTION_INTERVAL_SEC", "3600")),
    )
