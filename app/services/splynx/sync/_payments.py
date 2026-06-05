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
from app.models.finance.ar.customer import Customer
from app.models.finance.ar.customer_payment import (
    CustomerPayment,
    PaymentStatus,
)
from app.models.finance.ar.external_sync import EntityType
from app.models.finance.ar.invoice import Invoice, InvoiceStatus
from app.models.finance.ar.payment_allocation import (
    PaymentAllocation,
)
from app.services.splynx.client import SplynxError, SplynxPayment

from ._constants import (
    SPLYNX_SYNC_MIN_DATE,
    SYSTEM_USER_ID,
    _PRE_CUTOFF_SENTINEL,
)
from ._types import SyncResult

logger = logging.getLogger(__name__)


class PaymentSyncMixin:
    """Payment sync operations."""

    # Provided by other mixins at runtime
    db: Any
    organization_id: UUID
    client: Any
    _customer_cache: dict[int, UUID]

    # Methods from other mixins
    _compute_hash: Any
    _has_changed: Any
    _record_sync: Any
    _get_synced_entity: Any
    _get_or_create_customer_id: Any
    _load_customer_cache: Any
    _load_payment_methods: Any
    _get_bank_account_for_payment: Any
    _map_payment_method: Any
    _get_payment_method_name: Any
    _parse_date: Any
    _generate_payment_number: Any
    _reprime_tenant_context: Any

    def sync_payments(
        self,
        date_from: date | None = None,
        date_to: date | None = None,
        created_by_user_id: UUID | None = None,
        batch_size: int | None = None,
        skip_unchanged: bool = True,
    ) -> SyncResult:
        """Sync payments from Splynx."""
        result = SyncResult(success=True, entity_type="payments")
        processed = 0

        self._load_payment_methods()

        if not self._customer_cache:
            self._load_customer_cache()

        try:
            for splynx_payment in self.client.get_payments(
                date_from=date_from,
                date_to=date_to,
            ):
                if batch_size and processed >= batch_size:
                    result.message = f"Batch limit ({batch_size}) reached"
                    break

                try:
                    savepoint = self.db.begin_nested()
                    self._sync_single_payment(
                        splynx_payment,
                        result,
                        created_by_user_id,
                        skip_unchanged,
                    )
                    savepoint.commit()
                    processed += 1

                    if processed % 500 == 0:
                        self.db.commit()
                        self._reprime_tenant_context()
                        self.db.expunge_all()
                        logger.info(
                            "Progress: %d payments processed",
                            processed,
                        )

                except Exception as e:
                    try:
                        savepoint.rollback()
                    except Exception:
                        self.db.rollback()
                    result.errors.append(f"Payment {splynx_payment.id}: {e!s}")
                    logger.exception(
                        "Error syncing payment %s",
                        splynx_payment.id,
                    )

            self.db.flush()
            result.message = (
                f"Synced {result.created} new, "
                f"{result.updated} updated, "
                f"{result.skipped} skipped payments"
            )
            logger.info(result.message)

        except SplynxError as e:
            result.success = False
            result.message = f"Splynx API error: {e.message}"
            result.errors.append(result.message)
            logger.error(result.message)

        return result

    def _sync_single_payment(
        self,
        splynx_payment: SplynxPayment,
        result: SyncResult,
        created_by_user_id: UUID | None = None,
        skip_unchanged: bool = True,
    ) -> None:
        """Sync a single payment."""
        external_id = str(splynx_payment.id)

        data_hash = self._compute_hash(
            {
                "invoice_id": splynx_payment.invoice_id,
                "amount": str(splynx_payment.amount),
                "date": splynx_payment.date,
                "payment_type": splynx_payment.payment_type,
                "reference": splynx_payment.reference,
            }
        )

        if skip_unchanged and not self._has_changed(
            EntityType.PAYMENT, external_id, data_hash
        ):
            result.skipped += 1
            return

        local_id = self._get_synced_entity(EntityType.PAYMENT, external_id)

        invoice: Invoice | None = None
        if splynx_payment.invoice_id:
            correlation_id = f"splynx-inv-{splynx_payment.invoice_id}"
            stmt = select(Invoice).where(
                Invoice.organization_id == self.organization_id,
                Invoice.correlation_id == correlation_id,
            )
            invoice = self.db.scalar(stmt)

            if not invoice:
                result.skipped += 1
                result.errors.append(
                    f"Payment {splynx_payment.id}: Invoice "
                    f"{splynx_payment.invoice_id} not synced"
                )
                return

        customer_id: UUID
        if invoice:
            customer_id = invoice.customer_id
            currency_code = (
                invoice.currency_code or settings.default_functional_currency_code
            )
        else:
            resolved_cust = self._get_or_create_customer_id(splynx_payment.customer_id)
            if not resolved_cust:
                result.skipped += 1
                result.errors.append(
                    f"Payment {splynx_payment.id}: Customer "
                    f"{splynx_payment.customer_id} not synced"
                )
                return
            customer_id = resolved_cust
            customer = self.db.get(Customer, customer_id)
            currency_code = (
                customer.currency_code if customer else None
            ) or settings.default_functional_currency_code

        bank_account_id = self._get_bank_account_for_payment(
            splynx_payment.payment_type, currency_code
        )
        payment_method = self._map_payment_method(splynx_payment.payment_type)
        method_name = self._get_payment_method_name(splynx_payment.payment_type)

        payment_date = self._parse_date(splynx_payment.date) or date.today()

        if payment_date < SPLYNX_SYNC_MIN_DATE:
            self._record_sync(
                EntityType.PAYMENT,
                str(splynx_payment.id),
                _PRE_CUTOFF_SENTINEL,
            )
            result.skipped += 1
            return

        payment: CustomerPayment | None = None
        if local_id:
            payment = self.db.get(CustomerPayment, local_id)

        # Fallback idempotency guard: a prior sync run or data re-import may
        # have created this payment without recording the ExternalSync
        # mapping. Match on the Splynx natural key to avoid inserting a
        # duplicate (mirrors the invoice sync). Taking the update path below
        # also re-records the mapping via _update_existing_payment, so the
        # next sync resolves it through _get_synced_entity directly.
        if payment is None:
            payment = self.db.scalar(
                select(CustomerPayment).where(
                    CustomerPayment.organization_id == self.organization_id,
                    CustomerPayment.splynx_id == external_id,
                )
            )

        if payment is not None:
            self._update_existing_payment(
                payment,
                splynx_payment,
                invoice,
                customer_id,
                payment_date,
                payment_method,
                currency_code,
                bank_account_id,
                method_name,
                external_id,
                data_hash,
                result,
            )
            return

        self._create_new_payment(
            splynx_payment,
            invoice,
            customer_id,
            payment_date,
            payment_method,
            currency_code,
            bank_account_id,
            method_name,
            external_id,
            data_hash,
            created_by_user_id,
            result,
        )

    def _update_existing_payment(
        self,
        payment: CustomerPayment,
        splynx_payment: SplynxPayment,
        invoice: Invoice | None,
        customer_id: UUID,
        payment_date: date,
        payment_method: Any,
        currency_code: str,
        bank_account_id: UUID | None,
        method_name: str,
        external_id: str,
        data_hash: str,
        result: SyncResult,
    ) -> None:
        """Update an existing payment and its allocation."""
        alloc_stmt = select(PaymentAllocation).where(
            PaymentAllocation.payment_id == payment.payment_id
        )
        allocation = self.db.scalar(alloc_stmt)
        old_allocated_amount = (
            allocation.allocated_amount if allocation else Decimal("0")
        )
        old_invoice_id = allocation.invoice_id if allocation else None

        if allocation:
            old_invoice = self.db.get(Invoice, allocation.invoice_id)
        else:
            old_invoice = None

        payment.customer_id = customer_id
        payment.payment_date = payment_date
        payment.payment_method = payment_method
        payment.currency_code = currency_code
        payment.gross_amount = splynx_payment.amount
        payment.amount = splynx_payment.amount
        payment.functional_currency_amount = splynx_payment.amount
        payment.bank_account_id = bank_account_id
        payment.reference = splynx_payment.reference or splynx_payment.receipt_number
        payment.description = (
            f"Splynx payment via {method_name}. {splynx_payment.comment or ''}"
        ).strip()
        payment.splynx_id = str(splynx_payment.id)
        payment.splynx_receipt_number = splynx_payment.receipt_number
        payment.last_synced_at = datetime.now(UTC)

        if invoice:
            if allocation:
                if allocation.invoice_id != invoice.invoice_id and old_invoice:
                    old_invoice.amount_paid = max(
                        Decimal("0"),
                        old_invoice.amount_paid - allocation.allocated_amount,
                    )
                    if old_invoice.amount_paid >= old_invoice.total_amount:
                        old_invoice.status = InvoiceStatus.PAID
                    elif old_invoice.amount_paid > Decimal("0"):
                        old_invoice.status = InvoiceStatus.PARTIALLY_PAID
                    else:
                        old_invoice.status = InvoiceStatus.POSTED

                allocation.invoice_id = invoice.invoice_id
                allocation.allocated_amount = splynx_payment.amount
                allocation.allocation_date = payment_date
            else:
                allocation = PaymentAllocation(
                    payment_id=payment.payment_id,
                    invoice_id=invoice.invoice_id,
                    allocated_amount=splynx_payment.amount,
                    allocation_date=payment_date,
                )
                self.db.add(allocation)

            if old_invoice_id == invoice.invoice_id:
                delta = splynx_payment.amount - old_allocated_amount
                invoice.amount_paid = min(
                    max(
                        Decimal("0"),
                        invoice.amount_paid + delta,
                    ),
                    invoice.total_amount,
                )
            else:
                invoice.amount_paid = min(
                    invoice.amount_paid + splynx_payment.amount,
                    invoice.total_amount,
                )

            if invoice.amount_paid >= invoice.total_amount:
                invoice.status = InvoiceStatus.PAID
            elif invoice.amount_paid > Decimal("0"):
                invoice.status = InvoiceStatus.PARTIALLY_PAID
            else:
                invoice.status = InvoiceStatus.POSTED

        self._record_sync(
            EntityType.PAYMENT,
            external_id,
            payment.payment_id,
            data_hash,
        )
        result.updated += 1

    def _create_new_payment(
        self,
        splynx_payment: SplynxPayment,
        invoice: Invoice | None,
        customer_id: UUID,
        payment_date: date,
        payment_method: Any,
        currency_code: str,
        bank_account_id: UUID | None,
        method_name: str,
        external_id: str,
        data_hash: str,
        created_by_user_id: UUID | None,
        result: SyncResult,
    ) -> None:
        """Create a new payment record."""
        payment_number = self._generate_payment_number(payment_date)

        payment = CustomerPayment(
            organization_id=self.organization_id,
            customer_id=customer_id,
            payment_number=payment_number,
            payment_date=payment_date,
            payment_method=payment_method,
            currency_code=currency_code,
            gross_amount=splynx_payment.amount,
            amount=splynx_payment.amount,
            wht_amount=Decimal("0"),
            functional_currency_amount=splynx_payment.amount,
            bank_account_id=bank_account_id,
            reference=(splynx_payment.reference or splynx_payment.receipt_number),
            description=(
                f"Splynx payment via {method_name}. {splynx_payment.comment or ''}"
            ).strip(),
            status=PaymentStatus.CLEARED,
            correlation_id=f"splynx-pmt-{splynx_payment.id}",
            created_by_user_id=(created_by_user_id or SYSTEM_USER_ID),
            splynx_id=str(splynx_payment.id),
            splynx_receipt_number=(splynx_payment.receipt_number),
            last_synced_at=datetime.now(UTC),
        )
        self.db.add(payment)
        self.db.flush()

        if invoice:
            allocation = PaymentAllocation(
                payment_id=payment.payment_id,
                invoice_id=invoice.invoice_id,
                allocated_amount=splynx_payment.amount,
                allocation_date=payment_date,
            )
            self.db.add(allocation)

            invoice.amount_paid = min(
                invoice.amount_paid + splynx_payment.amount,
                invoice.total_amount,
            )

            if invoice.amount_paid >= invoice.total_amount:
                invoice.status = InvoiceStatus.PAID
            elif invoice.amount_paid > Decimal("0"):
                invoice.status = InvoiceStatus.PARTIALLY_PAID
        else:
            logger.info(
                "Payment %s created as unapplied (no invoice_id from Splynx)",
                splynx_payment.id,
            )

        self._record_sync(
            EntityType.PAYMENT,
            external_id,
            payment.payment_id,
            data_hash,
        )
        result.created += 1

    def _ensure_payment_gl_posted(
        self,
        payment: CustomerPayment,
        created_by_user_id: UUID | None = None,
    ) -> None:
        """Post payment to GL if CLEARED but no journal entry."""
        from app.services.finance.ar.customer_payment import (
            CustomerPaymentService,
        )

        CustomerPaymentService.ensure_gl_posted(
            self.db,
            payment,
            posted_by_user_id=created_by_user_id,
        )
