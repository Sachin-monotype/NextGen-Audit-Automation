"""Shared MongoClient factory — TLS CA bundle for Atlas on macOS."""

from __future__ import annotations

from urllib.parse import urlparse

from pymongo import MongoClient


def create_mongo_client(url: str, **kwargs) -> MongoClient:
    """Return a MongoClient.

    Atlas (``mongodb+srv://`` or explicit tls/ssl) uses certifi's CA bundle.
    Plain ``mongodb://127.0.0.1`` must not force TLS — local mongod has none.
    """
    parsed = urlparse(url or "")
    scheme = (parsed.scheme or "").lower()
    query = (parsed.query or "").lower()
    wants_tls = scheme == "mongodb+srv" or "tls=true" in query or "ssl=true" in query
    if wants_tls:
        try:
            import certifi

            kwargs.setdefault("tlsCAFile", certifi.where())
        except ImportError:
            pass
    return MongoClient(url, **kwargs)
