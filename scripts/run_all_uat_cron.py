"""Execute and validate ALL or specific cron/scheduler events in the UAT environment.

Target:
  GCID:       7d57c3c0-0de4-41fe-b76b-5a2b6c4727ab
  Email:      agentqatest@gmail.com
  User ID:    9cd7be32-a6a9-11f1-b7aa-0e25f5905f65
  Queue:      mt.platform.raw_events.resolver.queue
  RabbitMQ:   rabbitmq-us-east-1.monotype-uat.com:5671/mt-connect
"""

from __future__ import annotations

import argparse
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
import pika
from pika import BasicProperties
from pymongo import MongoClient

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "python"))

# Ensure UAT environment
os.environ["AUDIT_TARGET"] = "uat"
os.environ["CRON_DEFAULT_GCID"] = "7d57c3c0-0de4-41fe-b76b-5a2b6c4727ab"
os.environ["GLOBAL_CUSTOMER_ID"] = "7d57c3c0-0de4-41fe-b76b-5a2b6c4727ab"
os.environ["CRON_USER_ID"] = "9cd7be32-a6a9-11f1-b7aa-0e25f5905f65"
os.environ["OAUTH_USER_ID"] = "9cd7be32-a6a9-11f1-b7aa-0e25f5905f65"
os.environ["CRON_PROFILE_ID"] = "9cd7be32-a6a9-11f1-b7aa-0e25f5905f65"
os.environ["AUDIT_PROFILE_ID"] = "9cd7be32-a6a9-11f1-b7aa-0e25f5905f65"
os.environ["QA_LOGIN_EMAIL"] = "agentqatest@gmail.com"
os.environ["GMAIL_USER"] = "agentqatest@gmail.com"

from audit_validator.env_profiles import apply_audit_profile
from audit_validator.config import load_config
from audit_validator.cron.payloads import (
    load_cron_cases,
    normalize_cron_payload,
    amqp_routing_key_for_payload,
    CRON_NO_ENRICHER_OPERATIONS,
)
from audit_validator.case_keys import cron_case_key
from audit_validator.generation_tracker import record_generation

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("uat_cron_runner")

TARGET_QUEUE = "mt.platform.raw_events.resolver.queue"


@dataclass
class UATCronExecution:
    case_id: str
    routing_key: str
    operation: str
    service: str
    cid: str
    eid: str
    target_queue: str = TARGET_QUEUE
    publish_status: str = "PASS"
    mongo_raw: str = "PENDING"
    mongo_enriched: str = "PENDING"
    error: str = ""


def get_mongo_db():
    urls = [
        os.getenv("MONGO_DB_URL_UAT", "mongodb://localhost:27017"),
        os.getenv("MONGO_DB_URL"),
    ]
    for url in urls:
        if not url:
            continue
        try:
            client = MongoClient(url, serverSelectionTimeoutMS=2000)
            client.admin.command("ping")
            return client["AuditLogsUAT"]
        except Exception:
            continue
    return None


def main():
    parser = argparse.ArgumentParser(description="Trigger and validate cron events in UAT environment")
    parser.add_argument("--case", "-c", default=None, help="Specific cron case ID to run (e.g. userexpiring, lmsopen, licneseexpiry)")
    parser.add_argument("--five", action="store_true", help="Trigger specifically the 5 requested triggers: G-4, G-3, C-1, B-6, A-14")
    parser.add_argument("--gcid", default="7d57c3c0-0de4-41fe-b76b-5a2b6c4727ab", help="Customer ID (GCID)")
    parser.add_argument("--email", default="agentqatest@gmail.com", help="User Email")
    parser.add_argument("--user-id", default="9cd7be32-a6a9-11f1-b7aa-0e25f5905f65", help="User ID / Profile ID")
    parser.add_argument("--dry-run", action="store_true", help="Print prepared payloads without publishing")
    parser.add_argument("--list", action="store_true", help="List all available cron cases")
    parser.add_argument("--wait-sec", type=int, default=15, help="Seconds to wait after publishing before checking MongoDB")
    args = parser.parse_args()

    cron_cases = load_cron_cases()

    if args.list:
        print(f"Available Cron Cases ({len(cron_cases)} cases):")
        for c in cron_cases:
            print(f"  - {c.case_id:32} | routingKey={c.routing_key:35} | operation={c.operation}")
        print("  - byofLicenceOverused              | routingKey=byof.licence.overused        | operation=byofLicenceOverused (dynamic)")
        print("  - byofLicenceExpiringSoon          | routingKey=byof.licence.expiring        | operation=notifyByofLicenceExpiry (dynamic)")
        print("  - --five                           | Triggers G-4, G-3, C-1, B-6, A-14 directly")
        return

    profile = apply_audit_profile(project_root=PROJECT_ROOT)
    cfg = load_config(PROJECT_ROOT)

    gcid = args.gcid
    email = args.email
    user_id = args.user_id
    profile_id = user_id
    contract_id = str(uuid.uuid4())
    licence_name = "UAT Automation Test License"
    style_id = "fF6hmA54_o"
    font_name = "Helvetica Now"

    log.info("=================================================================")
    log.info("Starting Cron Events Trigger in UAT Environment")
    log.info("Target Profile:  %s (%s)", profile.name, profile.label)
    log.info("RabbitMQ URL:    %s", cfg.rabbitmq.url)
    log.info("Destination Q:   %s (DIRECT DELIVERY)", TARGET_QUEUE)
    log.info("GCID:            %s", gcid)
    log.info("User Email:      %s", email)
    log.info("User/Profile ID: %s", user_id)
    if args.five:
        log.info("Selected Mode:   5 REQUESTED TRIGGERS (G-4, G-3, C-1, B-6, A-14)")
    elif args.case:
        log.info("Selected Case:   %s", args.case)
    else:
        log.info("Selected Case:   ALL CASES")
    log.info("=================================================================")

    now = datetime.now(timezone.utc)
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    expiry_15d = (now + timedelta(days=15)).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    executions: list[UATCronExecution] = []
    published_items: list[tuple[UATCronExecution, dict]] = []

    if args.five:
        # 1. G-4: Account expiring soon
        g4_cid, g4_eid = str(uuid.uuid4()), str(uuid.uuid4())
        g4_payload = {
            "xCorrelationId": g4_cid,
            "eventId": g4_eid,
            "occurredAt": now_iso,
            "routingKey": "user.account.expiring",
            "source": {"service": "user-mgmt-service", "trigger": "G-4", "operation": "weekly_account_expiry"},
            "actor": {"globalCustomerId": gcid, "globalUserId": user_id, "globalProfileId": profile_id, "email": email, "orgId": "org_h8efE2q37VYQOGB4", "parentCustomerId": gcid},
            "subject": {
                "id": [str(uuid.uuid4())],
                "triggerCode": "G-4",
                "event": "User Account Expiring in 7 Days",
                "scheduledAt": now_iso,
                "triggeredAt": now_iso,
                "workspaceId": gcid,
                "customerId": gcid,
                "expiringUsers": [{
                    "userId": user_id,
                    "profileId": profile_id,
                    "name": "Sachin Koirala",
                    "role": "Company Admin",
                    "expiryDate": (now + timedelta(days=7)).strftime("%b %d, %Y"),
                    "customerId": gcid
                }]
            },
            "recipients": [],
            "recipientType": "SELF"
        }
        g4_exec = UATCronExecution(case_id="G-4_AccountExpiringSoon", routing_key="user.account.expiring", operation="weekly_account_expiry", service="user-mgmt-service", cid=g4_cid, eid=g4_eid)
        executions.append(g4_exec)
        published_items.append((g4_exec, g4_payload))

        # 2. G-3: Auto account deactivated (SELF)
        g3_cid, g3_eid = str(uuid.uuid4()), str(uuid.uuid4())
        g3_payload = {
            "xCorrelationId": g3_cid,
            "eventId": g3_eid,
            "occurredAt": now_iso,
            "routingKey": "user.account.deactivated",
            "source": {"service": "user-mgmt-service", "trigger": "G-3", "operation": "auto_deactivated_user"},
            "actor": {"globalCustomerId": gcid, "globalUserId": user_id, "globalProfileId": profile_id, "email": email, "orgId": "org_h8efE2q37VYQOGB4", "parentCustomerId": gcid},
            "subject": {
                "id": [str(uuid.uuid4())],
                "triggerCode": "G-3",
                "event": "Your Account Deactivated",
                "scheduledAt": now_iso,
                "triggeredAt": now_iso,
                "workspaceId": gcid,
                "customerId": gcid,
                "deactivatedUsers": [{
                    "userId": user_id,
                    "profileId": profile_id,
                    "name": "Sachin Koirala",
                    "role": "Company Admin",
                    "expiryDate": (now - timedelta(days=1)).strftime("%b %d, %Y"),
                    "customerId": gcid
                }]
            },
            "recipients": [],
            "recipientType": "SELF"
        }
        g3_exec = UATCronExecution(case_id="G-3_AutoAccountDeactivated", routing_key="user.account.deactivated", operation="auto_deactivated_user", service="user-mgmt-service", cid=g3_cid, eid=g3_eid)
        executions.append(g3_exec)
        published_items.append((g3_exec, g3_payload))

        # 3. C-1: Project BYOF License Expiry Warning
        c1_cid, c1_eid = str(uuid.uuid4()), str(uuid.uuid4())
        c1_contract = str(uuid.uuid4())
        c1_payload = {
            "xCorrelationId": c1_cid,
            "eventId": c1_eid,
            "eventVersion": 1,
            "occurredAt": now_iso,
            "routingKey": "project.byof.warning",
            "source": {"service": "byof-license-service", "trigger": "C-1b", "operation": "byofLicenceExpiring", "operationState": "success"},
            "actor": {"globalCustomerId": gcid, "globalUserId": user_id, "globalProfileId": profile_id, "email": email, "orgId": "org_h8efE2q37VYQOGB4", "parentCustomerId": gcid},
            "subject": {
                "id": [c1_contract],
                "triggerCode": "C-1b",
                "event": "Project BYOF: Licence Expiry Warning",
                "contractId": [c1_contract],
                "contract": {
                    "contractId": c1_contract,
                    "companyId": gcid,
                    "licenceName": "Helvetica Enterprise Project License",
                    "isFreeToUse": False,
                    "isPayOnce": False,
                    "isReviewed": True,
                    "licenceType": "DESKTOP",
                    "linkedImportedFontScope": "GLOBAL",
                    "licenceStartDate": (now - timedelta(days=335)).strftime("%Y-%m-%dT00:00:00.000Z"),
                    "licenceEndDate": (now + timedelta(days=30)).strftime("%Y-%m-%dT00:00:00.000Z"),
                    "totalLicensedSeats": 20,
                    "usedSeats": 5,
                    "costPerSeat": 50,
                    "totalCost": 1000,
                    "status": "EXPIRING_SOON",
                    "notifyBeforeExpiry": True,
                    "notifyDays": 30,
                    "createdAt": now_iso,
                    "createdBy": user_id,
                    "updatedAt": now_iso,
                    "updatedBy": user_id,
                },
                "styles": [{
                    "styleId": style_id,
                    "styleName": "Helvetica Now Display",
                    "fontName": "Helvetica Now",
                    "linkedAt": now_iso,
                    "linkedBy": user_id,
                }],
                "enrichedSnapshot": {
                    "projectDetails": {
                        "id": "proj-uat-001",
                        "name": "Audit Test Project"
                    }
                }
            }
        }
        c1_exec = UATCronExecution(case_id="C-1_ProjectByofLicenseExpiryWarning", routing_key="project.byof.warning", operation="byofLicenceExpiring", service="byof-license-service", cid=c1_cid, eid=c1_eid)
        executions.append(c1_exec)
        published_items.append((c1_exec, c1_payload))

        # 4. B-6: User auto deactivated (PERM)
        b6_cid, b6_eid = str(uuid.uuid4()), str(uuid.uuid4())
        b6_payload = {
            "xCorrelationId": b6_cid,
            "eventId": b6_eid,
            "occurredAt": now_iso,
            "routingKey": "user.account.deactivated",
            "source": {"service": "user-mgmt-service", "trigger": "B-6", "operation": "auto_deactivated_user"},
            "actor": {"globalCustomerId": gcid, "globalUserId": user_id, "globalProfileId": profile_id, "email": email, "orgId": "org_h8efE2q37VYQOGB4", "parentCustomerId": gcid},
            "subject": {
                "id": [str(uuid.uuid4())],
                "triggerCode": "B-6",
                "event": "User Auto-Deactivated (Account Expiry)",
                "scheduledAt": now_iso,
                "triggeredAt": now_iso,
                "workspaceId": gcid,
                "customerId": gcid,
                "deactivatedUsers": [{
                    "userId": user_id,
                    "profileId": profile_id,
                    "name": "Sachin Koirala",
                    "role": "Company Admin",
                    "expiryDate": (now - timedelta(days=1)).strftime("%b %d, %Y"),
                    "customerId": gcid
                }]
            },
            "recipients": [],
            "recipientType": "PERM"
        }
        b6_exec = UATCronExecution(case_id="B-6_UserAutoDeactivated", routing_key="user.account.deactivated", operation="auto_deactivated_user", service="user-mgmt-service", cid=b6_cid, eid=b6_eid)
        executions.append(b6_exec)
        published_items.append((b6_exec, b6_payload))

        # 5. A-14: Quarterly report submitted
        a14_cid, a14_eid = str(uuid.uuid4()), str(uuid.uuid4())
        a14_payload = {
            "xCorrelationId": a14_cid,
            "eventId": a14_eid,
            "eventVersion": 1,
            "occurredAt": now_iso,
            "routingKey": "reporting.quarterly.submitted",
            "source": {
                "service": "license-management-service",
                "trigger": "A-14",
                "operation": "submitFontUsageReport",
                "operationState": "success",
                "platform": "nextGen",
                "platformEnvironment": "server",
                "platformVersion": "1.0.0"
            },
            "actor": {"globalCustomerId": gcid, "globalUserId": user_id, "globalProfileId": profile_id, "email": email, "orgId": "org_h8efE2q37VYQOGB4", "parentCustomerId": gcid},
            "subject": {
                "id": [str(uuid.uuid4())],
                "triggerCode": "A-14",
                "type": "entitlement",
                "eventName": "Quarterly Report Submitted",
                "globalCustomerIds": [gcid],
                "quarterLabel": "Q3 2026",
                "endDate": "2026-09-30T00:00:00.000Z",
                "metadata": {
                    "result": {
                        "submittedAt": now_iso,
                        "quarterLabel": "Q3 2026",
                        "submittedBy": {
                            "user": {
                                "firstName": "Sachin",
                                "lastName": "Koirala",
                                "email": email
                            }
                        }
                    }
                }
            }
        }
        a14_exec = UATCronExecution(case_id="A-14_QuarterlyReportSubmitted", routing_key="reporting.quarterly.submitted", operation="submitFontUsageReport", service="license-management-service", cid=a14_cid, eid=a14_eid)
        executions.append(a14_exec)
        published_items.append((a14_exec, a14_payload))

    else:
        cases_to_run = cron_cases
        if args.case:
            matching = [c for c in cron_cases if c.case_id.lower() == args.case.lower()]
            if not matching and args.case not in ("byofLicenceOverused", "byofLicenceExpiringSoon"):
                log.error("Unknown case '%s'. Use --list to see available cases.", args.case)
                sys.exit(1)
            cases_to_run = matching

        for case in cases_to_run:
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
                    actor["orgId"] = "org_h8efE2q37VYQOGB4"
                    actor["parentCustomerId"] = gcid

                cid = str(payload["xCorrelationId"])
                eid = str(payload["eventId"])
                rk = str(payload.get("routingKey") or case.routing_key).strip()
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

                exec_item = UATCronExecution(
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

        # Dynamic BYOF Overuse case
        if not args.case or args.case.lower() == "byoflicenceoverused":
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
                    "orgId": "org_h8efE2q37VYQOGB4",
                    "parentCustomerId": gcid,
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
                        "licenceStartDate": (now - timedelta(days=30)).strftime("%Y-%m-%dT00:00:00.000Z"),
                        "licenceEndDate": expiry_15d,
                        "totalLicensedSeats": 10,
                        "usedSeats": 15,
                        "costPerSeat": 50,
                        "totalCost": 500,
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
            overuse_item = UATCronExecution(
                case_id="byofLicenceOverused",
                routing_key="byof.licence.overused",
                operation="byofLicenceOverused",
                service="byof-license-service",
                cid=overuse_cid,
                eid=overuse_eid,
            )
            executions.append(overuse_item)
            published_items.append((overuse_item, overuse_payload))

        # Dynamic BYOF Expiring Soon case
        if not args.case or args.case.lower() == "byoflicenceexpiringsoon":
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
                    "orgId": "org_h8efE2q37VYQOGB4",
                    "parentCustomerId": gcid,
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
                        "licenceStartDate": (now - timedelta(days=335)).strftime("%Y-%m-%dT00:00:00.000Z"),
                        "licenceEndDate": expiry_15d,
                        "totalLicensedSeats": 10,
                        "usedSeats": 2,
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
            expiring_item = UATCronExecution(
                case_id="byofLicenceExpiringSoon",
                routing_key="byof.licence.expiring",
                operation="notifyByofLicenceExpiry",
                service="byof-license-service",
                cid=expiring_cid,
                eid=expiring_eid,
            )
            executions.append(expiring_item)
            published_items.append((expiring_item, expiring_payload))

    if args.dry_run:
        log.info("DRY-RUN MODE: %d events prepared:", len(published_items))
        for exec_item, payload in published_items:
            print(f"\n--- [{exec_item.case_id}] (rk={exec_item.routing_key}) ---")
            print(json.dumps(payload, indent=2))
        return

    # Connect to RabbitMQ and publish directly to mt.platform.raw_events.resolver.queue
    log.info("Connecting to RabbitMQ UAT (%s)...", cfg.rabbitmq.url)
    params = pika.URLParameters(cfg.rabbitmq.url)
    params.socket_timeout = 10
    conn = pika.BlockingConnection(params)
    ch = conn.channel()

    log.info("Publishing %d cron event(s) directly to queue '%s'...", len(published_items), TARGET_QUEUE)

    for exec_item, payload in published_items:
        cid = exec_item.cid
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        props = BasicProperties(
            content_type="application/json",
            delivery_mode=2,
            headers={"x-correlation-id": cid},
        )
        try:
            # Publish directly to mt.platform.raw_events.resolver.queue
            ch.basic_publish(
                exchange="",
                routing_key=TARGET_QUEUE,
                body=body,
                properties=props,
            )
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
            log.info("✓ Published [%s] rk=%s cid=%s -> %s", exec_item.case_id, exec_item.routing_key, cid[:8], TARGET_QUEUE)
        except Exception as e:
            exec_item.publish_status = "FAIL"
            exec_item.error = str(e)
            log.error("✗ Failed to publish [%s]: %s", exec_item.case_id, e)
        time.sleep(0.2)

    conn.close()

    # Wait for resolver processing
    wait_sec = args.wait_sec
    log.info("All events published. Waiting %ds for resolver consumption & MongoDB ingestion...", wait_sec)
    time.sleep(wait_sec)

    # Check MongoDB AuditLogsUAT
    mongo_db = get_mongo_db()
    if mongo_db is not None:
        log.info("================ Checking MongoDB (AuditLogsUAT) ================")
        for exec_item in executions:
            raw_doc = mongo_db.raw.find_one({"event.xCorrelationId": exec_item.cid})
            enriched_doc = mongo_db.enriched.find_one({"event.xCorrelationId": exec_item.cid})
            dlq_doc = mongo_db.dlq.find_one({"event.xCorrelationId": exec_item.cid})

            exec_item.mongo_raw = "PASS" if raw_doc else "NOT_FOUND_YET"
            if enriched_doc:
                exec_item.mongo_enriched = "PASS"
            elif dlq_doc:
                exec_item.mongo_enriched = "DLQ"
            elif exec_item.operation in CRON_NO_ENRICHER_OPERATIONS:
                exec_item.mongo_enriched = "PASSTHROUGH (WARN)"
            else:
                exec_item.mongo_enriched = "NO_ENRICHED"
            log.info("  [%s] Raw=%s | Enriched=%s (cid=%s)", exec_item.case_id, exec_item.mongo_raw, exec_item.mongo_enriched, exec_item.cid[:8])
    else:
        log.warning("MongoDB not accessible; skipping DB verification.")

    output_path = PROJECT_ROOT / "uat_cron_results.json"
    results_data = {
        "timestamp": now_iso,
        "environment": "uat",
        "target_queue": TARGET_QUEUE,
        "target_user": email,
        "gcid": gcid,
        "user_id": user_id,
        "profile_id": profile_id,
        "total_cron_cases": len(executions),
        "published_pass": sum(1 for e in executions if e.publish_status == "PASS"),
        "published_fail": sum(1 for e in executions if e.publish_status == "FAIL"),
        "executions": [asdict(e) for e in executions],
    }
    output_path.write_text(json.dumps(results_data, indent=2), encoding="utf-8")
    log.info("Results written to %s", output_path)
    log.info("Finished: %d/%d successfully delivered to %s", results_data["published_pass"], len(executions), TARGET_QUEUE)


if __name__ == "__main__":
    main()
