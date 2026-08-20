"""Execute and validate ALL cron/scheduler events in the PREPROD environment for user sachinclaudecoder@gmail.com."""

from __future__ import annotations

import copy
import json
import logging
import os
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from pymongo import MongoClient

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "python"))

# Ensure Preprod environment
os.environ["AUDIT_TARGET"] = "preprod"
os.environ["CRON_DEFAULT_GCID"] = "4a949153-9cab-4023-b31c-8336a8a3ec46"
os.environ["GLOBAL_CUSTOMER_ID"] = "4a949153-9cab-4023-b31c-8336a8a3ec46"
os.environ["CRON_USER_ID"] = "f3122c02-fe31-4487-8bb3-57156d84f2f8"
os.environ["OAUTH_USER_ID"] = "f3122c02-fe31-4487-8bb3-57156d84f2f8"
os.environ["CRON_PROFILE_ID"] = "c893d079-9940-11f1-ac0d-0e0a04e472ab"
os.environ["AUDIT_PROFILE_ID"] = "c893d079-9940-11f1-ac0d-0e0a04e472ab"
os.environ["CRON_BYOF_CONTRACT_ID"] = "e1a7701c-03be-4d83-8da9-5b53e98a33a3"
os.environ["CRON_BYOF_USER_ID"] = "f3122c02-fe31-4487-8bb3-57156d84f2f8"
os.environ["NOTIFICATION_CLEANUP_USER_ID"] = "f3122c02-fe31-4487-8bb3-57156d84f2f8"
os.environ["QA_LOGIN_EMAIL"] = "sachinclaudecoder@gmail.com"
os.environ["GMAIL_USER"] = "sachinclaudecoder@gmail.com"

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
log = logging.getLogger("preprod_cron_runner")


@dataclass
class PreprodCronExecution:
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
    profile = apply_audit_profile(project_root=PROJECT_ROOT)
    cfg = load_config(PROJECT_ROOT)
    mysql_cfg = load_mysql_config()

    gcid = "4a949153-9cab-4023-b31c-8336a8a3ec46"
    user_id = "f3122c02-fe31-4487-8bb3-57156d84f2f8"
    profile_id = "c893d079-9940-11f1-ac0d-0e0a04e472ab"
    email = "sachinclaudecoder@gmail.com"

    log.info("=================================================================")
    log.info("Starting ALL Cron Validation in PREPROD Environment")
    log.info("Target Profile:  %s (%s)", profile.name, profile.label)
    log.info("RabbitMQ vhost:  %s", profile.rabbitmq_vhost)
    log.info("Mongo DB Name:   %s", os.getenv("MONGO_DB_NAME"))
    log.info("User Email:      %s", email)
    log.info("User ID:         %s", user_id)
    log.info("Profile ID:      %s", profile_id)
    log.info("GCID:            %s", gcid)
    log.info("=================================================================")

    # 1. Fetch real contract & imported fonts from DB
    log.info("Fetching real contracts and imported fonts from Preprod DB...")
    conn = connect(mysql_cfg)
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM byof_license_management.contracts WHERE company_id = %s LIMIT 1;", (gcid,))
        real_contract = cur.fetchone()

        cur.execute(
            "SELECT r.import_id, r.company_id, s.style_id FROM import_context.import_records r "
            "JOIN import_context.import_record_styles s ON r.import_id = s.import_id "
            "WHERE r.company_id = %s LIMIT 1;",
            (gcid,),
        )
        font_row = cur.fetchone()
        style_id = font_row["style_id"] if font_row else "Oy3JT1zy_o"
        font_name = "Scanport 1 Regular"
    conn.close()

    contract_id = real_contract["contract_id"] if real_contract else "e1a7701c-03be-4d83-8da9-5b53e98a33a3"
    licence_name = real_contract["licence_name"] if real_contract else "Everest Nighters"
    log.info("Real Contract: ID=%s, Name=%s", contract_id, licence_name)
    log.info("Real Font:     StyleID=%s, FontName=%s", style_id, font_name)

    # 2. Load all file-based cron cases
    cron_cases = load_cron_cases()
    log.info("Found %d cron cases in data/cron_payloads", len(cron_cases))

    # Also prepare extra dynamic trigger cases (like BYOF overuse, etc.)
    now = datetime.now(timezone.utc)
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    expiry_15d = (now + timedelta(days=15)).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    executions: list[PreprodCronExecution] = []
    published_items: list[tuple[PreprodCronExecution, dict]] = []

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

            # Re-ensure specific fields for preprod user
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
            if isinstance(subj, dict) and "styles" in subj and isinstance(subj["styles"], list):
                for st in subj["styles"]:
                    if isinstance(st, dict):
                        st["styleId"] = style_id
                        st["styleName"] = font_name
                        st["fontName"] = font_name

            exec_item = PreprodCronExecution(
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

    # Add specific extra trigger: BYOF Overuse (A-8)
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
                "foundry": "Foundry Preprod",
                "vendorName": "Vendor Preprod",
                "currency": "USD",
                "title": 0,
                "registeredUsers": 0,
                "notes": "Preprod Overuse Test",
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
    overuse_item = PreprodCronExecution(
        case_id="byofLicenceOverused",
        routing_key="byof.licence.overused",
        operation="byofLicenceOverused",
        service="byof-license-service",
        cid=overuse_cid,
        eid=overuse_eid,
    )
    executions.append(overuse_item)
    published_items.append((overuse_item, overuse_payload))

    # Add specific extra trigger: BYOF Expiring (A-6 with exact expiry date)
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
                "foundry": "Foundry Preprod",
                "vendorName": "Vendor Preprod",
                "currency": "USD",
                "title": 0,
                "registeredUsers": 0,
                "notes": "Preprod Expiring Test",
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
    expiring_item = PreprodCronExecution(
        case_id="byofLicenceExpiringSoon",
        routing_key="byof.licence.expiring",
        operation="notifyByofLicenceExpiry",
        service="byof-license-service",
        cid=expiring_cid,
        eid=expiring_eid,
    )
    executions.append(expiring_item)
    published_items.append((expiring_item, expiring_payload))

    # 3. Publish all cron events to RabbitMQ
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

    # 4. Wait for processing / settlement
    wait_sec = 18
    log.info("All cron events published. Waiting %ds for resolver ingestion, MongoDB save, and notification processing...", wait_sec)
    time.sleep(wait_sec)

    # 5. Check MongoDB AuditLogsPreprod
    log.info("================ Checking MongoDB (AuditLogsPreprod) ================")
    mongo_client = MongoClient(os.getenv("MONGO_DB_URL"), serverSelectionTimeoutMS=5000)
    mongo_db = mongo_client["AuditLogsPreprod"]

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

    # 6. Check MySQL mt_notification for generated notifications
    log.info("================ Checking MySQL (mt_notification) ================")
    conn = connect(mysql_cfg)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, trigger_code, title, body, variables, created_at FROM mt_notification.notifications "
            "WHERE customer_id = %s OR user_id = %s ORDER BY id DESC LIMIT 25;",
            (gcid, profile_id),
        )
        recent_notifs = cur.fetchall()
        log.info("Found %d recent notifications for user in mt_notification:", len(recent_notifs))
        for n in recent_notifs:
            log.info("  [ID %s] Trigger=%s | Title='%s' | Created=%s", n["id"], n["trigger_code"], n["title"], n["created_at"])
    conn.close()

    # 7. Write Results JSON
    report_file = PROJECT_ROOT / "preprod_cron_results.json"
    results_data = {
        "environment": "preprod",
        "user_email": email,
        "user_id": user_id,
        "profile_id": profile_id,
        "gcid": gcid,
        "contract_id": contract_id,
        "font_style_id": style_id,
        "total_cases": len(executions),
        "published_count": sum(1 for e in executions if e.publish_status == "PASS"),
        "timestamp": now_iso,
        "cases": [
            {
                "case_id": e.case_id,
                "routing_key": e.routing_key,
                "operation": e.operation,
                "service": e.service,
                "correlation_id": e.cid,
                "publish_status": e.publish_status,
                "mongo_raw": e.mongo_raw,
                "mongo_enriched": e.mongo_enriched,
            }
            for e in executions
        ],
    }
    report_file.write_text(json.dumps(results_data, indent=2), encoding="utf-8")
    log.info("Results report saved to %s", report_file)


if __name__ == "__main__":
    main()
