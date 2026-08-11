#!/usr/bin/env python3
"""Seed posted AR/AP transactions into the demo DB so data-dependent e2e
assertions (aging buckets, trial-balance totals, invoice/receipt lists,
customer/supplier balances, dashboard metrics) have real data.

Idempotent: every invoice is tagged purpose="E2E demo txn"; re-running skips
creation when such invoices already exist.

Run against the disposable demo DB only:
    ENFORCE_ORG_FILTER=false PYTHONPATH=/root/dotmac \
        .venv/bin/python scripts/seed_demo_transactions.py
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select

from app.db.session_context import session_for_org
from app.models.auth import UserCredential
from app.models.finance.ap.supplier import Supplier
from app.models.finance.ap.supplier_invoice import (
    SupplierInvoice,
    SupplierInvoiceType,
)
from app.models.finance.ar.customer import Customer
from app.models.finance.ar.invoice import Invoice, InvoiceType
from app.models.finance.gl.account import Account
from app.models.finance.gl.fiscal_period import FiscalPeriod, PeriodStatus
from app.services.finance.gl.fiscal_period import FiscalPeriodService
from app.models.person import Person
from app.services.finance.ap.supplier_invoice import (
    InvoiceLineInput,
    SupplierInvoiceInput,
    SupplierInvoiceService,
)
from app.services.finance.ar.invoice import (
    ARInvoiceInput,
    ARInvoiceLineInput,
    ARInvoiceService,
)

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("seed_demo_txn")

DEFAULT_ORG_ID = UUID("00000000-0000-0000-0000-000000000001")
TXN_MARKER = "E2E demo txn"
# Days back from today → exercises aging buckets: current, 1-30, 31-60, 61-90, 90+
AGE_OFFSETS = [3, 20, 45, 75, 100]


def _account(db, org_id, code):
    return db.scalar(
        select(Account).where(
            Account.organization_id == org_id, Account.account_code == code
        )
    )


def main() -> int:
    results = {"ar_posted": 0, "ap_posted": 0, "errors": []}
    # One known organization, so this is per-org work: scope the session
    # to it rather than running the whole seed unscoped.
    org_id = DEFAULT_ORG_ID
    with session_for_org(org_id) as db:
        cred = db.scalar(
            select(UserCredential).where(UserCredential.username == "e2e_testuser")
        )
        if not cred:
            print("e2e_testuser not found — run seed_admin first")
            return 1
        person = db.get(Person, cred.person_id)
        user_id = person.id

        customer = db.scalar(
            select(Customer).where(Customer.organization_id == org_id).limit(1)
        )
        supplier = db.scalar(
            select(Supplier).where(Supplier.organization_id == org_id).limit(1)
        )
        revenue = _account(db, org_id, "4000")
        expense = _account(db, org_id, "5000")
        if not (customer and supplier and revenue and expense):
            print(
                f"missing prerequisites: customer={bool(customer)} "
                f"supplier={bool(supplier)} rev={bool(revenue)} exp={bool(expense)}"
            )
            return 1

        # Per-date idempotency: collect invoice_dates already seeded with our
        # marker so re-runs top up missing dates without duplicating.
        existing_ar_dates = set(
            db.scalars(
                select(Invoice.invoice_date).where(
                    Invoice.organization_id == org_id, Invoice.purpose == TXN_MARKER
                )
            ).all()
        )
        existing_ap_dates = set(
            db.scalars(
                select(SupplierInvoice.invoice_date).where(
                    SupplierInvoice.organization_id == org_id,
                    SupplierInvoice.purpose == TXN_MARKER,
                )
            ).all()
        )

        today = date.today()

        # Open any past-dated FUTURE periods so back-dated invoices can post
        # (the demo seeds all periods FUTURE except the current month).
        past_future = db.scalars(
            select(FiscalPeriod).where(
                FiscalPeriod.organization_id == org_id,
                FiscalPeriod.status == PeriodStatus.FUTURE,
                FiscalPeriod.start_date <= today,
            )
        ).all()
        for period in past_future:
            try:
                FiscalPeriodService.open_period(
                    db, org_id, period.fiscal_period_id, user_id
                )
                db.commit()
            except Exception as e:  # noqa: BLE001
                db.rollback()
                results["errors"].append(f"open period {period.period_number}: {e}")

        ar_currency = customer.currency_code
        ap_currency = supplier.currency_code

        # ---- AR invoices (posted, spread across aging buckets) ----
        for i, days in enumerate(AGE_OFFSETS):
            inv_date = today - timedelta(days=days)
            if inv_date in existing_ar_dates:
                continue
            try:
                inv = ARInvoiceService.create_invoice(
                    db,
                    org_id,
                    ARInvoiceInput(
                        customer_id=customer.customer_id,
                        invoice_type=InvoiceType.STANDARD,
                        invoice_date=inv_date,
                        due_date=inv_date + timedelta(days=30),
                        currency_code=ar_currency,
                        exchange_rate=Decimal("1"),
                        purpose=TXN_MARKER,
                        lines=[
                            ARInvoiceLineInput(
                                description=f"Demo services {i + 1}",
                                quantity=Decimal("1"),
                                unit_price=Decimal("100000") + Decimal(i * 25000),
                                revenue_account_id=revenue.account_id,
                            )
                        ],
                    ),
                    user_id,
                )
                db.flush()
                ARInvoiceService.submit_invoice(db, org_id, inv.invoice_id, user_id)
                ARInvoiceService.approve_invoice(db, org_id, inv.invoice_id, user_id)
                ARInvoiceService.post_invoice(
                    db, org_id, inv.invoice_id, user_id, posting_date=inv_date
                )
                db.commit()
                results["ar_posted"] += 1
            except Exception as e:  # noqa: BLE001 - per-item, keep going
                db.rollback()
                logger.exception("AR invoice (%s) failed", inv_date)
                results["errors"].append(f"AR {inv_date}: {e}")

        # ---- AP bills (posted) ----
        for i, days in enumerate(AGE_OFFSETS[:3]):
            inv_date = today - timedelta(days=days)
            if inv_date in existing_ap_dates:
                continue
            try:
                bill = SupplierInvoiceService.create_invoice(
                    db,
                    org_id,
                    SupplierInvoiceInput(
                        supplier_id=supplier.supplier_id,
                        invoice_type=SupplierInvoiceType.STANDARD,
                        invoice_date=inv_date,
                        received_date=inv_date,
                        due_date=inv_date + timedelta(days=30),
                        currency_code=ap_currency,
                        exchange_rate=Decimal("1"),
                        purpose=TXN_MARKER,
                        supplier_invoice_number=f"DEMO-BILL-{i + 1}",
                        lines=[
                            InvoiceLineInput(
                                description=f"Demo supplies {i + 1}",
                                quantity=Decimal("1"),
                                unit_price=Decimal("50000") + Decimal(i * 15000),
                                expense_account_id=expense.account_id,
                            )
                        ],
                    ),
                    user_id,
                )
                db.flush()
                SupplierInvoiceService.submit_invoice(
                    db, org_id, bill.invoice_id, user_id
                )
                SupplierInvoiceService.approve_invoice(
                    db, org_id, bill.invoice_id, user_id
                )
                SupplierInvoiceService.post_invoice(
                    db, org_id, bill.invoice_id, user_id, posting_date=inv_date
                )
                db.commit()
                results["ap_posted"] += 1
            except Exception as e:  # noqa: BLE001
                db.rollback()
                logger.exception("AP bill (%s) failed", inv_date)
                results["errors"].append(f"AP {inv_date}: {e}")

    print(
        f"Seeded demo transactions: ar_posted={results['ar_posted']}, "
        f"ap_posted={results['ap_posted']}, errors={len(results['errors'])}"
    )
    for err in results["errors"][:5]:
        print(f"  - {err}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
