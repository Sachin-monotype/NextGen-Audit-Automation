"""Bridge to vendored audit_validator package (python/audit_validator/)."""

from __future__ import annotations

import logging
import json
import os
import sys
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class JobRecord:
    id: str
    kind: str
    status: JobStatus
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    logs: list[str] = field(default_factory=list)
    result: dict[str, Any] | None = None
    error: str | None = None


class JobStore:
    """In-memory job registry with optional disk persistence (survives tab switches /
    backend reloads). Keeps the last N jobs so Compare/Generate can restore logs.
    """

    def __init__(self, persist_path: Path | None = None, *, max_jobs: int = 40) -> None:
        self._jobs: dict[str, JobRecord] = {}
        self._lock = threading.Lock()
        self._persist_path = persist_path
        self._max_jobs = max_jobs
        self._load()

    def _load(self) -> None:
        if not self._persist_path or not self._persist_path.is_file():
            return
        try:
            import json

            raw = json.loads(self._persist_path.read_text(encoding="utf-8"))
            for item in raw.get("jobs") or []:
                if not isinstance(item, dict) or not item.get("id"):
                    continue
                status_raw = item.get("status") or "pending"
                try:
                    status = JobStatus(status_raw)
                except ValueError:
                    status = JobStatus.FAILED
                # A process restart cannot continue a mid-flight job — mark stale.
                if status in (JobStatus.PENDING, JobStatus.RUNNING):
                    status = JobStatus.FAILED
                    item["error"] = item.get("error") or "Job interrupted (backend restarted)"
                    item["finished_at"] = item.get("finished_at") or _now()
                self._jobs[str(item["id"])] = JobRecord(
                    id=str(item["id"]),
                    kind=str(item.get("kind") or ""),
                    status=status,
                    created_at=str(item.get("created_at") or _now()),
                    started_at=item.get("started_at"),
                    finished_at=item.get("finished_at"),
                    params=dict(item.get("params") or {}),
                    logs=list(item.get("logs") or [])[-200:],
                    result=item.get("result"),
                    error=item.get("error"),
                )
        except Exception as exc:  # noqa: BLE001
            log.warning("Could not load job store: %s", exc)

    def _persist(self) -> None:
        if not self._persist_path:
            return
        try:
            import json

            self._persist_path.parent.mkdir(parents=True, exist_ok=True)
            jobs = sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)[
                : self._max_jobs
            ]
            payload = {
                "jobs": [
                    {
                        "id": j.id,
                        "kind": j.kind,
                        "status": j.status.value if isinstance(j.status, JobStatus) else j.status,
                        "created_at": j.created_at,
                        "started_at": j.started_at,
                        "finished_at": j.finished_at,
                        "params": j.params,
                        "logs": j.logs[-200:],
                        "result": j.result,
                        "error": j.error,
                    }
                    for j in jobs
                ]
            }
            self._persist_path.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n",
                encoding="utf-8",
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("Could not persist job store: %s", exc)

    def create(self, kind: str, params: dict[str, Any]) -> JobRecord:
        job = JobRecord(
            id=str(uuid.uuid4()),
            kind=kind,
            status=JobStatus.PENDING,
            created_at=_now(),
            params=params,
        )
        with self._lock:
            self._jobs[job.id] = job
            # Cap memory
            if len(self._jobs) > self._max_jobs:
                oldest = sorted(self._jobs.values(), key=lambda j: j.created_at)[
                    : max(0, len(self._jobs) - self._max_jobs)
                ]
                for o in oldest:
                    self._jobs.pop(o.id, None)
            self._persist()
        return job

    def get(self, job_id: str) -> JobRecord | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list_jobs(self, limit: int = 20) -> list[JobRecord]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)
        return jobs[:limit]

    def append_log(self, job_id: str, line: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job.logs.append(line)
                if len(job.logs) > 400:
                    job.logs = job.logs[-400:]
                # Persist periodically (every 5 lines) so refresh keeps logs.
                if len(job.logs) % 5 == 0:
                    self._persist()

    def update(self, job_id: str, **kwargs: Any) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            for key, val in kwargs.items():
                setattr(job, key, val)
            self._persist()

    def request_cancel(self, job_id: str) -> JobRecord | None:
        """Mark a running/pending job cancelled so worker loops can stop."""
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            if job.status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
                return job
            job.status = JobStatus.CANCELLED
            job.finished_at = _now()
            job.error = job.error or "Cancelled by user"
            job.logs.append("⏹ Cancelled by user — aborting remaining work")
            self._persist()
            return job

    def is_cancelled(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            return bool(job and job.status == JobStatus.CANCELLED)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_audit_path(project_root: Path) -> None:
    python_dir = project_root / "python"
    if str(python_dir) not in sys.path:
        sys.path.insert(0, str(python_dir))


def _flows_for_operations(operations: list[str]) -> frozenset[str] | None:
    if not operations:
        return None
    from audit_validator.simulation.flow_catalog import flow_operations
    from audit_validator.touchpoint.scenarios import parse_selection_id

    ops_set = set()
    for raw in operations:
        op, _touch = parse_selection_id(raw)
        if op:
            ops_set.add(op)
    flows: set[str] = set()
    for fo in flow_operations():
        if fo.graphql_operation in ops_set or audit_label_match(fo.label, ops_set):
            flows.add(fo.flow)
    return frozenset(flows) if flows else None


def audit_label_match(label: str, ops_set: set[str]) -> bool:
    base = label.split(" (")[0].strip()
    return base in ops_set


def _routing_keys_map(project_root: Path) -> dict[str, str]:
    import json

    rk_path = project_root / "python" / "audit_validator" / "data" / "outbound-routing-map.json"
    if rk_path.is_file():
        return json.loads(rk_path.read_text(encoding="utf-8"))
    return {}


class AuditBridge:
    def __init__(
        self,
        project_root: Path,
        store: JobStore,
        db: "AuditDatabase | None" = None,
        ingestion: Any | None = None,
    ) -> None:
        self.project_root = project_root
        self.store = store
        self.db = db
        self.ingestion = ingestion
        _ensure_audit_path(project_root)

    def _ensure_ingestion(self, job_id: str) -> dict[str, Any]:
        """Start RabbitMQ → Mongo dump if it is not already running.

        Generate verify polls Mongo for owned correlations — without live
        ingestion those events never land and the poll always times out.
        """
        if self.ingestion is None:
            self.store.append_log(
                job_id,
                "⚠ Ingestion manager not wired — start Live ingestion on Enrich/Raw "
                "(or restart backend) so events dump into Mongo",
            )
            return {"running": False, "ensured": False}

        try:
            before = self.ingestion.status()
            already = bool(before.get("running"))
            status = self.ingestion.start() if not already else before
            running = bool(status.get("running"))
            if already:
                self.store.append_log(
                    job_id,
                    "✓ Live ingestion already running (RabbitMQ → Mongo continuous dump)",
                )
            elif running:
                self.store.append_log(
                    job_id,
                    "✓ Started live ingestion from backend (RabbitMQ → Mongo continuous dump)",
                )
            else:
                self.store.append_log(
                    job_id,
                    "✖ Could not start live ingestion — Mongo verify may stay empty. "
                    f"Detail: {status.get('error') or status}",
                )
            return {**status, "ensured": running, "was_already_running": already}
        except Exception as exc:  # noqa: BLE001
            self.store.append_log(job_id, f"✖ Ingestion start failed: {exc}")
            return {"running": False, "ensured": False, "error": str(exc)}

    def start_generate(
        self,
        *,
        operations: list[str] | None = None,
        validate: bool = False,
        skip_passed: bool = False,
        include_ingress: bool = False,
    ) -> JobRecord:
        job = self.store.create(
            "generate",
            {
                "operations": operations or [],
                "validate": validate,
                "skip_passed": skip_passed,
                "include_ingress": include_ingress,
            },
        )
        thread = threading.Thread(
            target=self._run_generate,
            args=(job.id, operations or [], validate, skip_passed, include_ingress),
            daemon=True,
        )
        thread.start()
        return job

    def cancel_job(self, job_id: str) -> JobRecord | None:
        """Abort a running Generate / Generate & validate / Compare job."""
        job = self.store.request_cancel(job_id)
        return job

    def _finalize_job(self, job_id: str, **kwargs: Any) -> None:
        """Apply terminal status unless the user already cancelled."""
        if self.store.is_cancelled(job_id):
            cur = self.store.get(job_id)
            if cur and kwargs.get("result") is not None and cur.result is None:
                self.store.update(job_id, result=kwargs["result"])
            return
        self.store.update(job_id, **kwargs)

    def start_compare(
        self,
        operations: list[str],
        sample_source: str = "fresh",
        field_paths_by_op: dict[str, list[str]] | None = None,
    ) -> JobRecord:
        job = self.store.create(
            "compare",
            {
                "operations": operations,
                "sample_source": sample_source,
                "field_paths_by_op": field_paths_by_op or {},
            },
        )
        thread = threading.Thread(
            target=self._run_compare,
            args=(job.id, operations, sample_source, field_paths_by_op),
            daemon=True,
        )
        thread.start()
        return job

    def _ensure_token(self, job_id: str) -> dict[str, Any]:
        """Ensure a valid Bearer token before triggering events (auto-refresh if expired)."""
        try:
            from audit_validator.token_manager import ensure_fresh_bearer

            status = ensure_fresh_bearer(
                self.project_root,
                log=lambda msg: self.store.append_log(job_id, msg),
            )
            return status.as_dict()
        except Exception as exc:  # noqa: BLE001
            self.store.append_log(job_id, f"⚠ Token check failed: {exc}")
            return {"error": str(exc)}

    def _log_context(self, job_id: str) -> None:
        from audit_validator.config import load_config

        cfg = load_config(self.project_root)
        target = os.getenv("AUDIT_TARGET", "pp")
        gql = os.getenv("NEXTGEN_GRAPHQL_ENDPOINT", os.getenv("GRAPHQL_ENDPOINT", ""))
        rmq = cfg.rabbitmq

        self.store.append_log(job_id, f"▸ Target environment: {target.upper()}")
        self.store.append_log(job_id, f"▸ GraphQL: {gql}")
        self.store.append_log(job_id, f"▸ Queues: raw={rmq.raw_queue} · enrich={rmq.enriched_queue}")
        if rmq.dead_letter_queue:
            self.store.append_log(job_id, f"▸ DLQ: {rmq.dead_letter_queue}")

    def _preflight_connectivity(self, job_id: str) -> list[str]:
        """Probe RabbitMQ + GraphQL before a generate run. Returns the list of blocked
        systems (empty = good to go)."""
        self.store.append_log(job_id, "▸ Preflight: checking RabbitMQ + GraphQL reachability…")
        blocked: list[str] = []
        try:
            from audit_validator.health_probes import probe_graphql, probe_rabbitmq

            for name, probe in (("RabbitMQ", probe_rabbitmq), ("GraphQL", probe_graphql)):
                result = probe()
                if result.get("state") == "blocked":
                    blocked.append(name)
                    self.store.append_log(
                        job_id, f"✖ {name} unreachable — {result.get('detail', 'blocked')}"
                    )
                else:
                    self.store.append_log(job_id, f"✓ {name} reachable")
        except Exception as exc:  # noqa: BLE001 — never let preflight itself crash the job
            self.store.append_log(job_id, f"⚠ Preflight check skipped: {exc}")
            return []
        if blocked:
            self.store.append_log(
                job_id,
                "✖ Aborting — connect to the corporate VPN, then re-run. "
                "(Check the API Health tab to confirm connectivity.)",
            )
        return blocked

    def _verify_mongo(self, job_id: str, operations: list[str]) -> dict[str, Any]:
        """Poll owned correlations until they land in raw + enriched (or timeout)."""
        from audit_validator.generate_run_report import save_generate_run, verify_owned_queue_landing

        checked = list(operations or [])
        if not checked and self.db:
            try:
                checked = self.db.comparable_operations()[:50]
            except Exception:
                checked = []

        if not self.db:
            self.store.append_log(job_id, "⚠ Mongo unavailable — skipping raw/enrich verify")
            return {
                "checked": checked,
                "raw_found": [],
                "enriched_found": [],
                "summary": {"total": len(checked), "success": 0, "needs_work": len(checked)},
                "raw_queue": os.getenv("RAW_EVENTS_QUEUE", ""),
                "enriched_queue": os.getenv("ENRICHED_EVENTS_QUEUE", ""),
            }

        report = verify_owned_queue_landing(
            self.db,
            checked,
            project_root=self.project_root,
            progress=lambda msg: self.store.append_log(job_id, msg),
        )
        report["job_id"] = job_id
        try:
            save_generate_run(report, project_root=self.project_root)
            self.store.append_log(
                job_id, "▸ Saved generate-run report → reports/generate-runs/last.json"
            )
        except Exception as exc:  # noqa: BLE001
            self.store.append_log(job_id, f"⚠ Could not persist generate-run report: {exc}")
        return report

    def _run_generate(
        self,
        job_id: str,
        operations: list[str],
        validate: bool,
        skip_passed: bool,
        include_ingress: bool,
    ) -> None:
        self.store.update(job_id, status=JobStatus.RUNNING, started_at=_now())
        label = "Generate & validate" if validate else "Generate"
        scope = ", ".join(operations) if operations else "all operations"
        self.store.append_log(job_id, f"▸ {label} — {scope}")

        token_status = self._ensure_token(job_id)
        self._log_context(job_id)

        # Preflight: generation needs RabbitMQ (capture) + GraphQL (trigger). Off-VPN both
        # are blocked and the pipeline would otherwise hang on socket timeouts for minutes
        # and look "frozen". Fail fast with a clear, actionable message instead.
        blocked = self._preflight_connectivity(job_id)
        if blocked:
            self.store.update(
                job_id,
                status=JobStatus.FAILED,
                finished_at=_now(),
                result={"exit_code": 1, "token": token_status, "unreachable": blocked},
                error=f"Cannot reach {', '.join(blocked)} — connect to the corporate VPN and retry.",
            )
            return

        # Must dump subscription queues into Mongo for the whole run — otherwise
        # owned-correlation verify at the end never sees raw/enrich.
        ingest_status = self._ensure_ingestion(job_id)

        class _Handler(logging.Handler):
            def __init__(self, store: JobStore, jid: str) -> None:
                super().__init__()
                self._store = store
                self._job_id = jid

            def emit(self, record: logging.LogRecord) -> None:
                # Drop transport-level chatter from pika/urllib so the job log stays readable.
                if record.name.split(".")[0] in ("pika", "urllib3", "asyncio"):
                    return
                msg = record.getMessage()
                if _is_informative_log(msg):
                    self._store.append_log(self._job_id, msg)

        handler = _Handler(self.store, job_id)
        handler.setLevel(logging.INFO)
        root_logger = logging.getLogger()
        root_logger.addHandler(handler)

        # Selections may mix GraphQL ops, ingress cases (ingress:<id>) and cron
        # cases (cron:<id>). Empty selection = full catalog (GQL + ingress + cron).
        from audit_validator.operation_sources import (
            catalog_selection_ids,
            operation_source_report,
            split_selection,
        )

        catalog = operation_source_report()["catalog"]
        id_to_op = {c["id"]: c["operation"] for c in catalog}
        selection = list(operations) if operations else catalog_selection_ids()
        if not operations:
            self.store.append_log(
                job_id,
                f"▸ Empty selection → full catalog ({len(selection)} items: "
                f"graphql + ingress + cron)",
            )
        sel = split_selection(selection)
        gql_ops = sel["graphql"]
        ingress_cases = sel["ingress_cases"]
        cron_cases = sel["cron_cases"]
        from audit_validator.touchpoint.scenarios import parse_selection_id

        resolved_ops = []
        for i in selection:
            op_name = id_to_op.get(i) or parse_selection_id(i)[0] or i
            if op_name and not op_name.startswith(("ingress:", "cron:")):
                resolved_ops.append(op_name)
        resolved_ops = list(dict.fromkeys(resolved_ops))

        flow_set = _flows_for_operations(gql_ops)
        if gql_ops and not flow_set:
            self.store.append_log(job_id, f"▸ GraphQL selection will use touchpoint runner: {len(gql_ops)} id(s)")

        try:
            exit_code = 0
            mongo_status: dict[str, Any] = {}
            scenario_results: list[dict[str, Any]] = []

            if validate and not operations and include_ingress:
                self.store.append_log(job_id, "▸ Running full pipeline (generate + validate)…")
                from audit_validator.pipeline.full import run_full_validation

                exit_code = run_full_validation(
                    project_root=self.project_root,
                    skip_ingress=False,
                    skip_passed=skip_passed,
                )
            elif validate:
                self.store.append_log(
                    job_id,
                    f"▸ Phase 1: Trigger + capture for {len(selection)} item(s) "
                    f"(graphql={len(gql_ops)}, cron={len(cron_cases)}, ingress={len(ingress_cases)})…",
                )
                if gql_ops:
                    scenario_code, scenario_results = self._run_touchpoint_scenarios(job_id, gql_ops)
                    exit_code = max(exit_code, scenario_code)
                if cron_cases:
                    exit_code = max(exit_code, self._run_cron_cases(job_id, cron_cases))
                if ingress_cases:
                    exit_code = max(exit_code, self._run_ingress_cases(job_id, ingress_cases))
                self.store.append_log(job_id, "▸ Phase 2: Wait for scenario events in Mongo…")
                self._verify_scenario_results(scenario_results, wait_sec=75)
                self.store.append_log(job_id, "▸ Phase 2b: Source validation per touchpoint…")
                # Per-touchpoint Results rows: activateFamily(global), activateFamily(list), …
                if scenario_results:
                    val_result = self._run_scenario_source_validation(job_id, scenario_results)
                else:
                    val_result = self._run_source_validation(job_id, resolved_ops)
                ingest_status = self._ensure_ingestion(job_id)
                mongo_status = self._verify_mongo(job_id, resolved_ops)
                self._verify_scenario_results(scenario_results, wait_sec=0)
                mongo_status["scenarios"] = scenario_results
                mongo_status["validate"] = True
                mongo_status["ingestion"] = ingest_status
                try:
                    from audit_validator.generate_run_report import save_generate_run

                    save_generate_run(mongo_status, project_root=self.project_root)
                except Exception:  # noqa: BLE001
                    pass
                sc_fail = sum(
                    1 for s in scenario_results if str(s.get("status") or "").upper() == "FAIL"
                )
                self._finalize_job(
                    job_id,
                    status=JobStatus.COMPLETED
                    if exit_code == 0 and val_result.get("failed", 0) == 0 and sc_fail == 0
                    else JobStatus.FAILED,
                    finished_at=_now(),
                    result={
                        "exit_code": exit_code,
                        "validation": val_result,
                        "mongo": mongo_status,
                        "token": token_status,
                    },
                    error=None
                    if exit_code == 0 and val_result.get("failed", 0) == 0 and sc_fail == 0
                    else "Pipeline or validation failed",
                )
                return
            else:
                if self.store.is_cancelled(job_id):
                    return
                if gql_ops:
                    self.store.append_log(
                        job_id,
                        f"▸ Triggering GraphQL touchpoint scenarios ({len(gql_ops)} selection id(s))",
                    )
                    scenario_code, scenario_results = self._run_touchpoint_scenarios(job_id, gql_ops)
                    exit_code = max(exit_code, scenario_code)
                if self.store.is_cancelled(job_id):
                    return
                if cron_cases:
                    exit_code = max(exit_code, self._run_cron_cases(job_id, cron_cases))
                if ingress_cases:
                    exit_code = max(exit_code, self._run_ingress_cases(job_id, ingress_cases))

            if self.store.is_cancelled(job_id):
                return

            ops_to_check = resolved_ops or list(flow_set or [])
            # Re-ensure just before poll — user may have stopped ingestion mid-run.
            ingest_status = self._ensure_ingestion(job_id)
            mongo_status = self._verify_mongo(job_id, ops_to_check if ops_to_check else [])
            self._verify_scenario_results(scenario_results)
            mongo_status["scenarios"] = scenario_results
            mongo_status["validate"] = bool(validate)
            mongo_status["ingestion"] = ingest_status
            try:
                from audit_validator.generate_run_report import save_generate_run

                save_generate_run(mongo_status, project_root=self.project_root)
            except Exception:  # noqa: BLE001
                pass

            self._finalize_job(
                job_id,
                status=JobStatus.COMPLETED if exit_code == 0 else JobStatus.FAILED,
                finished_at=_now(),
                result={"exit_code": exit_code, "mongo": mongo_status, "token": token_status},
                error=None if exit_code == 0 else f"Pipeline exit code {exit_code}",
            )
        except Exception as exc:
            log.exception("Generate job failed")
            if not self.store.is_cancelled(job_id):
                self.store.update(
                    job_id,
                    status=JobStatus.FAILED,
                    finished_at=_now(),
                    error=str(exc),
                )
        finally:
            root_logger.removeHandler(handler)

    def _verify_scenario_results(
        self, scenarios: list[dict[str, Any]], *, wait_sec: float = 0
    ) -> None:
        """Attach raw/enriched landing state (+ JSON bodies) to each scenario correlation."""
        if not self.db:
            return
        import time

        from audit_validator.generate_run_report import _event_for_report

        deadline = time.monotonic() + max(wait_sec, 0)
        pending = [s for s in scenarios if s.get("xCorrelationId") and s.get("operation")]
        while pending:
            still: list[dict[str, Any]] = []
            for scenario in pending:
                cid = str(scenario.get("xCorrelationId") or "")
                op = str(scenario.get("operation") or "")
                try:
                    raw, enriched = self.db.latest_pair(
                        op, require_pair=False, correlation_id=cid
                    )
                    scenario["raw"] = bool(raw)
                    scenario["enriched"] = bool(enriched)
                    scenario["raw_event"] = _event_for_report(raw)
                    scenario["enriched_event"] = _event_for_report(enriched)
                    if not (raw and enriched) and time.monotonic() < deadline:
                        still.append(scenario)
                except Exception:
                    scenario["raw"] = False
                    scenario["enriched"] = False
                    scenario["raw_event"] = None
                    scenario["enriched_event"] = None
                    if time.monotonic() < deadline:
                        still.append(scenario)
            if not still or time.monotonic() >= deadline:
                break
            time.sleep(min(5.0, max(deadline - time.monotonic(), 0.5)))
            pending = still

        for scenario in scenarios:
            if "raw" not in scenario:
                scenario.setdefault("raw", False)
                scenario.setdefault("enriched", False)

    def _run_touchpoint_scenarios(
        self, job_id: str, selection: list[str]
    ) -> tuple[int, list[dict[str, Any]]]:
        """Generate GraphQL events per touchpoint scenario (create→seed→trigger→cleanup)."""
        from audit_validator.generation_tracker import record_generation
        from audit_validator.simulation.client import DualEndpointGraphQLClient
        from audit_validator.simulation.config import load_simulation_config
        from audit_validator.touchpoint.payloads import FLOW_DEFS
        from audit_validator.touchpoint.scenarios import expand_selection_to_scenarios
        from audit_validator.simulation.touchpoint_runner import run_scenario

        # Re-read auth/context from .env each run (uvicorn does not reload .env on edit).
        try:
            from dotenv import dotenv_values

            disk = dotenv_values(self.project_root / ".env") or {}
            for key in (
                "BEARER_TOKEN",
                "NEXTGEN_BEARER_TOKEN",
                "OAUTH_GCID",
                "OAUTH_ORG",
                "GRAPHQL_CONTEXT_CUSTOMER_ID",
                "GRAPHQL_USE_CUSTOMER_CONTEXT",
                "GRAPHQL_SEND_OWN_CONTEXT_HEADER",
            ):
                if key in disk:
                    os.environ[key] = str(disk.get(key) or "")
        except Exception:  # noqa: BLE001
            pass

        scenarios = expand_selection_to_scenarios(selection)
        if not scenarios:
            self.store.append_log(job_id, "⚠ No GraphQL touchpoint scenarios to run")
            return 0, []

        from audit_validator.auth import customer_context_header_id

        cfg = load_simulation_config(self.project_root)
        client = DualEndpointGraphQLClient(cfg)
        client.set_project_root(self.project_root)
        # Match browser / e2e: never echo own GCID as x-context-customerid
        # (that requires MANAGE_COMPANIES and FORBIDs normal mutations).
        try:
            profile = client.request(
                "query GetProfile { getProfile { id customer { id } } }"
            )
            profile_customer = (
                ((profile.get("getProfile") or {}).get("customer") or {}).get("id") or ""
            )
        except Exception as exc:  # noqa: BLE001
            self.store.append_log(job_id, f"⚠ getProfile for context: {exc}")
            profile_customer = ""
        context_id = customer_context_header_id(
            use_customer_context=bool(getattr(cfg, "use_customer_context", False)),
            customer_context_id=getattr(cfg, "customer_context_id", "") or "",
            profile_customer_id=profile_customer,
        )
        if context_id:
            client.set_customer_id(context_id)
            self.store.append_log(job_id, f"  context header: {context_id[:8]}…")
        else:
            self.store.append_log(job_id, "  context header: off (own company)")
        self.store.append_log(
            job_id,
            f"▸ Touchpoint generate: {len(scenarios)} scenario(s) "
            f"(cleanup={'on' if (os.getenv('GENERATE_CLEANUP', '1') not in {'0','false','no'}) else 'off'})",
        )

        fails = 0
        scenario_rows: list[dict[str, Any]] = []
        ops_for_verify: list[str] = []
        for sc in scenarios:
            if self.store.is_cancelled(job_id):
                self.store.append_log(job_id, "⏹ Generate aborted — skipping remaining scenarios")
                break
            op = sc["operation"]
            touch = sc["touchpoint"]
            steps = list(sc.get("steps") or [op])
            # Ensure FLOW_DEFS steps win when available
            if op in FLOW_DEFS and touch in FLOW_DEFS[op]:
                steps = list(FLOW_DEFS[op][touch])
            result = run_scenario(
                client=client,
                cfg=cfg,
                operation=op,
                touchpoint=touch,
                steps=steps,
                scenario_id=sc["id"],
                log_fn=lambda m, jid=job_id: self.store.append_log(jid, m),
            )
            target_step = next(
                (s for s in reversed(result.step_results) if s.get("op") == op),
                {},
            )
            scenario_rows.append(
                {
                    "scenario_id": sc["id"],
                    "operation": op,
                    "touchpoint": touch,
                    "label": None,  # filled below after display name
                    "steps": steps,
                    "status": result.status,
                    "xCorrelationId": result.correlation_id,
                    "input": target_step.get("input") or {},
                    "graphql_response": target_step.get("response") or {},
                    "error": result.error,
                    "raw": False,
                    "enriched": False,
                    "raw_event": None,
                    "enriched_event": None,
                    "source": "be",
                    "channel": "BE",
                }
            )
            # Persist mutation response + trigger context for source validation
            resp = target_step.get("response")
            try:
                from audit_validator.auth import jwt_identity
                from audit_validator.simulation.trigger_context import (
                    build_trigger_context,
                    save_trigger_context,
                )
                from audit_validator.touchpoint.scenarios import scenario_display_name as _sdn

                display = _sdn(op, touch, be=True)
                scenario_rows[-1]["label"] = display
                gql_dir = self.project_root / "payload" / "graphql"
                gql_dir.mkdir(parents=True, exist_ok=True)
                if isinstance(resp, dict) and resp:
                    (gql_dir / f"{op}.json").write_text(
                        json.dumps(resp, indent=2, default=str),
                        encoding="utf-8",
                    )
                    (gql_dir / f"{display}.json").write_text(
                        json.dumps(resp, indent=2, default=str),
                        encoding="utf-8",
                    )
                ctx = build_trigger_context(
                    operation=op,
                    correlation_id=result.correlation_id,
                    graphql_response=resp if isinstance(resp, dict) else {},
                    graphql_input=target_step.get("input") if isinstance(target_step.get("input"), dict) else {},
                    user_agent=getattr(cfg, "nextgen_user_agent", None),
                    jwt_identity=jwt_identity(),
                    success=result.status == "PASS",
                )
                save_trigger_context(self.project_root, op, ctx)
                save_trigger_context(self.project_root, display, ctx)
            except Exception as exc:  # noqa: BLE001
                self.store.append_log(job_id, f"  ⚠ Could not save trigger context for {op}: {exc}")
            if result.correlation_id:
                from audit_validator.touchpoint.scenarios import scenario_display_name as _sdn_be

                be_label = _sdn_be(op, touch, be=True)
                scenario_rows[-1]["label"] = be_label
                record_generation(
                    op,
                    result.correlation_id,
                    project_root=self.project_root,
                    kind="graphql",
                    meta={
                        "touchpoint": touch,
                        "scenario_id": sc["id"],
                        "status": result.status,
                        "display": be_label,
                    },
                )
                if be_label != op:
                    record_generation(
                        be_label,
                        result.correlation_id,
                        project_root=self.project_root,
                        kind="graphql",
                        meta={"touchpoint": touch, "scenario_id": sc["id"], "status": result.status},
                    )
                ops_for_verify.append(op)
            if result.status != "PASS":
                fails += 1
                self.store.append_log(
                    job_id,
                    f"✖ {sc['id']}: {result.error or result.status}",
                )
            else:
                self.store.append_log(
                    job_id,
                    f"✓ {sc['id']} cid={(result.correlation_id or '')[:8]}",
                )

        # Stash nothing extra — mongo verify uses resolved_ops
        return (1 if fails else 0), scenario_rows

    def _run_e2e(
        self,
        job_id: str,
        flow_set: frozenset[str] | None,
        skip_passed: bool,
        operations: list[str] | None = None,
    ) -> int:
        from audit_validator.pipeline.e2e import run_e2e
        from audit_validator.report_paths import coverage_json, result_xlsx, validation_json

        if flow_set:
            from audit_validator.config import load_config
            from audit_validator.pipeline.e2e import (
                _print_pipeline_summary,
                _write_e2e_reports,
                collect_and_validate,
            )
            from audit_validator.models import ValidationStatus
            from audit_validator.simulation.flow_catalog import audit_operation

            cfg = load_config(self.project_root)
            # When the user targets specific operations, don't inject the whole cron
            # suite — that's what made "generate 1 op" fire dozens of unrelated events.
            targeted = bool(operations)
            settle_ops: frozenset[str] | None = None
            if targeted:
                from dataclasses import replace

                # Targeted runs must be BOUNDED and NON-DESTRUCTIVE:
                #  - never purge the shared platform queue (it feeds the ingestion
                #    service and other consumers — purging it is what wiped 2000+ msgs),
                #  - don't drain the pre-flow backlog,
                #  - cap the settle wait; we exit as soon as the SELECTED ops enrich
                #    (via settle_operations) instead of waiting for global queue idle.
                settle_sec = float(os.getenv("TARGETED_SETTLE_SEC", "60"))
                cfg = replace(
                    cfg,
                    settle_after_flows_sec=settle_sec,
                    enriched_catchup_sec=0.0,
                    enriched_backlog_drain_sec=0.0,
                    purge_test_queues_on_e2e=False,
                    purge_queues_on_e2e=False,
                )
                settle_ops = frozenset(operations)
            self.store.append_log(job_id, "▸ Starting RabbitMQ consumer — capturing raw events…")
            e2e = collect_and_validate(
                cfg,
                flow_filter=flow_set,
                include_cron=not targeted,
                settle_operations=settle_ops,
                purge_before=False if targeted else None,
                purge_after=False if targeted else None,
            )
            self.store.append_log(job_id, "▸ Capturing enriched events…")

            if targeted:
                ops_set = set(operations or [])
                kept = [
                    r
                    for r in e2e.validation_results
                    if r.operation in ops_set or audit_operation(r.operation) in ops_set
                ]
                dropped = len(e2e.validation_results) - len(kept)
                e2e.validation_results = kept
                self.store.append_log(
                    job_id,
                    f"▸ Scoped validation to {len(kept)} result(s) for selected "
                    f"operation(s); ignored {dropped} sibling event(s) from the same flow.",
                )

            _print_pipeline_summary(cfg, e2e)
            _write_e2e_reports(
                cfg,
                e2e,
                report_path=str(validation_json(cfg.project_root)),
                coverage_path=str(coverage_json(cfg.project_root)),
                csv_path=str(cfg.project_root / "temp" / "results.csv"),
                xlsx_path=str(result_xlsx(cfg.project_root)),
            )
            any_fail = any(r.status == ValidationStatus.FAIL for r in e2e.validation_results)
            # For a targeted run, ignore sibling flow failures in the exit code.
            if targeted:
                return 1 if any_fail else 0
            return 1 if any_fail or e2e.flows_exit_code != 0 else 0

        return run_e2e(
            project_root=self.project_root,
            report_path=str(validation_json(self.project_root)),
            coverage_path=str(coverage_json(self.project_root)),
            csv_path=str(self.project_root / "temp" / "results.csv"),
            xlsx_path=str(result_xlsx(self.project_root)),
            skip_passed=skip_passed,
            include_cron=True,
        )

    def _targeted_cfg(self, cfg: Any) -> Any:
        """Bounded, non-destructive config for a targeted/cron run (see _run_e2e)."""
        from dataclasses import replace

        settle_sec = float(os.getenv("TARGETED_SETTLE_SEC", "60"))
        return replace(
            cfg,
            settle_after_flows_sec=settle_sec,
            enriched_catchup_sec=0.0,
            enriched_backlog_drain_sec=0.0,
            purge_test_queues_on_e2e=False,
            purge_queues_on_e2e=False,
        )

    def _run_cron_cases(self, job_id: str, case_ids: list[str]) -> int:
        """Inject the selected cron/scheduler payloads and validate raw↔enriched."""
        from audit_validator.config import load_config
        from audit_validator.models import ValidationStatus
        from audit_validator.pipeline.e2e import (
            _print_pipeline_summary,
            _write_e2e_reports,
            collect_and_validate,
        )
        from audit_validator.report_paths import coverage_json, result_xlsx, validation_json

        cfg = self._targeted_cfg(load_config(self.project_root))
        self.store.append_log(job_id, f"▸ Injecting {len(case_ids)} cron scheduler payload(s)…")
        e2e = collect_and_validate(
            cfg,
            skip_flows=True,
            include_cron=True,
            cron_case_filter=frozenset(case_ids),
            purge_before=False,
            purge_after=False,
        )
        _print_pipeline_summary(cfg, e2e)
        _write_e2e_reports(
            cfg,
            e2e,
            report_path=str(validation_json(cfg.project_root)),
            coverage_path=str(coverage_json(cfg.project_root)),
            csv_path=str(cfg.project_root / "temp" / "results.csv"),
            xlsx_path=str(result_xlsx(cfg.project_root)),
        )
        any_fail = any(r.status == ValidationStatus.FAIL for r in e2e.validation_results)
        return 1 if any_fail else 0

    def _run_ingress_cases(self, job_id: str, case_ids: list[str]) -> int:
        """Send the selected desktop/plugin payloads through the resolver Ingress API."""
        from audit_validator.ingress.runner import run_ingress_validation
        from audit_validator.report_paths import ingress_results_json

        settle_sec = float(os.getenv("TARGETED_SETTLE_SEC", "60"))
        self.store.append_log(
            job_id, f"▸ Sending {len(case_ids)} ingress event(s) to the Ingress API…"
        )
        run = run_ingress_validation(
            project_root=self.project_root,
            case_filter=frozenset(case_ids),
            report_path=ingress_results_json(self.project_root),
            settle_sec=settle_sec,
            purge_before=False,
        )
        self.store.append_log(
            job_id,
            f"  ✓ Ingress — PASS={run.pass_count} FAIL={run.fail_count} WARN={run.warn_count}",
        )
        return 1 if run.fail_count else 0

    def _run_scenario_source_validation(
        self, job_id: str, scenarios: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Validate each touchpoint scenario under activateFamily(global) etc. for Results."""
        from audit_validator.touchpoint.scenarios import scenario_display_name
        from bson import json_util

        enrich_dir = self.project_root / "payload" / "enrich"
        raw_dir = self.project_root / "payload" / "raw"
        gql_dir = self.project_root / "payload" / "graphql"
        enrich_dir.mkdir(parents=True, exist_ok=True)
        raw_dir.mkdir(parents=True, exist_ok=True)
        gql_dir.mkdir(parents=True, exist_ok=True)

        display_ops: list[str] = []
        for sc in scenarios:
            if str(sc.get("status") or "").upper() != "PASS":
                continue
            enriched = sc.get("enriched_event")
            if not isinstance(enriched, dict) or not enriched:
                continue
            display = scenario_display_name(
                str(sc.get("operation") or ""),
                sc.get("touchpoint"),
                ui=str(sc.get("source") or "").lower() == "ui",
                be=str(sc.get("source") or "").lower() != "ui",
            )
            # Safe filename (parentheses OK on macOS/linux)
            (enrich_dir / f"{display}.json").write_text(
                json_util.dumps(enriched, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            raw = sc.get("raw_event")
            if isinstance(raw, dict) and raw:
                (raw_dir / f"{display}.json").write_text(
                    json_util.dumps(raw, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
            resp = sc.get("graphql_response")
            if isinstance(resp, dict) and resp:
                (gql_dir / f"{display}.json").write_text(
                    json.dumps(resp, indent=2, default=str),
                    encoding="utf-8",
                )
            try:
                from audit_validator.auth import jwt_identity
                from audit_validator.simulation.trigger_context import (
                    build_trigger_context,
                    save_trigger_context,
                )

                cfg_ua = None
                try:
                    from audit_validator.simulation.config import load_simulation_config

                    cfg_ua = load_simulation_config(self.project_root).nextgen_user_agent
                except Exception:
                    pass
                ctx = build_trigger_context(
                    operation=str(sc.get("operation") or ""),
                    correlation_id=str(sc.get("xCorrelationId") or "") or None,
                    graphql_response=resp if isinstance(resp, dict) else {},
                    graphql_input=sc.get("input") if isinstance(sc.get("input"), dict) else {},
                    user_agent=cfg_ua,
                    jwt_identity=jwt_identity(),
                    success=True,
                )
                save_trigger_context(self.project_root, display, ctx)
            except Exception as exc:  # noqa: BLE001
                self.store.append_log(job_id, f"  ⚠ Trigger context for {display}: {exc}")
            display_ops.append(display)
            self.store.append_log(job_id, f"  ✓ Staged scenario sample {display}")

        if not display_ops:
            self.store.append_log(
                job_id,
                "⚠ No PASS scenarios with enriched JSON — falling back to operation-level validate",
            )
            bare = sorted({str(s.get("operation") or "") for s in scenarios if s.get("operation")})
            return self._run_source_validation(job_id, [o for o in bare if o])

        return self._run_source_validation(job_id, display_ops, skip_stage=True)

    def _run_source_validation(
        self,
        job_id: str,
        operations: list[str],
        field_paths_by_op: dict[str, list[str]] | None = None,
        *,
        skip_stage: bool = False,
    ) -> dict[str, Any]:
        if skip_stage:
            ops = [o for o in operations if (self.project_root / "payload" / "enrich" / f"{o}.json").is_file()]
            missing = [o for o in operations if o not in ops]
            if missing:
                self.store.append_log(job_id, f"  ⚠ Missing staged enrich for: {', '.join(missing[:8])}")
            if not ops:
                raise RuntimeError("No staged enriched samples for selected scenario operations")
            self.store.append_log(job_id, f"▸ Validating {len(ops)} pre-staged scenario sample(s)…")
        else:
            ops = self._stage_mongo_samples(job_id, operations)
            if not ops:
                raise RuntimeError("No enriched samples in Mongo for selected operations")

        from audit_validator.source_validation.runner import run_source_validation

        routing_keys = _routing_keys_map(self.project_root)
        job = self.store.get(job_id)
        job_kind = job.kind if job else "compare"
        saved_ops = 0

        def _row_dict(r: Any) -> dict[str, Any]:
            return {
                "operation": r.operation,
                "field": r.field,
                "field_path": r.field_path,
                "node": r.node,
                "sub_node": r.sub_node,
                "layer": r.layer,
                "source_system": r.source_system,
                "source_api": r.source_api,
                "value_in_source": r.value_in_source,
                "value_in_enriched": r.value_in_enriched,
                "match_status": r.match_status,
                "notes": r.notes,
                "routing_key": routing_keys.get(
                    str(r.operation).split("(", 1)[0] if "(" in str(r.operation) else r.operation,
                    "",
                ),
            }

        def _on_operation_rows(operation: str, op_rows: list[Any]) -> None:
            """Flush each op to Results immediately so Compared times update mid-run."""
            nonlocal saved_ops
            from .comparison_store import save_batch_results

            try:
                save_batch_results(
                    self.project_root,
                    rows=[_row_dict(r) for r in op_rows],
                    job_id=job_id,
                    job_kind=job_kind,
                    compared_at=_now(),
                )
                saved_ops += 1
                if saved_ops == 1 or saved_ops % 10 == 0:
                    self.store.append_log(
                        job_id, f"  ✓ Snapshot saved ({saved_ops}) — {operation}"
                    )
            except Exception as exc:  # noqa: BLE001
                log.warning("Progressive save %s failed: %s", operation, exc)

        report = run_source_validation(
            project_root=self.project_root,
            operations=ops,
            iteration=1,
            sample_source="fresh",
            progress=lambda msg: self.store.append_log(job_id, msg),
            on_operation_rows=_on_operation_rows,
            field_paths_by_op=field_paths_by_op,
        )
        rows = [_row_dict(r) for r in report.comparison_rows]
        self.store.append_log(
            job_id,
            f"▸ Validation done — PASS={report.passed} FAIL={report.failed} SKIP={report.skipped}",
        )
        result = {
            "passed": report.passed,
            "failed": report.failed,
            "skipped": report.skipped,
            "rows": rows,
            "operations": ops,
        }
        try:
            from .comparison_store import save_batch_results

            # Final coalesce write (covers any op the progressive callback skipped).
            save_batch_results(
                self.project_root,
                rows=rows,
                job_id=job_id,
                job_kind=job_kind,
                compared_at=_now(),
            )
            self.store.append_log(
                job_id,
                f"▸ Saved latest comparison snapshot for {len(ops)} operation(s)",
            )
        except Exception as exc:  # noqa: BLE001 — persistence must not fail the job
            log.warning("Could not persist latest comparison: %s", exc)
        return result

    def _stage_mongo_samples(self, job_id: str, operations: list[str]) -> list[str]:
        import json

        from bson import json_util

        if not self.db:
            return operations

        enriched_dir = self.project_root / "payload" / "enrich"
        enriched_dir.mkdir(parents=True, exist_ok=True)
        raw_dir = self.project_root / "payload" / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)

        ops = operations or self.db.comparable_operations()
        self.store.append_log(
            job_id,
            f"▸ Pairing raw+enriched by xCorrelationId for {len(ops)} operation(s)…",
        )
        staged: list[str] = []
        missing_pair: list[str] = []
        owned_hits = 0
        try:
            from audit_validator.generation_tracker import get_owned_correlation
        except Exception:
            get_owned_correlation = None  # type: ignore[assignment]
        our_profile = ""
        try:
            from audit_validator.auth import resolve_our_profile_id

            our_profile = resolve_our_profile_id(project_root=self.project_root) or ""
        except Exception:
            our_profile = ""

        for op in ops:
            # Touchpoint variants (e.g. activateFamily(global)) don't exist as a
            # distinct source.operation in Mongo — reuse the enriched sample staged
            # during the last Generate run so Compare can re-validate them.
            if "(" in op and op.endswith(")"):
                staged_file = enriched_dir / f"{op}.json"
                if staged_file.is_file():
                    staged.append(op)
                    self.store.append_log(
                        job_id, f"  ✓ Using staged touchpoint sample for {op}"
                    )
                    continue
                # No pre-staged sample (e.g. compare launched straight from a
                # Generate-in-UI run). Each UI touchpoint scenario minted its own
                # correlation id, so pair raw+enrich by that owned cid and stage it
                # now — this keeps the compared count 1:1 with Generation Status and
                # preserves the (UI) label on the Result row.
                base_touch_op = op.split("(", 1)[0].strip() or op
                owned_cid = (
                    get_owned_correlation(op, project_root=self.project_root)
                    if get_owned_correlation
                    else None
                )
                t_raw = t_enriched = None
                if owned_cid:
                    t_raw, t_enriched = self.db.latest_pair(
                        base_touch_op, require_pair=True, correlation_id=owned_cid
                    )
                if not (t_raw and t_enriched):
                    try:
                        from audit_validator.generation_tracker import list_owned

                        t_entry = (list_owned(project_root=self.project_root).get("by_operation") or {}).get(op) or {}
                    except Exception:
                        t_entry = {}
                    find_fp = getattr(self.db, "find_fingerprint_pair", None)
                    if callable(find_fp) and (
                        owned_cid or t_entry.get("profile_id") or t_entry.get("generated_at")
                    ):
                        t_raw, t_enriched, _m = find_fp(
                            base_touch_op,
                            actor_global_user_id=our_profile or t_entry.get("profile_id"),
                            since_iso=t_entry.get("generated_at"),
                            event_id=t_entry.get("eventId") or t_entry.get("event_id"),
                        )
                if t_raw and t_enriched:
                    cid = t_enriched.get("xCorrelationId", "") or (owned_cid or "")
                    (enriched_dir / f"{op}.json").write_text(
                        json_util.dumps(t_enriched, indent=2, ensure_ascii=False), encoding="utf-8"
                    )
                    (raw_dir / f"{op}.json").write_text(
                        json_util.dumps(t_raw, indent=2, ensure_ascii=False), encoding="utf-8"
                    )
                    staged.append(op)
                    if owned_cid and cid == owned_cid:
                        owned_hits += 1
                    self.store.append_log(
                        job_id, f"  ✓ Paired {op} (owned xCorrelationId={cid}) from Mongo"
                    )
                    continue
                self.store.append_log(
                    job_id,
                    f"  ⚠ Skip {op}: no staged sample and no owned raw+enrich pair in Mongo yet "
                    "— run Generate & validate for this touchpoint first",
                )
                missing_pair.append(op)
                continue
            owned_cid = (
                get_owned_correlation(op, project_root=self.project_root)
                if get_owned_correlation
                else None
            )
            raw, enriched = self.db.latest_pair(
                op,
                require_pair=True,
                correlation_id=owned_cid,
                actor_global_user_id=our_profile or None,
            )
            if owned_cid and raw and enriched:
                owned_hits += 1
            elif owned_cid and not (raw and enriched):
                # We minted a cid but Mongo doesn't have the pair yet — fall back to
                # fingerprint (actor + time window / eventId) then latest for our actor.
                self.store.append_log(
                    job_id,
                    f"  ⚠ Owned cid {owned_cid[:8]}… not in Mongo yet for {op} — "
                    f"trying fingerprint / actor latest",
                )
                try:
                    from audit_validator.generation_tracker import list_owned

                    entry = (list_owned(project_root=self.project_root).get("by_operation") or {}).get(op) or {}
                except Exception:
                    entry = {}
                find_fp = getattr(self.db, "find_fingerprint_pair", None)
                if callable(find_fp):
                    raw, enriched, method = find_fp(
                        op,
                        actor_global_user_id=our_profile or entry.get("profile_id"),
                        since_iso=entry.get("generated_at"),
                        event_id=entry.get("eventId") or entry.get("event_id"),
                    )
                    if raw and enriched:
                        self.store.append_log(job_id, f"  ✓ Fingerprint pair for {op} via {method}")
                if not (raw and enriched):
                    raw, enriched = self.db.latest_pair(
                        op, require_pair=True, actor_global_user_id=our_profile or None
                    )

            if not (raw and enriched) and not owned_cid:
                # No owned cid on the envelope path — try fingerprint first.
                try:
                    from audit_validator.generation_tracker import list_owned

                    entry = (list_owned(project_root=self.project_root).get("by_operation") or {}).get(op) or {}
                except Exception:
                    entry = {}
                find_fp = getattr(self.db, "find_fingerprint_pair", None)
                if callable(find_fp) and (our_profile or entry.get("profile_id") or entry.get("generated_at")):
                    raw, enriched, method = find_fp(
                        op,
                        actor_global_user_id=our_profile or entry.get("profile_id"),
                        since_iso=entry.get("generated_at"),
                        event_id=entry.get("eventId") or entry.get("event_id"),
                    )
                    if raw and enriched:
                        self.store.append_log(
                            job_id, f"  ✓ Fingerprint pair for {op} via {method} (no owned cid)"
                        )

            if not (raw and enriched):
                missing_pair.append(op)
                # Explain why: is it enriched-only, raw-only, or neither?
                raw_only, enr_only = self.db.latest_pair(op, require_pair=False)
                if enr_only and not raw_only:
                    reason = "enriched present but no matching raw"
                elif raw_only and not enr_only:
                    reason = "raw present but no matching enriched (dead-lettered?)"
                elif raw_only and enr_only:
                    reason = "raw+enriched exist but xCorrelationId differs — not a real pair"
                else:
                    reason = "no documents in Mongo"
                self.store.append_log(job_id, f"  ⚠ Skip {op}: {reason}")
                continue
            cid = enriched.get("xCorrelationId", "")
            ownership = "owned" if (owned_cid and cid == owned_cid) else "latest"
            (enriched_dir / f"{op}.json").write_text(
                json_util.dumps(enriched, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            (raw_dir / f"{op}.json").write_text(
                json_util.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            staged.append(op)
            self.store.append_log(
                job_id, f"  ✓ Paired {op} ({ownership} xCorrelationId={cid})"
            )

        self.store.append_log(
            job_id,
            f"▸ Validating {len(staged)} paired operation(s) "
            f"({owned_hits} from our generate correlations); "
            f"skipped {len(missing_pair)} without a raw+enrich pair",
        )
        return staged

    def _run_compare(
        self,
        job_id: str,
        operations: list[str],
        sample_source: str,
        field_paths_by_op: dict[str, list[str]] | None = None,
    ) -> None:
        self.store.update(job_id, status=JobStatus.RUNNING, started_at=_now())
        self.store.append_log(job_id, f"▸ Source validation for {len(operations)} operation(s)…")
        if field_paths_by_op:
            n = sum(len(v) for v in field_paths_by_op.values())
            self.store.append_log(job_id, f"  · Selective attributes: {n} field path(s) across ops")
        try:
            token_status = self._ensure_token(job_id)
            self._warn_on_stale_sources(job_id, token_status)
            val_result = self._run_source_validation(
                job_id, operations, field_paths_by_op=field_paths_by_op
            )
            val_result["token"] = token_status
            self.store.update(
                job_id,
                status=JobStatus.COMPLETED,
                finished_at=_now(),
                result=val_result,
            )
        except Exception as exc:
            log.exception("Compare job failed")
            self.store.update(
                job_id,
                status=JobStatus.FAILED,
                finished_at=_now(),
                error=str(exc),
            )

    def _warn_on_stale_sources(self, job_id: str, token_status: dict[str, Any]) -> None:
        """Surface source-auth readiness so empty results aren't misread as data mismatches."""
        try:
            from audit_validator.source_validation.config import load_source_validation_config

            cfg = load_source_validation_config(self.project_root)
            if not cfg.discovery_ready:
                self.store.append_log(
                    job_id,
                    "  ⚠ Discovery/Typesense token unavailable — font source rows may show as "
                    "SKIP (source not fetched), not real mismatches.",
                )
            if not cfg.cms_ready:
                self.store.append_log(job_id, "  ⚠ CMS not configured — customer source rows will SKIP.")
            if not cfg.ums_ready:
                self.store.append_log(job_id, "  ⚠ UMS not configured — user/role source rows will SKIP.")
            err = token_status.get("error")
            if err:
                self.store.append_log(job_id, f"  ⚠ Token refresh reported: {err}")
        except Exception as exc:  # noqa: BLE001
            self.store.append_log(job_id, f"  ⚠ Source readiness check failed: {exc}")


_INFORMATIVE_KEYWORDS = (
    "pass", "fail", "error", "skip", "blocked",
    "correlation", "raw", "enrich", "mongo",
    "trigger", "validate", "compare", "phase",
    "casepilot", "testrail", "token", "timeout",
    "unreachable", "vpn", "selected", "scenario",
)


_NOISE_MARKERS = (
    "connection workflow",
    "selectconnection",
    "asyncssltransport",
    "pika version",
    "closing connection",
    "closing channel",
    "received <channel",
    "aborting transport",
    "amqp stack",
    "stack terminated",
    "user-initiated close",
    "connectionclosedbyclient",
    "normal shutdown",
    "blockingconnection",
    "channel number",
    "transport=",
    "http request",
    "debug:",
    "deleted asset",
    "cleanup",
    "🗑",
    "heartbeat",
    "prefetch",
    "source context",
    "getprofile",
    "context header",
    "casepilot running",
    "still running",
)


def _is_informative_log(msg: str) -> bool:
    """Keep only high-signal job log lines (phases, pass/fail, warnings)."""
    text = (msg or "").strip()
    if not text:
        return False
    lower = text.lower()
    if any(marker in lower for marker in _NOISE_MARKERS):
        return False
    if text.startswith(("▸", "✓", "✖", "⚠", "  ✓", "  ⚠", "  ✖")):
        return True
    if len(text) > 220:
        return False
    return any(kw in lower for kw in _INFORMATIVE_KEYWORDS)
