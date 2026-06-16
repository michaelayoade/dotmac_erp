from __future__ import annotations

import logging
import time
from uuid import UUID

from sqlalchemy.orm import Session

from app.services.dotmac_sub.client import DotmacSubConfig

from ._bank_mapping import BankMappingMixin
from ._base import BaseSyncMixin
from ._credit_notes import CreditNoteSyncMixin
from ._invoices import InvoiceSyncMixin
from ._payments import PaymentSyncMixin
from ._resellers import ResellerSyncMixin
from ._subscribers import SubscriberSyncMixin
from ._types import FullSyncResult, SyncResult

logger = logging.getLogger(__name__)


class DotmacSubSyncService(
    BaseSyncMixin,
    BankMappingMixin,
    ResellerSyncMixin,
    SubscriberSyncMixin,
    InvoiceSyncMixin,
    PaymentSyncMixin,
    CreditNoteSyncMixin,
):
    """Sync data from dotmac_sub into the DotMac ERP AR ledger.

    Order matters: resellers (parents) → subscribers (children) → invoices →
    payments → credit notes.
    """

    def __init__(
        self,
        db: Session,
        organization_id: UUID,
        ar_control_account_id: UUID,
        default_revenue_account_id: UUID | None = None,
        config: DotmacSubConfig | None = None,
        bank_name_mapping: dict[str, str | None] | None = None,
    ) -> None:
        super().__init__(
            db=db,
            organization_id=organization_id,
            ar_control_account_id=ar_control_account_id,
            default_revenue_account_id=default_revenue_account_id,
            config=config,
            bank_name_mapping=bank_name_mapping,
        )

    def sync_all(
        self,
        created_by_user_id: UUID | None = None,
        batch_size: int | None = None,
        skip_unchanged: bool = True,
    ) -> FullSyncResult:
        start = time.time()
        logger.info("Starting full dotmac_sub sync for org %s", self.organization_id)

        resellers = self.sync_resellers(
            created_by_user_id=created_by_user_id, skip_unchanged=skip_unchanged
        )
        subscribers = self.sync_subscribers(
            created_by_user_id=created_by_user_id,
            batch_size=batch_size,
            skip_unchanged=skip_unchanged,
        )
        invoices = self.sync_invoices(
            created_by_user_id=created_by_user_id,
            batch_size=batch_size,
            skip_unchanged=skip_unchanged,
        )
        payments = self.sync_payments(
            created_by_user_id=created_by_user_id,
            batch_size=batch_size,
            skip_unchanged=skip_unchanged,
        )
        credit_notes = self.sync_credit_notes(
            created_by_user_id=created_by_user_id,
            batch_size=batch_size,
            skip_unchanged=skip_unchanged,
        )

        post_stats = self.post_unposted_payments(created_by_user_id=created_by_user_id)
        if post_stats["errors"]:
            payments.errors.extend(post_stats["errors"][:100])

        duration = time.time() - start
        total_errors = (
            len(resellers.errors)
            + len(subscribers.errors)
            + len(invoices.errors)
            + len(payments.errors)
            + len(credit_notes.errors)
        )
        logger.info(
            "Full dotmac_sub sync done in %.2fs (%d errors, %d posted, %d suppressed)",
            duration,
            total_errors,
            post_stats["posted"],
            post_stats["suppressed"],
        )
        return FullSyncResult(
            resellers=resellers,
            subscribers=subscribers,
            invoices=invoices,
            payments=payments,
            credit_notes=credit_notes,
            total_errors=total_errors,
            duration_seconds=round(duration, 2),
        )

    # ---- Targeted single-entity sync (shared with the webhook dispatcher) ----

    def sync_subscriber_by_id(
        self, subscriber_id: str, created_by_user_id: UUID | None = None
    ) -> SyncResult:
        result = SyncResult(success=True, entity_type="subscribers")
        sub = self.client.get_subscriber(subscriber_id)
        if sub.reseller_id:
            self._cache_reseller(sub.reseller_id)
        self._sync_single_subscriber(
            sub, created_by_user_id, result, skip_unchanged=False
        )
        self.db.flush()
        return result

    def sync_invoice_by_id(
        self, invoice_id: str, created_by_user_id: UUID | None = None
    ) -> SyncResult:
        result = SyncResult(success=True, entity_type="invoices")
        inv = self.client.get_invoice(invoice_id)
        self._sync_single_invoice(inv, created_by_user_id, result, skip_unchanged=False)
        self.db.flush()
        return result

    def sync_payment_by_id(
        self, payment_id: str, created_by_user_id: UUID | None = None
    ) -> SyncResult:
        result = SyncResult(success=True, entity_type="payments")
        pay = self.client.get_payment(payment_id)
        self._sync_single_payment(pay, result, created_by_user_id, skip_unchanged=False)
        self.db.flush()
        post = self.post_unposted_payments(created_by_user_id=created_by_user_id)
        if post["errors"]:
            result.errors.extend(post["errors"][:50])
        return result
