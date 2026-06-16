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

from sqlalchemy import select

from app.config import settings
from app.models.finance.ar.external_sync import EntityType
from app.models.finance.ar.invoice import (
    Invoice,
    InvoiceType,
)
from app.models.finance.ar.invoice_line import InvoiceLine
from app.models.finance.ar.invoice_line_tax import InvoiceLineTax
from app.services.splynx.client import (
    SplynxCreditNote,
    SplynxError,
    SplynxInvoice,
)

from ._constants import (
    SPLYNX_SYNC_MIN_DATE,
    SYSTEM_USER_ID,
    _PRE_CUTOFF_SENTINEL,
)
from ._types import SyncResult

logger = logging.getLogger(__name__)


class InvoiceSyncMixin:
    """Invoice sync operations."""

    # Provided by other mixins at runtime
    db: Any
    organization_id: UUID
    client: Any
    ar_control_account_id: UUID
    default_revenue_account_id: UUID | None
    _customer_cache: dict[int, UUID]
    sales_tax_code: Any

    # Methods from other mixins
    _compute_hash: Any
    _has_changed: Any
    _record_sync: Any
    _get_synced_entity: Any
    _get_existing_invoice: Any
    _get_or_create_customer_id: Any
    _load_customer_cache: Any
    _parse_date: Any
    _generate_invoice_number: Any
    _map_invoice_status: Any
    _extract_tax: Any
    _create_line_tax_record: Any
    _reprime_tenant_context: Any

    def sync_invoices(
        self,
        date_from: date | None = None,
        date_to: date | None = None,
        status: str | None = None,
        created_by_user_id: UUID | None = None,
        batch_size: int | None = None,
        skip_unchanged: bool = True,
    ) -> SyncResult:
        """Sync invoices from Splynx."""
        result = SyncResult(success=True, entity_type="invoices")
        processed = 0

        if not self._customer_cache:
            self._load_customer_cache()

        try:
            for splynx_invoice in self.client.get_invoices(
                date_from=date_from,
                date_to=date_to,
                status=status,
            ):
                if batch_size and processed >= batch_size:
                    result.message = f"Batch limit ({batch_size}) reached"
                    break

                try:
                    savepoint = self.db.begin_nested()
                    self._sync_single_invoice(
                        splynx_invoice,
                        created_by_user_id,
                        result,
                        skip_unchanged,
                    )
                    savepoint.commit()
                    processed += 1

                    if processed % 500 == 0:
                        self.db.commit()
                        self._reprime_tenant_context()
                        self.db.expunge_all()
                        logger.info(
                            "Progress: %d invoices processed",
                            processed,
                        )

                except Exception as e:
                    try:
                        savepoint.rollback()
                    except Exception:
                        self.db.rollback()
                    result.errors.append(f"Invoice {splynx_invoice.number}: {e!s}")
                    logger.exception(
                        "Error syncing invoice %s",
                        splynx_invoice.number,
                    )

            self.db.flush()
            result.message = (
                f"Synced {result.created} new, "
                f"{result.updated} updated, "
                f"{result.skipped} skipped invoices"
            )
            logger.info(result.message)

        except SplynxError as e:
            result.success = False
            result.message = f"Splynx API error: {e.message}"
            result.errors.append(result.message)
            logger.error(result.message)

        return result

    def _sync_single_invoice(
        self,
        splynx_invoice: SplynxInvoice,
        created_by_user_id: UUID | None,
        result: SyncResult,
        skip_unchanged: bool = True,
    ) -> None:
        """Sync a single invoice."""
        external_id = str(splynx_invoice.id)

        data_hash = self._compute_hash(
            {
                "number": splynx_invoice.number,
                "total": str(splynx_invoice.total),
                "total_due": str(splynx_invoice.total_due),
                "status": splynx_invoice.status,
                "date_created": splynx_invoice.date_created,
            }
        )

        if skip_unchanged and not self._has_changed(
            EntityType.INVOICE, external_id, data_hash
        ):
            result.skipped += 1
            return

        local_id = self._get_synced_entity(EntityType.INVOICE, external_id)
        existing = None
        if local_id:
            existing = self.db.get(Invoice, local_id)
        if not existing:
            stmt = select(Invoice).where(
                Invoice.organization_id == self.organization_id,
                Invoice.splynx_id == str(splynx_invoice.id),
            )
            existing = self.db.scalar(stmt)
        if not existing:
            existing = self._get_existing_invoice(f"SPL-INV-{splynx_invoice.id}")

        customer_id = self._get_or_create_customer_id(splynx_invoice.customer_id)
        if not customer_id:
            result.skipped += 1
            result.errors.append(
                f"Invoice {splynx_invoice.number}: Customer "
                f"{splynx_invoice.customer_id} not synced"
            )
            return

        invoice_date = self._parse_date(splynx_invoice.date_created) or date.today()
        due_date = self._parse_date(splynx_invoice.date_till) or invoice_date

        if invoice_date < SPLYNX_SYNC_MIN_DATE:
            self._record_sync(
                EntityType.INVOICE,
                str(splynx_invoice.id),
                _PRE_CUTOFF_SENTINEL,
            )
            result.skipped += 1
            return

        amount_paid = splynx_invoice.total - splynx_invoice.total_due
        status = self._map_invoice_status(
            splynx_invoice.status, splynx_invoice.total_due
        )
        currency_code = (
            splynx_invoice.currency or settings.default_functional_currency_code
        )
        subtotal, tax_amount = self._extract_tax(splynx_invoice.total)

        if existing:
            existing.customer_id = customer_id
            existing.invoice_date = invoice_date
            existing.due_date = due_date
            existing.currency_code = currency_code
            existing.subtotal = subtotal
            existing.tax_amount = tax_amount
            existing.total_amount = splynx_invoice.total
            existing.functional_currency_amount = splynx_invoice.total
            existing.amount_paid = amount_paid
            existing.status = status
            existing.notes = splynx_invoice.note
            existing.splynx_id = str(splynx_invoice.id)
            existing.splynx_number = splynx_invoice.number
            existing.last_synced_at = datetime.now(UTC)

            self._replace_invoice_lines(
                existing.invoice_id,
                splynx_invoice,
                is_credit_note=False,
            )

            result.updated += 1
            self._record_sync(
                EntityType.INVOICE,
                external_id,
                existing.invoice_id,
                data_hash,
            )
        else:
            invoice_number = self._generate_invoice_number(invoice_date)

            invoice = Invoice(
                organization_id=self.organization_id,
                customer_id=customer_id,
                invoice_number=invoice_number,
                invoice_type=InvoiceType.STANDARD,
                invoice_date=invoice_date,
                due_date=due_date,
                currency_code=currency_code,
                subtotal=subtotal,
                tax_amount=tax_amount,
                total_amount=splynx_invoice.total,
                amount_paid=amount_paid,
                functional_currency_amount=splynx_invoice.total,
                status=status,
                ar_control_account_id=self.ar_control_account_id,
                source_document_type="splynx_invoice",
                correlation_id=(f"splynx-inv-{splynx_invoice.id}"),
                notes=splynx_invoice.note,
                internal_notes=(
                    f"Imported from Splynx. Original ID: {splynx_invoice.id}"
                ),
                created_by_user_id=(created_by_user_id or SYSTEM_USER_ID),
                splynx_id=str(splynx_invoice.id),
                splynx_number=splynx_invoice.number,
                last_synced_at=datetime.now(UTC),
            )
            self.db.add(invoice)
            self.db.flush()

            self._create_invoice_lines(
                invoice.invoice_id,
                splynx_invoice,
                is_credit_note=False,
            )

            result.created += 1
            self._record_sync(
                EntityType.INVOICE,
                external_id,
                invoice.invoice_id,
                data_hash,
            )

    def _create_invoice_lines(
        self,
        invoice_id: UUID,
        splynx_doc: SplynxInvoice | SplynxCreditNote,
        *,
        is_credit_note: bool = False,
    ) -> None:
        """Create InvoiceLine records from Splynx items array."""
        if not self.default_revenue_account_id:
            return

        tc = self.sales_tax_code
        items = getattr(splynx_doc, "items", []) or []
        label = "Credit Note" if is_credit_note else "Invoice"

        if items:
            for seq, item in enumerate(items, 1):
                qty = Decimal(str(item.get("quantity", 1)))
                price = Decimal(str(item.get("unit_price", item.get("price", 0))))
                total = Decimal(str(item.get("total", 0)))
                if total == Decimal("0") and qty and price:
                    total = qty * price
                desc = item.get("description") or item.get("service_name") or ""
                if not desc:
                    desc = f"Splynx {label} {splynx_doc.number} - line {seq}"

                line_subtotal, line_tax = self._extract_tax(total)
                if is_credit_note:
                    # Match the canonical negative-amount convention for credit
                    # notes (mirrors app/services/finance/ar/invoice.py).
                    line_subtotal = -abs(line_subtotal)
                    line_tax = -abs(line_tax)

                line = InvoiceLine(
                    invoice_id=invoice_id,
                    line_number=seq,
                    description=desc,
                    quantity=qty,
                    unit_price=price,
                    discount_percentage=Decimal("0"),
                    discount_amount=Decimal("0"),
                    line_amount=line_subtotal,
                    tax_amount=line_tax,
                    tax_code_id=(tc.tax_code_id if tc else None),
                    revenue_account_id=(self.default_revenue_account_id),
                )
                self.db.add(line)
                self.db.flush()

                self._create_line_tax_record(line.line_id, line_subtotal, line_tax)
        else:
            line_subtotal, line_tax = self._extract_tax(splynx_doc.total)
            if is_credit_note:
                # Match the canonical negative-amount convention for credit
                # notes (mirrors app/services/finance/ar/invoice.py).
                line_subtotal = -abs(line_subtotal)
                line_tax = -abs(line_tax)

            line = InvoiceLine(
                invoice_id=invoice_id,
                line_number=1,
                description=(f"Splynx {label} {splynx_doc.number}"),
                quantity=Decimal("1"),
                unit_price=line_subtotal,
                discount_percentage=Decimal("0"),
                discount_amount=Decimal("0"),
                line_amount=line_subtotal,
                tax_amount=line_tax,
                tax_code_id=tc.tax_code_id if tc else None,
                revenue_account_id=(self.default_revenue_account_id),
            )
            self.db.add(line)
            self.db.flush()

            self._create_line_tax_record(line.line_id, line_subtotal, line_tax)

    def _replace_invoice_lines(
        self,
        invoice_id: UUID,
        splynx_doc: SplynxInvoice | SplynxCreditNote,
        *,
        is_credit_note: bool = False,
    ) -> None:
        """Delete existing lines and recreate from Splynx data."""
        from sqlalchemy import delete

        tax_stmt = delete(InvoiceLineTax).where(
            InvoiceLineTax.line_id.in_(
                select(InvoiceLine.line_id).where(InvoiceLine.invoice_id == invoice_id)
            )
        )
        self.db.execute(tax_stmt)
        stmt = delete(InvoiceLine).where(InvoiceLine.invoice_id == invoice_id)
        self.db.execute(stmt)
        self._create_invoice_lines(
            invoice_id,
            splynx_doc,
            is_credit_note=is_credit_note,
        )

    def _ensure_invoice_gl_posted(
        self,
        invoice: Invoice,
        created_by_user_id: UUID | None = None,
    ) -> None:
        """Post invoice to GL if it has a postable status."""
        from app.services.finance.ar.invoice import (
            ARInvoiceService,
        )

        ARInvoiceService.ensure_gl_posted(
            self.db,
            invoice,
            posted_by_user_id=created_by_user_id,
        )
