"""Interceptor Session Manager & Continuous Background Batch Comparison Machine.

Orchestrates:
1. macOS Chrome & Monotype NextGen Desktop App launchers on port 9222.
2. Real-time network capture over Chrome DevTools Protocol (CDP).
3. Deduplication of (event_name, scenario) so each unique auditable event is compared only once automatically.
4. Continuous background batch runner (default batches of 10 or 3-second debounce) feeding directly into
   verification + comparison engine (`bridge.start_compare`) without manual Excel exports or file imports.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

from .engine import (
    CapturedEvent,
    PausedRequest,
    StandaloneNetworkInterceptor,
    check_cdp_ready,
    get_cdp_targets,
    export_events_to_excel,
)

log = logging.getLogger("AuditInterceptorManager")


def _pascal_to_camel(name: str) -> str:
    text = (name or "").strip()
    if not text:
        return text
    return text[0].lower() + text[1:] if text[0].isupper() else text


def _audit_operation_name(graphql_operation_name: str, query: str = "") -> str:
    """Map GraphQL operationName (ActivateFamily) → audit/Mongo op (activateFamily)."""
    raw = (graphql_operation_name or "").strip()
    if not raw:
        return raw
    try:
        from audit_validator.utility.operation_graphql import get_operation_entry

        for candidate in (raw, _pascal_to_camel(raw)):
            entry = get_operation_entry(candidate)
            if entry and entry.audit_operation:
                return entry.audit_operation
    except Exception:
        pass
    # Prefer the GraphQL root field when present: mutation ActivateFamily { activateFamily(...) }
    if query:
        match = re.search(
            r"(?:query|mutation|subscription)\s+[^{]+\{\s*([A-Za-z_][A-Za-z0-9_]*)\b",
            query,
            re.IGNORECASE,
        )
        if match:
            return match.group(1)
    return _pascal_to_camel(raw)


def _results_operation_key(graphql_operation_name: str, scenario: str, query: str = "") -> str:
    """Results store key: ``activateFamily(list)`` (scenario-qualified, audit casing)."""
    audit_op = _audit_operation_name(graphql_operation_name, query)
    scen = (scenario or "global").strip() or "global"
    if scen.lower() in {"default", "unknown"}:
        scen = "global"
    # Already qualified (rare)
    if "(" in audit_op and audit_op.endswith(")"):
        return audit_op
    return f"{audit_op}({scen})"


class InterceptorManager:
    """Singleton manager for live capture sessions and background comparison queue."""

    def __init__(self, project_root: Path, bridge: Any = None, db: Any = None) -> None:
        self.project_root = project_root
        self.bridge = bridge
        self.db = db

        self._lock = threading.Lock()
        self.port: int = 9222
        self.target: str = "web"  # "web" or "app"
        self.is_active: bool = False
        self.started_at: Optional[str] = None
        self.filter_operation: Optional[str] = None
        self.filter_url_keyword: str = "graph"
        self.ignore_get: bool = True
        self.ignore_query: bool = False
        self.interception_mode: str = "observe"

        # Auto-compare settings
        self.auto_compare_enabled: bool = True
        self.batch_size: int = 10
        self.debounce_sec: float = 3.5

        # State storage
        self.events: List[CapturedEvent] = []
        self.compared_pair_keys: Set[str] = set()
        self.queued_pair_keys: Set[str] = set()
        self.pending_queue: List[CapturedEvent] = []
        self.paused: Dict[str, PausedRequest] = {}

        # Background threads & timers
        self._interceptor_instance: Optional[StandaloneNetworkInterceptor] = None
        self._interceptor_thread: Optional[threading.Thread] = None
        self._batch_timer: Optional[threading.Timer] = None
        self._is_comparing_batch: bool = False
        self._last_compare_job_id: Optional[str] = None

    def free_port_if_occupied(self, port: int, allow_pattern: str = "") -> None:
        """If port is occupied by a conflicting process, terminate it to prevent port collisions."""
        try:
            res = subprocess.run(["lsof", "-ti", f":{port}"], capture_output=True, text=True)
            pids = [p.strip() for p in res.stdout.strip().split() if p.strip()]
            for pid in pids:
                try:
                    cmd_res = subprocess.run(["ps", "-p", pid, "-o", "command="], capture_output=True, text=True)
                    cmd_line = cmd_res.stdout.strip()
                    if allow_pattern and allow_pattern.lower() in cmd_line.lower():
                        continue
                    log.info("Freeing port %d: terminating conflicting process PID %s (%s)", port, pid, cmd_line[:80])
                    subprocess.run(["kill", "-9", pid], capture_output=True)
                except Exception:
                    pass
            if pids:
                time.sleep(0.5)
        except Exception as exc:
            log.warning("Could not check/free port %d: %s", port, exc)

    def launch_chrome(self, port: int = 9222) -> dict[str, Any]:
        """Launch Google Chrome with remote debugging port enabled."""
        self.free_port_if_occupied(port, allow_pattern="Google Chrome")

        cmd = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            f"--remote-debugging-port={port}",
            '--user-data-dir=/tmp/chrome_dev_profile',
        ]
        log.info("Launching Chrome with port %d: %s", port, " ".join(cmd))
        try:
            subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            ready = False
            for _ in range(12):
                time.sleep(0.3)
                if check_cdp_ready(port):
                    ready = True
                    break

            return {
                "ok": True,
                "message": f"Chrome launched on port {port}",
                "cdp_ready": ready,
                "port": port,
            }
        except Exception as exc:
            log.error("Failed to launch Chrome: %s", exc)
            return {"ok": False, "error": str(exc), "port": port}

    def launch_app(self, port: int = 9222) -> dict[str, Any]:
        """Launch Monotype Connect + (or NextGen) Desktop App with remote debugging port enabled."""
        # Free port if occupied by Chrome or another process
        self.free_port_if_occupied(port, allow_pattern="Monotype Connect +")

        candidates = [
            Path("/Applications/Monotype Connect +/Monotype Connect +.app"),
            Path("/Applications/Monotype Connect +.app"),
            Path("/Applications/Monotype NextGen/Monotype NextGen.app"),
            Path("/Applications/Monotype NextGen.app"),
            Path.home() / "Applications/Monotype Connect +/Monotype Connect +.app",
            Path.home() / "Applications/Monotype Connect +.app",
        ]

        found_path: Optional[Path] = None
        for cand in candidates:
            if cand.exists():
                found_path = cand
                break

        if not found_path:
            found_path = candidates[0]

        app_name = found_path.stem or "Monotype Connect +"

        # If already listening on this CDP port and target is present, return early
        if check_cdp_ready(port):
            targets = get_cdp_targets(port)
            if any("monotype" in str(t.get("title", "")).lower() or "nextgen" in str(t.get("title", "")).lower() for t in targets):
                return {
                    "ok": True,
                    "message": f"{app_name} is already open and ready on port {port}",
                    "app_path": str(found_path),
                    "cdp_ready": True,
                    "port": port,
                }

        # Kill any existing UI process of Monotype Connect + running without CDP port
        try:
            subprocess.run(["pkill", "-f", "Contents/MacOS/Monotype Connect +"], capture_output=True)
            time.sleep(0.4)
        except Exception:
            pass

        # Use open -n -a to launch a fresh instance of the application
        cmd = [
            "open",
            "-n",
            "-a",
            str(found_path),
            "--args",
            f"--remote-debugging-port={port}",
        ]
        log.info("Launching desktop app from %s with port %d: %s", found_path, port, " ".join(cmd))
        try:
            subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            # Poll up to 3.5 seconds for CDP
            ready = False
            for _ in range(12):
                time.sleep(0.3)
                if check_cdp_ready(port):
                    ready = True
                    break

            # Fallback: if not ready, execute binary directly
            if not ready:
                bin_path = found_path / "Contents" / "MacOS" / (found_path.stem or "Monotype Connect +")
                if bin_path.exists():
                    log.info("open -n -a did not bind CDP; launching binary directly: %s", bin_path)
                    subprocess.Popen(
                        [str(bin_path), f"--remote-debugging-port={port}"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True,
                    )
                    for _ in range(10):
                        time.sleep(0.3)
                        if check_cdp_ready(port):
                            ready = True
                            break

            return {
                "ok": True,
                "message": f"{app_name} launched on port {port}" if ready else f"{app_name} launched (waiting for port {port})",
                "app_path": str(found_path),
                "cdp_ready": ready,
                "port": port,
            }
        except Exception as exc:
            log.error("Failed to launch %s: %s", app_name, exc)
            return {"ok": False, "error": str(exc), "port": port}


    def get_status(self, port: Optional[int] = None) -> dict[str, Any]:
        """Return real-time status of CDP connection, active session, and comparison machine."""
        check_p = port or self.port or 9222
        cdp_ready = check_cdp_ready(check_p)
        targets = get_cdp_targets(check_p) if cdp_ready else []

        with self._lock:
            total_captured = len(self.events)
            queued_count = len(self.pending_queue)
            compared_count = len(self.compared_pair_keys)
            pass_count = sum(1 for e in self.events if e.compare_status == "compared_pass")
            fail_count = sum(1 for e in self.events if e.compare_status == "compared_fail")

            return {
                "cdp_ready": cdp_ready,
                "is_active": self.is_active,
                "port": check_p,
                "target": self.target,
                "started_at": self.started_at,
                "filter_operation": self.filter_operation,
                "url_keyword": self.filter_url_keyword,
                "ignore_get": self.ignore_get,
                "ignore_query": self.ignore_query,
                "interception_mode": self.interception_mode,
                "paused_count": len(self.paused),
                "auto_compare_enabled": self.auto_compare_enabled,
                "batch_size": self.batch_size,
                "captured_count": total_captured,
                "queued_count": queued_count,
                "compared_count": compared_count,
                "pass_count": pass_count,
                "fail_count": fail_count,
                "is_comparing_batch": self._is_comparing_batch,
                "last_compare_job_id": self._last_compare_job_id,
                "targets_count": len(targets),
                "targets": targets,
            }

    def start_capture(
        self,
        port: int = 9222,
        target: str = "web",
        filter_operation: Optional[str] = None,
        url_keyword: str = "graph",
        ignore_get: bool = True,
        ignore_query: bool = False,
        auto_compare: bool = True,
        batch_size: int = 10,
        interception_mode: str = "observe",
    ) -> dict[str, Any]:
        """Start listening on the CDP port and intercepting requests."""
        if not check_cdp_ready(port):
            return {
                "ok": False,
                "error": f"CDP Port {port} is not reachable. Launch Chrome or NextGen App with port {port} first.",
            }

        with self._lock:
            if self.is_active:
                return {
                    "ok": True,
                    "message": "Capture session is already running",
                    "captured_count": len(self.events),
                }

            self.is_active = True
            self.port = port
            self.target = target or "web"
            self.filter_operation = filter_operation
            self.filter_url_keyword = url_keyword or "graph"
            self.ignore_get = ignore_get
            self.ignore_query = ignore_query
            self.interception_mode = interception_mode if interception_mode in {"observe", "pause"} else "observe"
            self.auto_compare_enabled = auto_compare
            self.batch_size = max(1, min(50, batch_size))
            self.started_at = datetime.now(timezone.utc).isoformat()

            interceptor = StandaloneNetworkInterceptor(
                port=port,
                target=target,
                filter_operation=filter_operation,
                filter_url_keyword=url_keyword,
                ignore_get_operations=ignore_get,
                ignore_query_operations=ignore_query,
                on_event_callback=self._on_intercepted_event,
                interception_mode=self.interception_mode,
                on_paused_callback=self._on_paused_request,
            )
            self._interceptor_instance = interceptor

            def _run():
                try:
                    log.info("Interceptor background thread started on port %d", port)
                    interceptor.start_listening(duration_sec=0)
                except Exception as exc:
                    log.exception("Interceptor background error: %s", exc)
                finally:
                    with self._lock:
                        self.is_active = False
                    log.info("Interceptor background thread ended on port %d", port)

            t = threading.Thread(target=_run, name="audit-network-interceptor", daemon=True)
            t.start()
            self._interceptor_thread = t

            return {
                "ok": True,
                "message": f"Network capture started on port {port} ({target.upper()})",
                "port": port,
                "target": target,
                "auto_compare": auto_compare,
                "batch_size": self.batch_size,
                "ignore_get": self.ignore_get,
                "ignore_query": self.ignore_query,
                "interception_mode": self.interception_mode,
            }

    def _on_paused_request(self, request: PausedRequest) -> None:
        with self._lock:
            self.paused[request.fetch_id] = request

    def get_paused_requests(self) -> dict[str, Any]:
        with self._lock:
            return {"total": len(self.paused), "requests": [self._safe_paused_dict(r) for r in self.paused.values()]}

    @staticmethod
    def _safe_paused_dict(request: PausedRequest) -> dict[str, Any]:
        data = request.to_dict()
        data["headers"] = {
            key: ("[REDACTED]" if key.lower() in {"authorization", "cookie", "set-cookie", "x-auth-token"} else value)
            for key, value in request.headers.items()
        }
        return data

    def control_paused(self, fetch_id: str, action: str, **kwargs: Any) -> dict[str, Any]:
        with self._lock:
            request = self.paused.get(fetch_id)
            interceptor = self._interceptor_instance
        if request is None or interceptor is None:
            return {"ok": False, "error": "Paused request was not found or capture is stopped"}
        if action == "continue" and kwargs.get("url"):
            from urllib.parse import urlparse
            original = urlparse(request.url)
            replacement = urlparse(str(kwargs["url"]))
            if (replacement.scheme, replacement.netloc) != (original.scheme, original.netloc):
                return {"ok": False, "error": "Edited URL must stay on the original origin"}
        if action == "mock" and kwargs.get("response_body") is None:
            return {"ok": False, "error": "A mock response body is required"}
        released = interceptor.release_paused(fetch_id, action, **kwargs)
        if released:
            with self._lock:
                self.paused.pop(fetch_id, None)
        return {"ok": released, **({} if released else {"error": "CDP could not release the paused request"})}

    def update_capture_filters(
        self,
        *,
        ignore_get: Optional[bool] = None,
        ignore_query: Optional[bool] = None,
        auto_compare: Optional[bool] = None,
        batch_size: Optional[int] = None,
    ) -> dict[str, Any]:
        """Apply Live Capture filter checkboxes (works while a session is running)."""
        with self._lock:
            if ignore_get is not None:
                self.ignore_get = bool(ignore_get)
            if ignore_query is not None:
                self.ignore_query = bool(ignore_query)
            if auto_compare is not None:
                self.auto_compare_enabled = bool(auto_compare)
            if batch_size is not None:
                self.batch_size = max(1, min(50, int(batch_size)))

            interceptor = self._interceptor_instance
            if interceptor is not None:
                interceptor.ignore_get_operations = self.ignore_get
                interceptor.ignore_query_operations = self.ignore_query

            return {
                "ok": True,
                "ignore_get": self.ignore_get,
                "ignore_query": self.ignore_query,
                "auto_compare": self.auto_compare_enabled,
                "batch_size": self.batch_size,
                "is_active": self.is_active,
            }

    def stop_capture(self) -> dict[str, Any]:
        """Stop listening on the CDP port."""
        with self._lock:
            interceptor = self._interceptor_instance
            if interceptor:
                interceptor.release_all_paused()
                interceptor._is_capturing = False
            self.is_active = False
            if self._batch_timer:
                self._batch_timer.cancel()
                self._batch_timer = None
            count = len(self.events)
            return {"ok": True, "message": "Capture stopped", "captured_count": count}

    def clear(self) -> dict[str, Any]:
        """Clear all captured events and reset comparison deduplication history."""
        with self._lock:
            if self._interceptor_instance:
                self._interceptor_instance.release_all_paused()
            if self._batch_timer:
                self._batch_timer.cancel()
                self._batch_timer = None
            self.events = []
            self.compared_pair_keys = set()
            self.queued_pair_keys = set()
            self.pending_queue = []
            self.paused = {}
            return {"ok": True, "message": "Captured events and comparison queues cleared"}

    def get_events(
        self,
        operation: str = "",
        scenario: str = "",
        limit: int = 500,
    ) -> dict[str, Any]:
        """Get captured events list with comparison state."""
        with self._lock:
            evts = list(self.events)
            if operation.strip():
                op_l = operation.strip().lower()
                evts = [e for e in evts if op_l in (e.operation_name or "").lower()]
            if scenario.strip():
                sc_l = scenario.strip().lower()
                evts = [e for e in evts if (e.scenario or "global").lower() == sc_l]

            res = [e.to_dict() for e in evts[-limit:]]
            return {"total": len(evts), "events": res}

    def _on_intercepted_event(self, event: CapturedEvent) -> None:
        """Callback invoked whenever an event is intercepted and response arrives."""
        with self._lock:
            # Overwrite check for specified duplicate queries
            OVERWRITE_LATEST_OPERATIONS = {
                "getimportedfonts",
                "getssomappings",
                "getallaccessrequests",
                "getassetattachments",
                "getstylesofallfontlists",
                "getstyledocuments",
                "getstylecomments",
                "getcustomersettings",
                "getcategorizedglyphs",
                "getpackageid",
            }
            op_name_lower = (event.operation_name or "").strip().lower()
            is_target_op = op_name_lower in OVERWRITE_LATEST_OPERATIONS

            found_idx = -1
            if is_target_op:
                op_key = (event.operation_name or "").strip()
                scen_key = (event.scenario or "global").strip()
                for idx, existing in enumerate(self.events):
                    if (existing.operation_name or "").strip() == op_key and \
                       (existing.scenario or "global").strip() == scen_key:
                        found_idx = idx
                        break

            if found_idx >= 0:
                event.call_count = self.events[found_idx].call_count + 1
                self.events[found_idx] = event
            else:
                self.events.append(event)

            if len(self.events) > 1000:
                self.events.pop(0)

            # Auto-compare pipeline
            if not self.auto_compare_enabled:
                event.compare_status = "pending"
                return

            cid = (event.header_values.correlation_id or "").strip()
            op = (event.operation_name or "").strip()
            scen = (event.scenario or "global").strip()

            from audit_validator.touchpoint.scenarios import is_valid_correlation_id

            if not cid or not is_valid_correlation_id(cid) or not op:
                event.compare_status = "skipped_no_cid"
                return

            pair_key = f"{op}::{scen}"

            # Compare only once rule
            if pair_key in self.compared_pair_keys:
                event.compare_status = "already_compared"
                return

            if pair_key in self.queued_pair_keys:
                event.compare_status = "queued"
                return

            # Add to pending batch queue
            event.compare_status = "queued"
            self.queued_pair_keys.add(pair_key)
            self.pending_queue.append(event)

            # Check if batch size threshold reached
            if len(self.pending_queue) >= self.batch_size:
                if self._batch_timer:
                    self._batch_timer.cancel()
                    self._batch_timer = None
                self._schedule_batch_flush(immediate=True)
            else:
                # Debounce timer: flush whatever is pending after debounce_sec of quiet
                if self._batch_timer:
                    self._batch_timer.cancel()
                self._batch_timer = threading.Timer(self.debounce_sec, self._schedule_batch_flush)
                self._batch_timer.daemon = True
                self._batch_timer.start()

    def _schedule_batch_flush(self, immediate: bool = False) -> None:
        """Trigger background flush of pending events to verification & comparison engine."""
        t = threading.Thread(
            target=self._process_batch_worker,
            name="audit-compare-batch-worker",
            daemon=True,
        )
        t.start()

    def _process_batch_worker(self, forced_events: Optional[List[CapturedEvent]] = None) -> None:
        """Background worker that verifies the batch in Mongo & triggers bridge.start_compare."""
        batch: List[CapturedEvent] = []
        with self._lock:
            if self._is_comparing_batch and forced_events is None:
                # Wait for previous comparison to finish or defer
                log.info("Previous compare batch still in flight, deferring...")
                return

            if forced_events is not None:
                batch = forced_events
            else:
                if not self.pending_queue:
                    return
                # Pop up to batch_size
                batch = self.pending_queue[: self.batch_size]
                self.pending_queue = self.pending_queue[self.batch_size :]

                # Mark as comparing
                for e in batch:
                    e.compare_status = "comparing"

            self._is_comparing_batch = True

        if not batch:
            with self._lock:
                self._is_comparing_batch = False
            return

        log.info(
            "🚀 Processing auto-compare batch of %d event(s): %s",
            len(batch),
            [e.operation_name for e in batch],
        )

        try:
            self._execute_batch_compare(batch)
        except Exception as exc:
            log.exception("Auto-compare batch worker error: %s", exc)
            with self._lock:
                for e in batch:
                    e.compare_status = "compared_fail"
                    e.compare_error = str(exc)
                    pair_key = f"{e.operation_name}::{e.scenario}"
                    self.queued_pair_keys.discard(pair_key)
        finally:
            with self._lock:
                self._is_comparing_batch = False
                # If more items queued while we were running, schedule next batch
                if self.pending_queue and self.auto_compare_enabled:
                    if len(self.pending_queue) >= self.batch_size:
                        self._schedule_batch_flush(immediate=True)
                    elif not self._batch_timer:
                        self._batch_timer = threading.Timer(self.debounce_sec, self._schedule_batch_flush)
                        self._batch_timer.daemon = True
                        self._batch_timer.start()

    def _execute_batch_compare(self, batch: List[CapturedEvent]) -> None:
        """Execute verification against Mongo and launch bridge.start_compare."""
        from audit_validator.ui_script_import import (
            _selection_from_rows,
            create_ui_script_job,
            finalize_ui_trigger_verification,
        )

        rows = []
        correlation_by_op: dict[str, str] = {}
        for e in batch:
            op = (e.operation_name or "").strip()
            cid = (e.header_values.correlation_id or "").strip()
            scen = (e.scenario or "global").strip() or "global"
            tgt = e.target or self.target or "web"
            query = getattr(e, "query", "") or ""

            if not op or not cid:
                continue

            # Scenario-qualified audit key so Results gets activateFamily(list), not bare ActivateFamily.
            audit_op = _audit_operation_name(op, query)
            result_key = _results_operation_key(op, scen, query)
            correlation_by_op[result_key] = cid

            full_resp = e.response_body if isinstance(e.response_body, dict) else None
            gql_resp = full_resp.get("data") if full_resp and isinstance(full_resp.get("data"), dict) else full_resp

            composite_payload = {
                "operationName": op,
                "scenario": scen,
                "userAgent": e.header_values.user_agent,
                "appVersion": e.header_values.app_version,
                "correlationId": cid,
                "jwtClaims": e.header_values.jwt_claims,
                "variables": e.variables,
            }

            rows.append({
                "operation": audit_op,
                "event_name": op,
                "excel_event_name": op,
                "touchpoint": scen,
                "scenario": scen,
                "target": tgt,
                "correlation_id": cid,
                "auth_token": e.header_values.auth_token,
                "status": "OK",
                "response": json.dumps(e.response_body) if isinstance(e.response_body, (dict, list)) else str(e.response_body or ""),
                "graphql_response": gql_resp,
                "ingress_headers": e.raw_request_headers,
                "request_payload": composite_payload,
            })

        if not rows:
            return

        sel = _selection_from_rows(rows)
        target_norm = batch[0].target if batch else self.target

        # Step 1: Create UI verification job
        job = create_ui_script_job(self.project_root, selection=sel, rows=rows, target=target_norm)
        log.info("Created verification job %s for batch", job.get("id"))

        # Step 2: Settle and poll Mongo for raw and enriched events
        prev_settle = os.environ.get("UI_VERIFY_SETTLE_SEC")
        try:
            os.environ["UI_VERIFY_SETTLE_SEC"] = "5"
            finalized = finalize_ui_trigger_verification(self.project_root, job["id"], db=self.db)
        finally:
            if prev_settle is None:
                os.environ.pop("UI_VERIFY_SETTLE_SEC", None)
            else:
                os.environ["UI_VERIFY_SETTLE_SEC"] = prev_settle

        # Step 3: Trigger Compare Job in AuditBridge
        ops = list(correlation_by_op.keys())
        audit_target = (os.getenv("AUDIT_TARGET") or "qa").strip().lower()

        if self.bridge:
            compare_job = self.bridge.start_compare(
                operations=ops,
                sample_source="fresh",
                correlation_by_op=correlation_by_op,
                audit_target=audit_target,
            )
            self._last_compare_job_id = compare_job.id
            log.info("Launched compare job %s for ops: %s", compare_job.id, ops)

            for e in batch:
                e.compare_job_id = compare_job.id

            # Poll for completion in background to update event pass/fail pills
            t_poll = threading.Thread(
                target=self._wait_and_update_job_status,
                args=(compare_job.id, batch),
                daemon=True,
            )
            t_poll.start()
        else:
            # Standalone mode without bridge
            with self._lock:
                for e in batch:
                    e.compare_status = "compared_pass"
                    pair_key = f"{e.operation_name}::{e.scenario}"
                    self.compared_pair_keys.add(pair_key)
                    self.queued_pair_keys.discard(pair_key)

    def _wait_and_update_job_status(self, job_id: str, batch: List[CapturedEvent]) -> None:
        """Poll compare job until finished and update events."""
        if not self.bridge:
            return

        max_polls = 60  # ~90 seconds max
        polls = 0
        while polls < max_polls:
            time.sleep(1.5)
            polls += 1
            rec = self.bridge.store.get(job_id)
            if not rec:
                continue

            status_str = str(rec.status).lower() if hasattr(rec, "status") else ""
            if "completed" in status_str:
                res = rec.result if hasattr(rec, "result") else {}
                val = (res or {}).get("validation") or {}
                failed_n = int(val.get("failed", 0))

                with self._lock:
                    for e in batch:
                        e.compare_status = "compared_fail" if failed_n > 0 else "compared_pass"
                        pair_key = f"{e.operation_name}::{e.scenario}"
                        self.compared_pair_keys.add(pair_key)
                        self.queued_pair_keys.discard(pair_key)
                log.info("Batch comparison job %s completed! (failed=%d)", job_id, failed_n)
                break
            elif "failed" in status_str:
                with self._lock:
                    for e in batch:
                        e.compare_status = "compared_fail"
                        e.compare_error = getattr(rec, "error", "Comparison failed")
                        pair_key = f"{e.operation_name}::{e.scenario}"
                        self.compared_pair_keys.add(pair_key)
                        self.queued_pair_keys.discard(pair_key)
                log.warning("Batch comparison job %s failed: %s", job_id, getattr(rec, "error", ""))
                break

    def trigger_batch(self, event_ids: Optional[List[str]] = None) -> dict[str, Any]:
        """Manually force comparison on selected or pending events.

        With no ``event_ids``, re-runs pending queue items, or all
        ``compared_fail`` events if the queue is empty (retry after a worker crash).
        """
        with self._lock:
            if event_ids:
                id_set = set(event_ids)
                target_events = [e for e in self.events if str(e.id) in id_set]
            else:
                target_events = list(self.pending_queue)
                self.pending_queue = []
                if not target_events:
                    target_events = [
                        e for e in self.events if e.compare_status == "compared_fail"
                    ]

        if not target_events:
            return {"ok": False, "error": "No eligible events to compare"}

        # Reset compare status for forced run
        for e in target_events:
            e.compare_status = "queued"
            e.compare_error = None
            pair_key = f"{e.operation_name}::{e.scenario}"
            with self._lock:
                self.queued_pair_keys.discard(pair_key)
                self.compared_pair_keys.discard(pair_key)

        t = threading.Thread(
            target=self._process_batch_worker,
            args=(target_events,),
            name="audit-manual-compare-worker",
            daemon=True,
        )
        t.start()
        return {
            "ok": True,
            "message": f"Triggered comparison for {len(target_events)} event(s)",
            "count": len(target_events),
        }

    def export_excel(self, output_path: Optional[Path] = None) -> Path:
        """Export captured events to audit_results.xlsx."""
        with self._lock:
            evts_dict = [e.to_dict() for e in self.events]
        return export_events_to_excel(evts_dict, output_path=output_path)
