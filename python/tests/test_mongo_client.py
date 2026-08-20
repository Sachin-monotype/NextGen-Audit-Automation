"""Local mongodb:// URLs must not force TLS (Atlas srv still does)."""

from unittest.mock import patch

from audit_validator.mongo_client import create_mongo_client


def test_local_url_does_not_force_tls():
    with patch("audit_validator.mongo_client.MongoClient") as mock:
        create_mongo_client("mongodb://127.0.0.1:27017")
        kwargs = mock.call_args.kwargs
        assert "tlsCAFile" not in kwargs


def test_atlas_srv_sets_ca_bundle():
    with patch("audit_validator.mongo_client.MongoClient") as mock:
        create_mongo_client("mongodb+srv://u:p@cluster.mongodb.net/")
        kwargs = mock.call_args.kwargs
        assert "tlsCAFile" in kwargs
