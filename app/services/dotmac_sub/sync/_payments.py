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
    PaymentMethod,
    PaymentStatus,
)
from app.models.finance.ar.external_sync import EntityType
from app.models.finance.ar.invoice import Invoice
from app.models.finance.ar.payment_allocation import PaymentAllocation
from app.services.dotmac_sub.client import DotmacSubError, PaymentRecord

from ._constants import DOTMAC_SUB_SYNC_MIN_DATE, SYSTEM_USER_ID, _PRE_CUTOFF_SENTINEL
from ._types import SyncResult

logger = logging.getLogger(__name__)

# dotmac_sub payment statuses that represent settled cash.
_SETTLED_STATUSES = {"succeeded", "partially_refunded"}


class PaymentSyncMixin:
    """Sync dotmac_sub payments → ERP AR receipts (wholesale GL-suppression)."""

    db: Any
    client: Any
    organization_id: UUID
    _account_cache: dict[str, UUID]

    _compute_hash: Any
    _has_changed: Any
    _record_sync: Any
    _get_synced_entity: Any
    _get_customer_for_account: Any
    _load_payment_channels: Any
    _get_bank_account_for_channel: Any
    _channel_name: Any
    _parse_date: Any
    _generate_payment_number: Any
    _reprime_tenant_context: Any

    def sync_payments(
        self,
        account_id: str | None = None,
        status: str | None = None,
        created_by_user_id: UUID | None = None,
        batch_size: int | None = None,
        skip_unchanged: bool = True,
    ) -> SyncResult:
        result = SyncResult(success=True, entity_type="payments")
        processed = 0
        self._load_payment_channels()
        try:
            for pay in self.client.get_payments(account_id=account_id, status=status):
                if batch_size and processed >= batch_size:
                    result.message = f"Batch limit ({batch_size}) reached"
                    break
                try:
                    savepoint = self.db.begin_nested()
                    self._sync_single_payment(
                        pay, result, created_by_user_id, skip_unchanged
                    )
                    savepoint.commit()
                    processed += 1
                    if processed % 500 == 0:
                        self.db.commit()
                        self._reprime_tenant_context()
                        self.db.expunge_all()
                        logger.info("Progress: %d payments processed", processed)
                except Exception as e:  # noqa: BLE001
                    try:
                        savepoint.rollback()
                    except Exception:  # noqa: BLE001
                        self.db.rollback()
                    result.errors.append(f"Payment {pay.id}: {e!s}")
                    logger.exception("Error syncing payment %s", pay.id)
            self.db.flush()
            result.message = (
                f"Synced {result.created} new, {result.updated} updated, "
                f"{result.skipped} skipped payments"
            )
        except DotmacSubError as e:
            result.success = False
            result.message = f"dotmac_sub API error: {e.message}"
            result.errors.append(result.message)
            logger.error(result.message)
        return result

    def _sync_single_payment(
        self,
        pay: PaymentRecord,
        result: SyncResult,
        created_by_user_id: UUID | None,
        skip_unchanged: bool,
    ) -> None:
        external_id = pay.id

        if (pay.status or "").lower() not in _SETTLED_STATUSES:
            result.skipped += 1
            return

        data_hash = self._compute_hash(
            {
                "amount": str(pay.amount),
                "status": pay.status,
                "paid_at": pay.paid_at,
                "account_id": pay.effective_account_id,
                "channel": pay.payment_channel_id,
                "allocations": sorted(
                    (a.invoice_id, str(a.amount)) for a in pay.allocations
                ),
            }
        )
        if skip_unchanged and not self._has_changed(
            EntityType.PAYMENT, external_id, data_hash
        ):
            result.skipped += 1
            return

        account_id = pay.effective_account_id
        if not account_id:
            result.skipped += 1
            result.errors.append(f"Payment {pay.id}: no billing account")
            return

        customer_id = self._get_customer_for_account(account_id)
        if not customer_id:
            result.skipped += 1
            result.errors.append(
                f"Payment {pay.id}: account {account_id} not resolved to a customer"
            )
            return

        payment_date = self._parse_date(pay.paid_at) or date.today()
        if payment_date < DOTMAC_SUB_SYNC_MIN_DATE:
            self._record_sync(EntityType.PAYMENT, external_id, _PRE_CUTOFF_SENTINEL)
            result.skipped += 1
            return

        customer = self.db.get(Customer, customer_id)
        currency_code = (
            (customer.currency_code if customer else None)
            or pay.currency
            or settings.default_functional_currency_code
        )
        exch_rate, functional_amount = self._functional_amount(
            pay.amount, currency_code, payment_date
        )
        bank_account_id = self._get_bank_account_for_channel(
            pay.payment_channel_id, currency_code
        )
        method = self._map_payment_method(pay.payment_channel_id)
        channel_name = self._channel_name(pay.payment_channel_id) or "dotmac_sub"

        local_id = self._get_synced_entity(EntityType.PAYMENT, external_id)
        payment: CustomerPayment | None = None
        if local_id and local_id != _PRE_CUTOFF_SENTINEL:
            payment = self.db.get(CustomerPayment, local_id)
        if payment is None:
            payment = self.db.scalar(
                select(CustomerPayment)
                .where(
                    CustomerPayment.organization_id == self.organization_id,
                    CustomerPayment.dotmac_sub_id == external_id,
                )
                .order_by(CustomerPayment.created_at)
            )

        if payment is None:
            payment = CustomerPayment(
                organization_id=self.organization_id,
                customer_id=customer_id,
                payment_number=self._generate_payment_number(payment_date),
                payment_date=payment_date,
                payment_method=method,
                currency_code=currency_code,
                gross_amount=pay.amount,
                amount=pay.amount,
                wht_amount=Decimal("0"),
                exchange_rate=exch_rate,
                functional_currency_amount=functional_amount,
                bank_account_id=bank_account_id,
                reference=pay.external_id,
                description=(
                    f"dotmac_sub payment via {channel_name}. {pay.memo or ''}"
                ).strip(),
                status=PaymentStatus.CLEARED,
                correlation_id=f"dotmac-sub-pmt-{pay.id}",
                created_by_user_id=created_by_user_id or SYSTEM_USER_ID,
                dotmac_sub_id=pay.id,
                dotmac_sub_receipt_number=pay.external_id,
                last_synced_at=datetime.now(UTC),
            )
            self.db.add(payment)
            self.db.flush()
            result.created += 1
        else:
            payment.customer_id = customer_id
            payment.payment_date = payment_date
            payment.payment_method = method
            payment.currency_code = currency_code
            payment.gross_amount = pay.amount
            payment.amount = pay.amount
            payment.exchange_rate = exch_rate
            payment.functional_currency_amount = functional_amount
            payment.bank_account_id = bank_account_id
            payment.reference = pay.external_id
            payment.dotmac_sub_id = pay.id
            payment.dotmac_sub_receipt_number = pay.external_id
            payment.last_synced_at = datetime.now(UTC)
            result.updated += 1

        self._apply_allocations(payment, pay, payment_date)
        self._record_sync(
            EntityType.PAYMENT, external_id, payment.payment_id, data_hash
        )

    def _apply_allocations(
        self, payment: CustomerPayment, pay: PaymentRecord, payment_date: date
    ) -> None:
        from sqlalchemy import delete

        # Rebuild this payment's allocation records (payment -> invoice linkage,
        # used for AR aging / GL). Deleting is a no-op when none exist.
        self.db.execute(
            delete(PaymentAllocation).where(
                PaymentAllocation.payment_id == payment.payment_id
            )
        )

        for a in pay.allocations:
            inv = self.db.scalar(
                select(Invoice).where(
                    Invoice.organization_id == self.organization_id,
                    Invoice.dotmac_sub_id == a.invoice_id,
                )
            )
            if not inv:
                continue
            self.db.add(
                PaymentAllocation(
                    payment_id=payment.payment_id,
                    invoice_id=inv.invoice_id,
                    allocated_amount=a.amount,
                    allocation_date=payment_date,
                )
            )
            # NOTE: we deliberately do NOT touch inv.amount_paid / inv.status
            # here. The invoice sync owns them and sets them from dotmac_sub's
            # authoritative balance_due (_invoices.py: amount_paid = total -
            # balance_due), which already reflects this payment. Incrementing
            # amount_paid here double-counted the paid amount and flipped
            # partially-paid invoices to PAID on first import.

    def _map_payment_method(self, channel_id: str | None) -> PaymentMethod:
        name = (self._channel_name(channel_id) or "").lower()
        if "cash" in name:
            return PaymentMethod.CASH
        if "card" in name:
            return PaymentMethod.CARD
        return PaymentMethod.BANK_TRANSFER

    def _functional_amount(
        self, amount: Decimal, currency_code: str, on_date: date
    ) -> tuple[Decimal, Decimal]:
        """Resolve ``(exchange_rate, functional_amount)`` for a synced payment.

        ``exchange_rate`` is the foreign→functional rate (functional = amount *
        rate), matching ``CustomerPayment.exchange_rate`` semantics so GL posting
        derives the correct functional amount. Falls back to ``1.0`` (no
        conversion) when the payment is already in functional currency or no SPOT
        rate is configured. Never raises — a missing rate degrades gracefully
        rather than failing the whole payment sync.
        """
        from app.services.finance.platform.fx import FXService

        info = FXService.lookup_spot_rate(
            self.db, self.organization_id, currency_code, on_date
        )
        # In lookup_spot_rate, ``from`` is the org functional currency and ``to``
        # is currency_code, so ``inverse_rate`` is currency_code → functional.
        raw = info.get("inverse_rate")
        if raw in (None, ""):
            return Decimal("1"), amount
        try:
            rate = Decimal(str(raw))
        except (ValueError, ArithmeticError):
            return Decimal("1"), amount
        if rate <= 0:
            return Decimal("1"), amount
        return rate, (amount * rate).quantize(Decimal("0.000001"))

    def post_unposted_payments(
        self, created_by_user_id: UUID | None = None
    ) -> dict[str, Any]:
        """Post CLEARED dotmac_sub payments to the GL — standard AR behaviour.

        The reseller→subscriber link is a CRM/grouping dimension only (the main
        company "dotmac" is itself a reseller), so the sync applies NO special
        wholesale suppression: every CLEARED synced payment posts to the GL
        exactly like the rest of the ERP's AR payments. ``parent_customer_id``
        stays grouping-only, consistent with existing ERP convention — this sync
        must not mutate regular GL behaviour.
        """
        from app.services.finance.ar.customer_payment import CustomerPaymentService

        stats: dict[str, Any] = {"posted": 0, "errors": []}

        stmt = select(CustomerPayment).where(
            CustomerPayment.organization_id == self.organization_id,
            CustomerPayment.status == PaymentStatus.CLEARED,
            CustomerPayment.journal_entry_id.is_(None),
            CustomerPayment.dotmac_sub_id.is_not(None),
        )
        for payment in self.db.scalars(stmt).all():
            try:
                if CustomerPaymentService.ensure_gl_posted(
                    self.db, payment, posted_by_user_id=created_by_user_id
                ):
                    stats["posted"] += 1
            except Exception as e:  # noqa: BLE001
                logger.exception("Failed to GL-post payment %s", payment.payment_id)
                stats["errors"].append(str(e))
        return stats
