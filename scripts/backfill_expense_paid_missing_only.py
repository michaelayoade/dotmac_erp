#!/usr/bin/env python3
"""Backfill only PAID claims still missing paid_on/payment_reference."""

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
        "missing_claims": 0,
        "erp_refs_found": 0,
        "claims_updated": 0,
        "no_payment_entry_ref": 0,
        "errors": 0,
    }

    with SessionLocal() as db, ERPNextClient(cfg) as client:
        db.execute(text("SET statement_timeout TO '600s'"))

        missing = db.execute(
            text(
                """
                select claim_id, erpnext_id
                from expense.expense_claim
                where organization_id = :org_id
                  and status = 'PAID'
                  and claim_date between :start_date and :end_date
                  and erpnext_id is not null
                  and (
                    paid_on is null
                    or payment_reference is null
                    or btrim(payment_reference) = ''
                  )
                order by claim_date desc, claim_number
                """
            ),
            {
                "org_id": str(org_id),
                "start_date": start,
                "end_date": end,
            },
        ).all()

        stats["missing_claims"] = len(missing)

        for idx, row in enumerate(missing, 1):
            claim_id = row.claim_id
            erpnext_id = row.erpnext_id
            try:
                refs = client.list_documents(
                    doctype="Payment Entry Reference",
                    filters={
                        "reference_doctype": "Expense Claim",
                        "reference_name": erpnext_id,
                    },
                    fields=["parent", "reference_name", "allocated_amount", "modified"],
                    parent="Payment Entry",
                    order_by="modified desc",
                    limit_start=0,
                    limit_page_length=20,
                )

                if not refs:
                    stats["no_payment_entry_ref"] += 1
                    continue

                stats["erp_refs_found"] += 1

                chosen_parent = None
                chosen_date = None
                for r in refs:
                    parent = r.get("parent")
                    if not parent:
                        continue

                    pe = client.get_document("Payment Entry", parent)
                    if pe.get("docstatus") != 1:
                        continue
                    if pe.get("payment_type") != "Pay":
                        continue
                    if pe.get("party_type") != "Employee":
                        continue
                    pdate_raw = pe.get("posting_date")
                    if not pdate_raw:
                        continue
                    pdate = date.fromisoformat(str(pdate_raw))
                    if pdate < start or pdate > end:
                        continue

                    if chosen_date is None or pdate > chosen_date:
                        chosen_date = pdate
                        chosen_parent = parent

                if not chosen_parent or not chosen_date:
                    stats["no_payment_entry_ref"] += 1
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
                        "paid_on": chosen_date,
                        "payment_reference": str(chosen_parent)[:100],
                        "claim_id": claim_id,
                    },
                )
                stats["claims_updated"] += 1

                if stats["claims_updated"] % 25 == 0:
                    db.commit()

            except Exception:
                stats["errors"] += 1

            if idx % 50 == 0:
                db.commit()
                print(
                    f"progress {idx}/{len(missing)} "
                    f"updated={stats['claims_updated']} "
                    f"no_ref={stats['no_payment_entry_ref']} "
                    f"errors={stats['errors']}"
                )

        db.commit()

    print(stats)


if __name__ == "__main__":
    main()
