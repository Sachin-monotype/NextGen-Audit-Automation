"""Smoke-test MySQL source-truth connectivity (SELECT-only).

Usage (from repo root, venv active)::

    SOURCE_TRUTH=db python -m audit_validator.source_validation.db.smoke

Never runs INSERT/UPDATE/DELETE.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def main() -> int:
    # Ensure project root .env is loaded
    root = Path(__file__).resolve().parents[4]
    # parents: db -> source_validation -> audit_validator -> python -> repo
    if str(root / "python") not in sys.path:
        sys.path.insert(0, str(root / "python"))
    try:
        from dotenv import load_dotenv

        load_dotenv(root / ".env")
    except ImportError:
        pass

    from audit_validator.source_validation.db.connection import (
        connect,
        load_mysql_config,
        select_one,
    )
    from audit_validator.source_validation.db.clients import (
        AmsDbClient,
        CmsDbClient,
        UmsDbClient,
    )

    cfg = load_mysql_config()
    print(f"host={cfg.host}:{cfg.port} user={cfg.user} ssl={cfg.use_ssl} ready={cfg.ready}")
    if not cfg.ready:
        print("FAIL: set MYSQL_HOST / MYSQL_USER / MYSQL_PASSWORD in .env")
        return 2

    try:
        conn = connect(cfg)
    except Exception as exc:  # noqa: BLE001
        print(f"CONNECT FAIL: {exc}")
        print(
            "\nLikely cause: MySQL user host grant (asteria@'<your-public-ip>') or wrong password.\n"
            "Workbench can succeed from an allowlisted IP while this machine's egress "
            f"({os.getenv('PUBLIC_IP_HINT', 'unknown')}) is denied.\n"
            "Fix: ask DBA to GRANT SELECT for your current public IP, or run the app on "
            "the same network path as Workbench."
        )
        return 1

    with conn.cursor() as cur:
        cur.execute("SELECT CURRENT_USER() AS u, @@hostname AS host_name, VERSION() AS ver")
        print("session:", dict(cur.fetchone()))
    conn.close()

    gcid = os.getenv("SMOKE_GCID", "4a949153-9cab-4023-b31c-8336a8a3ec46")
    profile = os.getenv("SMOKE_PROFILE_ID", "bc195ef6-6884-11f1-a522-0e0a04e472ab")
    asset = os.getenv("SMOKE_ASSET_ID", "93d6cc16-1e14-4391-9f91-e867afaf15df")

    cms = CmsDbClient(cfg)
    ums = UmsDbClient(cfg)
    ams = AmsDbClient(cfg)

    cust = cms.get_customer_by_id(gcid, correlation_id="smoke")
    print("CMS:", json.dumps({"id": (cust or {}).get("id"), "displayName": (cust or {}).get("displayName"), "name": (cust or {}).get("name")}, default=str))

    # Column discovery for UMS
    cols = select_one(
        """
        SELECT GROUP_CONCAT(COLUMN_NAME ORDER BY ORDINAL_POSITION) AS cols
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA='user_management' AND TABLE_NAME='profiles'
        """,
        cfg=cfg,
    )
    print("UMS profiles columns:", (cols or {}).get("cols"))

    prof = ums.get_profile_by_id(profile, gcid, correlation_id="smoke")
    print(
        "UMS profile:",
        json.dumps(
            {
                "id": (prof or {}).get("id"),
                "customerId": (prof or {}).get("customerId"),
                "email": (prof or {}).get("email"),
                "role": (prof or {}).get("role"),
            },
            default=str,
        ),
    )

    asset_row = ams.get_asset_by_id(asset, "FontProject", correlation_id="smoke", global_customer_id=gcid)
    print(
        "AMS:",
        json.dumps(
            {
                "id": (asset_row or {}).get("id"),
                "assetType": (asset_row or {}).get("assetType"),
                "createdBy": (asset_row or {}).get("createdBy"),
                "globalCustomerId": (asset_row or {}).get("globalCustomerId"),
            },
            default=str,
        ),
    )
    print("OK — SELECT-only smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
