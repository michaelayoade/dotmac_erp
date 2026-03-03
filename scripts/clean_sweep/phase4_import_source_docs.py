"""
Phase 4: Import source documents from ERPNext and link to Phase 3 journals.

Sub-phases (sequential):
  4a. Sales Invoices (18,941) → ar.invoice + ar.invoice_line
  4b. Customer Payments (~16,746) → ar.customer_payment + ar.payment_allocation
  4c. Purchase Invoices (1,160) → ap.supplier_invoice + ap.supplier_invoice_line
  4d. Supplier Payments (~1,327) → ap.supplier_payment
  4e. Expense Claims (9,362) → expense.expense_claim + expense.expense_claim_item
  4f. Sync entity recreation for each imported document

Customer/Supplier lookup via preserved sync.sync_entity rows.

Usage:
    docker exec dotmac_erp_app python -m scripts.clean_sweep.phase4_import_source_docs
"""

from __future__ import annotations

import sys
from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from scripts.clean_sweep.config import (
    CURRENCY_CODE,
    DATE_END,
    DATE_START,
    DOC_BATCH_SIZE,
    ORG_ID,
    USER_ID,
    mysql_connect,
    norm_text,
    setup_logging,
    to_date,
    to_decimal,
)
from scripts.clean_sweep.phase2_accounts import load_account_map
from scripts.clean_sweep.phase3_import_gl import load_voucher_je_map

logger = setup_logging("phase4_import_source_docs")


def _trunc(value: str | None, max_len: int) -> str | None:
    """Truncate a string to max_len characters, or return None."""
    if value is None:
        return None
    return value[:max_len] if len(value) > max_len else value


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------


_DOCTYPE_TABLE: dict[str, str] = {
    "Customer": "ar.customer",
    "Supplier": "ap.supplier",
    "Employee": "hr.employee",
}


def _build_entity_lookup(
    db: Session,
    source_doctype: str,
) -> dict[str, UUID]:
    """Build ERPNext name → DotMac UUID lookup from sync_entity.

    Validates that target_id actually exists in the target table
    for Customer, Supplier, and Employee doctypes.
    """
    from app.models.sync import SyncEntity

    stmt = select(SyncEntity.source_name, SyncEntity.target_id).where(
        SyncEntity.organization_id == ORG_ID,
        SyncEntity.source_system == "erpnext",
        SyncEntity.source_doctype == source_doctype,
        SyncEntity.target_id.is_not(None),
    )
    raw = {str(name): tid for name, tid in db.execute(stmt).all()}

    # Validate target_id exists in the actual table
    target_table = _DOCTYPE_TABLE.get(source_doctype)
    if target_table and raw:
        # Determine PK column name
        pk_col = {
            "ar.customer": "customer_id",
            "ap.supplier": "supplier_id",
            "hr.employee": "employee_id",
        }.get(target_table, "id")

        placeholders = ", ".join(f"'{uid}'" for uid in raw.values())
        existing_ids = {
            UUID(str(row[0]))
            for row in db.execute(
                text(
                    f"SELECT {pk_col} FROM {target_table}"  # noqa: S608
                    f" WHERE {pk_col} IN ({placeholders})"
                )
            ).all()
        }
        missing = {name for name, tid in raw.items() if tid not in existing_ids}
        if missing:
            logger.warning(
                "  %d %s sync_entity rows point to deleted records — excluded",
                len(missing),
                source_doctype,
            )
        return {name: tid for name, tid in raw.items() if tid in existing_ids}

    return raw


def _create_sync_entity(
    db: Session,
    source_doctype: str,
    source_name: str,
    target_table: str,
    target_id: UUID,
) -> None:
    """Create a sync_entity row for an imported document."""
    from app.models.sync import SyncEntity

    # Check if already exists
    existing = db.scalar(
        select(SyncEntity.sync_id).where(
            SyncEntity.organization_id == ORG_ID,
            SyncEntity.source_system == "erpnext",
            SyncEntity.source_doctype == source_doctype,
            SyncEntity.source_name == source_name,
        )
    )
    if existing:
        return

    entity = SyncEntity(
        organization_id=ORG_ID,
        source_system="erpnext",
        source_doctype=source_doctype,
        source_name=source_name,
        target_table=target_table,
        target_id=target_id,
        sync_status="SYNCED",
        synced_at=datetime.now(UTC),
    )
    db.add(entity)


def _find_ar_control_account(db: Session, account_map: dict[str, UUID]) -> UUID:
    """Find the AR control account (1400 Trade Receivables)."""
    acct_id = account_map.get("Accounts Receivable - DT")
    if acct_id:
        return acct_id
    raise RuntimeError("AR control account not found in account map")


def _find_ap_control_account(db: Session, account_map: dict[str, UUID]) -> UUID:
    """Find the AP control account (2000 Trade Payables)."""
    acct_id = account_map.get("Expense Payable - DT")
    if acct_id:
        return acct_id
    raise RuntimeError("AP control account not found in account map")


# ---------------------------------------------------------------------------
# 4a. Sales Invoices
# ---------------------------------------------------------------------------


def _import_sales_invoices(
    db: Session,
    mysql_conn: Any,
    voucher_je_map: dict[str, UUID],
    customer_map: dict[str, UUID],
    account_map: dict[str, UUID],
    ar_control_id: UUID,
) -> int:
    """Import Sales Invoices from ERPNext → ar.invoice + ar.invoice_line."""
    from app.models.finance.ar.invoice import Invoice, InvoiceStatus, InvoiceType

    logger.info("4a. Importing Sales Invoices...")

    # Check existing invoices for idempotency
    existing = set(
        db.scalars(
            select(Invoice.erpnext_id).where(
                Invoice.organization_id == ORG_ID,
                Invoice.erpnext_id.is_not(None),
            )
        ).all()
    )

    with mysql_conn.cursor() as cur:
        cur.execute(
            """
            SELECT * FROM `tabSales Invoice`
            WHERE docstatus = 1
              AND posting_date >= %s AND posting_date < %s
              AND company = 'Dotmac Technologies'
            ORDER BY posting_date, name
        """,
            (str(DATE_START), str(DATE_END)),
        )
        invoices = cur.fetchall() or []

    logger.info("  Fetched %d Sales Invoices from ERPNext", len(invoices))

    # Fetch invoice items in bulk
    with mysql_conn.cursor() as cur:
        cur.execute(
            """
            SELECT * FROM `tabSales Invoice Item`
            WHERE docstatus = 1
              AND parent IN (
                  SELECT name FROM `tabSales Invoice`
                  WHERE docstatus = 1
                    AND posting_date >= %s AND posting_date < %s
                    AND company = 'Dotmac Technologies'
              )
            ORDER BY parent, idx
        """,
            (str(DATE_START), str(DATE_END)),
        )
        items = cur.fetchall() or []

    items_by_invoice: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        items_by_invoice[str(item["parent"])].append(item)

    count = 0
    for idx, inv in enumerate(invoices):
        erpnext_name = str(inv["name"])
        if erpnext_name in existing:
            continue

        customer_name = norm_text(inv.get("customer"))
        customer_id = customer_map.get(customer_name or "")

        if not customer_id:
            logger.warning("  No customer for %s: %s", erpnext_name, customer_name)
            continue

        # Map status
        erp_status = norm_text(inv.get("status")) or "Draft"
        outstanding = to_decimal(inv.get("outstanding_amount"))
        grand_total = to_decimal(inv.get("grand_total"))
        is_return = int(inv.get("is_return") or 0) == 1

        if erp_status in ("Paid", "Completed"):
            status = InvoiceStatus.PAID
        elif outstanding > 0 and outstanding < grand_total:
            status = InvoiceStatus.PARTIALLY_PAID
        elif erp_status == "Overdue":
            status = InvoiceStatus.OVERDUE
        elif erp_status in ("Cancelled", "Discarded"):
            status = InvoiceStatus.VOID
        else:
            status = InvoiceStatus.POSTED

        invoice_type = InvoiceType.CREDIT_NOTE if is_return else InvoiceType.STANDARD
        posting_date = to_date(inv["posting_date"]) or DATE_START
        due_date = to_date(inv.get("due_date")) or posting_date

        # Splynx data
        splynx_id = norm_text(inv.get("custom_splynx_invoice_id"))
        splynx_number = norm_text(inv.get("custom_splynx_number"))

        # Journal link from Phase 3
        je_id = voucher_je_map.get(erpnext_name)

        invoice_id = uuid4()
        invoice_number = norm_text(inv.get("name")) or f"SINV-{count + 1:05d}"

        new_invoice = Invoice(
            invoice_id=invoice_id,
            organization_id=ORG_ID,
            customer_id=customer_id,
            invoice_number=invoice_number[:30],
            invoice_type=invoice_type,
            invoice_date=posting_date,
            due_date=due_date,
            currency_code=norm_text(inv.get("currency")) or CURRENCY_CODE,
            exchange_rate=to_decimal(inv.get("conversion_rate")) or Decimal("1"),
            subtotal=to_decimal(inv.get("net_total")),
            tax_amount=to_decimal(inv.get("total_taxes_and_charges")),
            total_amount=grand_total,
            amount_paid=grand_total - outstanding,
            functional_currency_amount=to_decimal(inv.get("base_grand_total"))
            or grand_total,
            status=status,
            ar_control_account_id=ar_control_id,
            journal_entry_id=je_id,
            posting_status="POSTED" if je_id else "NOT_POSTED",
            notes=norm_text(inv.get("remarks")),
            created_by_user_id=USER_ID,
            posted_by_user_id=USER_ID if je_id else None,
            posted_at=datetime.now(UTC) if je_id else None,
            correlation_id=f"erpnext:Sales Invoice:{erpnext_name}",
            erpnext_id=erpnext_name,
            splynx_id=splynx_id,
            splynx_number=splynx_number,
        )
        db.add(new_invoice)

        # Invoice lines
        inv_items = items_by_invoice.get(erpnext_name, [])
        for line_idx, item in enumerate(inv_items, start=1):
            from app.models.finance.ar.invoice_line import InvoiceLine

            revenue_account_name = norm_text(item.get("income_account")) or ""
            revenue_account_id = account_map.get(revenue_account_name, ar_control_id)

            line = InvoiceLine(
                line_id=uuid4(),
                invoice_id=invoice_id,
                line_number=line_idx,
                description=norm_text(item.get("description"))
                or norm_text(item.get("item_name"))
                or "Item",
                quantity=to_decimal(item.get("qty")) or Decimal("1"),
                unit_price=to_decimal(item.get("rate")),
                line_amount=to_decimal(item.get("amount")),
                tax_amount=to_decimal(item.get("tax_amount")),
                revenue_account_id=revenue_account_id,
            )
            db.add(line)

        _create_sync_entity(db, "Sales Invoice", erpnext_name, "ar.invoice", invoice_id)
        count += 1

        if count % DOC_BATCH_SIZE == 0:
            db.commit()
            logger.info("    Committed %d invoices", count)

    db.commit()
    logger.info("  Sales Invoices imported: %d", count)
    return count


# ---------------------------------------------------------------------------
# 4b. Customer Payments
# ---------------------------------------------------------------------------


def _import_customer_payments(
    db: Session,
    mysql_conn: Any,
    voucher_je_map: dict[str, UUID],
    customer_map: dict[str, UUID],
    account_map: dict[str, UUID],
) -> int:
    """Import Payment Entry (Receive/Customer) → ar.customer_payment."""
    from app.models.finance.ar.customer_payment import (
        CustomerPayment,
        PaymentMethod,
        PaymentStatus,
    )

    logger.info("4b. Importing Customer Payments...")

    existing = set(
        db.scalars(
            select(CustomerPayment.erpnext_id).where(
                CustomerPayment.organization_id == ORG_ID,
                CustomerPayment.erpnext_id.is_not(None),
            )
        ).all()
    )

    with mysql_conn.cursor() as cur:
        cur.execute(
            """
            SELECT * FROM `tabPayment Entry`
            WHERE docstatus = 1
              AND payment_type = 'Receive'
              AND party_type = 'Customer'
              AND posting_date >= %s AND posting_date < %s
              AND company = 'Dotmac Technologies'
            ORDER BY posting_date, name
        """,
            (str(DATE_START), str(DATE_END)),
        )
        payments = cur.fetchall() or []

    logger.info("  Fetched %d Customer Payments from ERPNext", len(payments))

    # Fetch payment references for allocations
    with mysql_conn.cursor() as cur:
        cur.execute(
            """
            SELECT * FROM `tabPayment Entry Reference`
            WHERE docstatus = 1
              AND parent IN (
                  SELECT name FROM `tabPayment Entry`
                  WHERE docstatus = 1
                    AND payment_type = 'Receive'
                    AND party_type = 'Customer'
                    AND posting_date >= %s AND posting_date < %s
                    AND company = 'Dotmac Technologies'
              )
            ORDER BY parent, idx
        """,
            (str(DATE_START), str(DATE_END)),
        )
        refs = cur.fetchall() or []

    refs_by_payment: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ref in refs:
        refs_by_payment[str(ref["parent"])].append(ref)

    # Build invoice lookup for allocations (erpnext_id → invoice_id)
    from app.models.finance.ar.invoice import Invoice

    invoice_lookup = {
        eid: iid
        for eid, iid in db.execute(
            select(Invoice.erpnext_id, Invoice.invoice_id).where(
                Invoice.organization_id == ORG_ID,
                Invoice.erpnext_id.is_not(None),
            )
        ).all()
    }

    # Bank account lookup
    bank_map = _build_entity_lookup(db, "Bank Account")

    count = 0
    for pe in payments:
        erpnext_name = str(pe["name"])
        if erpnext_name in existing:
            continue

        customer_name = norm_text(pe.get("party"))
        customer_id = customer_map.get(customer_name or "")
        if not customer_id:
            logger.warning("  No customer for PE %s: %s", erpnext_name, customer_name)
            continue

        posting_date = to_date(pe["posting_date"]) or DATE_START
        paid_amount = to_decimal(pe.get("paid_amount"))
        je_id = voucher_je_map.get(erpnext_name)

        # Resolve bank account
        paid_to = norm_text(pe.get("paid_to"))
        bank_account_id: UUID | None = None
        for bank_name, bank_id in bank_map.items():
            if paid_to and bank_name in (paid_to or ""):
                bank_account_id = bank_id
                break

        # Resolve payment method
        mode = (norm_text(pe.get("mode_of_payment")) or "").lower()
        if "bank" in mode or "transfer" in mode:
            payment_method = PaymentMethod.BANK_TRANSFER
        elif "cash" in mode:
            payment_method = PaymentMethod.CASH
        elif "card" in mode:
            payment_method = PaymentMethod.CARD
        else:
            payment_method = PaymentMethod.BANK_TRANSFER

        payment_id = uuid4()
        payment_number = erpnext_name[:30]

        new_payment = CustomerPayment(
            payment_id=payment_id,
            organization_id=ORG_ID,
            customer_id=customer_id,
            payment_number=payment_number,
            payment_date=posting_date,
            payment_method=payment_method,
            currency_code=norm_text(pe.get("paid_to_account_currency"))
            or CURRENCY_CODE,
            gross_amount=paid_amount,
            amount=paid_amount,
            functional_currency_amount=to_decimal(pe.get("base_paid_amount"))
            or paid_amount,
            bank_account_id=bank_account_id,
            reference=_trunc(norm_text(pe.get("reference_no")), 100),
            description=norm_text(pe.get("remarks")),
            status=PaymentStatus.CLEARED,
            journal_entry_id=je_id,
            created_by_user_id=USER_ID,
            posted_by_user_id=USER_ID if je_id else None,
            posted_at=datetime.now(UTC) if je_id else None,
            correlation_id=f"erpnext:Payment Entry:{erpnext_name}",
            erpnext_id=erpnext_name,
        )
        db.add(new_payment)

        # Payment allocations
        for ref in refs_by_payment.get(erpnext_name, []):
            ref_name = norm_text(ref.get("reference_name"))
            if not ref_name:
                continue
            invoice_id = invoice_lookup.get(ref_name)
            if not invoice_id:
                continue

            from app.models.finance.ar.payment_allocation import PaymentAllocation

            alloc = PaymentAllocation(
                allocation_id=uuid4(),
                payment_id=payment_id,
                invoice_id=invoice_id,
                allocated_amount=to_decimal(ref.get("allocated_amount")),
                allocation_date=posting_date,
            )
            db.add(alloc)

        _create_sync_entity(
            db, "Payment Entry", erpnext_name, "ar.customer_payment", payment_id
        )
        count += 1

        if count % DOC_BATCH_SIZE == 0:
            db.commit()
            logger.info("    Committed %d customer payments", count)

    db.commit()
    logger.info("  Customer Payments imported: %d", count)
    return count


# ---------------------------------------------------------------------------
# 4c. Purchase Invoices
# ---------------------------------------------------------------------------


def _import_purchase_invoices(
    db: Session,
    mysql_conn: Any,
    voucher_je_map: dict[str, UUID],
    supplier_map: dict[str, UUID],
    account_map: dict[str, UUID],
    ap_control_id: UUID,
) -> int:
    """Import Purchase Invoices → ap.supplier_invoice + lines."""
    from app.models.finance.ap.supplier_invoice import (
        SupplierInvoice,
        SupplierInvoiceStatus,
        SupplierInvoiceType,
    )

    logger.info("4c. Importing Purchase Invoices...")

    existing = set(
        db.scalars(
            select(SupplierInvoice.correlation_id).where(
                SupplierInvoice.organization_id == ORG_ID,
                SupplierInvoice.correlation_id.is_not(None),
            )
        ).all()
    )

    with mysql_conn.cursor() as cur:
        cur.execute(
            """
            SELECT * FROM `tabPurchase Invoice`
            WHERE docstatus = 1
              AND posting_date >= %s AND posting_date < %s
              AND company = 'Dotmac Technologies'
            ORDER BY posting_date, name
        """,
            (str(DATE_START), str(DATE_END)),
        )
        invoices = cur.fetchall() or []

    # Items
    with mysql_conn.cursor() as cur:
        cur.execute(
            """
            SELECT * FROM `tabPurchase Invoice Item`
            WHERE docstatus = 1
              AND parent IN (
                  SELECT name FROM `tabPurchase Invoice`
                  WHERE docstatus = 1
                    AND posting_date >= %s AND posting_date < %s
                    AND company = 'Dotmac Technologies'
              )
            ORDER BY parent, idx
        """,
            (str(DATE_START), str(DATE_END)),
        )
        items = cur.fetchall() or []

    items_by_invoice: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        items_by_invoice[str(item["parent"])].append(item)

    logger.info("  Fetched %d Purchase Invoices from ERPNext", len(invoices))

    count = 0
    for inv in invoices:
        erpnext_name = str(inv["name"])
        corr_id = f"erpnext:Purchase Invoice:{erpnext_name}"
        if corr_id in existing:
            continue

        supplier_name = norm_text(inv.get("supplier"))
        supplier_id = supplier_map.get(supplier_name or "")
        if not supplier_id:
            logger.warning("  No supplier for PI %s: %s", erpnext_name, supplier_name)
            continue

        posting_date = to_date(inv["posting_date"]) or DATE_START
        due_date = to_date(inv.get("due_date")) or posting_date
        grand_total = to_decimal(inv.get("grand_total"))
        outstanding = to_decimal(inv.get("outstanding_amount"))
        is_return = int(inv.get("is_return") or 0) == 1
        je_id = voucher_je_map.get(erpnext_name)

        # Status
        if outstanding <= 0:
            status = SupplierInvoiceStatus.PAID
        elif outstanding < grand_total:
            status = SupplierInvoiceStatus.PARTIALLY_PAID
        else:
            status = SupplierInvoiceStatus.POSTED

        invoice_id = uuid4()

        new_invoice = SupplierInvoice(
            invoice_id=invoice_id,
            organization_id=ORG_ID,
            supplier_id=supplier_id,
            invoice_number=erpnext_name[:30],
            supplier_invoice_number=norm_text(inv.get("bill_no")),
            invoice_type=(
                SupplierInvoiceType.CREDIT_NOTE
                if is_return
                else SupplierInvoiceType.STANDARD
            ),
            invoice_date=posting_date,
            received_date=to_date(inv.get("bill_date")) or posting_date,
            due_date=due_date,
            currency_code=norm_text(inv.get("currency")) or CURRENCY_CODE,
            exchange_rate=to_decimal(inv.get("conversion_rate")) or Decimal("1"),
            subtotal=to_decimal(inv.get("net_total")),
            tax_amount=to_decimal(inv.get("total_taxes_and_charges")),
            total_amount=grand_total,
            amount_paid=grand_total - outstanding,
            functional_currency_amount=to_decimal(inv.get("base_grand_total"))
            or grand_total,
            status=status,
            ap_control_account_id=ap_control_id,
            journal_entry_id=je_id,
            posting_status="POSTED" if je_id else "NOT_POSTED",
            created_by_user_id=USER_ID,
            posted_by_user_id=USER_ID if je_id else None,
            posted_at=datetime.now(UTC) if je_id else None,
            correlation_id=corr_id,
            comments=norm_text(inv.get("remarks")),
        )
        db.add(new_invoice)

        # Invoice lines
        for line_idx, item in enumerate(
            items_by_invoice.get(erpnext_name, []), start=1
        ):
            from app.models.finance.ap.supplier_invoice_line import SupplierInvoiceLine

            expense_account_name = norm_text(item.get("expense_account")) or ""
            expense_account_id = account_map.get(expense_account_name)

            line = SupplierInvoiceLine(
                line_id=uuid4(),
                invoice_id=invoice_id,
                line_number=line_idx,
                description=norm_text(item.get("description"))
                or norm_text(item.get("item_name"))
                or "Item",
                quantity=to_decimal(item.get("qty")) or Decimal("1"),
                unit_price=to_decimal(item.get("rate")),
                line_amount=to_decimal(item.get("amount")),
                tax_amount=to_decimal(item.get("tax_amount")),
                expense_account_id=expense_account_id,
            )
            db.add(line)

        _create_sync_entity(
            db, "Purchase Invoice", erpnext_name, "ap.supplier_invoice", invoice_id
        )
        count += 1

        if count % DOC_BATCH_SIZE == 0:
            db.commit()
            logger.info("    Committed %d purchase invoices", count)

    db.commit()
    logger.info("  Purchase Invoices imported: %d", count)
    return count


# ---------------------------------------------------------------------------
# 4d. Supplier Payments
# ---------------------------------------------------------------------------


def _import_supplier_payments(
    db: Session,
    mysql_conn: Any,
    voucher_je_map: dict[str, UUID],
    supplier_map: dict[str, UUID],
) -> int:
    """Import Payment Entry (Pay/Supplier) → ap.supplier_payment."""
    from app.models.finance.ap.supplier_payment import (
        APPaymentMethod,
        APPaymentStatus,
        SupplierPayment,
    )

    logger.info("4d. Importing Supplier Payments...")

    existing = set(
        db.scalars(
            select(SupplierPayment.correlation_id).where(
                SupplierPayment.organization_id == ORG_ID,
                SupplierPayment.correlation_id.is_not(None),
            )
        ).all()
    )

    with mysql_conn.cursor() as cur:
        cur.execute(
            """
            SELECT * FROM `tabPayment Entry`
            WHERE docstatus = 1
              AND payment_type = 'Pay'
              AND party_type = 'Supplier'
              AND posting_date >= %s AND posting_date < %s
              AND company = 'Dotmac Technologies'
            ORDER BY posting_date, name
        """,
            (str(DATE_START), str(DATE_END)),
        )
        payments = cur.fetchall() or []

    logger.info("  Fetched %d Supplier Payments from ERPNext", len(payments))

    bank_map = _build_entity_lookup(db, "Bank Account")

    count = 0
    for pe in payments:
        erpnext_name = str(pe["name"])
        corr_id = f"erpnext:Payment Entry:{erpnext_name}"
        if corr_id in existing:
            continue

        supplier_name = norm_text(pe.get("party"))
        supplier_id = supplier_map.get(supplier_name or "")
        if not supplier_id:
            logger.warning("  No supplier for PE %s: %s", erpnext_name, supplier_name)
            continue

        posting_date = to_date(pe["posting_date"]) or DATE_START
        paid_amount = to_decimal(pe.get("paid_amount"))
        je_id = voucher_je_map.get(erpnext_name)

        # Bank account
        paid_from = norm_text(pe.get("paid_from"))
        bank_account_id: UUID | None = None
        for bank_name, bank_id in bank_map.items():
            if paid_from and bank_name in (paid_from or ""):
                bank_account_id = bank_id
                break

        # If no bank account found, use first available
        if not bank_account_id and bank_map:
            bank_account_id = next(iter(bank_map.values()))

        payment_id = uuid4()

        new_payment = SupplierPayment(
            payment_id=payment_id,
            organization_id=ORG_ID,
            supplier_id=supplier_id,
            payment_number=erpnext_name[:30],
            payment_date=posting_date,
            payment_method=APPaymentMethod.BANK_TRANSFER,
            currency_code=norm_text(pe.get("paid_from_account_currency"))
            or CURRENCY_CODE,
            amount=paid_amount,
            functional_currency_amount=to_decimal(pe.get("base_paid_amount"))
            or paid_amount,
            bank_account_id=bank_account_id or uuid4(),  # Must have a bank account
            reference=_trunc(norm_text(pe.get("reference_no")), 100),
            status=APPaymentStatus.CLEARED,
            journal_entry_id=je_id,
            created_by_user_id=USER_ID,
            posted_by_user_id=USER_ID if je_id else None,
            posted_at=datetime.now(UTC) if je_id else None,
            correlation_id=corr_id,
        )
        db.add(new_payment)

        _create_sync_entity(
            db, "Payment Entry", erpnext_name, "ap.supplier_payment", payment_id
        )
        count += 1

        if count % DOC_BATCH_SIZE == 0:
            db.commit()
            logger.info("    Committed %d supplier payments", count)

    db.commit()
    logger.info("  Supplier Payments imported: %d", count)
    return count


# ---------------------------------------------------------------------------
# 4e. Expense Claims
# ---------------------------------------------------------------------------


def _import_expense_claims(
    db: Session,
    mysql_conn: Any,
    voucher_je_map: dict[str, UUID],
    employee_map: dict[str, UUID],
    account_map: dict[str, UUID],
) -> int:
    """Import Expense Claims → expense.expense_claim + items."""
    from app.models.expense.expense_claim import (
        ExpenseClaim,
        ExpenseClaimItem,
        ExpenseClaimStatus,
    )

    logger.info("4e. Importing Expense Claims...")

    existing = set(
        db.scalars(
            select(ExpenseClaim.erpnext_id).where(
                ExpenseClaim.organization_id == ORG_ID,
                ExpenseClaim.erpnext_id.is_not(None),
            )
        ).all()
    )

    # Expense category lookup
    from app.models.expense.expense_claim import ExpenseCategory

    category_map: dict[str, UUID] = {}
    for cname, cid in db.execute(
        select(ExpenseCategory.category_name, ExpenseCategory.category_id).where(
            ExpenseCategory.organization_id == ORG_ID
        )
    ).all():
        category_map[str(cname).lower().strip()] = cid

    # ERPNext expense type → category lookup
    erpnext_type_map = _build_entity_lookup(db, "Expense Claim Type")

    with mysql_conn.cursor() as cur:
        cur.execute(
            """
            SELECT * FROM `tabExpense Claim`
            WHERE docstatus = 1
              AND posting_date >= %s AND posting_date < %s
              AND company = 'Dotmac Technologies'
            ORDER BY posting_date, name
        """,
            (str(DATE_START), str(DATE_END)),
        )
        claims = cur.fetchall() or []

    # Items
    with mysql_conn.cursor() as cur:
        cur.execute(
            """
            SELECT * FROM `tabExpense Claim Detail`
            WHERE docstatus = 1
              AND parent IN (
                  SELECT name FROM `tabExpense Claim`
                  WHERE docstatus = 1
                    AND posting_date >= %s AND posting_date < %s
                    AND company = 'Dotmac Technologies'
              )
            ORDER BY parent, idx
        """,
            (str(DATE_START), str(DATE_END)),
        )
        details = cur.fetchall() or []

    items_by_claim: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for d in details:
        items_by_claim[str(d["parent"])].append(d)

    logger.info("  Fetched %d Expense Claims from ERPNext", len(claims))

    # Default category fallback
    default_category_id = next(iter(category_map.values())) if category_map else None

    count = 0
    for claim in claims:
        erpnext_name = str(claim["name"])
        if erpnext_name in existing:
            continue

        employee_name = norm_text(claim.get("employee"))
        employee_id = employee_map.get(employee_name or "")

        posting_date = to_date(claim["posting_date"]) or DATE_START
        total_claimed = to_decimal(claim.get("total_claimed_amount"))
        total_approved = to_decimal(claim.get("total_sanctioned_amount"))

        # Status mapping
        erp_status = norm_text(claim.get("approval_status")) or "Draft"
        if erp_status == "Approved":
            status = ExpenseClaimStatus.APPROVED
            # Check if paid
            if norm_text(claim.get("status")) == "Paid":
                status = ExpenseClaimStatus.PAID
        elif erp_status == "Rejected":
            status = ExpenseClaimStatus.REJECTED
        else:
            status = ExpenseClaimStatus.SUBMITTED

        je_id = voucher_je_map.get(erpnext_name)

        claim_id = uuid4()

        new_claim = ExpenseClaim(
            claim_id=claim_id,
            organization_id=ORG_ID,
            claim_number=erpnext_name[:30],
            employee_id=employee_id,
            claim_date=posting_date,
            purpose=(norm_text(claim.get("expense_type")) or "Expense Claim")[:500],
            total_claimed_amount=total_claimed,
            total_approved_amount=total_approved,
            currency_code=CURRENCY_CODE,
            status=status,
            journal_entry_id=je_id,
            erpnext_id=erpnext_name,
            notes=norm_text(claim.get("remarks")),
        )
        db.add(new_claim)

        # Claim items
        for item_idx, detail in enumerate(
            items_by_claim.get(erpnext_name, []), start=1
        ):
            expense_type = norm_text(detail.get("expense_type")) or ""

            # Resolve category
            category_id = erpnext_type_map.get(expense_type) or default_category_id
            if not category_id:
                continue

            expense_date = to_date(detail.get("expense_date")) or posting_date

            raw_desc = norm_text(detail.get("description")) or expense_type or "Expense"
            item = ExpenseClaimItem(
                item_id=uuid4(),
                organization_id=ORG_ID,
                claim_id=claim_id,
                expense_date=expense_date,
                category_id=category_id,
                description=raw_desc[:500],
                claimed_amount=to_decimal(detail.get("amount")),
                approved_amount=to_decimal(detail.get("sanctioned_amount")),
            )
            db.add(item)

        _create_sync_entity(
            db, "Expense Claim", erpnext_name, "expense.expense_claim", claim_id
        )
        count += 1

        if count % DOC_BATCH_SIZE == 0:
            db.commit()
            logger.info("    Committed %d expense claims", count)

    db.commit()
    logger.info("  Expense Claims imported: %d", count)
    return count


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------


def main() -> None:
    from app.db import SessionLocal

    logger.info("=" * 60)
    logger.info("Phase 4: Import source documents from ERPNext")
    logger.info("=" * 60)

    account_map = load_account_map()
    voucher_je_map = load_voucher_je_map()
    logger.info(
        "Loaded %d account mappings, %d voucher→JE mappings",
        len(account_map),
        len(voucher_je_map),
    )

    mysql_conn = mysql_connect()

    try:
        with SessionLocal() as db:
            # Build lookup maps from preserved sync_entity rows
            customer_map = _build_entity_lookup(db, "Customer")
            supplier_map = _build_entity_lookup(db, "Supplier")
            employee_map = _build_entity_lookup(db, "Employee")
            logger.info(
                "Entity lookups: %d customers, %d suppliers, %d employees",
                len(customer_map),
                len(supplier_map),
                len(employee_map),
            )

            ar_control_id = _find_ar_control_account(db, account_map)
            ap_control_id = _find_ap_control_account(db, account_map)

            # 4a. Sales Invoices
            inv_count = _import_sales_invoices(
                db, mysql_conn, voucher_je_map, customer_map, account_map, ar_control_id
            )

        with SessionLocal() as db:
            customer_map = _build_entity_lookup(db, "Customer")
            # 4b. Customer Payments
            cp_count = _import_customer_payments(
                db, mysql_conn, voucher_je_map, customer_map, account_map
            )

        with SessionLocal() as db:
            supplier_map = _build_entity_lookup(db, "Supplier")
            # 4c. Purchase Invoices
            pi_count = _import_purchase_invoices(
                db, mysql_conn, voucher_je_map, supplier_map, account_map, ap_control_id
            )

        with SessionLocal() as db:
            supplier_map = _build_entity_lookup(db, "Supplier")
            # 4d. Supplier Payments
            sp_count = _import_supplier_payments(
                db, mysql_conn, voucher_je_map, supplier_map
            )

        with SessionLocal() as db:
            employee_map = _build_entity_lookup(db, "Employee")
            # 4e. Expense Claims
            ec_count = _import_expense_claims(
                db, mysql_conn, voucher_je_map, employee_map, account_map
            )

    finally:
        mysql_conn.close()

    logger.info("=" * 60)
    logger.info("Phase 4 complete.")
    logger.info("  Sales Invoices: %d", inv_count)
    logger.info("  Customer Payments: %d", cp_count)
    logger.info("  Purchase Invoices: %d", pi_count)
    logger.info("  Supplier Payments: %d", sp_count)
    logger.info("  Expense Claims: %d", ec_count)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logger.exception("Phase 4 failed")
        sys.exit(1)
