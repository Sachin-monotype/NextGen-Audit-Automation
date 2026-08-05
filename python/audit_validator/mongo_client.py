"""Shared MongoClient factory — TLS CA bundle for Atlas on macOS."""

from __future__ import annotations

from pymongo import MongoClient


def create_mongo_client(url: str, **kwargs) -> MongoClient:
    """Return a MongoClient that verifies Atlas TLS using certifi's CA bundle."""
    try:
        import certifi

        kwargs.setdefault("tlsCAFile", certifi.where())
    except ImportError:
        pass
    return MongoClient(url, **kwargs)
