from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlalchemy import func, or_, select

from app.models.finance.ar.customer import Customer, CustomerType
from app.models.finance.ar.external_sync import EntityType
from app.services.dotmac_sub.client import DotmacSubError, SubscriberRecord

from ._types import SyncResult

logger = logging.getLogger(__name__)

_COMPANY_CATEGORIES = {"business", "government", "ngo"}


class SubscriberSyncMixin:
    """Sync dotmac_sub subscribers → ERP customers (child of reseller, or house)."""

    db: Any
    client: Any
    organization_id: UUID
    ar_control_account_id: UUID
    default_revenue_account_id: UUID | None
    _subscriber_cache: dict[str, UUID]
    _reseller_cache: dict[str, UUID]
    SOURCE_PREFIX: str

    _compute_hash: Any
    _has_changed: Any
    _record_sync: Any
    _get_synced_entity: Any
    _get_customer_by_dotmac_sub_id: Any
    _reseller_customer_id: Any
    _reprime_tenant_context: Any

    def sync_subscribers(
        self,
        created_by_user_id: UUID | None = None,
        batch_size: int | None = None,
        skip_unchanged: bool = True,
    ) -> SyncResult:
        result = SyncResult(success=True, entity_type="subscribers")
        processed = 0
        try:
            for subscriber in self.client.get_subscribers():
                if batch_size and processed >= batch_size:
                    result.message = f"Batch limit ({batch_size}) reached"
                    break
                try:
                    savepoint = self.db.begin_nested()
                    self._sync_single_subscriber(
                        subscriber, created_by_user_id, result, skip_unchanged
                    )
                    savepoint.commit()
                    processed += 1
                    if processed % 500 == 0:
                        self.db.commit()
                        self._reprime_tenant_context()
                        self.db.expunge_all()
                        logger.info("Progress: %d subscribers processed", processed)
                except Exception as e:  # noqa: BLE001
                    try:
                        savepoint.rollback()
                    except Exception:  # noqa: BLE001
                        self.db.rollback()
                    result.errors.append(f"Subscriber {subscriber.id}: {e!s}")
                    logger.exception("Error syncing subscriber %s", subscriber.id)
            self.db.flush()
            result.message = (
                f"Synced {result.created} new, {result.updated} updated, "
                f"{result.skipped} skipped subscribers"
            )
        except DotmacSubError as e:
            result.success = False
            result.message = f"dotmac_sub API error: {e.message}"
            result.errors.append(result.message)
            logger.error(result.message)
        return result

    def _sync_single_subscriber(
        self,
        sub: SubscriberRecord,
        created_by_user_id: UUID | None,
        result: SyncResult,
        skip_unchanged: bool,
    ) -> None:
        external_id = sub.id
        data_hash = self._compute_hash(
            {
                "name": sub.full_name,
                "email": sub.email,
                "phone": sub.phone,
                "status": sub.status,
                "category": sub.category,
                "is_active": sub.is_active,
                "reseller_id": sub.reseller_id,
                "city": sub.city,
                "postal_code": sub.postal_code,
            }
        )
        if skip_unchanged and not self._has_changed(
            EntityType.CUSTOMER, external_id, data_hash
        ):
            self._cache_subscriber(external_id)
            result.skipped += 1
            return

        parent_id = (
            self._reseller_customer_id(sub.reseller_id) if sub.reseller_id else None
        )
        contact = {"email": sub.email, "phone": sub.phone, "dotmac_sub_id": sub.id}
        billing_address = {
            "street_1": sub.address_line1,
            "street_2": sub.address_line2,
            "city": sub.city,
            "region": sub.region,
            "postal_code": sub.postal_code,
            "country_code": sub.country_code,
        }
        is_company = (sub.category or "").lower() in _COMPANY_CATEGORIES

        existing = self._get_customer_by_dotmac_sub_id(sub.id)
        if not existing:
            existing = self._find_existing_subscriber(sub)

        if existing:
            existing.legal_name = sub.full_name
            existing.trading_name = sub.company_name
            existing.is_active = sub.is_active
            existing.tax_identification_number = sub.tax_id
            existing.primary_contact = contact
            existing.billing_address = billing_address
            if not existing.dotmac_sub_id:
                existing.dotmac_sub_id = sub.id
            existing.dotmac_sub_reseller_id = sub.reseller_id
            if parent_id and parent_id != existing.customer_id:
                existing.parent_customer_id = parent_id
            self._subscriber_cache[external_id] = existing.customer_id
            result.updated += 1
            self._record_sync(
                EntityType.CUSTOMER, external_id, existing.customer_id, data_hash
            )
            return

        customer = Customer(
            organization_id=self.organization_id,
            customer_code=f"{self.SOURCE_PREFIX}-{sub.account_number or sub.id}",
            customer_type=(
                CustomerType.COMPANY if is_company else CustomerType.INDIVIDUAL
            ),
            legal_name=sub.full_name,
            trading_name=sub.company_name,
            is_active=sub.is_active,
            tax_identification_number=sub.tax_id,
            ar_control_account_id=self.ar_control_account_id,
            default_revenue_account_id=self.default_revenue_account_id,
            primary_contact=contact,
            billing_address=billing_address,
            dotmac_sub_id=sub.id,
            dotmac_sub_reseller_id=sub.reseller_id,
            parent_customer_id=parent_id,
            created_by_user_id=created_by_user_id,
        )
        self.db.add(customer)
        self.db.flush()
        self._subscriber_cache[external_id] = customer.customer_id
        result.created += 1
        self._record_sync(
            EntityType.CUSTOMER, external_id, customer.customer_id, data_hash
        )

    def _find_existing_subscriber(self, sub: SubscriberRecord) -> Customer | None:
        email = (sub.email or "").strip().lower()
        phone = (sub.phone or "").strip().lower()
        filters = []
        if email:
            try:
                filters.append(
                    func.lower(Customer.primary_contact["email"].astext) == email
                )
            except NotImplementedError:
                pass
        if phone:
            try:
                filters.append(
                    func.lower(Customer.primary_contact["phone"].astext) == phone
                )
            except NotImplementedError:
                pass
        if filters:
            stmt = select(Customer).where(
                Customer.organization_id == self.organization_id, or_(*filters)
            )
            match = self.db.scalar(stmt)
            if match:
                return match

        name = (sub.full_name or "").strip()
        if name:
            stmt = select(Customer).where(
                Customer.organization_id == self.organization_id,
                func.lower(Customer.legal_name) == name.lower(),
            )
            matches = list(self.db.scalars(stmt).all())
            if len(matches) == 1:
                return matches[0]
        return None

    def _cache_subscriber(self, subscriber_id: str) -> None:
        if subscriber_id in self._subscriber_cache:
            return
        cust = self._get_customer_by_dotmac_sub_id(subscriber_id)
        if cust:
            self._subscriber_cache[subscriber_id] = cust.customer_id
