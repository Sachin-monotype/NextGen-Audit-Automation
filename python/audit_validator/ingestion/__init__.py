"""RabbitMQ → MongoDB ingestion (ported from the audit-sense service).

Continuously drains the platform subscription queues into MongoDB so the audit UI
always has fresh, complete raw + enriched pairs, then prunes to the latest N docs per
operation. Run standalone with ``python -m audit_validator.ingestion`` or control it
from the backend via ``IngestionService``.
"""

from .config import IngestionConfig, QueueBinding, IngestLaneConfig, load_ingestion_config, load_ingest_lanes
from .repository import MongoWriter
from .service import IngestionService
from .targets import ingest_mongo_databases, ingest_target_names, multi_target_ingestion_enabled

__all__ = [
    "IngestionConfig",
    "IngestLaneConfig",
    "QueueBinding",
    "load_ingestion_config",
    "load_ingest_lanes",
    "MongoWriter",
    "IngestionService",
    "ingest_mongo_databases",
    "ingest_target_names",
    "multi_target_ingestion_enabled",
]
