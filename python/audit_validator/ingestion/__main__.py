"""Run the ingestion service standalone: ``python -m audit_validator.ingestion``.

Loads ``.env`` from the repo root, then consumes the platform subscription queues
into MongoDB until interrupted (Ctrl-C / SIGTERM).
"""

from __future__ import annotations

import logging
import signal
import sys
import time
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None


def _load_env() -> None:
    if load_dotenv is None:
        return
    # repo root = python/audit_validator/ingestion → up 3
    root = Path(__file__).resolve().parents[3]
    env_file = root / ".env"
    if env_file.is_file():
        load_dotenv(env_file)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    _load_env()

    from .service import IngestionService

    service = IngestionService()
    service.start()

    stopping = {"flag": False}

    def _handle(signum, _frame):
        if stopping["flag"]:
            return
        stopping["flag"] = True
        logging.getLogger(__name__).info("Signal %s received — shutting down", signum)
        service.stop()

    signal.signal(signal.SIGINT, _handle)
    signal.signal(signal.SIGTERM, _handle)

    try:
        while not stopping["flag"] and service.running:
            time.sleep(2)
    finally:
        if not stopping["flag"]:
            service.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
