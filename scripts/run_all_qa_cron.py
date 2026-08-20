"""Execute and validate ALL cron/scheduler events in the QA environment for agentqatest@gmail.com."""

from __future__ import annotations

import copy
import json
import logging
import os
import sys
import time
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from pymongo import MongoClient

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "python"))

# Ensure QA environment
os.environ["AUDIT_TARGET"] = "qa"
os.environ["CRON_DEFAULT_GCID"] = "93bbce28-5143-497c-a959-1f9eada55230"
os.environ["GLOBAL_CUSTOMER_ID"] = "93bbce28-5143-497c-a959-1f9eada55230"
os.environ["CRON_USER_ID"] = "1a140831-8b1c-404c-ae68-5d3fb2284f78"
os.environ["OAUTH_USER_ID"] = "1a140831-8b1c-404c-ae68-5d3fb2284f78"
os.environ["CRON_PROFILE_ID"] = "5e0ff5be-97a1-11f1-ac0d-0e0a04e472ab"
os.environ["AUDIT_PROFILE_ID"] = "5e0ff5be-97a1-11f1-ac0d-0e0a04e472ab"
os.environ["QA_LOGIN_EMAIL"] = "agentqatest@gmail.com"
os.environ["GMAIL_USER"] = "agentqatest@gmail.com"

from audit_validator.env_profiles import apply_audit_profile
from audit_validator.config import load_config
from audit_validator.source_validation.db.connection import connect, load_mysql_config
from audit_validator.rabbitmq.publisher import publish_raw_event
from audit_validator.cron.payloads import (
    load_cron_cases,
    normalize_cron_payload,
    amqp_routing_key_for_payload,
    CRON_NO_ENRICHER_OPERATIONS,
)
from audit_validator.case_keys import cron_case_key
from audit_validator.generation_tracker import record_generation

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("qa_cron_runner")


@dataclass
class QACronExecution:
    case_id: str
    routing_key: str
    operation: str
    service: str
    cid: str
    eid: str
    publish_status: str = "PASS"
    mongo_raw: str = "PENDING"
    mongo_enriched: str = "PENDING"
    notification_status: str = "N/A"
    notif_id: str = ""
    notif_title: str = ""
    error: str = ""


def main():
    target_user = sys.argv[1] if len(sys.argv) > 1 else "agentqatest@gmail.com"
    profile = apply_audit_profile(project_root=PROJECT_ROOT)
    cfg = load_config(PROJECT_ROOT)
    mysql_cfg = load_mysql_config()

    log.info("=================================================================")
    log.info("Starting ALL Cron Events Execution in QA Environment")
    log.info("Target Profile:  %s (%s)", profile.name, profile.label)
    log.info("RabbitMQ vhost:  %s", profile.rabbitmq_vhost)
    log.info("User Filter:     %s", target_user)
    log.info("=================================================================")

    # 1. Fetch real details from DB
    log.info("Fetching real user, contract & imported fonts from QA DB...")
    conn = connect(mysql_cfg)
    with conn.cursor() as cur:
        if "@" in target_user:
            cur.execute(
                "SELECT profile_Id_uuid, user_id_uuid, customer_id_uuid, email FROM user_management_nextgenqa.vw_profile_details "
                "WHERE email = %s AND is_active = 1 LIMIT 1;",
                (target_user,),
            )
        else:
            cur.execute(
                "SELECT profile_Id_uuid, user_id_uuid, customer_id_uuid, email FROM user_management_nextgenqa.vw_profile_details "
                "WHERE customer_id_uuid = %s AND is_active = 1 LIMIT 1;",
                (target_user,),
            )
        prof = cur.fetchone()
        if prof:
            profile_id = prof["profile_Id_uuid"]
            user_id = prof["user_id_uuid"]
            gcid = prof["customer_id_uuid"]
            email = prof["email"]
        else:
            gcid = target_user
            cur.execute("SELECT profile_Id_uuid, user_id_uuid, email FROM user_management_nextgenqa.vw_profile_details WHERE customer_id_uuid = %s LIMIT 1;", (gcid,))
            p2 = cur.fetchone()
            if p2:
                profile_id = p2["profile_Id_uuid"]
                user_id = p2["user_id_uuid"]
                email = p2["email"]
            else:
                profile_id = "11f87a9e-85ab-11f1-ac0d-0e0a04e472ab"
                user_id = "4f754378-3642-44c5-92e9-5af969a68e0b"
                email = "monotype.staging+demo12321Julydevbugeula@gmail.com"

        cur.execute(
            "SELECT * FROM byof_license_management.contracts WHERE company_id = %s AND is_deleted = 0 LIMIT 1;",
            (gcid,),
        )
        real_contract = cur.fetchone()
        if not real_contract:
            cur.execute(
                "SELECT * FROM byof_license_management.contracts WHERE company_id = %s LIMIT 1;",
                (gcid,),
            )
            real_contract = cur.fetchone()

        cur.execute(
            "SELECT r.import_id, r.company_id, s.style_id FROM import_context_qa.import_records r "
            "JOIN import_context_qa.import_record_styles s ON r.import_id = s.import_id "
            "WHERE r.company_id = %s LIMIT 1;",
            (gcid,),
        )
        font_row = cur.fetchone()
        style_id = font_row["style_id"] if font_row else "fF6hmA54_o"
        font_name = real_contract["licence_name"] if real_contract else "Viva Kaiva Medium"
    conn.close()

    contract_id = real_contract["contract_id"] if real_contract else str(uuid.uuid4())
    licence_name = real_contract["licence_name"] if real_contract else "1234"

    log.info("Target GCID:     %s", gcid)
    log.info("Profile ID:      %s", profile_id)
    log.info("User ID:         %s", user_id)
    log.info("Email:           %s", email)
    log.info("Real Contract:   ID=%s, Name=%s", contract_id, licence_name)
    log.info("Real Font:       StyleID=%s, FontName=%s", style_id, font_name)

    # 2. Load all file-based cron cases
    cron_cases = load_cron_cases()
    log.info("Found %d cron cases in data/cron_payloads", len(cron_cases))

    now = datetime.now(timezone.utc)
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    expiry_15d = (now + timedelta(days=15)).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    executions: list[QACronExecution] = []
    published_items: list[tuple[QACronExecution, dict]] = []

    # Process all standard cron cases
    for case in cron_cases:
        try:
            raw = json.loads(case.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                continue
            payload = normalize_cron_payload(
                raw,
                case_id=case.case_id,
                gcid=gcid,
                user_id=user_id,
                profile_id=profile_id,
                byof_contract_id=contract_id if "byof" in case.case_id.lower() or "licen" in case.case_id.lower() else None,
            )

            # Ensure actor matches target user
            actor = payload.setdefault("actor", {})
            if isinstance(actor, dict):
                actor["globalCustomerId"] = gcid
                actor["globalUserId"] = user_id
                actor["globalProfileId"] = profile_id
                actor["email"] = email

            cid = str(payload["xCorrelationId"])
            eid = str(payload["eventId"])
            rk = str(payload.get("routingKey") or case.routing_key)
            op = case.operation or payload.get("source", {}).get("operation", case.case_id)
            srv = case.service or payload.get("source", {}).get("service", "scheduler")

            # Patch BYOF font style if applicable
            subj = payload.get("subject")
            if isinstance(subj, dict):
                if "styles" in subj and isinstance(subj["styles"], list):
                    for st in subj["styles"]:
                        if isinstance(st, dict):
                            st["styleId"] = style_id
                            st["styleName"] = font_name
                            st["fontName"] = font_name
                if "contract" in subj and isinstance(subj["contract"], dict):
                    c = subj["contract"]
                    c["contractId"] = contract_id
                    c["companyId"] = gcid
                    c["licenceName"] = licence_name
                    c["createdBy"] = user_id
                    c["updatedBy"] = user_id

            exec_item = QACronExecution(
                case_id=case.case_id,
                routing_key=rk,
                operation=op,
                service=srv,
                cid=cid,
                eid=eid,
            )
            executions.append(exec_item)
            published_items.append((exec_item, payload))
        except Exception as e:
            log.error("Failed to prepare cron case %s: %s", case.case_id, e)

    # 3. Add extra dynamic BYOF trigger: BYOF Overuse (A-8)
    overuse_cid = str(uuid.uuid4())
    overuse_eid = str(uuid.uuid4())
    overuse_payload = {
        "xCorrelationId": overuse_cid,
        "eventId": overuse_eid,
        "eventVersion": 1,
        "occurredAt": now_iso,
        "routingKey": "byof.licence.overused",
        "actor": {
            "globalUserId": user_id,
            "globalCustomerId": gcid,
            "globalProfileId": profile_id,
            "email": email,
        },
        "source": {
            "type": ["BYOF Licence Overused"],
            "service": "byof-license-service",
            "operation": "byofLicenceOverused",
            "operationState": "success",
        },
        "subject": {
            "id": [contract_id],
            "type": "BYOF Licence Overused",
            "contractId": [contract_id],
            "contract": {
                "contractId": contract_id,
                "companyId": gcid,
                "licenceName": licence_name,
                "isFreeToUse": False,
                "isPayOnce": False,
                "isReviewed": True,
                "licenceType": "DESKTOP",
                "linkedImportedFontScope": "GLOBAL",
                "projectId": None,
                "licenceStartDate": "2026-08-01T00:00:00.000Z",
                "licenceEndDate": expiry_15d,
                "totalLicensedSeats": 10,
                "usedSeats": 15,
                "costPerSeat": 50,
                "totalCost": 500,
                "foundry": "Foundry QA",
                "vendorName": "Vendor QA",
                "currency": "USD",
                "title": 0,
                "registeredUsers": 0,
                "notes": "QA Overuse Test for agentqatest",
                "notifyBeforeExpiry": True,
                "notifyDays": 30,
                "status": "ACTIVE",
                "createdAt": now_iso,
                "createdBy": user_id,
                "updatedAt": now_iso,
                "updatedBy": user_id,
            },
            "styles": [
                {
                    "styleId": style_id,
                    "styleName": font_name,
                    "fontName": font_name,
                    "linkedAt": now_iso,
                    "linkedBy": user_id,
                }
            ],
        },
    }
    overuse_item = QACronExecution(
        case_id="byofLicenceOverused",
        routing_key="byof.licence.overused",
        operation="byofLicenceOverused",
        service="byof-license-service",
        cid=overuse_cid,
        eid=overuse_eid,
    )
    executions.append(overuse_item)
    published_items.append((overuse_item, overuse_payload))

    # 4. Add extra dynamic BYOF trigger: BYOF Expiring (A-6)
    expiring_cid = str(uuid.uuid4())
    expiring_eid = str(uuid.uuid4())
    expiring_payload = {
        "xCorrelationId": expiring_cid,
        "eventId": expiring_eid,
        "eventVersion": 1,
        "occurredAt": now_iso,
        "routingKey": "byof.licence.expiring",
        "actor": {
            "globalUserId": user_id,
            "globalCustomerId": gcid,
            "globalProfileId": profile_id,
            "email": email,
        },
        "source": {
            "type": ["BYOF Licence Expiry"],
            "service": "byof-license-service",
            "operation": "notifyByofLicenceExpiry",
            "operationState": "success",
        },
        "subject": {
            "id": [contract_id],
            "type": "BYOF Licence Expiry",
            "contractId": [contract_id],
            "contract": {
                "contractId": contract_id,
                "companyId": gcid,
                "licenceName": licence_name,
                "isFreeToUse": False,
                "isPayOnce": False,
                "isReviewed": True,
                "licenceType": "DESKTOP",
                "linkedImportedFontScope": "GLOBAL",
                "projectId": None,
                "licenceStartDate": "2026-08-01T00:00:00.000Z",
                "licenceEndDate": expiry_15d,
                "totalLicensedSeats": 10,
                "usedSeats": 2,
                "costPerSeat": 50,
                "totalCost": 500,
                "foundry": "Foundry QA",
                "vendorName": "Vendor QA",
                "currency": "USD",
                "title": 0,
                "registeredUsers": 0,
                "notes": "QA Expiring Test for agentqatest",
                "notifyBeforeExpiry": True,
                "notifyDays": 30,
                "status": "EXPIRING_SOON",
                "createdAt": now_iso,
                "createdBy": user_id,
                "updatedAt": now_iso,
                "updatedBy": user_id,
            },
            "styles": [
                {
                    "styleId": style_id,
                    "styleName": font_name,
                    "fontName": font_name,
                    "linkedAt": now_iso,
                    "linkedBy": user_id,
                }
            ],
        },
    }
    expiring_item = QACronExecution(
        case_id="byofLicenceExpiringSoon",
        routing_key="byof.licence.expiring",
        operation="notifyByofLicenceExpiry",
        service="byof-license-service",
        cid=expiring_cid,
        eid=expiring_eid,
    )
    executions.append(expiring_item)
    published_items.append((expiring_item, expiring_payload))

    # 5. Publish all cron events to RabbitMQ in QA
    log.info("Publishing %d total cron events to RabbitMQ (vhost=%s)...", len(published_items), profile.rabbitmq_vhost)
    for exec_item, payload in published_items:
        amqp_rk = amqp_routing_key_for_payload(payload)
        try:
            publish_raw_event(cfg.rabbitmq, payload, amqp_routing_key=amqp_rk)
            exec_item.publish_status = "PASS"
            try:
                record_generation(
                    exec_item.operation,
                    exec_item.cid,
                    kind="cron",
                    project_root=PROJECT_ROOT,
                    case_key=cron_case_key(exec_item.case_id),
                    meta={
                        "case_id": exec_item.case_id,
                        "eventId": exec_item.eid,
                        "profile_id": profile_id,
                        "customer_id": gcid,
                        "email": email,
                    },
                )
            except Exception:
                pass
            log.info("✓ Published [%s] rk=%s cid=%s", exec_item.case_id, exec_item.routing_key, exec_item.cid[:8])
        except Exception as e:
            exec_item.publish_status = "FAIL"
            exec_item.error = str(e)
            log.error("✗ Failed to publish [%s]: %s", exec_item.case_id, e)
        time.sleep(0.2)

    # 6. Wait for processing / settlement
    wait_sec = 20
    log.info("All cron events published. Waiting %ds for resolver ingestion, MongoDB save, and notification processing in QA...", wait_sec)
    time.sleep(wait_sec)

    # 7. Check MongoDB AuditLogsQA
    log.info("================ Checking MongoDB (AuditLogsQA) ================")
    mongo_client = MongoClient(os.getenv("MONGO_DB_URL"), serverSelectionTimeoutMS=5000)
    mongo_db = mongo_client["AuditLogsQA"]

    for exec_item in executions:
        raw_doc = mongo_db.raw.find_one({"event.xCorrelationId": exec_item.cid})
        enriched_doc = mongo_db.enriched.find_one({"event.xCorrelationId": exec_item.cid})
        dlq_doc = mongo_db.dlq.find_one({"event.xCorrelationId": exec_item.cid})

        exec_item.mongo_raw = "PASS" if raw_doc else "FAIL"
        if enriched_doc:
            exec_item.mongo_enriched = "PASS"
        elif dlq_doc:
            exec_item.mongo_enriched = "DLQ"
        elif exec_item.operation in CRON_NO_ENRICHER_OPERATIONS:
            exec_item.mongo_enriched = "PASSTHROUGH (WARN)"
        else:
            exec_item.mongo_enriched = "NO_ENRICHED (WARN)"

    # 8. Check MySQL mt_notification_qa for generated notifications
    log.info("================ Checking MySQL (mt_notification_qa) ================")
    conn = connect(mysql_cfg)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, trigger_code, title, body, variables, created_at FROM mt_notification_qa.notifications "
            "WHERE customer_id = %s OR user_id = %s ORDER BY id DESC LIMIT 30;",
            (gcid, profile_id),
        )
        recent_notifs = cur.fetchall()
        log.info("Found %d recent notifications for user in mt_notification_qa:", len(recent_notifs))
        for n in recent_notifs:
            log.info("  [ID %s] Trigger=%s | Title='%s' | Created=%s", n["id"], n["trigger_code"], n["title"], n["created_at"])
    conn.close()

    # 9. Save Results JSON
    output_path = PROJECT_ROOT / "qa_cron_results.json"
    results_data = {
        "timestamp": now_iso,
        "environment": "qa",
        "target_user": email,
        "gcid": gcid,
        "profile_id": profile_id,
        "total_cron_cases": len(executions),
        "published_pass": sum(1 for e in executions if e.publish_status == "PASS"),
        "published_fail": sum(1 for e in executions if e.publish_status == "FAIL"),
        "recent_notifications_count": len(recent_notifs),
        "executions": [asdict(e) for e in executions],
    }
    output_path.write_text(json.dumps(results_data, indent=2), encoding="utf-8")
    log.info("Results written to %s", output_path)


if __name__ == "__main__":
    main()
