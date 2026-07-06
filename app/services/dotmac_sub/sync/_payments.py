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
    _functional_amount: Any

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
            # Not settled cash (e.g. refunded / voided). If a prior sync already
            # GL-posted this payment, its receipt journal must be reversed —
            # otherwise ERP keeps the cash on the books after a refund. Handled
            # idempotently; a never-posted payment is simply skipped.
            self._handle_unsettled_payment(pay, external_id, result, created_by_user_id)
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

        payment: CustomerPayment | None = self._find_local_payment(external_id)

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
            # If this payment is already GL-posted and a GL-relevant amount is
            # changing (e.g. succeeded -> partially_refunded), the old journal no
            # longer matches. Reverse it and drop the posting link so the
            # post_unposted_payments step re-posts at the new amount. Mutating
            # the amount in place would leave the GL at the old figure while the
            # subledger shows the new one. If the reversal can't be created,
            # leave the payment (and its GL) untouched rather than diverge — the
            # next sync retries.
            if self._posted_amount_changed(payment, pay.amount, functional_amount):
                if not self._reverse_posted_payment_gl(payment, created_by_user_id):
                    result.errors.append(
                        f"Payment {external_id}: GL reversal failed on amount "
                        "change; left unchanged to avoid GL/subledger divergence"
                    )
                    return
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

    @staticmethod
    def _posted_amount_changed(
        payment: CustomerPayment, new_amount: Decimal, new_functional: Decimal
    ) -> bool:
        """True when a GL-posted payment's cash amount is materially changing —
        the case that needs a GL reversal, not an in-place mutation."""
        return payment.journal_entry_id is not None and (
            payment.amount != new_amount
            or payment.functional_currency_amount != new_functional
        )

    def _reverse_posted_payment_gl(
        self,
        payment: CustomerPayment,
        created_by_user_id: UUID | None,
        *,
        reason: str | None = None,
        idempotency_suffix: str = "resync-reversal",
    ) -> bool:
        """Reverse a posted payment's GL journal and clear its posting link.

        Used two ways: on an amount change (the caller then leaves status CLEARED
        so ``post_unposted_payments`` re-posts at the new amount), and on a refund
        (the caller then sets status REVERSED so it is NOT re-posted). ``reason``
        and ``idempotency_suffix`` distinguish the two in the GL + idempotency key.

        Returns True on success. On any failure the posting link is left intact
        so the caller can decline to mutate the payment (no GL/subledger drift).
        Reversal is idempotent on the original journal id.
        """
        from app.services.finance.gl.reversal import ReversalService

        journal_id = payment.journal_entry_id
        if journal_id is None:
            return False
        user_id = created_by_user_id or payment.created_by_user_id or SYSTEM_USER_ID
        try:
            result = ReversalService.create_reversal(
                db=self.db,
                organization_id=self.organization_id,
                original_journal_id=journal_id,
                reversal_date=date.today(),
                created_by_user_id=user_id,
                reason=reason
                or (
                    "dotmac_sub payment amount changed on resync "
                    f"({payment.dotmac_sub_id})"
                ),
                auto_post=True,
                idempotency_key=(
                    f"{self.organization_id}:AR:PAY:{payment.payment_id}"
                    f":{idempotency_suffix}:{journal_id}"
                ),
            )
        except Exception:
            logger.exception("GL reversal errored for payment %s", payment.payment_id)
            return False

        if not getattr(result, "success", False):
            logger.error(
                "GL reversal failed for payment %s: %s",
                payment.payment_id,
                getattr(result, "message", "unknown"),
            )
            return False

        payment.journal_entry_id = None
        payment.posting_batch_id = None
        logger.info(
            "Reversed GL journal %s for payment %s (%s)",
            journal_id,
            payment.payment_id,
            idempotency_suffix,
        )
        return True

    def _find_local_payment(self, external_id: str) -> CustomerPayment | None:
        """Locate a previously-synced ERP payment for a dotmac_sub payment id —
        by the sync-state link first, then by ``dotmac_sub_id``."""
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
        return payment

    def _handle_unsettled_payment(
        self,
        pay: PaymentRecord,
        external_id: str,
        result: SyncResult,
        created_by_user_id: UUID | None,
    ) -> None:
        """Handle a dotmac_sub payment that is no longer settled cash (refunded /
        voided / failed).

        If a prior sync GL-posted it, reverse that receipt journal and mark the
        payment REVERSED so ``post_unposted_payments`` (which requires CLEARED)
        won't re-post it — closing the gap where a full refund left ERP
        overstating cash forever. Idempotent: an already-reversed payment is
        recorded and skipped.
        """
        payment = self._find_local_payment(external_id)
        status_hash = self._compute_hash(
            {"status": (pay.status or "").lower(), "amount": str(pay.amount)}
        )
        if payment is None:
            # Never synced/posted here — nothing on the GL to reverse.
            result.skipped += 1
            return
        if (
            payment.status == PaymentStatus.REVERSED
            and payment.journal_entry_id is None
        ):
            self._record_sync(
                EntityType.PAYMENT, external_id, payment.payment_id, status_hash
            )
            result.skipped += 1
            return
        if payment.journal_entry_id is not None:
            if not self._reverse_posted_payment_gl(
                payment,
                created_by_user_id,
                reason=(
                    f"dotmac_sub payment {(pay.status or 'unsettled').lower()} "
                    f"({payment.dotmac_sub_id})"
                ),
                idempotency_suffix="refund-reversal",
            ):
                result.errors.append(
                    f"Payment {external_id}: GL reversal failed on "
                    f"{pay.status}; left unchanged to avoid GL/subledger divergence"
                )
                return
        payment.status = PaymentStatus.REVERSED
        payment.last_synced_at = datetime.now(UTC)
        self._record_sync(
            EntityType.PAYMENT, external_id, payment.payment_id, status_hash
        )
        result.updated += 1

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
