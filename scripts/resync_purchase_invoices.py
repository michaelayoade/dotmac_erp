#!/usr/bin/env python3
"""
Resync ERPNext Purchase Invoices with WHT Extraction.

Reads directly from the ERPNext MariaDB dump (erpnext_db container) and
syncs all submitted purchase invoices into DotMac, separating VAT from WHT.

Steps:
  1. Read ERPNext data (invoices, items, taxes) from MariaDB
  2. Update existing ~180 invoices with WHT fields + create line tax records
  3. Create ~997 missing invoices with proper WHT/VAT separation
  4. Backfill WITHHOLDING tax transactions for the WHT report

Usage:
    python scripts/resync_purchase_invoices.py --dry-run          # Preview counts
    python scripts/resync_purchase_invoices.py --execute           # Run all steps
    python scripts/resync_purchase_invoices.py --execute --step 2  # Run specific step
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

import pymysql
from sqlalchemy import text
from sqlalchemy.orm import Session

# ── Bootstrap ────────────────────────────────────────────────────────────
sys.path.insert(0, ".")

from app.db import SessionLocal  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ── Data classes ─────────────────────────────────────────────────────────


@dataclass
class StepResult:
    """Result of a single step."""

    step: int
    name: str
    dry_run: bool
    details: dict[str, object] = field(default_factory=dict)

    def log_summary(self) -> None:
        logger.info("=" * 60)
        mode = "DRY RUN" if self.dry_run else "EXECUTED"
        logger.info("Step %d: %s [%s]", self.step, self.name, mode)
        for k, v in self.details.items():
            logger.info("  %-35s %s", k, v)
        logger.info("=" * 60)


@dataclass
class ERPNextInvoice:
    """Parsed ERPNext purchase invoice with children."""

    name: str
    supplier: str
    posting_date: date
    due_date: date | None
    net_total: Decimal
    total_taxes_and_charges: Decimal
    grand_total: Decimal
    outstanding_amount: Decimal
    base_grand_total: Decimal
    conversion_rate: Decimal
    currency: str
    status: str
    bill_no: str | None
    is_return: int
    items: list[dict[str, Any]] = field(default_factory=list)
    taxes: list[dict[str, Any]] = field(default_factory=list)

    @property
    def vat_amount(self) -> Decimal:
        """Sum of Add taxes (VAT)."""
        return sum(
            (
                abs(Decimal(str(t.get("tax_amount") or 0)))
                for t in self.taxes
                if str(t.get("add_deduct_tax") or "Add") != "Deduct"
            ),
            Decimal("0"),
        )

    @property
    def wht_amount(self) -> Decimal:
        """Sum of Deduct taxes (WHT)."""
        return sum(
            (
                abs(Decimal(str(t.get("tax_amount") or 0)))
                for t in self.taxes
                if str(t.get("add_deduct_tax") or "Add") == "Deduct"
            ),
            Decimal("0"),
        )

    @property
    def wht_rate(self) -> Decimal:
        """Highest WHT rate from deduct rows."""
        rates = [
            Decimal(str(t.get("rate") or 0))
            for t in self.taxes
            if str(t.get("add_deduct_tax") or "Add") == "Deduct"
        ]
        return max(rates, default=Decimal("0"))


# ── MariaDB helpers ──────────────────────────────────────────────────────


def get_mariadb_connection() -> pymysql.Connection:
    """Connect to ERPNext MariaDB dump (erpnext_db container, port 3307)."""
    return pymysql.connect(
        host="127.0.0.1",
        port=3307,
        user="root",
        password="root",
        database="erpnext",
        cursorclass=pymysql.cursors.DictCursor,
    )


def load_erpnext_data(conn: pymysql.Connection) -> dict[str, ERPNextInvoice]:
    """Load all submitted purchase invoices with items and taxes."""
    invoices: dict[str, ERPNextInvoice] = {}

    with conn.cursor() as cur:
        # 1. Load invoice headers
        cur.execute("""
            SELECT name, supplier, posting_date, due_date, net_total,
                   total_taxes_and_charges, grand_total, outstanding_amount,
                   base_grand_total, conversion_rate, currency, status,
                   bill_no, is_return
            FROM `tabPurchase Invoice`
            WHERE docstatus = 1
            ORDER BY posting_date, name
        """)
        for row in cur.fetchall():
            invoices[row["name"]] = ERPNextInvoice(
                name=row["name"],
                supplier=row["supplier"],
                posting_date=row["posting_date"],
                due_date=row["due_date"],
                net_total=Decimal(str(row["net_total"])),
                total_taxes_and_charges=Decimal(str(row["total_taxes_and_charges"])),
                grand_total=Decimal(str(row["grand_total"])),
                outstanding_amount=Decimal(str(row["outstanding_amount"])),
                base_grand_total=Decimal(str(row["base_grand_total"])),
                conversion_rate=Decimal(str(row["conversion_rate"])),
                currency=row["currency"] or "NGN",
                status=row["status"] or "Unpaid",
                bill_no=row["bill_no"],
                is_return=int(row["is_return"] or 0),
            )

        # 2. Load line items
        cur.execute("""
            SELECT parent, item_code, item_name, description,
                   qty, rate, amount, item_tax_amount, expense_account
            FROM `tabPurchase Invoice Item`
            WHERE parent IN (
                SELECT name FROM `tabPurchase Invoice` WHERE docstatus = 1
            )
            ORDER BY parent, idx
        """)
        for row in cur.fetchall():
            parent = row["parent"]
            if parent in invoices:
                invoices[parent].items.append(
                    {
                        "item_code": row["item_code"],
                        "item_name": row["item_name"],
                        "description": row["description"],
                        "qty": Decimal(str(row["qty"])),
                        "rate": Decimal(str(row["rate"])),
                        "amount": Decimal(str(row["amount"])),
                        "item_tax_amount": Decimal(str(row["item_tax_amount"] or 0)),
                        "expense_account": row["expense_account"],
                    }
                )

        # 3. Load taxes
        cur.execute("""
            SELECT parent, add_deduct_tax, rate, tax_amount, account_head,
                   description, is_tax_withholding_account
            FROM `tabPurchase Taxes and Charges`
            WHERE parent IN (
                SELECT name FROM `tabPurchase Invoice` WHERE docstatus = 1
            )
            ORDER BY parent, idx
        """)
        for row in cur.fetchall():
            parent = row["parent"]
            if parent in invoices:
                invoices[parent].taxes.append(
                    {
                        "add_deduct_tax": row["add_deduct_tax"],
                        "rate": Decimal(str(row["rate"])),
                        "tax_amount": Decimal(str(row["tax_amount"])),
                        "account_head": row["account_head"],
                        "description": row["description"],
                    }
                )

    return invoices


# ── Postgres helpers ─────────────────────────────────────────────────────


def get_org_id(db: Session) -> UUID:
    """Get the single organization ID."""
    row = db.execute(
        text("SELECT organization_id FROM core_org.organization LIMIT 1")
    ).fetchone()
    if not row:
        raise RuntimeError("No organization found")
    return UUID(str(row[0]))


def get_tax_code_id(
    db: Session, org_id: UUID, tax_type: str, rate: Decimal | None = None
) -> UUID | None:
    """Look up a DotMac tax code by type and optional rate."""
    if rate is not None:
        row = db.execute(
            text("""
                SELECT tax_code_id FROM tax.tax_code
                WHERE organization_id = :org_id
                  AND tax_type = :tax_type
                  AND tax_rate = :rate
                  AND is_active = true
                LIMIT 1
            """),
            {"org_id": org_id, "tax_type": tax_type, "rate": rate},
        ).fetchone()
        if row:
            return row[0]

    # Fallback: any active code of this type
    row = db.execute(
        text("""
            SELECT tax_code_id FROM tax.tax_code
            WHERE organization_id = :org_id
              AND tax_type = :tax_type
              AND is_active = true
            ORDER BY tax_code
            LIMIT 1
        """),
        {"org_id": org_id, "tax_type": tax_type},
    ).fetchone()
    return row[0] if row else None


def get_jurisdiction_id(db: Session, org_id: UUID) -> UUID | None:
    """Get the default tax jurisdiction."""
    row = db.execute(
        text("""
            SELECT jurisdiction_id FROM tax.tax_jurisdiction
            WHERE organization_id = :org_id
            ORDER BY jurisdiction_code LIMIT 1
        """),
        {"org_id": org_id},
    ).fetchone()
    return row[0] if row else None


def get_sync_entity_map(db: Session, org_id: UUID, doctype: str) -> dict[str, UUID]:
    """Build {source_name: target_id} map for a doctype."""
    rows = db.execute(
        text("""
            SELECT source_name, target_id FROM sync.sync_entity
            WHERE organization_id = :org_id
              AND source_system = 'erpnext'
              AND source_doctype = :doctype
              AND sync_status = 'SYNCED'
              AND target_id IS NOT NULL
        """),
        {"org_id": org_id, "doctype": doctype},
    ).fetchall()
    return {str(r[0]): r[1] for r in rows}


def get_ap_control_account_id(db: Session, org_id: UUID) -> UUID | None:
    """Get the AP control account (2000-series or Accounts Payable)."""
    row = db.execute(
        text("""
            SELECT account_id FROM gl.account
            WHERE organization_id = :org_id
              AND (account_code = '2000' OR account_name ILIKE '%accounts payable%')
              AND is_active = true
            ORDER BY account_code
            LIMIT 1
        """),
        {"org_id": org_id},
    ).fetchone()
    return row[0] if row else None


def get_user_id(db: Session, org_id: UUID) -> UUID:
    """Get a system user for audit trail."""
    row = db.execute(
        text("""
            SELECT id FROM public.people
            WHERE organization_id = :org_id
            ORDER BY created_at
            LIMIT 1
        """),
        {"org_id": org_id},
    ).fetchone()
    if not row:
        raise RuntimeError("No person found for audit trail")
    return row[0]


# Map ERPNext status → DotMac status
_STATUS_MAP: dict[str, str] = {
    "Draft": "DRAFT",
    "Unpaid": "APPROVED",
    "Overdue": "APPROVED",
    "Partly Paid": "PARTIALLY_PAID",
    "Paid": "PAID",
    "Return": "VOID",
    "Debit Note Issued": "VOID",
    "Cancelled": "VOID",
}


# ── Step 1: Read ERPNext data ───────────────────────────────────────────


def step1_read_erpnext(
    *, dry_run: bool
) -> tuple[StepResult, dict[str, ERPNextInvoice]]:
    """Load all submitted purchase invoices from ERPNext MariaDB."""
    result = StepResult(step=1, name="Read ERPNext data from MariaDB", dry_run=dry_run)

    conn = get_mariadb_connection()
    try:
        invoices = load_erpnext_data(conn)
    finally:
        conn.close()

    # Summarize
    with_wht = sum(1 for inv in invoices.values() if inv.wht_amount > 0)
    with_vat = sum(1 for inv in invoices.values() if inv.vat_amount > 0)
    total_wht = sum(inv.wht_amount for inv in invoices.values())
    total_vat = sum(inv.vat_amount for inv in invoices.values())
    total_items = sum(len(inv.items) for inv in invoices.values())

    result.details["invoices_loaded"] = len(invoices)
    result.details["with_wht"] = with_wht
    result.details["with_vat"] = with_vat
    result.details["total_wht_amount"] = f"₦{total_wht:,.2f}"
    result.details["total_vat_amount"] = f"₦{total_vat:,.2f}"
    result.details["total_line_items"] = total_items
    result.log_summary()
    return result, invoices


# ── Step 2: Update existing invoices ─────────────────────────────────────


def step2_update_existing(
    db: Session,
    org_id: UUID,
    invoices: dict[str, ERPNextInvoice],
    *,
    dry_run: bool,
) -> StepResult:
    """Update 180 already-synced invoices with WHT fields + line tax records."""
    result = StepResult(
        step=2, name="Update existing invoices with WHT", dry_run=dry_run
    )

    # Get sync entity map for purchase invoices
    pi_sync = get_sync_entity_map(db, org_id, "Purchase Invoice")
    vat_code_id = get_tax_code_id(db, org_id, "VAT")
    wht_fallback_id = get_tax_code_id(db, org_id, "WITHHOLDING")

    updated = 0
    wht_updated = 0
    line_taxes_created = 0
    skipped = 0
    errors: list[str] = []

    for source_name, target_id in pi_sync.items():
        inv = invoices.get(source_name)
        if not inv:
            skipped += 1
            continue

        try:
            # Resolve WHT code by rate
            wht_code_id: UUID | None = None
            if inv.wht_amount > 0:
                rate_decimal = inv.wht_rate / Decimal("100")
                wht_code_id = get_tax_code_id(db, org_id, "WITHHOLDING", rate_decimal)
                if not wht_code_id:
                    wht_code_id = wht_fallback_id

            if not dry_run:
                # Update invoice header
                db.execute(
                    text("""
                        UPDATE ap.supplier_invoice
                        SET tax_amount = :vat_amount,
                            withholding_tax_amount = :wht_amount,
                            withholding_tax_code_id = :wht_code_id
                        WHERE invoice_id = :invoice_id
                    """),
                    {
                        "vat_amount": inv.vat_amount,
                        "wht_amount": inv.wht_amount,
                        "wht_code_id": wht_code_id,
                        "invoice_id": target_id,
                    },
                )

                # Delete existing line tax records (clean slate)
                db.execute(
                    text("""
                        DELETE FROM ap.supplier_invoice_line_tax
                        WHERE line_id IN (
                            SELECT line_id FROM ap.supplier_invoice_line
                            WHERE invoice_id = :invoice_id
                        )
                    """),
                    {"invoice_id": target_id},
                )

                # Create new line tax records
                lines = db.execute(
                    text("""
                        SELECT line_id, line_amount
                        FROM ap.supplier_invoice_line
                        WHERE invoice_id = :invoice_id
                        ORDER BY line_number
                    """),
                    {"invoice_id": target_id},
                ).fetchall()

                total_line_amount = sum(
                    Decimal(str(l[1]))
                    for l in lines  # noqa: E741
                )
                if total_line_amount > 0 and lines:
                    for line_id, line_amount in lines:
                        line_amt = Decimal(str(line_amount))
                        share = line_amt / total_line_amount
                        quant = Decimal("0.01")
                        tax_seq = 1

                        if vat_code_id and inv.vat_amount > 0:
                            vat_share = (inv.vat_amount * share).quantize(quant)
                            db.execute(
                                text("""
                                    INSERT INTO ap.supplier_invoice_line_tax
                                    (line_id, tax_code_id, base_amount, tax_rate,
                                     tax_amount, is_inclusive, is_recoverable,
                                     recoverable_amount, sequence)
                                    VALUES (:line_id, :tax_code_id, :base_amount,
                                            :tax_rate, :tax_amount, false, true,
                                            :tax_amount, :seq)
                                """),
                                {
                                    "line_id": line_id,
                                    "tax_code_id": vat_code_id,
                                    "base_amount": line_amt,
                                    "tax_rate": Decimal("7.5"),
                                    "tax_amount": vat_share,
                                    "seq": tax_seq,
                                },
                            )
                            line_taxes_created += 1
                            tax_seq += 1

                        if wht_code_id and inv.wht_amount > 0:
                            wht_share = (inv.wht_amount * share).quantize(quant)
                            db.execute(
                                text("""
                                    INSERT INTO ap.supplier_invoice_line_tax
                                    (line_id, tax_code_id, base_amount, tax_rate,
                                     tax_amount, is_inclusive, is_recoverable,
                                     recoverable_amount, sequence)
                                    VALUES (:line_id, :tax_code_id, :base_amount,
                                            :tax_rate, :tax_amount, false, false,
                                            0, :seq)
                                """),
                                {
                                    "line_id": line_id,
                                    "tax_code_id": wht_code_id,
                                    "base_amount": line_amt,
                                    "tax_rate": inv.wht_rate,
                                    "tax_amount": wht_share,
                                    "seq": tax_seq,
                                },
                            )
                            line_taxes_created += 1

            updated += 1
            if inv.wht_amount > 0:
                wht_updated += 1

            if not dry_run:
                db.commit()  # Commit per invoice

        except Exception as e:
            logger.exception("Error updating %s", source_name)
            errors.append(f"{source_name}: {e}")
            db.rollback()

    result.details["existing_synced"] = len(pi_sync)
    result.details["updated"] = updated
    result.details["with_wht"] = wht_updated
    result.details["line_taxes_created"] = line_taxes_created
    result.details["skipped (not in ERPNext)"] = skipped
    result.details["errors"] = len(errors)
    if errors[:5]:
        result.details["sample_errors"] = "; ".join(errors[:5])
    result.log_summary()
    return result


# ── Step 3: Create missing invoices ──────────────────────────────────────


def step3_create_missing(
    db: Session,
    org_id: UUID,
    invoices: dict[str, ERPNextInvoice],
    *,
    dry_run: bool,
) -> StepResult:
    """Create ~997 purchase invoices not yet in DotMac."""
    result = StepResult(
        step=3, name="Create missing purchase invoices", dry_run=dry_run
    )

    # Lookup tables
    pi_sync = get_sync_entity_map(db, org_id, "Purchase Invoice")
    supplier_sync = get_sync_entity_map(db, org_id, "Supplier")
    item_sync = get_sync_entity_map(db, org_id, "Item")
    account_sync = get_sync_entity_map(db, org_id, "Account")
    ap_control_id = get_ap_control_account_id(db, org_id)
    user_id = get_user_id(db, org_id)
    vat_code_id = get_tax_code_id(db, org_id, "VAT")
    wht_fallback_id = get_tax_code_id(db, org_id, "WITHHOLDING")

    # Filter to only missing invoices
    missing = {name: inv for name, inv in invoices.items() if name not in pi_sync}

    created = 0
    with_wht = 0
    line_taxes_created = 0
    supplier_missing = 0
    errors: list[str] = []

    # Get current max PINV number for sequential numbering
    max_num_row = db.execute(
        text("""
            SELECT MAX(CAST(
                REGEXP_REPLACE(invoice_number, '[^0-9]', '', 'g') AS INTEGER
            ))
            FROM ap.supplier_invoice
            WHERE organization_id = :org_id
              AND invoice_number ~ '^PINV-'
        """),
        {"org_id": org_id},
    ).fetchone()
    seq_counter = (max_num_row[0] or 0) if max_num_row else 0

    for source_name, inv in missing.items():
        # Resolve supplier
        supplier_id = supplier_sync.get(inv.supplier)
        if not supplier_id:
            supplier_missing += 1
            errors.append(f"{source_name}: supplier '{inv.supplier}' not found")
            continue

        try:
            # Resolve WHT
            wht_code_id: UUID | None = None
            if inv.wht_amount > 0:
                rate_decimal = inv.wht_rate / Decimal("100")
                wht_code_id = get_tax_code_id(db, org_id, "WITHHOLDING", rate_decimal)
                if not wht_code_id:
                    wht_code_id = wht_fallback_id

            # Map status
            if inv.is_return:
                status = "VOID"
                invoice_type = "DEBIT_NOTE"
            else:
                status = _STATUS_MAP.get(inv.status, "APPROVED")
                invoice_type = "STANDARD"

            seq_counter += 1
            invoice_number = f"PINV-{seq_counter:05d}"

            functional_amount = inv.base_grand_total
            if not functional_amount:
                functional_amount = inv.grand_total * (
                    inv.conversion_rate or Decimal("1")
                )

            if not dry_run:
                # Insert invoice
                inv_row = db.execute(
                    text("""
                        INSERT INTO ap.supplier_invoice (
                            organization_id, invoice_number, supplier_invoice_number,
                            invoice_type, status, invoice_date, received_date, due_date,
                            supplier_id, currency_code, subtotal, tax_amount,
                            total_amount, amount_paid, functional_currency_amount,
                            exchange_rate, ap_control_account_id, created_by_user_id,
                            withholding_tax_amount, withholding_tax_code_id,
                            posting_status, three_way_match_status,
                            is_prepayment, prepayment_applied, is_intercompany
                        ) VALUES (
                            :org_id, :inv_num, :bill_no,
                            :inv_type, :status, :inv_date, :inv_date, :due_date,
                            :supplier_id, :currency, :subtotal, :tax_amount,
                            :total_amount, :amount_paid, :func_amount,
                            :exchange_rate, :ap_control, :user_id,
                            :wht_amount, :wht_code_id,
                            'NOT_POSTED', 'PENDING',
                            false, 0, false
                        )
                        RETURNING invoice_id
                    """),
                    {
                        "org_id": org_id,
                        "inv_num": invoice_number,
                        "bill_no": inv.bill_no,
                        "inv_type": invoice_type,
                        "status": status,
                        "inv_date": inv.posting_date,
                        "due_date": inv.due_date or inv.posting_date,
                        "supplier_id": supplier_id,
                        "currency": inv.currency[:3],
                        "subtotal": inv.net_total,
                        "tax_amount": inv.vat_amount,
                        "total_amount": inv.grand_total,
                        "amount_paid": inv.grand_total - inv.outstanding_amount,
                        "func_amount": functional_amount,
                        "exchange_rate": inv.conversion_rate or Decimal("1"),
                        "ap_control": ap_control_id,
                        "user_id": user_id,
                        "wht_amount": inv.wht_amount,
                        "wht_code_id": wht_code_id,
                    },
                )
                invoice_id = inv_row.fetchone()[0]

                # Create invoice lines
                total_line_amount = sum(
                    it.get("amount", Decimal("0")) for it in inv.items
                )
                for seq, item in enumerate(inv.items, 1):
                    item_id = item_sync.get(item.get("item_code") or "")
                    expense_account_id = account_sync.get(
                        item.get("expense_account") or ""
                    )
                    line_amount = item.get("amount", Decimal("0"))
                    # Distribute VAT across lines
                    if total_line_amount > 0:
                        share = line_amount / total_line_amount
                        line_tax = (inv.vat_amount * share).quantize(Decimal("0.01"))
                    else:
                        line_tax = Decimal("0")

                    line_row = db.execute(
                        text("""
                            INSERT INTO ap.supplier_invoice_line (
                                invoice_id, line_number, item_id, description,
                                quantity, unit_price, line_amount, tax_amount,
                                expense_account_id, capitalize_flag
                            ) VALUES (
                                :inv_id, :line_num, :item_id, :desc,
                                :qty, :rate, :amount, :tax_amount,
                                :expense_acct_id, false
                            )
                            RETURNING line_id
                        """),
                        {
                            "inv_id": invoice_id,
                            "line_num": seq,
                            "item_id": item_id,
                            "desc": str(
                                item.get("description")
                                or item.get("item_name")
                                or item.get("item_code")
                                or "Item"
                            )[:1000],
                            "qty": item.get("qty", Decimal("1")),
                            "rate": item.get("rate", Decimal("0")),
                            "amount": line_amount,
                            "tax_amount": line_tax,
                            "expense_acct_id": expense_account_id,
                        },
                    )
                    line_id = line_row.fetchone()[0]

                    # Create line tax records (VAT + WHT)
                    if total_line_amount > 0:
                        tax_seq = 1
                        if vat_code_id and inv.vat_amount > 0:
                            vat_share = (inv.vat_amount * share).quantize(
                                Decimal("0.01")
                            )
                            db.execute(
                                text("""
                                    INSERT INTO ap.supplier_invoice_line_tax
                                    (line_id, tax_code_id, base_amount, tax_rate,
                                     tax_amount, is_inclusive, is_recoverable,
                                     recoverable_amount, sequence)
                                    VALUES (:line_id, :tc, :base, :rate,
                                            :tax, false, true, :tax, :seq)
                                """),
                                {
                                    "line_id": line_id,
                                    "tc": vat_code_id,
                                    "base": line_amount,
                                    "rate": Decimal("7.5"),
                                    "tax": vat_share,
                                    "seq": tax_seq,
                                },
                            )
                            line_taxes_created += 1
                            tax_seq += 1

                        if wht_code_id and inv.wht_amount > 0:
                            wht_share = (inv.wht_amount * share).quantize(
                                Decimal("0.01")
                            )
                            db.execute(
                                text("""
                                    INSERT INTO ap.supplier_invoice_line_tax
                                    (line_id, tax_code_id, base_amount, tax_rate,
                                     tax_amount, is_inclusive, is_recoverable,
                                     recoverable_amount, sequence)
                                    VALUES (:line_id, :tc, :base, :rate,
                                            :tax, false, false, 0, :seq)
                                """),
                                {
                                    "line_id": line_id,
                                    "tc": wht_code_id,
                                    "base": line_amount,
                                    "rate": inv.wht_rate,
                                    "tax": wht_share,
                                    "seq": tax_seq,
                                },
                            )
                            line_taxes_created += 1

                # If no items, create a single dummy line
                if not inv.items:
                    db.execute(
                        text("""
                            INSERT INTO ap.supplier_invoice_line (
                                invoice_id, line_number, description,
                                quantity, unit_price, line_amount, tax_amount,
                                capitalize_flag
                            ) VALUES (
                                :inv_id, 1, 'Purchase Invoice',
                                1, :total, :total, :tax, false
                            )
                        """),
                        {
                            "inv_id": invoice_id,
                            "total": inv.grand_total,
                            "tax": inv.vat_amount,
                        },
                    )

                # Create sync_entity tracking record
                db.execute(
                    text("""
                        INSERT INTO sync.sync_entity (
                            organization_id, source_system, source_doctype,
                            source_name, target_table, target_id,
                            sync_status, synced_at
                        ) VALUES (
                            :org_id, 'erpnext', 'Purchase Invoice',
                            :source_name, 'ap.supplier_invoice', :target_id,
                            'SYNCED', NOW()
                        )
                        ON CONFLICT (organization_id, source_system, source_doctype, source_name)
                        DO UPDATE SET target_id = EXCLUDED.target_id,
                                      sync_status = 'SYNCED',
                                      synced_at = NOW()
                    """),
                    {
                        "org_id": org_id,
                        "source_name": source_name,
                        "target_id": invoice_id,
                    },
                )

            if not dry_run:
                db.commit()  # Commit per invoice

            created += 1
            if inv.wht_amount > 0:
                with_wht += 1

        except Exception as e:
            logger.exception("Error creating %s", source_name)
            errors.append(f"{source_name}: {e}")
            db.rollback()
            seq_counter -= 1  # Revert counter for failed invoice

    result.details["missing_invoices"] = len(missing)
    result.details["created"] = created
    result.details["with_wht"] = with_wht
    result.details["line_taxes_created"] = line_taxes_created
    result.details["supplier_not_found"] = supplier_missing
    result.details["errors"] = len(errors)
    if errors[:5]:
        result.details["sample_errors"] = "; ".join(errors[:5])
    result.log_summary()
    return result


# ── Step 4: Backfill WITHHOLDING tax transactions ────────────────────────


def step4_backfill_wht_transactions(
    db: Session,
    org_id: UUID,
    *,
    dry_run: bool,
) -> StepResult:
    """Create WITHHOLDING tax transactions from supplier_invoice_line_tax."""
    result = StepResult(
        step=4, name="Backfill WITHHOLDING tax transactions", dry_run=dry_run
    )

    # Count source rows (WHT line taxes)
    count_row = db.execute(
        text("""
            SELECT COUNT(*)
            FROM ap.supplier_invoice_line_tax silt
            JOIN ap.supplier_invoice_line sil ON sil.line_id = silt.line_id
            JOIN ap.supplier_invoice si ON si.invoice_id = sil.invoice_id
            JOIN tax.tax_code tc ON tc.tax_code_id = silt.tax_code_id
            WHERE si.organization_id = :org_id
              AND si.status NOT IN ('DRAFT', 'VOID')
              AND tc.tax_type = 'WITHHOLDING'
        """),
        {"org_id": org_id},
    ).fetchone()
    source_count = count_row[0] if count_row else 0

    # Idempotency check
    existing_row = db.execute(
        text("""
            SELECT COUNT(*)
            FROM tax.tax_transaction
            WHERE organization_id = :org_id
              AND transaction_type = 'WITHHOLDING'
              AND source_document_type = 'AP_INVOICE'
        """),
        {"org_id": org_id},
    ).fetchone()
    existing_count = existing_row[0] if existing_row else 0

    result.details["wht_line_tax_rows"] = source_count
    result.details["existing_wht_transactions"] = existing_count

    if existing_count > 0:
        # Delete existing to re-create (since we may have updated line taxes)
        if not dry_run:
            deleted = db.execute(
                text("""
                    DELETE FROM tax.tax_transaction
                    WHERE organization_id = :org_id
                      AND transaction_type = 'WITHHOLDING'
                      AND source_document_type = 'AP_INVOICE'
                """),
                {"org_id": org_id},
            )
            result.details["deleted_stale"] = deleted.rowcount
        else:
            result.details["would_delete_stale"] = existing_count

    if source_count == 0:
        result.details["action"] = "No WHT line tax records to process"
        result.log_summary()
        return result

    if dry_run:
        result.details["would_insert"] = source_count
        result.log_summary()
        return result

    # Bulk INSERT ... SELECT
    inserted = db.execute(
        text("""
            INSERT INTO tax.tax_transaction (
                organization_id, fiscal_period_id, tax_code_id, jurisdiction_id,
                transaction_type, transaction_date,
                source_document_type, source_document_id, source_document_line_id,
                source_document_reference,
                counterparty_type, counterparty_id, counterparty_name, counterparty_tax_id,
                currency_code, base_amount, tax_rate, tax_amount,
                exchange_rate, functional_base_amount, functional_tax_amount,
                recoverable_amount, non_recoverable_amount,
                tax_return_box, is_included_in_return
            )
            SELECT
                si.organization_id,
                fp.fiscal_period_id,
                silt.tax_code_id,
                tc.jurisdiction_id,
                'WITHHOLDING',
                si.invoice_date,
                'AP_INVOICE',
                si.invoice_id,
                silt.line_id,
                si.invoice_number,
                'SUPPLIER',
                si.supplier_id,
                s.legal_name,
                s.tax_identification_number,
                si.currency_code,
                silt.base_amount,
                silt.tax_rate,
                silt.tax_amount,
                COALESCE(si.exchange_rate, 1),
                silt.base_amount * COALESCE(si.exchange_rate, 1),
                silt.tax_amount * COALESCE(si.exchange_rate, 1),
                0,
                silt.tax_amount,
                tc.tax_return_box,
                false
            FROM ap.supplier_invoice_line_tax silt
            JOIN ap.supplier_invoice_line sil ON sil.line_id = silt.line_id
            JOIN ap.supplier_invoice si ON si.invoice_id = sil.invoice_id
            JOIN ap.supplier s ON s.supplier_id = si.supplier_id
            JOIN LATERAL (
                SELECT fiscal_period_id
                FROM gl.fiscal_period fp
                WHERE fp.organization_id = si.organization_id
                  AND si.invoice_date BETWEEN fp.start_date AND fp.end_date
                ORDER BY fp.start_date DESC, fp.fiscal_period_id
                LIMIT 1
            ) fp ON true
            JOIN tax.tax_code tc ON tc.tax_code_id = silt.tax_code_id
            WHERE si.organization_id = :org_id
              AND si.status NOT IN ('DRAFT', 'VOID')
              AND tc.tax_type = 'WITHHOLDING'
        """),
        {"org_id": org_id},
    )

    result.details["rows_inserted"] = inserted.rowcount
    result.log_summary()
    return result


# ── Also re-backfill INPUT tax transactions (VAT on AP) ──────────────────


def step4b_rebackfill_input_tax(
    db: Session,
    org_id: UUID,
    *,
    dry_run: bool,
) -> StepResult:
    """Re-backfill INPUT tax transactions from updated supplier_invoice_line_tax."""
    result = StepResult(step=4, name="Re-backfill INPUT tax (AP VAT)", dry_run=dry_run)

    # Delete existing INPUT AP transactions (they were from old incomplete data)
    existing_row = db.execute(
        text("""
            SELECT COUNT(*)
            FROM tax.tax_transaction
            WHERE organization_id = :org_id
              AND transaction_type = 'INPUT'
              AND source_document_type = 'AP_INVOICE'
        """),
        {"org_id": org_id},
    ).fetchone()
    existing_count = existing_row[0] if existing_row else 0
    result.details["existing_input_transactions"] = existing_count

    # Count new source rows
    count_row = db.execute(
        text("""
            SELECT COUNT(*)
            FROM ap.supplier_invoice_line_tax silt
            JOIN ap.supplier_invoice_line sil ON sil.line_id = silt.line_id
            JOIN ap.supplier_invoice si ON si.invoice_id = sil.invoice_id
            JOIN tax.tax_code tc ON tc.tax_code_id = silt.tax_code_id
            WHERE si.organization_id = :org_id
              AND si.status NOT IN ('DRAFT', 'VOID')
              AND tc.tax_type = 'VAT'
        """),
        {"org_id": org_id},
    ).fetchone()
    source_count = count_row[0] if count_row else 0
    result.details["vat_line_tax_rows"] = source_count

    if dry_run:
        result.details["would_delete"] = existing_count
        result.details["would_insert"] = source_count
        result.log_summary()
        return result

    # Delete stale
    if existing_count > 0:
        deleted = db.execute(
            text("""
                DELETE FROM tax.tax_transaction
                WHERE organization_id = :org_id
                  AND transaction_type = 'INPUT'
                  AND source_document_type = 'AP_INVOICE'
            """),
            {"org_id": org_id},
        )
        result.details["deleted"] = deleted.rowcount

    if source_count == 0:
        result.details["action"] = "No VAT line tax records to process"
        result.log_summary()
        return result

    # Bulk INSERT
    inserted = db.execute(
        text("""
            INSERT INTO tax.tax_transaction (
                organization_id, fiscal_period_id, tax_code_id, jurisdiction_id,
                transaction_type, transaction_date,
                source_document_type, source_document_id, source_document_line_id,
                source_document_reference,
                counterparty_type, counterparty_id, counterparty_name, counterparty_tax_id,
                currency_code, base_amount, tax_rate, tax_amount,
                exchange_rate, functional_base_amount, functional_tax_amount,
                recoverable_amount, non_recoverable_amount,
                tax_return_box, is_included_in_return
            )
            SELECT
                si.organization_id,
                fp.fiscal_period_id,
                silt.tax_code_id,
                tc.jurisdiction_id,
                'INPUT',
                si.invoice_date,
                'AP_INVOICE',
                si.invoice_id,
                silt.line_id,
                si.invoice_number,
                'SUPPLIER',
                si.supplier_id,
                s.legal_name,
                s.tax_identification_number,
                si.currency_code,
                silt.base_amount,
                silt.tax_rate,
                silt.tax_amount,
                COALESCE(si.exchange_rate, 1),
                silt.base_amount * COALESCE(si.exchange_rate, 1),
                silt.tax_amount * COALESCE(si.exchange_rate, 1),
                silt.tax_amount * COALESCE(tc.recovery_rate, 1),
                silt.tax_amount * (1 - COALESCE(tc.recovery_rate, 1)),
                tc.tax_return_box,
                false
            FROM ap.supplier_invoice_line_tax silt
            JOIN ap.supplier_invoice_line sil ON sil.line_id = silt.line_id
            JOIN ap.supplier_invoice si ON si.invoice_id = sil.invoice_id
            JOIN ap.supplier s ON s.supplier_id = si.supplier_id
            JOIN LATERAL (
                SELECT fiscal_period_id
                FROM gl.fiscal_period fp
                WHERE fp.organization_id = si.organization_id
                  AND si.invoice_date BETWEEN fp.start_date AND fp.end_date
                ORDER BY fp.start_date DESC, fp.fiscal_period_id
                LIMIT 1
            ) fp ON true
            JOIN tax.tax_code tc ON tc.tax_code_id = silt.tax_code_id
            WHERE si.organization_id = :org_id
              AND si.status NOT IN ('DRAFT', 'VOID')
              AND tc.tax_type = 'VAT'
        """),
        {"org_id": org_id},
    )

    result.details["rows_inserted"] = inserted.rowcount
    result.log_summary()
    return result


# ── Main ─────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Resync ERPNext purchase invoices with WHT extraction"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--dry-run", action="store_true", help="Report what would be done"
    )
    group.add_argument("--execute", action="store_true", help="Actually run the resync")
    parser.add_argument("--step", type=int, help="Run only a specific step (1-4)")
    args = parser.parse_args()

    dry_run = args.dry_run

    # Step 1 always runs (read ERPNext data)
    step1_result, invoices = step1_read_erpnext(dry_run=dry_run)

    with SessionLocal() as db:
        org_id = get_org_id(db)
        logger.info("Organization: %s", org_id)
        logger.info("Mode: %s", "DRY RUN" if dry_run else "EXECUTE")
        logger.info("")

        results: list[StepResult] = [step1_result]

        if args.step is None or args.step == 2:
            results.append(step2_update_existing(db, org_id, invoices, dry_run=dry_run))

        if args.step is None or args.step == 3:
            results.append(step3_create_missing(db, org_id, invoices, dry_run=dry_run))

        if args.step is None or args.step == 4:
            results.append(step4_backfill_wht_transactions(db, org_id, dry_run=dry_run))
            results.append(step4b_rebackfill_input_tax(db, org_id, dry_run=dry_run))

        if not dry_run:
            db.commit()
            logger.info("All changes committed.")
        else:
            logger.info("DRY RUN complete — no changes made.")

        # Summary
        logger.info("")
        logger.info("=" * 60)
        logger.info("SUMMARY")
        logger.info("=" * 60)
        for r in results:
            logger.info("  Step %d: %s", r.step, r.name)
            for k, v in r.details.items():
                logger.info("    %-35s %s", k, v)


if __name__ == "__main__":
    main()
