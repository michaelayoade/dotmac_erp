#!/usr/bin/env python3
"""Backfill expense claim paid metadata from ERPNext employee payment entries."""

from __future__ import annotations

import os
from datetime import date
from uuid import UUID

from sqlalchemy import text

from app.db import SessionLocal
from app.services.erpnext.client import ERPNextClient, ERPNextConfig


def main() -> None:
    org_id = UUID("00000000-0000-0000-0000-000000000001")
    start = date(2022, 1, 1)
    end = date(2025, 12, 31)

    cfg = ERPNextConfig(
        url=os.environ.get("ERPNEXT_URL", ""),
        api_key=os.environ.get("ERPNEXT_API_KEY", ""),
        api_secret=os.environ.get("ERPNEXT_API_SECRET", ""),
        company=os.environ.get("ERPNEXT_COMPANY") or "Dotmac Technologies",
    )

    stats = {
        "payment_entries_seen": 0,
        "in_year_range": 0,
        "expense_refs_seen": 0,
        "claims_found": 0,
        "claims_updated": 0,
        "claims_already_aligned": 0,
        "claims_missing": 0,
    }

    with SessionLocal() as db, ERPNextClient(cfg) as client:
        db.execute(text("SET statement_timeout TO '600s'"))
        pending_updates = 0

        for pe in client.get_all_documents(
            doctype="Payment Entry",
            filters={"docstatus": 1, "payment_type": "Pay", "party_type": "Employee"},
            fields=["name", "posting_date", "modified"],
            order_by="posting_date asc",
            batch_size=200,
        ):
            stats["payment_entries_seen"] += 1
            posting_date_raw = pe.get("posting_date")
            if not posting_date_raw:
                continue

            pdate = date.fromisoformat(str(posting_date_raw))
            if pdate < start or pdate > end:
                continue

            stats["in_year_range"] += 1
            full = client.get_document("Payment Entry", pe["name"])
            refs = full.get("references", []) or []

            for ref in refs:
                if ref.get("reference_doctype") != "Expense Claim":
                    continue
                ref_name = ref.get("reference_name")
                if not ref_name:
                    continue

                stats["expense_refs_seen"] += 1

                row = db.execute(
                    text(
                        """
                        with claim as (
                            select ec.claim_id, ec.status, ec.paid_on,
                                   coalesce(ec.payment_reference,'') as payment_reference
                            from expense.expense_claim ec
                            where ec.organization_id = :org_id
                              and (
                                ec.erpnext_id = :ref_name
                                or ec.claim_id = (
                                  select se.target_id
                                  from sync.sync_entity se
                                  where se.organization_id = :org_id
                                    and se.source_system = 'erpnext'
                                    and se.source_doctype = 'Expense Claim'
                                    and se.source_name = :ref_name
                                  limit 1
                                )
                              )
                            limit 1
                        )
                        select claim_id,
                               status::text as status,
                               paid_on,
                               payment_reference
                        from claim
                        """
                    ),
                    {"org_id": str(org_id), "ref_name": ref_name},
                ).first()

                if not row:
                    stats["claims_missing"] += 1
                    continue

                stats["claims_found"] += 1
                aligned = (
                    row.status == "PAID"
                    and row.paid_on == pdate
                    and row.payment_reference == pe["name"]
                )
                if aligned:
                    stats["claims_already_aligned"] += 1
                    continue

                db.execute(
                    text(
                        """
                        update expense.expense_claim
                        set status = 'PAID'::expense.expense_claim_status,
                            paid_on = :paid_on,
                            payment_reference = :payment_reference,
                            updated_at = now()
                        where claim_id = :claim_id
                        """
                    ),
                    {
                        "paid_on": pdate,
                        "payment_reference": str(pe["name"])[:100],
                        "claim_id": row.claim_id,
                    },
                )
                stats["claims_updated"] += 1
                pending_updates += 1

                if pending_updates >= 25:
                    db.commit()
                    pending_updates = 0

            if stats["payment_entries_seen"] % 200 == 0:
                db.commit()
                print(
                    f"progress pe={stats['payment_entries_seen']} "
                    f"in_range={stats['in_year_range']} "
                    f"refs={stats['expense_refs_seen']} "
                    f"updated={stats['claims_updated']}"
                )

        db.commit()

    print(stats)


if __name__ == "__main__":
    main()
