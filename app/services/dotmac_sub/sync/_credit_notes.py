from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

try:
    from datetime import UTC  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover
    UTC = timezone.utc  # type: ignore[assignment]

from sqlalchemy import select

from app.models.finance.ar.external_sync import EntityType
from app.models.finance.ar.invoice import Invoice, InvoiceStatus, InvoiceType
from app.services.dotmac_sub.client import CreditNoteRecord, DotmacSubError

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
    _generate_credit_note_number: Any
    _extract_tax: Any
    _create_lines: Any
    _replace_lines: Any
    _reprime_tenant_context: Any

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
        try:
            for cn in self.client.get_credit_notes(
                account_id=account_id, status=status
            ):
                if batch_size and processed >= batch_size:
                    result.message = f"Batch limit ({batch_size}) reached"
                    break
                try:
                    savepoint = self.db.begin_nested()
                    self._sync_single_credit_note(
                        cn, created_by_user_id, result, skip_unchanged
                    )
                    savepoint.commit()
                    processed += 1
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
                "number": cn.credit_number,
                "total": str(cn.total),
                "status": cn.status,
                "invoice_id": cn.invoice_id,
            }
        )
        if skip_unchanged and not self._has_changed(
            EntityType.CREDIT_NOTE, external_id, data_hash
        ):
            result.skipped += 1
            return

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
            existing.customer_id = customer_id
            existing.subtotal = cn_subtotal
            existing.tax_amount = cn_tax
            existing.total_amount = cn_total
            existing.functional_currency_amount = cn_total
            existing.dotmac_sub_id = cn.id
            existing.dotmac_sub_number = cn.credit_number
            existing.last_synced_at = datetime.now(UTC)
            self._replace_lines(existing.invoice_id, cn, is_credit_note=True)
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
            amount_paid=cn_total if cn.status == "applied" else Decimal("0"),
            functional_currency_amount=cn_total,
            status=InvoiceStatus.POSTED,
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
        result.created += 1
        self._record_sync(
            EntityType.CREDIT_NOTE, external_id, credit_note.invoice_id, data_hash
        )
