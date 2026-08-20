"""Trigger imported font expire event in QA environment for a specific GCID using real DB records."""

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
from audit_validator.generation_tracker import record_generation
from audit_validator.case_keys import cron_case_key

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("trigger_imported_font_expire")


def trigger_imported_font_expire(target: str = "agentqatest@gmail.com"):
    apply_audit_profile(project_root=PROJECT_ROOT)
    cfg = load_config(PROJECT_ROOT)
    mysql_cfg = load_mysql_config()

    log.info("Connecting to MySQL QA DB for target: %s", target)
    conn = connect(mysql_cfg)

    with conn.cursor() as cur:
        # 1. Fetch user/profile details
        if "@" in target:
            cur.execute(
                "SELECT profile_Id_uuid, user_id_uuid, customer_id_uuid, email FROM user_management_nextgenqa.vw_profile_details "
                "WHERE email = %s AND is_active = 1 LIMIT 1;",
                (target,),
            )
        else:
            cur.execute(
                "SELECT profile_Id_uuid, user_id_uuid, customer_id_uuid, email FROM user_management_nextgenqa.vw_profile_details "
                "WHERE customer_id_uuid = %s AND is_active = 1 LIMIT 1;",
                (target,),
            )
        prof = cur.fetchone()
        if not prof:
            if "@" in target:
                cur.execute(
                    "SELECT profile_Id_uuid, user_id_uuid, customer_id_uuid, email FROM user_management_nextgenqa.vw_profile_details "
                    "WHERE email LIKE %s LIMIT 1;",
                    (f"%{target}%",),
                )
                prof = cur.fetchone()

        if prof:
            profile_id = prof["profile_Id_uuid"]
            user_id = prof["user_id_uuid"]
            gcid = prof["customer_id_uuid"]
            email = prof["email"]
        else:
            gcid = target
            profile_id = "5e0ff5be-97a1-11f1-ac0d-0e0a04e472ab"
            user_id = "1a140831-8b1c-404c-ae68-5d3fb2284f78"
            email = "agentqatest@gmail.com"

        log.info("Target GCID: %s, Profile ID: %s, User ID: %s, Email: %s", gcid, profile_id, user_id, email)

        # 2. Fetch imported font style
        cur.execute(
            """
            SELECT r.import_id, r.company_id, r.imported_by, s.style_id 
            FROM import_context_qa.import_records r 
            JOIN import_context_qa.import_record_styles s ON r.import_id = s.import_id 
            WHERE r.company_id = %s LIMIT 1;
            """,
            (gcid,),
        )
        font_row = cur.fetchone()
        style_id = font_row["style_id"] if font_row else "1E8v8U1U_t"

        # 3. Fetch real contract
        cur.execute(
            "SELECT * FROM byof_license_management.contracts WHERE company_id = %s AND is_deleted = 0 LIMIT 1;",
            (gcid,),
        )
        db_contract = cur.fetchone()
        if not db_contract:
            cur.execute(
                "SELECT * FROM byof_license_management.contracts WHERE company_id = %s LIMIT 1;",
                (gcid,),
            )
            db_contract = cur.fetchone()

    conn.close()

    font_name = db_contract["licence_name"] if db_contract else "DynaPuff-WEB"
    contract_id = db_contract["contract_id"] if db_contract else str(uuid.uuid4())

    log.info("Profile Found: ID=%s, UserID=%s, Email=%s", profile_id, user_id, email)
    log.info("Real Imported Font from DB: StyleID=%s, FontName=%s", style_id, font_name)
    log.info(
        "Real Contract from DB: ID=%s, Name=%s, Type=%s, DB Status=%s",
        contract_id,
        db_contract.get("licence_name") if db_contract else "N/A",
        db_contract.get("licence_type") if db_contract else "N/A",
        db_contract.get("status") if db_contract else "N/A",
    )

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    contract_dict = {
        "contractId": db_contract["contract_id"],
        "companyId": gcid,
        "licenceName": db_contract["licence_name"],
        "isFreeToUse": bool(db_contract["is_free_to_use"]),
        "isPayOnce": bool(db_contract.get("isPayOnce", 0)),
        "isReviewed": bool(db_contract["is_reviewed"]),
        "licenceType": db_contract["licence_type"] or "WEB",
        "linkedImportedFontScope": db_contract["linked_imported_font_scope"] or "GLOBAL",
        "projectId": db_contract.get("project_id"),
        "licenceStartDate": db_contract["licence_start_date"].isoformat() if db_contract.get("licence_start_date") else "2026-08-01T00:00:00.000Z",
        "licenceEndDate": db_contract["licence_end_date"].isoformat() if db_contract.get("licence_end_date") else "2026-08-15T00:00:00.000Z",
        "totalLicensedSeats": db_contract["total_licensed_seats"] or 10,
        "usedSeats": db_contract["used_seats"] or 0,
        "costPerSeat": float(db_contract["cost_per_seat"]) if db_contract.get("cost_per_seat") else None,
        "totalCost": float(db_contract["total_cost"]) if db_contract.get("total_cost") else 100.0,
        "licencePurchaseDate": db_contract["licence_purchase_date"].isoformat() if db_contract.get("licence_purchase_date") else None,
        "foundry": db_contract.get("foundry") or "The DynaPuff Project Authors",
        "vendorName": db_contract.get("vendor_name") or "Google Fonts",
        "contractReference": db_contract.get("contract_reference"),
        "purchaseOrder": db_contract.get("purchase_order"),
        "invoiceNumber": db_contract.get("invoice_number"),
        "currency": db_contract.get("currency") or "USD",
        "title": 0,
        "registeredUsers": 0,
        "notes": db_contract.get("notes") or f"Real imported font license in DB for GCID {gcid}",
        "notifyBeforeExpiry": True,
        "notifyDays": 30,
        "status": "EXPIRED",
        "createdAt": db_contract["created_at"].isoformat() if db_contract.get("created_at") else now_iso,
        "createdBy": user_id,
        "updatedAt": now_iso,
        "updatedBy": user_id,
    }

    cid = str(uuid.uuid4())
    eid = str(uuid.uuid4())

    payload = {
        "xCorrelationId": cid,
        "eventId": eid,
        "eventVersion": 1,
        "occurredAt": now_iso,
        "routingKey": "byof.licence.expired",
        "actor": {
            "globalUserId": user_id,
            "globalCustomerId": gcid,
            "globalProfileId": profile_id,
            "email": email,
        },
        "source": {
            "type": ["BYOF Licence Expiry"],
            "service": "byof-license-service",
            "operation": "byofLicenceExpired",
            "operationState": "success",
        },
        "subject": {
            "id": [contract_dict["contractId"]],
            "type": "BYOF Licence Expiry",
            "contractId": [contract_dict["contractId"]],
            "contract": contract_dict,
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
    log.info("Publishing 'byof.licence.expired' (rk=%s, cid=%s, eid=%s)...", amqp_rk, cid, eid)
    publish_raw_event(cfg.rabbitmq, payload, amqp_routing_key=amqp_rk)

    try:
        record_generation(
            "byofLicenceExpired",
            cid,
            kind="cron",
            project_root=PROJECT_ROOT,
            case_key=cron_case_key("byofLicenceExpired"),
            meta={
                "case_id": "byofLicenceExpired",
                "eventId": eid,
                "profile_id": profile_id,
                "customer_id": gcid,
                "email": email,
                "style_id": style_id,
                "font_name": font_name,
                "contract_id": contract_id,
            },
        )
    except Exception as ex:
        log.warning("Generation record note: %s", ex)

    log.info("Event published successfully. Waiting 15s for ingestion and enrichment in QA...")
    time.sleep(15)

    mongo_client = MongoClient(os.getenv("MONGO_DB_URL"), serverSelectionTimeoutMS=5000)
    mongo_db = mongo_client[os.getenv("MONGO_DB_NAME", "AuditLogsQA")]

    raw_doc = mongo_db.raw.find_one({"event.xCorrelationId": cid})
    enriched_doc = mongo_db.enriched.find_one({"event.xCorrelationId": cid})
    dlq_doc = mongo_db.dlq.find_one({"event.xCorrelationId": cid})

    log.info("================ Verification Results ================")
    log.info("Correlation ID: %s", cid)
    log.info("Event ID:       %s", eid)
    log.info("Mongo Raw:      %s", "PASS" if raw_doc else "FAIL")
    log.info("Mongo Enriched: %s", "PASS" if enriched_doc else ("DLQ" if dlq_doc else "NOT_FOUND / PENDING"))

    if enriched_doc:
        log.info("Enriched Source Operation: %s", enriched_doc.get("event", {}).get("source", {}).get("operation"))
        log.info("Enriched Actor:            %s", enriched_doc.get("event", {}).get("actor"))
        log.info("Enriched Subject ID:       %s", enriched_doc.get("event", {}).get("subject", {}).get("id"))

    log.info("Checking mt_notification_qa notifications table...")
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
    gcid_arg = sys.argv[1] if len(sys.argv) > 1 else "07039f61-4910-47f6-9784-3087b267079b"
    trigger_imported_font_expire(gcid_arg)
