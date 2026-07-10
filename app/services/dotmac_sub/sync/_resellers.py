from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlalchemy import func, select

from app.models.finance.ar.customer import Customer, CustomerType
from app.models.finance.ar.external_sync import EntityType
from app.services.dotmac_sub.client import DotmacSubError, ResellerRecord

from ._types import SyncResult

logger = logging.getLogger(__name__)


class ResellerSyncMixin:
    """Sync dotmac_sub resellers → ERP parent customers (no parent_customer_id)."""

    db: Any
    client: Any
    organization_id: UUID
    ar_control_account_id: UUID
    default_revenue_account_id: UUID | None
    _reseller_cache: dict[str, UUID]
    SOURCE_PREFIX: str

    _compute_hash: Any
    _has_changed: Any
    _record_sync: Any
    _get_synced_entity: Any
    _reprime_tenant_context: Any
    _customer_code: Any

    def sync_resellers(
        self,
        created_by_user_id: UUID | None = None,
        skip_unchanged: bool = True,
    ) -> SyncResult:
        result = SyncResult(success=True, entity_type="resellers")
        processed = 0
        try:
            for reseller in self.client.get_resellers():
                try:
                    savepoint = self.db.begin_nested()
                    self._sync_single_reseller(
                        reseller, created_by_user_id, result, skip_unchanged
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
                    result.errors.append(f"Reseller {reseller.id}: {e!s}")
                    logger.exception("Error syncing reseller %s", reseller.id)
            self.db.flush()
            result.message = (
                f"Synced {result.created} new, {result.updated} updated, "
                f"{result.skipped} skipped resellers"
            )
        except DotmacSubError as e:
            result.success = False
            result.message = f"dotmac_sub API error: {e.message}"
            result.errors.append(result.message)
            logger.error(result.message)
        return result

    def _sync_single_reseller(
        self,
        reseller: ResellerRecord,
        created_by_user_id: UUID | None,
        result: SyncResult,
        skip_unchanged: bool,
    ) -> None:
        external_id = reseller.id
        data_hash = self._compute_hash(
            {
                "name": reseller.name,
                "code": reseller.code,
                "email": reseller.contact_email,
                "phone": reseller.contact_phone,
                "is_active": reseller.is_active,
            }
        )
        if skip_unchanged and not self._has_changed(
            EntityType.RESELLER, external_id, data_hash
        ):
            self._cache_reseller(external_id)
            result.skipped += 1
            return

        existing = self._get_reseller_customer(external_id)
        promoted = False
        if not existing:
            # Merge-on-promotion: a reseller created by promoting an existing
            # subscriber is the same real-world entity as that subscriber's
            # customer row. Promote the row into the reseller parent instead
            # of creating an empty duplicate — its AR history then belongs to
            # the reseller without touching any posted document.
            existing = self._find_promotable_customer(reseller)
            promoted = existing is not None
        if existing:
            if promoted:
                existing.customer_type = CustomerType.COMPANY
                logger.info(
                    "Promoting existing customer %s into reseller %s parent "
                    "(matched by contact email)",
                    existing.customer_id,
                    external_id,
                )
            existing.legal_name = reseller.name
            existing.is_active = reseller.is_active
            existing.primary_contact = {
                "email": reseller.contact_email,
                "phone": reseller.contact_phone,
                "dotmac_sub_reseller_id": reseller.id,
            }
            if not existing.dotmac_sub_id:
                existing.dotmac_sub_id = reseller.id
            existing.dotmac_sub_reseller_id = reseller.id
            self._reseller_cache[external_id] = existing.customer_id
            result.updated += 1
            self._record_sync(
                EntityType.RESELLER, external_id, existing.customer_id, data_hash
            )
            return

        customer = Customer(
            organization_id=self.organization_id,
            customer_code=self._customer_code("R", reseller.id),
            customer_type=CustomerType.COMPANY,
            legal_name=reseller.name,
            trading_name=reseller.code,
            is_active=reseller.is_active,
            ar_control_account_id=self.ar_control_account_id,
            default_revenue_account_id=self.default_revenue_account_id,
            primary_contact={
                "email": reseller.contact_email,
                "phone": reseller.contact_phone,
                "dotmac_sub_reseller_id": reseller.id,
            },
            dotmac_sub_id=reseller.id,
            dotmac_sub_reseller_id=reseller.id,
            created_by_user_id=created_by_user_id,
        )
        self.db.add(customer)
        self.db.flush()
        self._reseller_cache[external_id] = customer.customer_id
        result.created += 1
        self._record_sync(
            EntityType.RESELLER, external_id, customer.customer_id, data_hash
        )

    def _find_promotable_customer(self, reseller: ResellerRecord) -> Customer | None:
        """Find the subscriber-created customer that IS this reseller, if any.

        Matched by the reseller's contact email against the customer's
        ``primary_contact.email``; only an unambiguous single parentless,
        not-yet-reseller, sub-synced match promotes — anything else falls
        back to creating a fresh parent.
        """
        email = (reseller.contact_email or "").strip().lower()
        if not email:
            return None
        stmt = (
            select(Customer)
            .where(
                Customer.organization_id == self.organization_id,
                Customer.parent_customer_id.is_(None),
                Customer.dotmac_sub_reseller_id.is_(None),
                Customer.dotmac_sub_id.isnot(None),
                func.lower(Customer.primary_contact["email"].astext) == email,
            )
            .limit(2)
        )
        matches = list(self.db.scalars(stmt))
        if len(matches) != 1:
            return None
        return matches[0]

    def _get_reseller_customer(self, reseller_id: str) -> Customer | None:
        local_id = self._get_synced_entity(EntityType.RESELLER, reseller_id)
        if local_id:
            cust = self.db.get(Customer, local_id)
            if cust:
                return cust
        stmt = select(Customer).where(
            Customer.organization_id == self.organization_id,
            Customer.dotmac_sub_reseller_id == reseller_id,
            Customer.parent_customer_id.is_(None),
        )
        return self.db.scalar(stmt)

    def _cache_reseller(self, reseller_id: str) -> None:
        if reseller_id in self._reseller_cache:
            return
        cust = self._get_reseller_customer(reseller_id)
        if cust:
            self._reseller_cache[reseller_id] = cust.customer_id
