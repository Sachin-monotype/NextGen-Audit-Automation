"""Capture GraphQL/curl trigger context for source validation (not Raw Mongo)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def build_trigger_context(
    *,
    operation: str,
    correlation_id: str | None,
    graphql_response: dict[str, Any] | None = None,
    graphql_input: dict[str, Any] | None = None,
    user_agent: str | None = None,
    jwt_identity: dict[str, Any] | None = None,
    success: bool | None = None,
) -> dict[str, Any]:
    """Build the trigger payload we compare enriched envelope fields against."""
    ua = (
        user_agent
        or os.getenv("NEXTGEN_USER_AGENT")
        or (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
        )
    )
    service = os.getenv("AUDIT_SOURCE_SERVICE", "mtconnect-api").strip() or "mtconnect-api"
    platform = os.getenv("AUDIT_SOURCE_PLATFORM", "nextGen").strip() or "nextGen"
    env = (
        os.getenv("AUDIT_SOURCE_PLATFORM_ENVIRONMENT")
        or os.getenv("SOURCE_PLATFORM_ENVIRONMENT")
        or "web"
    ).strip() or "web"
    version = os.getenv("AUDIT_SOURCE_PLATFORM_VERSION", "1.0.0").strip() or "1.0.0"

    op_state = "success"
    if success is False:
        op_state = "failure"
    elif isinstance(graphql_response, dict):
        # Heuristic: GraphQL errors / success:false
        for node in graphql_response.values():
            if isinstance(node, dict) and node.get("success") is False:
                op_state = "failure"
                break

    return {
        "operation": operation,
        "xCorrelationId": correlation_id or "",
        "correlation_id": correlation_id or "",
        "eventVersion": os.getenv("AUDIT_EVENT_VERSION", "1").strip() or "1",
        "graphql_response": graphql_response or {},
        "graphql_input": graphql_input or {},
        "jwt_identity": jwt_identity or {},
        "request": {
            "userAgent": ua,
            "user-agent": ua,
            "service": service,
            "platform": platform,
            "platformEnvironment": env,
            "platformVersion": version,
            "operation": operation,
            "operationState": op_state,
            "operationIndex": 0,
        },
        "source": {
            "operation": operation,
            "service": service,
            "platform": platform,
            "platformEnvironment": env,
            "platformVersion": version,
            "actorUserAgent": ua,
            "operationState": op_state,
            "operationIndex": 0,
            "type": ["user"],
        },
    }


def save_trigger_context(
    project_root: Path,
    display_name: str,
    context: dict[str, Any],
) -> Path:
    trigger_dir = project_root / "payload" / "trigger"
    trigger_dir.mkdir(parents=True, exist_ok=True)
    path = trigger_dir / f"{display_name}.json"
    path.write_text(json.dumps(context, indent=2, default=str), encoding="utf-8")
    return path


def load_trigger_context(project_root: Path, operation: str) -> dict[str, Any] | None:
    path = project_root / "payload" / "trigger" / f"{operation}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None
