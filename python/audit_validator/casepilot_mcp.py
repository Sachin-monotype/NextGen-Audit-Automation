"""CasePilot MCP HTTP/SSE client (streamable MCP at /mcp).

Auth: ``Authorization: Bearer cp_api_…`` (CASEPILOT_API_KEY).
Docs: CasePilot → Connections → CasePilot MCP API / Confluence CasePilot MCP page.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


DEFAULT_MCP_URL = "https://casepilot.monotype-pp.com/mcp"


@dataclass
class CasePilotConfig:
    api_key: str
    mcp_url: str = DEFAULT_MCP_URL
    ui_base_url: str = ""
    ui_username: str = ""
    ui_password: str = ""
    ui_browser: str = "chrome"
    ui_headless: bool = False
    ui_isolated: bool = True
    ui_app_type: str = "web"

    @property
    def configured(self) -> bool:
        return bool(self.api_key.strip())

    def ui_config(self) -> dict[str, Any]:
        return {
            "app_type": self.ui_app_type or "web",
            "base_url": self.ui_base_url,
            "username": self.ui_username,
            "password": self.ui_password,
            "browser": self.ui_browser or "chrome",
            "headless": bool(self.ui_headless),
            "isolated": bool(self.ui_isolated),
        }

    def ui_config_ready(self) -> bool:
        cfg = self.ui_config()
        return bool(cfg.get("base_url") and cfg.get("username") and cfg.get("password"))


def load_casepilot_config() -> CasePilotConfig:
    # Prefer CASEPILOT_API_KEY; accept CASEPILOT_API_TOKEN as alias.
    key = (
        os.getenv("CASEPILOT_API_KEY", "").strip()
        or os.getenv("CASEPILOT_API_TOKEN", "").strip()
    )
    headless_raw = os.getenv("CASEPILOT_UI_HEADLESS", "false").strip().lower()
    return CasePilotConfig(
        api_key=key,
        mcp_url=(os.getenv("CASEPILOT_MCP_URL", "").strip() or DEFAULT_MCP_URL),
        ui_base_url=(
            os.getenv("CASEPILOT_UI_BASE_URL", "").strip()
            or os.getenv("NEXTGEN_UI_URL", "").strip()
            or "https://nextgen.monotype-pp.com"
        ),
        ui_username=(
            os.getenv("CASEPILOT_UI_USERNAME", "").strip()
            or os.getenv("OAUTH_USERNAME", "").strip()
        ),
        ui_password=(
            os.getenv("CASEPILOT_UI_PASSWORD", "").strip()
            or os.getenv("OAUTH_PASSWORD", "").strip()
        ),
        ui_browser=os.getenv("CASEPILOT_UI_BROWSER", "chrome").strip() or "chrome",
        ui_headless=headless_raw in {"1", "true", "yes", "on"},
        ui_isolated=os.getenv("CASEPILOT_UI_ISOLATED", "true").strip().lower()
        not in {"0", "false", "no", "off"},
        ui_app_type=os.getenv("CASEPILOT_UI_APP_TYPE", "web").strip() or "web",
    )


class CasePilotMcpError(RuntimeError):
    def __init__(self, message: str, *, payload: Any = None):
        super().__init__(message)
        self.payload = payload


class CasePilotMcpClient:
    """Minimal streamable-HTTP MCP client for CasePilot tools."""

    def __init__(self, config: CasePilotConfig | None = None):
        self.config = config or load_casepilot_config()
        self._session_id: str | None = None
        self._req_id = 0

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    def _headers(self, *, with_session: bool = True) -> dict[str, str]:
        if not self.config.configured:
            raise CasePilotMcpError("CASEPILOT_API_KEY is not set")
        # Cloudflare Error 1010 blocks Python-urllib's default User-Agent.
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.config.api_key}",
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
            ),
            "Origin": "https://casepilot.monotype-pp.com",
            "Referer": "https://casepilot.monotype-pp.com/",
        }
        if with_session and self._session_id:
            headers["mcp-session-id"] = self._session_id
        return headers

    def _post(self, body: dict[str, Any], *, with_session: bool = True) -> tuple[dict[str, str], str]:
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            self.config.mcp_url,
            data=data,
            headers=self._headers(with_session=with_session),
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                hdrs = {k.lower(): v for k, v in resp.headers.items()}
                text = resp.read().decode("utf-8", errors="replace")
                return hdrs, text
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise CasePilotMcpError(
                f"CasePilot MCP HTTP {exc.code}: {detail[:500]}",
                payload={"status": exc.code, "body": detail},
            ) from exc
        except urllib.error.URLError as exc:
            raise CasePilotMcpError(f"CasePilot MCP unreachable: {exc}") from exc

    @staticmethod
    def _parse_sse_json(text: str) -> Any:
        """Return the last JSON-RPC payload from an SSE or plain JSON body."""
        text = (text or "").strip()
        if not text:
            return None
        if text.startswith("{"):
            return json.loads(text)
        payloads: list[Any] = []
        for line in text.splitlines():
            if line.startswith("data:"):
                raw = line[5:].strip()
                if not raw or raw == "[DONE]":
                    continue
                try:
                    payloads.append(json.loads(raw))
                except json.JSONDecodeError:
                    continue
        if not payloads:
            # Some servers emit multiline data blocks
            m = re.findall(r"data:\s*(\{.*?\})(?=\n(?:event:|data:|$))", text, re.S)
            for chunk in m:
                try:
                    payloads.append(json.loads(chunk))
                except json.JSONDecodeError:
                    continue
        return payloads[-1] if payloads else None

    def ensure_session(self) -> str:
        if self._session_id:
            return self._session_id
        hdrs, text = self._post(
            {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "nextgen-audit-automation", "version": "1.0"},
                },
            },
            with_session=False,
        )
        sid = hdrs.get("mcp-session-id")
        if not sid:
            raise CasePilotMcpError("CasePilot MCP did not return mcp-session-id", payload=text)
        self._session_id = sid
        # Required by streamable HTTP MCP
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        _ = text  # initialize result discarded; tools/call will fail if session bad
        return sid

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        self.ensure_session()
        hdrs, text = self._post(
            {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments or {}},
            }
        )
        # Refresh session id if server rotates it
        if hdrs.get("mcp-session-id"):
            self._session_id = hdrs["mcp-session-id"]
        msg = self._parse_sse_json(text)
        if not isinstance(msg, dict):
            raise CasePilotMcpError("Invalid CasePilot MCP response", payload=text[:2000])
        if "error" in msg:
            err = msg["error"]
            raise CasePilotMcpError(
                str(err.get("message") or err),
                payload=msg,
            )
        result = msg.get("result") or {}
        if result.get("isError"):
            raise CasePilotMcpError(f"CasePilot tool error: {name}", payload=result)

        merged: dict[str, Any] = {}
        structured = result.get("structuredContent")
        if isinstance(structured, dict):
            merged.update(structured)
        # Prefer richer text JSON when structuredContent is a thin stub (e.g. only ok=true)
        for block in result.get("content") or []:
            if not isinstance(block, dict) or block.get("type") != "text":
                continue
            raw = block.get("text") or ""
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                if "text" not in merged and raw.strip():
                    merged["text"] = raw
                continue
            if isinstance(parsed, dict):
                # Text wins for missing keys; keep structured ok flags
                for k, v in parsed.items():
                    if k not in merged or merged.get(k) in (None, "", [], {}):
                        merged[k] = v
                    elif k in {"job_ids", "jobs", "results", "runs"} and not merged.get(k):
                        merged[k] = v
        if not merged:
            return result if isinstance(result, dict) else {"ok": True, "result": result}
        # Always attach deep-extracted job ids for callers
        ids = extract_casepilot_job_ids(merged)
        if ids and not merged.get("job_ids"):
            merged["job_ids"] = ids
        return merged

    def preflight(self) -> dict[str, Any]:
        return self.call_tool("casepilot_preflight")

    def list_connectors(self) -> dict[str, Any]:
        return self.call_tool("list_connectors")

    def connection_info(self) -> dict[str, Any]:
        return self.call_tool("get_mcp_connection_info")

    def fetch_testrail_cases(self, case_ids: list[int | str]) -> dict[str, Any]:
        return self.call_tool("fetch_testrail_cases", {"case_ids": case_ids})

    def run_testrail_ui_tests(
        self,
        case_ids: list[int | str],
        *,
        ui_config: dict[str, Any] | None = None,
        context_summary: str = "",
        context_description: str = "",
        context_hints: dict[str, str] | None = None,
        wait_for_completion: bool = False,
        stop_on_failure: bool = True,
    ) -> dict[str, Any]:
        cfg = ui_config if ui_config is not None else self.config.ui_config()
        if not (cfg.get("base_url") and cfg.get("username") and cfg.get("password")):
            raise CasePilotMcpError(
                "ui_config requires base_url, username, and password "
                "(set CASEPILOT_UI_* or OAUTH_USERNAME/OAUTH_PASSWORD)"
            )
        args: dict[str, Any] = {
            "case_ids": case_ids,
            "ui_config": cfg,
            "wait_for_completion": wait_for_completion,
            "stop_on_failure": stop_on_failure,
        }
        if context_summary:
            args["context_summary"] = context_summary
        if context_description:
            args["context_description"] = context_description
        if context_hints:
            args["context_hints"] = context_hints
        return self.call_tool("run_testrail_ui_tests", args)

    def get_run_status(self, job_id: int) -> dict[str, Any]:
        return self.call_tool("get_run_status", {"job_id": int(job_id)})


def extract_casepilot_job_ids(payload: Any) -> list[int]:
    """Deep-collect numeric CasePilot runner job ids from any response shape."""
    found: list[int] = []
    seen: set[int] = set()

    def _add(val: Any) -> None:
        if isinstance(val, bool):
            return
        if isinstance(val, int) and val > 0 and val not in seen:
            seen.add(val)
            found.append(val)
        elif isinstance(val, str) and val.strip().isdigit():
            _add(int(val.strip()))

    def _walk(node: Any, *, depth: int = 0) -> None:
        if depth > 10 or node is None:
            return
        if isinstance(node, dict):
            for key, val in node.items():
                lk = str(key).lower()
                if lk in {"job_id", "runner_job_id"}:
                    _add(val)
                elif lk in {"job_ids", "runner_job_ids"}:
                    if isinstance(val, list):
                        for item in val:
                            _add(item)
                    else:
                        _add(val)
                elif lk in {"jobs", "runs", "results", "queued_jobs"} and isinstance(val, list):
                    for item in val:
                        if isinstance(item, dict):
                            for k in ("job_id", "runner_job_id"):
                                if k in item:
                                    _add(item.get(k))
                        else:
                            _add(item)
                else:
                    _walk(val, depth=depth + 1)
        elif isinstance(node, list):
            for item in node:
                _walk(item, depth=depth + 1)

    _walk(payload)
    if isinstance(payload, dict):
        text = str(payload.get("text") or "")
        if text:
            for m in re.finditer(r"\bjob_id[\"']?\s*[:=]\s*(\d+)", text, re.I):
                _add(int(m.group(1)))
    return found


def parse_testrail_case_ids(raw: str | list[Any] | None) -> list[int | str]:
    """Accept ``C73298777``, ``73298777``, comma/space lists, or JSON arrays."""
    if raw is None:
        return []
    if isinstance(raw, list):
        out: list[int | str] = []
        for item in raw:
            out.extend(parse_testrail_case_ids(str(item)))
        return out
    text = str(raw).strip()
    if not text or text.upper() in {"TR-TBD", "TBD", "N/A"}:
        return []
    parts = re.split(r"[,;\s]+", text)
    ids: list[int | str] = []
    for part in parts:
        p = part.strip()
        if not p:
            continue
        m = re.fullmatch(r"[Cc]?(\d+)", p)
        if m:
            ids.append(int(m.group(1)))
        else:
            ids.append(p)
    return ids


def health_check() -> dict[str, Any]:
    """Connectivity smoke test for API / UI status panels."""
    cfg = load_casepilot_config()
    out: dict[str, Any] = {
        "configured": cfg.configured,
        "mcp_url": cfg.mcp_url,
        "ui_config_ready": cfg.ui_config_ready(),
        "ok": False,
    }
    if not cfg.configured:
        out["error"] = "CASEPILOT_API_KEY not set"
        return out
    try:
        client = CasePilotMcpClient(cfg)
        pre = client.preflight()
        conn = client.list_connectors()
        info = client.connection_info()
        out.update(
            {
                "ok": bool(pre.get("ok")),
                "preflight": pre,
                "connectors": {
                    "registered": (pre.get("connector") or {}).get("registered"),
                    "online": (pre.get("connector") or {}).get("online"),
                    "runners": conn.get("runners") or [],
                },
                "connection_info": {
                    "mcp_url": info.get("mcp_url"),
                    "dashboard_url": info.get("dashboard_url"),
                    "email": info.get("email"),
                },
            }
        )
    except Exception as exc:
        out["ok"] = False
        out["error"] = str(exc)
    return out
