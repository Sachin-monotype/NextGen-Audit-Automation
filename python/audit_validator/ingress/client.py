"""HTTP client for mt-audit-log-resolver Ingress API (/v1/audit-events)."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass

import requests

from ..auth import _strip_bearer
from ..models import JsonDict

log = logging.getLogger(__name__)

_DEFAULT_URL = "https://mt-audit-log-resolver-service-preprod.monotype-pp.com/v1/audit-events"


@dataclass(frozen=True)
class IngressClientConfig:
    base_url: str
    bearer_token: str
    machine_id: str = ""
    unique_id: str = ""
    user_agent: str = "mt-audit-log-automation/1.0"
    app_version: str = "1.0.0.0"
    os_platform: str = "MAC"
    os_version: str = "26.5.0"
    cpu_arch: str = "arm64"
    request_source: str = "MT_CONNECT_BS"

    @property
    def ready(self) -> bool:
        return bool(self.base_url and _strip_bearer(self.bearer_token))


def resolve_ingress_bearer_token() -> str:
    """Same bearer chain as NextGen UI / GraphQL (NEXTGEN_BEARER_TOKEN first)."""
    explicit = _strip_bearer(os.getenv("INGRESS_BEARER_TOKEN", ""))
    if explicit:
        return explicit
    from ..auth import resolve_nextgen_bearer_token

    return resolve_nextgen_bearer_token() or ""


def load_ingress_client_config() -> IngressClientConfig:
    from .runtime_context import resolve_ingress_runtime_context

    token = resolve_ingress_bearer_token()
    ctx = resolve_ingress_runtime_context()
    return IngressClientConfig(
        base_url=(os.getenv("INGRESS_API_URL") or _DEFAULT_URL).rstrip("/"),
        bearer_token=token,
        machine_id=ctx.machine_id,
        unique_id=ctx.unique_id,
        user_agent=(os.getenv("INGRESS_USER_AGENT") or "mt-audit-log-automation/1.0").strip(),
        app_version=ctx.app_version,
        os_platform=ctx.os_platform,
        os_version=ctx.os_version,
        request_source=(os.getenv("INGRESS_REQUEST_SOURCE") or "MT_CONNECT_BS").strip(),
        cpu_arch=ctx.cpu_arch,
    )


def _headers(cfg: IngressClientConfig, payload: JsonDict) -> dict[str, str]:
    actor = payload.get("actor") or {}
    machine_id = cfg.machine_id or str(actor.get("machineId") or "").strip()
    unique_id = cfg.unique_id or str(actor.get("uniqueId") or "").strip()
    ua = cfg.user_agent
    if machine_id and unique_id and "NGAPP-BS" not in ua:
        ua = (
            f"NGAPP-BS/{cfg.app_version}; (mac {cfg.os_version.lower()}; {cfg.cpu_arch} "
            f"{machine_id}; {unique_id})"
        )
    headers = {
        "Accept": "application/json",
        "Accept-Language": "en",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {_strip_bearer(cfg.bearer_token)}",
        "User-Agent": ua,
        "x-dt-app-version": cfg.app_version,
        "x-os-platform": cfg.os_platform,
        "x-os-version": cfg.os_version,
        "x-request-source": cfg.request_source,
        "x-unauthorized-redirect": "false",
    }
    if machine_id:
        headers["x-machine-id"] = machine_id
    if unique_id:
        headers["x-unique-id"] = unique_id
    cid = str(payload.get("xCorrelationId") or "")
    if cid:
        headers["x-correlation-id"] = cid
    return headers


class IngressClient:
    def __init__(self, cfg: IngressClientConfig | None = None) -> None:
        self._cfg = cfg or load_ingress_client_config()
        self._session = requests.Session()

    def post_event(self, payload: JsonDict) -> tuple[int, str]:
        """POST single audit envelope (API accepts a one-element array)."""
        url = self._cfg.base_url
        body = json.dumps([payload], ensure_ascii=False)
        resp = self._session.post(
            url,
            data=body,
            headers=_headers(self._cfg, payload),
            timeout=60,
        )
        text = resp.text or ""
        if resp.status_code >= 400:
            log.warning("Ingress POST %s → %s %s", url, resp.status_code, text[:300])
        else:
            log.info("Ingress POST ok (%s) correlation=%s", resp.status_code, str(payload.get("xCorrelationId", ""))[:8])
        return resp.status_code, text
