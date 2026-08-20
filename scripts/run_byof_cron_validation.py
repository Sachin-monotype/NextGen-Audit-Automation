"""Run BYOF cron validation using real imported font and license from DB for agentqatest."""

from __future__ import annotations

import json
import logging
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from pymongo import MongoClient

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "python"))

from audit_validator.env_profiles import apply_audit_profile
from audit_validator.config import load_config
from audit_validator.source_validation.db.connection import connect, load_mysql_config
from audit_validator.rabbitmq.publisher import publish_raw_event
from audit_validator.cron.payloads import amqp_routing_key_for_payload
from audit_validator.case_keys import cron_case_key
from audit_validator.generation_tracker import record_generation

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("byof_cron_runner")


def main():
    apply_audit_profile(project_root=PROJECT_ROOT)
    cfg = load_config(PROJECT_ROOT)
    mysql_cfg = load_mysql_config()

    gcid = "93bbce28-5143-497c-a959-1f9eada55230"
    user_id = "1a140831-8b1c-404c-ae68-5d3fb2284f78"
    profile_id = "5e0ff5be-97a1-11f1-ac0d-0e0a04e472ab"
    email = "agentqatest@gmail.com"

    log.info("Fetching real contracts and imported fonts from DB for GCID=%s...", gcid)
    conn = connect(mysql_cfg)
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM byof_license_management.contracts WHERE company_id = %s AND is_deleted = 0 LIMIT 1;", (gcid,))
        active_contract = cur.fetchone()

        cur.execute("SELECT * FROM byof_license_management.contracts WHERE company_id = %s AND status = %s LIMIT 1;", (gcid, "EXPIRED"))
        expired_contract = cur.fetchone() or active_contract

        cur.execute("SELECT * FROM byof_license_management.contracts WHERE company_id = %s AND licence_name LIKE %s LIMIT 1;", (gcid, "%overuse%"))
        overused_contract = cur.fetchone() or active_contract

        cur.execute("SELECT r.import_id, r.company_id, s.style_id FROM import_context_qa.import_records r JOIN import_context_qa.import_record_styles s ON r.import_id = s.import_id WHERE r.company_id = %s LIMIT 1;", (gcid,))
        font_row = cur.fetchone()
        style_id = font_row["style_id"] if font_row else "fF6hmA54_o"
        font_name = "Scanport 1 Regular"
    conn.close()

    log.info("Active Contract: ID=%s, Name=%s, Status=%s", active_contract["contract_id"], active_contract["licence_name"], active_contract["status"])
    log.info("Imported Font: StyleID=%s, FontName=%s", style_id, font_name)

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    def make_contract_dict(c, status_override=None, used_seats_override=None):
        return {
            "contractId": c["contract_id"],
            "companyId": gcid,
            "licenceName": c["licence_name"],
            "isFreeToUse": bool(c["is_free_to_use"]),
            "isPayOnce": bool(c.get("isPayOnce", 0)),
            "isReviewed": bool(c["is_reviewed"]),
            "licenceType": c["licence_type"],
            "linkedImportedFontScope": c["linked_imported_font_scope"] or "GLOBAL",
            "projectId": c["project_id"],
            "licenceStartDate": c["licence_start_date"].isoformat() if c.get("licence_start_date") else "2026-08-01T00:00:00.000Z",
            "licenceEndDate": c["licence_end_date"].isoformat() if c.get("licence_end_date") else "2026-08-26T00:00:00.000Z",
            "totalLicensedSeats": c["total_licensed_seats"] or 10,
            "usedSeats": used_seats_override if used_seats_override is not None else (c["used_seats"] or 0),
            "costPerSeat": float(c["cost_per_seat"]) if c.get("cost_per_seat") else None,
            "totalCost": float(c["total_cost"]) if c.get("total_cost") else 100.0,
            "licencePurchaseDate": c["licence_purchase_date"].isoformat() if c.get("licence_purchase_date") else None,
            "foundry": c.get("foundry") or "Test Foundry",
            "vendorName": c.get("vendor_name"),
            "contractReference": c.get("contract_reference"),
            "purchaseOrder": c.get("purchase_order"),
            "invoiceNumber": c.get("invoice_number"),
            "currency": c.get("currency") or "USD",
            "title": 0,
            "registeredUsers": 0,
            "notes": c.get("notes") or "Real imported font license in DB",
            "notifyBeforeExpiry": True,
            "notifyDays": 30,
            "status": status_override or c["status"],
            "createdAt": c["created_at"].isoformat() if c.get("created_at") else now_iso,
            "createdBy": user_id,
            "updatedAt": c["updated_at"].isoformat() if c.get("updated_at") else now_iso,
            "updatedBy": user_id,
        }

    cases = [
        {
            "case_id": "byofLicenceExpiring",
            "trigger_code": "A-6",
            "title_label": "A-6 byof_licence_expiry_warning / byof.licence.expiring",
            "routingKey": "byof.licence.expiring",
            "operation": "notifyByofLicenceExpiry",
            "contract": make_contract_dict(active_contract, status_override="EXPIRING_SOON"),
            "type": "BYOF Licence Expiry",
        },
        {
            "case_id": "byofLicenceExpired",
            "trigger_code": "A-7",
            "title_label": "A-7 byof_licence_expired / byof.licence.expired",
            "routingKey": "byof.licence.expired",
            "operation": "byofLicenceExpired",
            "contract": make_contract_dict(expired_contract, status_override="EXPIRED"),
            "type": "BYOF Licence Expiry",
        },
        {
            "case_id": "byofLicenceOverused",
            "trigger_code": "A-8",
            "title_label": "A-8 byof_overuse_detected / byof.licence.overused",
            "routingKey": "byof.licence.overused",
            "operation": "byofLicenceOverused",
            "contract": make_contract_dict(overused_contract, status_override="ACTIVE", used_seats_override=15),
            "type": "BYOF Licence Overused",
        },
    ]

    for c in cases:
        cid = str(uuid.uuid4())
        eid = str(uuid.uuid4())
        payload = {
            "xCorrelationId": cid,
            "eventId": eid,
            "eventVersion": 1,
            "occurredAt": now_iso,
            "routingKey": c["routingKey"],
            "actor": {
                "globalUserId": user_id,
                "globalCustomerId": gcid,
                "globalProfileId": profile_id,
                "email": email,
            },
            "source": {
                "type": [c["type"]],
                "service": "byof-license-service",
                "operation": c["operation"],
                "operationState": "success",
            },
            "subject": {
                "id": [c["contract"]["contractId"]],
                "type": c["type"],
                "contractId": [c["contract"]["contractId"]],
                "contract": c["contract"],
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
        amqp_rk = amqp_routing_key_for_payload(payload)
        log.info("Publishing %s (rk=%s, cid=%s)...", c["title_label"], c["routingKey"], cid[:8])
        publish_raw_event(cfg.rabbitmq, payload, amqp_routing_key=amqp_rk)
        c["cid"] = cid
        c["eid"] = eid
        c["payload"] = payload

        try:
            record_generation(
                c["operation"],
                cid,
                kind="cron",
                project_root=PROJECT_ROOT,
                case_key=cron_case_key(c["case_id"]),
                meta={
                    "case_id": c["case_id"],
                    "eventId": eid,
                    "profile_id": profile_id,
                    "customer_id": gcid,
                    "email": email,
                },
            )
        except Exception:
            pass

        time.sleep(1)

    log.info("All 3 BYOF cron events published. Waiting 12s for queue ingestion and enrichment...")
    time.sleep(12)

    log.info("================ Verification Results ================")
    mongo_client = MongoClient(os.getenv("MONGO_DB_URL"), serverSelectionTimeoutMS=5000)
    mongo_db = mongo_client[os.getenv("MONGO_DB_NAME", "AuditLogsQA")]

    for c in cases:
        raw_doc = mongo_db.raw.find_one({"event.xCorrelationId": c["cid"]})
        enriched_doc = mongo_db.enriched.find_one({"event.xCorrelationId": c["cid"]})
        dlq_doc = mongo_db.dlq.find_one({"event.xCorrelationId": c["cid"]})

        raw_ok = "PASS" if raw_doc else "FAIL"
        enriched_ok = "PASS" if enriched_doc else ("DLQ" if dlq_doc else "PENDING/WARN")

        log.info("[%s]", c["title_label"])
        log.info("  Correlation ID: %s", c["cid"])
        log.info("  Mongo Raw:      %s", raw_ok)
        log.info("  Mongo Enriched: %s", enriched_ok)

    log.info("Checking notification table in mt_notification_qa...")
    conn = connect(mysql_cfg)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, trigger_code, title, body, variables, created_at FROM mt_notification_qa.notifications "
            "WHERE customer_id = %s OR user_id = %s ORDER BY created_at DESC LIMIT 5;",
            (gcid, profile_id),
        )
        notifs = cur.fetchall()
        for n in notifs:
            log.info("  Notif #%s [%s]: Title='%s' Body='%s' Created=%s", n["id"], n["trigger_code"], n["title"], n["body"], n["created_at"])
    conn.close()


if __name__ == "__main__":
    main()
