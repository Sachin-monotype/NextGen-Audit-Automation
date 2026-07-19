"""GraphQL HTTP client."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import requests

from .config import GraphQLSimulationConfig

_GRAPHQL_NAMED_OP = re.compile(
    r"(?:query|mutation|subscription)\s+(\w+)",
    re.IGNORECASE,
)
_GRAPHQL_KIND = re.compile(r"^\s*(query|mutation|subscription)\b", re.IGNORECASE)


def apollo_operation_name(export_name: str) -> str:
    """UPDATE_PRIVATE_TAG -> UpdatePrivateTag"""
    return "".join(part.capitalize() for part in export_name.split("_"))


def operation_name_from_document(document: str) -> str:
    """Extract CreateTeam from `mutation CreateTeam(...) { ... }`."""
    match = _GRAPHQL_NAMED_OP.search(document.strip())
    if match:
        return match.group(1)
    root = re.search(r"{\s*(\w+)\s*(?:\(|{)", document)
    if root:
        name = root.group(1)
        return name[0].upper() + name[1:]
    return "AnonymousOperation"


def is_mutation_document(document: str) -> bool:
    return _GRAPHQL_KIND.match(document.strip()).group(1).lower() == "mutation"


class GraphQLClient:
    def __init__(
        self,
        cfg: GraphQLSimulationConfig,
        *,
        admin: bool = False,
        bearer_token: str | None = None,
        endpoint: str | None = None,
    ) -> None:
        self._cfg = cfg
        self._bearer_token = bearer_token or cfg.bearer_token
        if endpoint is not None:
            self._endpoint = endpoint
        elif admin:
            self._endpoint = cfg.admin_endpoint
        else:
            self._endpoint = cfg.api_endpoint
        self._customer_id: str | None = None
        # Last x-correlation-id we minted for a request — Compare uses this to
        # fetch *our* event instead of whoever happened to fire the same op last.
        self.last_correlation_id: str | None = None
        self._project_root: Path | None = getattr(cfg, "project_root", None)

    def set_customer_id(self, customer_id: str) -> None:
        self._customer_id = customer_id

    def set_project_root(self, project_root: Path | None) -> None:
        self._project_root = project_root

    def _mint_correlation(self) -> str:
        """Mint x-correlation-id for this user (UUID shaped, user-scoped namespace).

        Format matches product curls (UUID). We derive UUID v5 from the Bearer
        identity + a fresh UUID4 so each request is unique but still tied to the
        acting user (email / idp / gcid) — same idea as the QA sheet: one cid
        per user-triggered GraphQL call that we can look up later.
        """
        import uuid

        try:
            from audit_validator.auth import jwt_identity

            ident = jwt_identity(self._bearer_token)
        except Exception:
            ident = {}
        user_key = (
            str(ident.get("email") or "").strip()
            or str(ident.get("idp_user_id") or "").strip()
            or str(ident.get("gcid") or "").strip()
            or "anonymous"
        )
        # UUID5 namespace from user + random → still a valid UUID for headers/Mongo.
        cid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"monotype-audit:{user_key}:{uuid.uuid4()}"))
        self.last_correlation_id = cid
        return cid

    def _uses_nextgen_bff(self) -> bool:
        return self._endpoint.rstrip("/").endswith("/graph")

    def _base_headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._bearer_token}",
            "Content-Type": "application/json",
            "accept-language": self._cfg.accept_language,
            # Own the correlation so audit envelopes carry a cid we can look up.
            "x-correlation-id": self._mint_correlation(),
        }
        if self._customer_id:
            headers["x-context-customerid"] = self._customer_id
        return headers

    def request(self, document: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        if self._uses_nextgen_bff():
            return self.request_apollo(
                operation_name_from_document(document),
                document,
                variables,
                browser=True,
            )
        headers = self._base_headers()
        headers["accept"] = "application/json"
        minted = headers.get("x-correlation-id")

        resp = requests.post(
            self._endpoint,
            headers=headers,
            json={"query": document, "variables": variables or {}},
            timeout=120,
        )
        resp.raise_for_status()
        self._capture_correlation(resp, fallback=minted)
        body = resp.json()
        if body.get("errors"):
            raise RuntimeError(str(body["errors"]))
        return body.get("data") or body

    def _capture_correlation(self, resp: Any, *, fallback: str | None = None) -> str | None:
        """Prefer response ``correlation-id`` (Cloudflare-safe) over minted x-correlation-id."""
        from audit_validator.correlation import extract_correlation

        cid = extract_correlation(
            response_headers=getattr(resp, "headers", None),
            fallback=fallback,
        )
        if cid:
            self.last_correlation_id = cid
        return cid

    def request_apollo(
        self,
        operation_name: str,
        document: str,
        variables: dict[str, Any] | None = None,
        *,
        browser: bool = False,
    ) -> dict[str, Any]:
        """NextGen /graph expects Apollo Client payload shape."""
        headers = self._base_headers()
        headers["accept"] = "application/graphql-response+json,application/json;q=0.9"
        minted = headers.get("x-correlation-id")
        if browser or self._uses_nextgen_bff():
            headers.update(
                {
                    "origin": self._cfg.nextgen_origin,
                    "referer": self._cfg.nextgen_referer,
                    "user-agent": self._cfg.nextgen_user_agent,
                    "sec-ch-ua": '"Google Chrome";v="149", "Chromium";v="149", "Not)A;Brand";v="24"',
                    "sec-ch-ua-mobile": "?0",
                    "sec-ch-ua-platform": '"macOS"',
                    "sec-fetch-dest": "empty",
                    "sec-fetch-mode": "cors",
                    "sec-fetch-site": "same-origin",
                }
            )

        resp = requests.post(
            self._endpoint,
            headers=headers,
            json={
                "operationName": operation_name,
                "variables": variables or {},
                "extensions": {
                    "clientLibrary": {"name": "@apollo/client", "version": "4.0.9"},
                },
                "query": document,
            },
            timeout=120,
        )
        resp.raise_for_status()
        self._capture_correlation(resp, fallback=minted)
        body = resp.json()
        if body.get("errors"):
            raise RuntimeError(str(body["errors"]))
        return body.get("data") or body


class DualEndpointGraphQLClient:
    """
    PP preprod routing:
      - mutations → NextGen BFF /graph (audit events)
      - queries   → mtconnect /graphql
    """

    def __init__(
        self,
        cfg: GraphQLSimulationConfig,
        *,
        admin: bool = False,
        bearer_token: str | None = None,
    ) -> None:
        self._cfg = cfg
        token = bearer_token or cfg.bearer_token
        from ..auth import resolve_nextgen_bearer_token

        nextgen_token = bearer_token or resolve_nextgen_bearer_token() or cfg.nextgen_bearer_token
        self._api = GraphQLClient(
            cfg,
            admin=admin,
            bearer_token=token,
            endpoint=cfg.admin_endpoint if admin else cfg.api_endpoint,
        )
        self._bff = GraphQLClient(cfg, bearer_token=nextgen_token, endpoint=cfg.nextgen_endpoint)
        self.nextgen_client = self._bff

    def set_customer_id(self, customer_id: str) -> None:
        self._api.set_customer_id(customer_id)
        self._bff.set_customer_id(customer_id)

    def request(self, document: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        if is_mutation_document(document):
            data = self._bff.request(document, variables)
            self.last_correlation_id = self._bff.last_correlation_id
            return data
        data = self._api.request(document, variables)
        self.last_correlation_id = self._api.last_correlation_id
        return data

    def request_apollo(
        self,
        operation_name: str,
        document: str,
        variables: dict[str, Any] | None = None,
        *,
        browser: bool = False,
    ) -> dict[str, Any]:
        data = self._bff.request_apollo(operation_name, document, variables, browser=browser)
        self.last_correlation_id = self._bff.last_correlation_id
        return data

    @property
    def last_correlation_id(self) -> str | None:
        return getattr(self, "_last_correlation_id", None)

    @last_correlation_id.setter
    def last_correlation_id(self, value: str | None) -> None:
        self._last_correlation_id = value

    def set_project_root(self, project_root: Path | None) -> None:
        self._api.set_project_root(project_root)
        self._bff.set_project_root(project_root)
