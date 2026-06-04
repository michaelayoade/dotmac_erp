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
    InvoiceStatus,
    InvoiceType,
)
from app.services.splynx.client import SplynxCreditNote, SplynxError

from ._constants import (
    SPLYNX_SYNC_MIN_DATE,
    SYSTEM_USER_ID,
    _PRE_CUTOFF_SENTINEL,
)
from ._types import SyncResult

logger = logging.getLogger(__name__)


class CreditNoteSyncMixin:
    """Credit note sync operations."""

    # Provided by other mixins at runtime
    db: Any
    organization_id: UUID
    client: Any
    ar_control_account_id: UUID
    _customer_cache: dict[int, UUID]

    # Methods from other mixins
    _compute_hash: Any
    _has_changed: Any
    _record_sync: Any
    _get_synced_entity: Any
    _get_existing_invoice: Any
    _get_or_create_customer_id: Any
    _load_customer_cache: Any
    _parse_date: Any
    _generate_credit_note_number: Any
    _extract_tax: Any
    _create_invoice_lines: Any
    _replace_invoice_lines: Any
    _reprime_tenant_context: Any

    def sync_credit_notes(
        self,
        date_from: date | None = None,
        date_to: date | None = None,
        created_by_user_id: UUID | None = None,
        batch_size: int | None = None,
        skip_unchanged: bool = True,
    ) -> SyncResult:
        """Sync credit notes from Splynx."""
        result = SyncResult(success=True, entity_type="credit_notes")
        processed = 0

        if not self._customer_cache:
            self._load_customer_cache()

        try:
            for splynx_cn in self.client.get_credit_notes(
                date_from=date_from,
                date_to=date_to,
            ):
                if batch_size and processed >= batch_size:
                    result.message = f"Batch limit ({batch_size}) reached"
                    break

                try:
                    savepoint = self.db.begin_nested()
                    self._sync_single_credit_note(
                        splynx_cn,
                        created_by_user_id,
                        result,
                    )
                    savepoint.commit()
                    processed += 1

                    if processed % 500 == 0:
                        self.db.commit()
                        self._reprime_tenant_context()
                        self.db.expunge_all()
                        logger.info(
                            "Progress: %d credit notes processed",
                            processed,
                        )

                except Exception as e:
                    try:
                        savepoint.rollback()
                    except Exception:
                        self.db.rollback()
                    result.errors.append(f"Credit Note {splynx_cn.number}: {e!s}")
                    logger.exception(
                        "Error syncing credit note %s",
                        splynx_cn.number,
                    )

            self.db.flush()
            result.message = (
                f"Synced {result.created} new, "
                f"{result.updated} updated, "
                f"{result.skipped} skipped credit notes"
            )
            logger.info(result.message)

        except SplynxError as e:
            result.success = False
            result.message = f"Splynx API error: {e.message}"
            result.errors.append(result.message)
            logger.error(result.message)

        return result

    def _sync_single_credit_note(
        self,
        splynx_cn: SplynxCreditNote,
        created_by_user_id: UUID | None,
        result: SyncResult,
    ) -> None:
        """Sync a single credit note."""
        external_id = str(splynx_cn.id)

        data_hash = self._compute_hash(
            {
                "number": splynx_cn.number,
                "total": str(splynx_cn.total),
                "status": splynx_cn.status,
                "date_created": splynx_cn.date_created,
                "note": splynx_cn.note,
            }
        )

        local_id = self._get_synced_entity(EntityType.CREDIT_NOTE, external_id)
        existing = None
        if local_id:
            existing = self.db.get(Invoice, local_id)
        if not existing:
            stmt = select(Invoice).where(
                Invoice.organization_id == self.organization_id,
                Invoice.splynx_id == str(splynx_cn.id),
                Invoice.invoice_type == InvoiceType.CREDIT_NOTE,
            )
            existing = self.db.scalar(stmt)
        if not existing:
            existing = self._get_existing_invoice(f"SPL-CN-{splynx_cn.id}")

        customer_id = self._get_or_create_customer_id(splynx_cn.customer_id)
        if not customer_id:
            result.skipped += 1
            result.errors.append(
                f"Credit Note {splynx_cn.number}: Customer "
                f"{splynx_cn.customer_id} not synced"
            )
            return

        cn_date = self._parse_date(splynx_cn.date_created) or date.today()

        if cn_date < SPLYNX_SYNC_MIN_DATE:
            self._record_sync(
                EntityType.CREDIT_NOTE,
                str(splynx_cn.id),
                _PRE_CUTOFF_SENTINEL,
            )
            result.skipped += 1
            return

        cn_subtotal, cn_tax = self._extract_tax(splynx_cn.total)
        # Credit notes follow the canonical negative-amount convention used by
        # the native AR creator (app/services/finance/ar/invoice.py) and assumed
        # by the AR poster and the day-book reports. Splynx sends positive
        # totals, so negate the header amounts here (line amounts are negated in
        # _create_invoice_lines / _replace_invoice_lines via is_credit_note).
        cn_subtotal = -abs(cn_subtotal)
        cn_tax = -abs(cn_tax)
        cn_total = -abs(splynx_cn.total)

        if existing:
            existing.customer_id = customer_id
            existing.invoice_date = cn_date
            existing.due_date = cn_date
            existing.subtotal = cn_subtotal
            existing.tax_amount = cn_tax
            existing.total_amount = cn_total
            existing.functional_currency_amount = cn_total
            existing.notes = splynx_cn.note
            existing.splynx_id = str(splynx_cn.id)
            existing.splynx_number = splynx_cn.number
            existing.last_synced_at = datetime.now(UTC)

            self._replace_invoice_lines(
                existing.invoice_id,
                splynx_cn,
                is_credit_note=True,
            )

            result.updated += 1
            self._record_sync(
                EntityType.CREDIT_NOTE,
                external_id,
                existing.invoice_id,
                data_hash,
            )
        else:
            invoice_number = self._generate_credit_note_number(cn_date)

            invoice = Invoice(
                organization_id=self.organization_id,
                customer_id=customer_id,
                invoice_number=invoice_number,
                invoice_type=InvoiceType.CREDIT_NOTE,
                invoice_date=cn_date,
                due_date=cn_date,
                currency_code=(settings.default_functional_currency_code),
                subtotal=cn_subtotal,
                tax_amount=cn_tax,
                total_amount=cn_total,
                amount_paid=Decimal("0"),
                functional_currency_amount=cn_total,
                status=InvoiceStatus.POSTED,
                ar_control_account_id=(self.ar_control_account_id),
                source_document_type="splynx_credit_note",
                correlation_id=(f"splynx-cn-{splynx_cn.id}"),
                notes=splynx_cn.note,
                internal_notes=(f"Imported from Splynx. Original ID: {splynx_cn.id}"),
                created_by_user_id=(created_by_user_id or SYSTEM_USER_ID),
                splynx_id=str(splynx_cn.id),
                splynx_number=splynx_cn.number,
                last_synced_at=datetime.now(UTC),
            )
            self.db.add(invoice)
            self.db.flush()

            self._create_invoice_lines(
                invoice.invoice_id,
                splynx_cn,
                is_credit_note=True,
            )

            result.created += 1
            self._record_sync(
                EntityType.CREDIT_NOTE,
                external_id,
                invoice.invoice_id,
                data_hash,
            )
