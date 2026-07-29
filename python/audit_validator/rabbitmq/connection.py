"""Shared pika connection helpers — AMQPS SSL for corporate brokers.

Python's default trust store (certifi) often lacks Monotype/internal CAs that
Windows already trusts. Configure via:

  RABBITMQ_CA_CERT=/path/to/corp-root-or-bundle.pem   # preferred
  RABBITMQ_SSL_VERIFY=false                           # local/dev only
"""

from __future__ import annotations

import logging
import os
import ssl
from urllib.parse import urlparse

import pika

log = logging.getLogger(__name__)


def _truthy(name: str, default: str = "true") -> bool:
    return os.getenv(name, default).strip().lower() not in ("0", "false", "no", "off")


def url_parameters(url: str) -> pika.URLParameters:
    """Build URLParameters with optional corporate CA / verify override for amqps."""
    params = pika.URLParameters(url)
    apply_ssl_options(params, url)
    return params


def apply_ssl_options(params: pika.URLParameters, url: str) -> None:
    """Attach ssl_options when the URL scheme is amqps / amqp+ssl."""
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("amqps", "amqp+ssl"):
        return

    ca_cert = (os.getenv("RABBITMQ_CA_CERT") or os.getenv("RABBITMQ_SSL_CA") or "").strip()
    verify = _truthy("RABBITMQ_SSL_VERIFY", "true")

    if ca_cert:
        context = ssl.create_default_context(cafile=ca_cert)
        log.info("RabbitMQ SSL: using CA bundle %s", ca_cert)
    elif not verify:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        log.warning(
            "RabbitMQ SSL: certificate verification disabled "
            "(RABBITMQ_SSL_VERIFY=false). Prefer RABBITMQ_CA_CERT for production."
        )
    else:
        # Default system/certifi trust — leave pika defaults alone.
        return

    params.ssl_options = pika.SSLOptions(context, parsed.hostname)
