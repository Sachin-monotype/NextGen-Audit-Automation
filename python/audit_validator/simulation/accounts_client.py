"""Monotype Accounts API — customer profile update and password reset (non-mtconnect)."""

from __future__ import annotations

from typing import Any

import requests

from .config import AccountsApiConfig

CUSTOMER_UPDATE_MUTATION = """
mutation CustomerUpdate($input: CustomerInput!) {
  customerUpdate(input: $input) {
    firstName
    lastName
    __typename
  }
}
""".strip()


class AccountsApiClient:
    def __init__(self, cfg: AccountsApiConfig) -> None:
        self._cfg = cfg

    def customer_update(
        self,
        *,
        shopify_customer_gid: str,
        first_name: str,
        last_name: str,
        current_first_name: str,
        current_last_name: str,
    ) -> dict[str, Any]:
        headers = {
            "Authorization": self._cfg.bearer_token,
            "Content-Type": "application/json",
            "accept": "*/*",
        }
        if self._cfg.id_token:
            headers["custom-idtoken"] = self._cfg.id_token

        resp = requests.post(
            self._cfg.graphql_endpoint,
            headers=headers,
            json={
                "operationName": "CustomerUpdate",
                "variables": {
                    "input": {
                        "id": shopify_customer_gid,
                        "firstName": first_name,
                        "lastName": last_name,
                        "currentFirstName": current_first_name,
                        "currentLastName": current_last_name,
                    }
                },
                "query": CUSTOMER_UPDATE_MUTATION,
            },
            timeout=120,
        )
        resp.raise_for_status()
        body = resp.json()
        if body.get("errors"):
            raise RuntimeError(str(body["errors"]))
        return body.get("data") or body

    def request_password_reset(self, email: str | None = None) -> None:
        target = (email or self._cfg.reset_password_email).strip()
        if not target:
            raise RuntimeError("ACCOUNTS_RESET_PASSWORD_EMAIL or OAUTH_USERNAME is required")

        resp = requests.post(
            f"{self._cfg.rest_base_url.rstrip('/')}/api/changePassword",
            headers={
                "Content-Type": "application/json",
                "accept": "application/json, text/plain, */*",
            },
            json={"email": target},
            timeout=120,
        )
        resp.raise_for_status()
