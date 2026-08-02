from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

try:
    from datetime import UTC  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover
    UTC = timezone.utc  # type: ignore[assignment]

from sqlalchemy import select

from app.models.finance.ar.external_sync import EntityType
from app.models.finance.ar.invoice import Invoice, InvoiceStatus, InvoiceType
from app.services.dotmac_sub.client import (
    CreditNoteRecord,
    DotmacSubError,
    DotmacSubParseError,
)

from ._base import next_watermark
from ._constants import (
    DOTMAC_SUB_SYNC_MIN_DATE,
    SYSTEM_USER_ID,
    _PRE_CUTOFF_SENTINEL,
)
from ._types import SyncResult

logger = logging.getLogger(__name__)


class CreditNoteSyncMixin:
    """Sync dotmac_sub credit notes → ERP credit-note invoices (negative)."""

    db: Any
    client: Any
    organization_id: UUID
    ar_control_account_id: UUID

    _compute_hash: Any
    _has_changed: Any
    _record_sync: Any
    _get_synced_entity: Any
    _get_customer_for_account: Any
    _parse_date: Any
    _parse_datetime: Any
    _get_sync_watermark: Any
    _advance_sync_watermark: Any
    _generate_credit_note_number: Any
    _extract_tax: Any
    _create_lines: Any
    _replace_lines: Any
    _posted_invoice_accounting_changed: Any
    _reverse_posted_invoice_gl: Any
    _ensure_synced_invoice_posted: Any
    _reprime_tenant_context: Any
    _functional_amount: Any

    def sync_credit_notes(
        self,
        account_id: str | None = None,
        status: str | None = None,
        created_by_user_id: UUID | None = None,
        batch_size: int | None = None,
        skip_unchanged: bool = True,
    ) -> SyncResult:
        result = SyncResult(success=True, entity_type="credit_notes")
        processed = 0
        use_watermark = account_id is None and status is None
        watermark = (
            self._get_sync_watermark(EntityType.CREDIT_NOTE) if use_watermark else None
        )
        updated_since = watermark.isoformat() if watermark else None
        max_ok: datetime | None = None
        min_error: datetime | None = None

        def _on_parse_error(exc: DotmacSubParseError) -> None:
            # A row asserted unusable money facts (strict client parser).
            # Fail THAT row exactly like a savepoint-failed row — recorded,
            # watermark held at it for retry — while the pull continues.
            nonlocal min_error
            result.errors.append(str(exc))
            logger.error("Rejected dotmac_sub credit-note row at parse: %s", exc)
            parse_row_updated_at = self._parse_datetime(exc.updated_at)
            if parse_row_updated_at is not None:
                min_error = (
                    parse_row_updated_at
                    if min_error is None
                    else min(min_error, parse_row_updated_at)
                )

        try:
            for cn in self.client.get_credit_notes(
                account_id=account_id,
                status=status,
                updated_since=updated_since,
                on_parse_error=_on_parse_error,
            ):
                if batch_size and processed >= batch_size:
                    result.message = f"Batch limit ({batch_size}) reached"
                    break
                row_updated_at = self._parse_datetime(cn.updated_at)
                try:
                    savepoint = self.db.begin_nested()
                    self._sync_single_credit_note(
                        cn, created_by_user_id, result, skip_unchanged
                    )
                    savepoint.commit()
                    processed += 1
                    if row_updated_at is not None:
                        max_ok = (
                            row_updated_at
                            if max_ok is None
                            else max(max_ok, row_updated_at)
                        )
                    if processed % 500 == 0:
                        self.db.commit()
                        self._reprime_tenant_context()
                        self.db.expunge_all()
                except Exception as e:  # noqa: BLE001
                    try:
                        savepoint.rollback()
                    except Exception:  # noqa: BLE001
                        self.db.rollback()
                    result.errors.append(f"Credit note {cn.credit_number}: {e!s}")
                    logger.exception("Error syncing credit note %s", cn.id)
                    if row_updated_at is not None:
                        min_error = (
                            row_updated_at
                            if min_error is None
                            else min(min_error, row_updated_at)
                        )
            if use_watermark:
                self._advance_sync_watermark(
                    EntityType.CREDIT_NOTE,
                    next_watermark(watermark, max_ok, min_error),
                )
            self.db.flush()
            result.message = (
                f"Synced {result.created} new, {result.updated} updated, "
                f"{result.skipped} skipped credit notes"
            )
        except DotmacSubError as e:
            result.success = False
            result.message = f"dotmac_sub API error: {e.message}"
            result.errors.append(result.message)
            logger.error(result.message)
        return result

    def _sync_single_credit_note(
        self,
        cn: CreditNoteRecord,
        created_by_user_id: UUID | None,
        result: SyncResult,
        skip_unchanged: bool,
    ) -> None:
        external_id = cn.id
        data_hash = self._compute_hash(
            {
                "account_id": cn.account_id,
                "number": cn.credit_number,
                "currency": cn.currency,
                "subtotal": str(cn.subtotal),
                "tax_total": str(cn.tax_total),
                "total": str(cn.total),
                "applied_total": str(cn.applied_total),
                "status": cn.status,
                "invoice_id": cn.invoice_id,
                "issued_at": cn.issued_at,
                "memo": cn.memo,
                "lines": [
                    {
                        "id": line.id,
                        "quantity": str(line.quantity),
                        "unit_price": str(line.unit_price),
                        "amount": str(line.amount),
                        "tax_rate_id": line.tax_rate_id,
                        "tax_application": line.tax_application,
                    }
                    for line in sorted(cn.lines, key=lambda item: item.id)
                ],
            }
        )
        if skip_unchanged and not self._has_changed(
            EntityType.CREDIT_NOTE, external_id, data_hash
        ):
            result.skipped += 1
            return

        source_status = (cn.status or "").lower()
        if source_status == "draft":
            result.skipped += 1
            return

        # E4 money boundary: admit only exact, currency-tagged source money
        # facts (rejects float, missing currency, excess minor-unit precision).
        # A rejection fails this row's savepoint, never the whole sync run.
        cn.boundary_money()
        if source_status == "void":
            local_status = InvoiceStatus.VOID
        elif source_status == "applied":
            local_status = InvoiceStatus.PAID
        elif source_status == "partially_applied":
            local_status = InvoiceStatus.PARTIALLY_PAID
        else:
            local_status = InvoiceStatus.POSTED

        customer_id = self._get_customer_for_account(cn.account_id)
        if not customer_id:
            result.skipped += 1
            result.errors.append(
                f"Credit note {cn.credit_number}: account {cn.account_id} "
                "not resolved to a customer"
            )
            return

        cn_subtotal = -abs(cn.subtotal)
        cn_tax = -abs(cn.tax_total)
        cn_total = -abs(cn.total)
        from datetime import date as _date

        # Use the credit note's real issue date (so it lands in the right fiscal
        # period), and honour the same historical cutoff as invoices/payments so
        # a pre-cutoff credit note isn't imported without its invoice.
        cn_date = self._parse_date(cn.issued_at) or _date.today()
        if cn_date < DOTMAC_SUB_SYNC_MIN_DATE:
            self._record_sync(EntityType.CREDIT_NOTE, external_id, _PRE_CUTOFF_SENTINEL)
            result.skipped += 1
            return

        # Convert to functional currency (was booked at face value while payments
        # convert). cn_total is already negative, so functional stays negative.
        exch_rate, functional_amount = self._functional_amount(
            cn_total, cn.currency, cn_date
        )

        local_id = self._get_synced_entity(EntityType.CREDIT_NOTE, external_id)
        existing: Invoice | None = None
        if local_id and local_id != _PRE_CUTOFF_SENTINEL:
            existing = self.db.get(Invoice, local_id)
        if not existing:
            existing = self.db.scalar(
                select(Invoice).where(
                    Invoice.organization_id == self.organization_id,
                    Invoice.dotmac_sub_id == external_id,
                    Invoice.invoice_type == InvoiceType.CREDIT_NOTE,
                )
            )

        if existing:
            if existing.journal_entry_id is not None and (
                local_status == InvoiceStatus.VOID
                or self._posted_invoice_accounting_changed(
                    existing,
                    cn,
                    invoice_date=cn_date,
                    currency_code=cn.currency,
                    exchange_rate=exch_rate,
                    functional_amount=functional_amount,
                    is_credit_note=True,
                )
            ):
                if not self._reverse_posted_invoice_gl(
                    existing,
                    created_by_user_id,
                    reason=(
                        f"dotmac_sub credit note {cn.status or 'changed'} ({cn.id})"
                    ),
                ):
                    raise ValueError(
                        "ERP could not reverse the existing credit-note journal; "
                        "the source correction was not applied"
                    )
            existing.customer_id = customer_id
            existing.invoice_date = cn_date
            existing.due_date = cn_date
            existing.currency_code = cn.currency
            existing.subtotal = cn_subtotal
            existing.tax_amount = cn_tax
            existing.total_amount = cn_total
            existing.amount_paid = -abs(cn.applied_total)
            existing.functional_currency_amount = functional_amount
            existing.exchange_rate = exch_rate
            existing.status = local_status
            existing.notes = cn.memo
            existing.dotmac_sub_id = cn.id
            existing.dotmac_sub_number = cn.credit_number
            existing.last_synced_at = datetime.now(UTC)
            self._replace_lines(existing.invoice_id, cn, is_credit_note=True)
            self._ensure_synced_invoice_posted(existing, created_by_user_id)
            result.updated += 1
            self._record_sync(
                EntityType.CREDIT_NOTE, external_id, existing.invoice_id, data_hash
            )
            return

        credit_note = Invoice(
            organization_id=self.organization_id,
            customer_id=customer_id,
            invoice_number=self._generate_credit_note_number(cn_date),
            invoice_type=InvoiceType.CREDIT_NOTE,
            invoice_date=cn_date,
            due_date=cn_date,
            currency_code=cn.currency,
            subtotal=cn_subtotal,
            tax_amount=cn_tax,
            total_amount=cn_total,
            amount_paid=-abs(cn.applied_total),
            functional_currency_amount=functional_amount,
            exchange_rate=exch_rate,
            status=local_status,
            ar_control_account_id=self.ar_control_account_id,
            source_document_type="dotmac_sub_credit_note",
            correlation_id=f"dotmac-sub-cn-{cn.id}",
            notes=cn.memo,
            internal_notes=f"Imported from dotmac_sub. Original CN ID: {cn.id}",
            created_by_user_id=created_by_user_id or SYSTEM_USER_ID,
            dotmac_sub_id=cn.id,
            dotmac_sub_number=cn.credit_number,
            last_synced_at=datetime.now(UTC),
        )
        self.db.add(credit_note)
        self.db.flush()
        self._create_lines(credit_note.invoice_id, cn, is_credit_note=True)
        self._ensure_synced_invoice_posted(credit_note, created_by_user_id)
        result.created += 1
        self._record_sync(
            EntityType.CREDIT_NOTE, external_id, credit_note.invoice_id, data_hash
        )
