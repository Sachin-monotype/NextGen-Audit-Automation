"""RabbitMQ → MongoDB ingestion (ported from the audit-sense service).

Continuously drains the platform subscription queues into MongoDB so the audit UI
always has fresh, complete raw + enriched pairs, then prunes to the latest N docs per
operation. Run standalone with ``python -m audit_validator.ingestion`` or control it
from the backend via ``IngestionService``.
"""

from .config import IngestionConfig, QueueBinding, load_ingestion_config
from .repository import MongoWriter
from .service import IngestionService

__all__ = [
    "IngestionConfig",
    "QueueBinding",
    "load_ingestion_config",
    "MongoWriter",
    "IngestionService",
]
