"""
Sync specific ERPNext Payment Entry documents by name.

Useful when we need to backfill a small set of ACC-PAY entries without running a
full payment_entries sync.
"""

from __future__ import annotations

import argparse
import os
from uuid import UUID

from sqlalchemy import text

from app.db import SessionLocal
from app.services.erpnext.client import ERPNextClient, ERPNextConfig
from app.services.erpnext.sync.base import SyncResult
from app.services.erpnext.sync.payment_entry import PaymentEntrySyncService

ORG_ID = UUID("00000000-0000-0000-0000-000000000001")
ADMIN_PERSON_ID = UUID("c8e5f2ee-4f9f-46d0-a6c7-22e4f717a58b")


def main() -> None:
    print("ERPNext API sync is disabled. Use SQL-based sync tooling.")
    raise SystemExit(2)

    ap = argparse.ArgumentParser()
    ap.add_argument("--name", action="append", dest="names", required=True)
    args = ap.parse_args()

    cfg = ERPNextConfig(
        url=os.environ.get("ERPNEXT_URL", ""),
        api_key=os.environ.get("ERPNEXT_API_KEY", ""),
        api_secret=os.environ.get("ERPNEXT_API_SECRET", ""),
        company=os.environ.get("ERPNEXT_COMPANY") or "Dotmac Technologies",
    )

    result = SyncResult(entity_type="Payment Entry")

    with SessionLocal() as db, ERPNextClient(cfg) as client:
        svc = PaymentEntrySyncService(
            db=db, organization_id=ORG_ID, user_id=ADMIN_PERSON_ID
        )
        # Avoid app-level DB statement timeouts for this backfill.
        db.execute(text("SET statement_timeout TO '600s'"))

        for name in args.names:
            doc = client.get_document("Payment Entry", name)
            # get_document returns the full doc, including child tables.
            svc._sync_single_record(doc, result)  # noqa: SLF001 - internal but pragmatic for tooling
            db.commit()

    print(  # noqa: T201
        {
            "total": result.total_records,
            "synced": result.synced_count,
            "skipped": result.skipped_count,
            "errors": result.error_count,
        }
    )


if __name__ == "__main__":
    main()
