"""CasePilot MCP HTTP/SSE client (streamable MCP at /mcp).

Auth: ``Authorization: Bearer cp_api_…`` (CASEPILOT_API_KEY).

CasePilot sits behind Cloudflare with **broken session affinity**: ``initialize``
often lands on instance A while the next ``tools/call`` hits instance B → HTTP 404
``Session not found``. Spec-compliant fix: on 404, start a **new** session and
retry the same tool call (fresh initialize per attempt) until it succeeds.
"""

from __future__ import annotations

import http.cookiejar
import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


DEFAULT_MCP_URL = "https://casepilot.monotype-pp.com/mcp"

# LB flakiness: ~30–70% of init→call pairs miss affinity. 15 tries ≈ ~99.99% if p=0.3.
_SESSION_RETRY_MAX = int(os.getenv("CASEPILOT_SESSION_RETRIES", "15") or "15")


@dataclass
class CasePilotConfig:
    api_key: str
    mcp_url: str = DEFAULT_MCP_URL
    public_url: str = ""  # REST base, e.g. https://casepilot.monotype-pp.com
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
    key = (
        os.getenv("CASEPILOT_API_KEY", "").strip()
        or os.getenv("CASEPILOT_API_TOKEN", "").strip()
    )
    headless_raw = os.getenv("CASEPILOT_UI_HEADLESS", "false").strip().lower()
    mcp_url = os.getenv("CASEPILOT_MCP_URL", "").strip() or DEFAULT_MCP_URL
    # REST base for the jobs/status fallback. Defaults to the MCP host minus /mcp.
    public_url = os.getenv("CASEPILOT_PUBLIC_URL", "").strip()
    if not public_url:
        public_url = re.sub(r"/mcp/?$", "", mcp_url).rstrip("/")
    return CasePilotConfig(
        api_key=key,
        mcp_url=mcp_url,
        public_url=public_url,
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
    """Streamable-HTTP MCP client with LB-safe session retry."""

    def __init__(self, config: CasePilotConfig | None = None):
        self.config = config or load_casepilot_config()
        self._session_id: str | None = None
        self._req_id = 0
        # Cloudflare pins a backend instance via an affinity cookie (__cf_bm / lb
        # cookies). urllib.urlopen drops cookies, so initialize and the follow-up
        # tools/call used to land on different instances → "Session not found".
        # A shared cookie jar keeps both requests on the same instance, which both
        # fixes the session errors and removes the need to re-initialize per call.
        self._cookies = http.cookiejar.CookieJar()
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self._cookies)
        )

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    def _reset_session(self) -> None:
        self._session_id = None
        # Drop the affinity cookie too so the next initialize can land on a
        # healthy instance instead of the one that just lost our session.
        try:
            self._cookies.clear()
        except Exception:
            pass

    @staticmethod
    def _is_session_missing(exc: CasePilotMcpError | str | dict[str, Any] | None) -> bool:
        text = ""
        if isinstance(exc, CasePilotMcpError):
            text = str(exc)
            if isinstance(exc.payload, dict):
                text += " " + json.dumps(exc.payload)
            elif exc.payload is not None:
                text += " " + str(exc.payload)
        elif isinstance(exc, dict):
            text = json.dumps(exc)
        else:
            text = str(exc or "")
        low = text.lower()
        return (
            "session not found" in low
            or "session expired" in low
            or ("mcp-session" in low and "not found" in low)
        )

    @staticmethod
    def _is_ip_banned(exc: CasePilotMcpError | str | dict[str, Any] | None) -> bool:
        text = str(exc or "")
        if isinstance(exc, CasePilotMcpError) and exc.payload is not None:
            text += " " + (
                json.dumps(exc.payload) if isinstance(exc.payload, dict) else str(exc.payload)
            )
        low = text.lower()
        return (
            "ip_banned" in low
            or ("ip address" in low and "blocked" in low)
            or "error 1006" in low
            or ("cloudflare" in low and "403" in low and "access denied" in low)
        )

    @staticmethod
    def _friendly_http_error(code: int, detail: str) -> str:
        low = (detail or "").lower()
        if code == 403 and (
            "ip_banned" in low or "blocked your ip" in low or "error 1006" in low
        ):
            return (
                "CasePilot Cloudflare blocked this machine's IP (Error 1006 / ip_banned). "
                "Ask CasePilot/Cloudflare admins to unblock your IP, connect via corporate VPN, "
                "and avoid hammering MCP. This is not a TestRail or recipe bug."
            )
        if code == 404 and "session not found" in low:
            # Internal signal for retry — not shown to users until retries exhaust.
            return "Session not found"
        return f"CasePilot MCP HTTP {code}: {(detail or '')[:400]}"

    def _headers(self, *, with_session: bool = True) -> dict[str, str]:
        if not self.config.configured:
            raise CasePilotMcpError("CASEPILOT_API_KEY is not set")
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
            with self._opener.open(req, timeout=120) as resp:
                hdrs = {k.lower(): v for k, v in resp.headers.items()}
                text = resp.read().decode("utf-8", errors="replace")
                return hdrs, text
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise CasePilotMcpError(
                self._friendly_http_error(exc.code, detail),
                payload={"status": exc.code, "body": detail},
            ) from exc
        except urllib.error.URLError as exc:
            raise CasePilotMcpError(f"CasePilot MCP unreachable: {exc}") from exc

    @staticmethod
    def _parse_sse_json(text: str) -> Any:
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
            m = re.findall(r"data:\s*(\{.*?\})(?=\n(?:event:|data:|$))", text, re.S)
            for chunk in m:
                try:
                    payloads.append(json.loads(chunk))
                except json.JSONDecodeError:
                    continue
        return payloads[-1] if payloads else None

    def ensure_session(self, *, force: bool = False) -> str:
        """Open a new MCP session via initialize (no notifications/initialized).

        CasePilot often 404s ``notifications/initialized``; tools/call works without it
        when the request hits the same LB instance that handled initialize.
        """
        if self._session_id and not force:
            return self._session_id
        self._reset_session()
        hdrs, text = self._post(
            {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "nextgen-audit-automation", "version": "1.2"},
                },
            },
            with_session=False,
        )
        sid = hdrs.get("mcp-session-id")
        if not sid:
            raise CasePilotMcpError(
                "CasePilot MCP did not return mcp-session-id", payload=text
            )
        self._session_id = sid
        return sid

    def _decode_tool_result(self, text: str) -> dict[str, Any]:
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
            raise CasePilotMcpError("CasePilot tool error", payload=result)

        merged: dict[str, Any] = {}
        structured = result.get("structuredContent")
        if isinstance(structured, dict):
            merged.update(structured)
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
                for k, v in parsed.items():
                    if k not in merged or merged.get(k) in (None, "", [], {}):
                        merged[k] = v
                    elif k in {"job_ids", "jobs", "results", "runs"} and not merged.get(k):
                        merged[k] = v
        if not merged:
            return result if isinstance(result, dict) else {"ok": True, "result": result}
        ids = extract_casepilot_job_ids(merged)
        if ids and not merged.get("job_ids"):
            merged["job_ids"] = ids
        return merged

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """Call an MCP tool with LB-safe session retry.

        With the shared cookie jar the affinity cookie keeps ``initialize`` and
        ``tools/call`` on the same instance, so we reuse the existing session
        (fast) and only re-initialize when the server reports ``Session not
        found`` (up to ``CASEPILOT_SESSION_RETRIES``). Never retries IP bans.
        """
        last_err: CasePilotMcpError | None = None
        max_tries = max(1, _SESSION_RETRY_MAX)
        for attempt in range(max_tries):
            try:
                # Reuse the affinity-pinned session; force a fresh one only after a
                # prior "Session not found" reset (attempt > 0 with no session).
                self.ensure_session()
                hdrs, text = self._post(
                    {
                        "jsonrpc": "2.0",
                        "id": self._next_id(),
                        "method": "tools/call",
                        "params": {"name": name, "arguments": arguments or {}},
                    }
                )
                if hdrs.get("mcp-session-id"):
                    self._session_id = hdrs["mcp-session-id"]
                return self._decode_tool_result(text)
            except CasePilotMcpError as exc:
                last_err = exc
                if self._is_ip_banned(exc):
                    raise
                if self._is_session_missing(exc):
                    self._reset_session()
                    # Short backoff so we are less likely to hit the same cold shard.
                    time.sleep(min(0.2 * (attempt + 1), 1.5))
                    continue
                raise
        assert last_err is not None
        raise CasePilotMcpError(
            (
                f"CasePilot MCP session affinity failed after {max_tries} attempts "
                f"(tool={name}). Cloudflare is routing initialize and tools/call to "
                f"different instances. Retry Send — this is a CasePilot infra issue, "
                f"not your TestRail steps. Last error: {last_err}"
            ),
            payload=getattr(last_err, "payload", None),
        ) from last_err

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

    # ---- Batch job status (wait_for_run_jobs + REST fallback) -------------------

    def wait_for_run_jobs(self, job_ids: list[int]) -> dict[str, Any]:
        """Single MCP call that returns the status of every queued runner job.

        Replaces looping ``get_run_status`` per job (and ``wait_for_completion``).
        """
        ids = [int(x) for x in job_ids if str(x).strip().lstrip("-").isdigit()]
        return self.call_tool("wait_for_run_jobs", {"job_ids": ids})

    def rest_jobs_status(self, job_ids: list[int]) -> dict[str, Any]:
        """REST fallback: POST {public_url}/api/mcp/v1/jobs/status with the same Bearer.

        Used when the MCP session is lost / disconnected — this path has no session
        affinity problem, so it always reaches a healthy instance.
        """
        import httpx

        ids = [int(x) for x in job_ids if str(x).strip().lstrip("-").isdigit()]
        base = (self.config.public_url or "").rstrip("/")
        if not base:
            raise CasePilotMcpError("CASEPILOT_PUBLIC_URL is not set for REST status fallback")
        url = f"{base}/api/mcp/v1/jobs/status"
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        try:
            resp = httpx.post(url, json={"job_ids": ids}, headers=headers, timeout=30.0)
        except httpx.HTTPError as exc:
            raise CasePilotMcpError(f"CasePilot REST jobs/status unreachable: {exc}") from exc
        if resp.status_code >= 400:
            raise CasePilotMcpError(
                self._friendly_http_error(resp.status_code, resp.text),
                payload={"status": resp.status_code, "body": resp.text[:800]},
            )
        try:
            return resp.json()
        except Exception as exc:  # noqa: BLE001
            raise CasePilotMcpError("CasePilot REST jobs/status returned non-JSON", payload=resp.text[:800]) from exc

    def poll_jobs_status(self, job_ids: list[int]) -> list[dict[str, Any]]:
        """Non-blocking snapshot of each job's current status.

        Prefers the REST ``/api/mcp/v1/jobs/status`` endpoint (fast, no MCP session).
        Falls back to per-job ``get_run_status`` MCP calls when REST is unavailable.
        Never blocks until jobs finish — safe for UI refresh polling.
        """
        ids = [int(x) for x in job_ids if str(x).strip().lstrip("-").isdigit()]
        if not ids:
            return []
        try:
            payload = self.rest_jobs_status(ids)
            return normalize_job_statuses(payload, ids)
        except CasePilotMcpError:
            pass
        # REST unavailable — one MCP call per job (still non-blocking).
        rows: list[dict[str, Any]] = []
        for jid in ids:
            try:
                st = self.get_run_status(jid)
                if "job_id" not in st:
                    st = {"job_id": jid, **st}
                rows.append(st)
            except CasePilotMcpError as exc:
                if self._is_ip_banned(exc):
                    raise
                rows.append({"job_id": jid, "status": "pending", "error": str(exc)})
        return normalize_job_statuses(rows, ids)

    def batch_run_status(self, job_ids: list[int]) -> list[dict[str, Any]]:
        """Alias for non-blocking status polling (used by UI refresh)."""
        return self.poll_jobs_status(job_ids)

    def wait_for_jobs_terminal(
        self,
        job_ids: list[int],
        *,
        poll_secs: float = 15.0,
        max_wait_secs: float = 1200.0,
        on_poll=None,
    ) -> list[dict[str, Any]]:
        """Block until every job is completed/failed/cancelled (or timeout).

        Prefers the single ``wait_for_run_jobs`` MCP call; on ``Session not found``
        or MCP disconnect it polls the REST ``/api/mcp/v1/jobs/status`` endpoint
        every ``poll_secs`` seconds. Never re-queues tests.
        """
        ids = [int(x) for x in job_ids if str(x).strip().lstrip("-").isdigit()]
        if not ids:
            return []
        deadline = time.monotonic() + max_wait_secs
        last: list[dict[str, Any]] = []
        while True:
            # Non-blocking poll — never call wait_for_run_jobs here (it blocks until
            # jobs finish and urllib times out on long UI runs).
            try:
                statuses = self.poll_jobs_status(ids)
            except CasePilotMcpError as exc:
                if self._is_ip_banned(exc):
                    raise
                # Session lost → REST-only retry on next tick.
                try:
                    statuses = normalize_job_statuses(self.rest_jobs_status(ids), ids)
                except CasePilotMcpError:
                    statuses = last
            last = statuses or last
            if on_poll:
                try:
                    on_poll(statuses)
                except Exception:  # noqa: BLE001
                    pass
            states = {str(s.get("status") or "").lower() for s in statuses}
            if statuses and states <= _TERMINAL_STATES:
                return statuses
            if time.monotonic() >= deadline:
                return last
            time.sleep(poll_secs)


_TERMINAL_STATES = {
    "completed",
    "passed",
    "pass",
    "success",
    "failed",
    "error",
    "cancelled",
    "canceled",
}


def normalize_job_statuses(payload: Any, job_ids: list[int]) -> list[dict[str, Any]]:
    """Flatten wait_for_run_jobs / REST jobs/status responses to [{job_id, status, …}].

    Accepts the various shapes CasePilot returns: ``{jobs:[…]}``, ``{results:[…]}``,
    ``{runs:[…]}``, ``{statuses:[…]}``, a bare list, or ``{"<id>": {...}}``.
    """
    def _rows_from(node: Any) -> list[dict[str, Any]]:
        if isinstance(node, list):
            return [r for r in node if isinstance(r, dict)]
        if isinstance(node, dict):
            for key in ("jobs", "results", "runs", "statuses", "job_statuses"):
                val = node.get(key)
                if isinstance(val, list):
                    return [r for r in val if isinstance(r, dict)]
            # Mapping of id -> status dict
            mapped = [
                {"job_id": k, **v} if isinstance(v, dict) else {"job_id": k, "status": v}
                for k, v in node.items()
                if str(k).strip().lstrip("-").isdigit()
            ]
            if mapped:
                return mapped
        return []

    rows = _rows_from(payload)
    out: list[dict[str, Any]] = []
    by_id: dict[int, dict[str, Any]] = {}
    for r in rows:
        jid = r.get("job_id") or r.get("runner_job_id") or r.get("id")
        try:
            jid_int = int(jid)
        except (TypeError, ValueError):
            jid_int = 0
        row = {**r}
        if jid_int:
            row["job_id"] = jid_int
            by_id[jid_int] = row
        out.append(row)
    # Ensure every requested id is represented (pending if the server omitted it).
    for jid in job_ids:
        if jid not in by_id:
            placeholder = {"job_id": jid, "status": "pending"}
            out.append(placeholder)
            by_id[jid] = placeholder
    # Prefer one row per requested id, in request order.
    return [by_id[jid] for jid in job_ids if jid in by_id] or out


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
        # One resilient preflight is enough for the modal badge; avoid stacking
        # three flaky calls that used to surface "session expired" to the UI.
        pre = client.preflight()
        out.update(
            {
                "ok": bool(pre.get("ok")),
                "preflight": pre,
                "connectors": {
                    "registered": (pre.get("connector") or {}).get("registered"),
                    "online": (pre.get("connector") or {}).get("online"),
                    "runners": [],
                },
                "connection_info": {
                    "mcp_url": cfg.mcp_url,
                    "email": pre.get("email"),
                },
            }
        )
        # Best-effort extras — never fail health if these flake
        try:
            conn = client.list_connectors()
            out["connectors"]["runners"] = conn.get("runners") or []
        except Exception:  # noqa: BLE001
            pass
        try:
            info = client.connection_info()
            out["connection_info"].update(
                {
                    "mcp_url": info.get("mcp_url") or cfg.mcp_url,
                    "dashboard_url": info.get("dashboard_url"),
                    "email": info.get("email") or pre.get("email"),
                }
            )
        except Exception:  # noqa: BLE001
            pass
    except Exception as exc:
        out["ok"] = False
        out["error"] = str(exc)
    return out
