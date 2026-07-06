from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

try:
    from datetime import UTC  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover
    UTC = timezone.utc  # type: ignore[assignment]

from sqlalchemy import delete, select

from app.models.finance.ar.external_sync import EntityType
from app.models.finance.ar.invoice import Invoice, InvoiceType
from app.models.finance.ar.invoice_line import InvoiceLine
from app.models.finance.ar.invoice_line_tax import InvoiceLineTax
from app.services.dotmac_sub.client import (
    CreditNoteRecord,
    DotmacSubError,
    InvoiceRecord,
)

from ._constants import DOTMAC_SUB_SYNC_MIN_DATE, SYSTEM_USER_ID, _PRE_CUTOFF_SENTINEL
from ._types import SyncResult

logger = logging.getLogger(__name__)


class InvoiceSyncMixin:
    """Sync dotmac_sub invoices → ERP AR subledger (not GL-posted)."""

    db: Any
    client: Any
    organization_id: UUID
    ar_control_account_id: UUID
    default_revenue_account_id: UUID | None
    sales_tax_code: Any

    _compute_hash: Any
    _has_changed: Any
    _record_sync: Any
    _get_synced_entity: Any
    _get_existing_invoice: Any
    _get_customer_for_account: Any
    _parse_date: Any
    _generate_invoice_number: Any
    _generate_credit_note_number: Any
    _map_invoice_status: Any
    _extract_tax: Any
    _create_line_tax_record: Any
    _reprime_tenant_context: Any
    _functional_amount: Any

    def sync_invoices(
        self,
        account_id: str | None = None,
        status: str | None = None,
        created_by_user_id: UUID | None = None,
        batch_size: int | None = None,
        skip_unchanged: bool = True,
    ) -> SyncResult:
        result = SyncResult(success=True, entity_type="invoices")
        processed = 0
        try:
            for inv in self.client.get_invoices(account_id=account_id, status=status):
                if batch_size and processed >= batch_size:
                    result.message = f"Batch limit ({batch_size}) reached"
                    break
                try:
                    savepoint = self.db.begin_nested()
                    self._sync_single_invoice(
                        inv, created_by_user_id, result, skip_unchanged
                    )
                    savepoint.commit()
                    processed += 1
                    if processed % 500 == 0:
                        self.db.commit()
                        self._reprime_tenant_context()
                        self.db.expunge_all()
                        logger.info("Progress: %d invoices processed", processed)
                except Exception as e:  # noqa: BLE001
                    try:
                        savepoint.rollback()
                    except Exception:  # noqa: BLE001
                        self.db.rollback()
                    result.errors.append(f"Invoice {inv.invoice_number}: {e!s}")
                    logger.exception("Error syncing invoice %s", inv.id)
            self.db.flush()
            result.message = (
                f"Synced {result.created} new, {result.updated} updated, "
                f"{result.skipped} skipped invoices"
            )
        except DotmacSubError as e:
            result.success = False
            result.message = f"dotmac_sub API error: {e.message}"
            result.errors.append(result.message)
            logger.error(result.message)
        return result

    def _sync_single_invoice(
        self,
        inv: InvoiceRecord,
        created_by_user_id: UUID | None,
        result: SyncResult,
        skip_unchanged: bool,
    ) -> None:
        external_id = inv.id
        data_hash = self._compute_hash(
            {
                "number": inv.invoice_number,
                "total": str(inv.total),
                "balance_due": str(inv.balance_due),
                "status": inv.status,
                "issued_at": inv.issued_at,
            }
        )
        if skip_unchanged and not self._has_changed(
            EntityType.INVOICE, external_id, data_hash
        ):
            result.skipped += 1
            return

        if inv.is_proforma:
            result.skipped += 1
            return

        local_id = self._get_synced_entity(EntityType.INVOICE, external_id)
        existing: Invoice | None = None
        if local_id and local_id != _PRE_CUTOFF_SENTINEL:
            existing = self.db.get(Invoice, local_id)
        if not existing:
            existing = self.db.scalar(
                select(Invoice).where(
                    Invoice.organization_id == self.organization_id,
                    Invoice.dotmac_sub_id == external_id,
                )
            )

        customer_id = self._get_customer_for_account(inv.account_id)
        if not customer_id:
            result.skipped += 1
            result.errors.append(
                f"Invoice {inv.invoice_number}: account {inv.account_id} "
                "not resolved to a customer"
            )
            return

        invoice_date = self._parse_date(inv.issued_at) or date.today()
        due_date = self._parse_date(inv.due_at) or invoice_date

        if invoice_date < DOTMAC_SUB_SYNC_MIN_DATE:
            self._record_sync(EntityType.INVOICE, external_id, _PRE_CUTOFF_SENTINEL)
            result.skipped += 1
            return

        amount_paid = inv.total - inv.balance_due
        status = self._map_invoice_status(inv.status, inv.balance_due)
        subtotal = inv.subtotal
        tax_amount = inv.tax_total
        # Convert the receivable to functional currency (was booked at face value,
        # while payments convert — so multi-currency AR never netted to zero).
        exch_rate, functional_amount = self._functional_amount(
            inv.total, inv.currency, invoice_date
        )

        if existing:
            existing.customer_id = customer_id
            existing.invoice_date = invoice_date
            existing.due_date = due_date
            existing.currency_code = inv.currency
            existing.subtotal = subtotal
            existing.tax_amount = tax_amount
            existing.total_amount = inv.total
            existing.functional_currency_amount = functional_amount
            existing.exchange_rate = exch_rate
            existing.amount_paid = amount_paid
            existing.status = status
            existing.notes = inv.memo
            existing.dotmac_sub_id = inv.id
            existing.dotmac_sub_number = inv.invoice_number
            existing.last_synced_at = datetime.now(UTC)
            self._replace_lines(existing.invoice_id, inv, is_credit_note=False)
            result.updated += 1
            self._record_sync(
                EntityType.INVOICE, external_id, existing.invoice_id, data_hash
            )
            return

        invoice = Invoice(
            organization_id=self.organization_id,
            customer_id=customer_id,
            invoice_number=self._generate_invoice_number(invoice_date),
            invoice_type=InvoiceType.STANDARD,
            invoice_date=invoice_date,
            due_date=due_date,
            currency_code=inv.currency,
            subtotal=subtotal,
            tax_amount=tax_amount,
            total_amount=inv.total,
            amount_paid=amount_paid,
            functional_currency_amount=functional_amount,
            exchange_rate=exch_rate,
            status=status,
            ar_control_account_id=self.ar_control_account_id,
            source_document_type="dotmac_sub_invoice",
            correlation_id=f"dotmac-sub-inv-{inv.id}",
            notes=inv.memo,
            internal_notes=f"Imported from dotmac_sub. Original ID: {inv.id}",
            created_by_user_id=created_by_user_id or SYSTEM_USER_ID,
            dotmac_sub_id=inv.id,
            dotmac_sub_number=inv.invoice_number,
            last_synced_at=datetime.now(UTC),
        )
        self.db.add(invoice)
        self.db.flush()
        self._create_lines(invoice.invoice_id, inv, is_credit_note=False)
        result.created += 1
        self._record_sync(
            EntityType.INVOICE, external_id, invoice.invoice_id, data_hash
        )

    def _create_lines(
        self,
        invoice_id: UUID,
        doc: InvoiceRecord | CreditNoteRecord,
        *,
        is_credit_note: bool,
    ) -> None:
        if not self.default_revenue_account_id:
            return
        tc = self.sales_tax_code
        # InvoiceLineRecord and CreditNoteLineRecord are structurally identical;
        # type as Any so the union of the two list types doesn't collapse to
        # ``list[object]`` under mypy invariance.
        lines: list[Any] = list(doc.lines)
        label = "Credit Note" if is_credit_note else "Invoice"
        number = getattr(doc, "credit_number", None) or getattr(
            doc, "invoice_number", ""
        )

        if lines:
            for seq, item in enumerate(lines, 1):
                total = item.amount or (item.quantity * item.unit_price)
                line_subtotal, line_tax = self._extract_tax(total)
                if is_credit_note:
                    line_subtotal = -abs(line_subtotal)
                    line_tax = -abs(line_tax)
                self._add_line(
                    invoice_id,
                    seq,
                    item.description or f"dotmac_sub {label} {number} - line {seq}",
                    item.quantity,
                    item.unit_price,
                    line_subtotal,
                    line_tax,
                    tc,
                )
        else:
            total = doc.total
            line_subtotal, line_tax = self._extract_tax(total)
            if is_credit_note:
                line_subtotal = -abs(line_subtotal)
                line_tax = -abs(line_tax)
            self._add_line(
                invoice_id,
                1,
                f"dotmac_sub {label} {number}",
                Decimal("1"),
                line_subtotal,
                line_subtotal,
                line_tax,
                tc,
            )

    def _add_line(
        self,
        invoice_id: UUID,
        seq: int,
        description: str,
        quantity: Decimal,
        unit_price: Decimal,
        line_amount: Decimal,
        tax_amount: Decimal,
        tc: Any,
    ) -> None:
        line = InvoiceLine(
            invoice_id=invoice_id,
            line_number=seq,
            description=description,
            quantity=quantity,
            unit_price=unit_price,
            discount_percentage=Decimal("0"),
            discount_amount=Decimal("0"),
            line_amount=line_amount,
            tax_amount=tax_amount,
            tax_code_id=tc.tax_code_id if tc else None,
            revenue_account_id=self.default_revenue_account_id,
        )
        self.db.add(line)
        self.db.flush()
        self._create_line_tax_record(line.line_id, line_amount, tax_amount)

    def _replace_lines(
        self,
        invoice_id: UUID,
        doc: InvoiceRecord | CreditNoteRecord,
        *,
        is_credit_note: bool,
    ) -> None:
        self.db.execute(
            delete(InvoiceLineTax).where(
                InvoiceLineTax.line_id.in_(
                    select(InvoiceLine.line_id).where(
                        InvoiceLine.invoice_id == invoice_id
                    )
                )
            )
        )
        self.db.execute(delete(InvoiceLine).where(InvoiceLine.invoice_id == invoice_id))
        self._create_lines(invoice_id, doc, is_credit_note=is_credit_note)
