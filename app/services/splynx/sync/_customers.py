from __future__ import annotations

import logging
from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import func, or_, select

from app.models.finance.ar.customer import Customer, CustomerType
from app.models.finance.ar.external_sync import EntityType
from app.services.splynx.client import SplynxCustomer, SplynxError

from ._types import SyncResult

logger = logging.getLogger(__name__)


class CustomerSyncMixin:
    """Customer sync operations."""

    # Provided by BaseSyncMixin at runtime
    db: Any
    organization_id: UUID
    client: Any
    ar_control_account_id: UUID
    default_revenue_account_id: UUID | None
    _customer_cache: dict[int, UUID]
    _partner_cache: dict[int, UUID]
    SOURCE_PREFIX: str

    # Provided by BaseSyncMixin
    _compute_hash: Any
    _has_changed: Any
    _record_sync: Any
    _get_synced_entity: Any
    _get_existing_customer: Any
    _make_customer_code: Any
    _reprime_tenant_context: Any

    def sync_customers(
        self,
        date_from: date | None = None,
        date_to: date | None = None,
        created_by_user_id: UUID | None = None,
        batch_size: int | None = None,
        skip_unchanged: bool = True,
    ) -> SyncResult:
        """Sync customers from Splynx."""
        result = SyncResult(success=True, entity_type="customers")
        processed = 0

        try:
            for splynx_customer in self.client.get_customers(
                date_from=date_from,
                date_to=date_to,
            ):
                if batch_size and processed >= batch_size:
                    result.message = f"Batch limit ({batch_size}) reached"
                    break

                try:
                    savepoint = self.db.begin_nested()
                    self._sync_single_customer(
                        splynx_customer,
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
                            "Progress: %d customers processed",
                            processed,
                        )

                except Exception as e:
                    try:
                        savepoint.rollback()
                    except Exception:
                        self.db.rollback()
                    result.errors.append(f"Customer {splynx_customer.id}: {e!s}")
                    logger.exception(
                        "Error syncing customer %s",
                        splynx_customer.id,
                    )

            self.db.flush()
            result.message = (
                f"Synced {result.created} new, "
                f"{result.updated} updated, "
                f"{result.skipped} skipped customers"
            )
            logger.info(result.message)

        except SplynxError as e:
            result.success = False
            result.message = f"Splynx API error: {e.message}"
            result.errors.append(result.message)
            logger.error(result.message)

        return result

    def _sync_single_customer(
        self,
        splynx_customer: SplynxCustomer,
        created_by_user_id: UUID | None,
        result: SyncResult,
        skip_unchanged: bool = True,
    ) -> None:
        """Sync a single customer."""
        external_id = str(splynx_customer.id)

        data_hash = self._compute_hash(
            {
                "name": splynx_customer.name,
                "login": splynx_customer.login,
                "email": splynx_customer.email,
                "phone": splynx_customer.phone,
                "status": splynx_customer.status,
                "company": splynx_customer.company,
                "street_1": splynx_customer.street_1,
                "street_2": splynx_customer.street_2,
                "city": splynx_customer.city,
                "zip_code": splynx_customer.zip_code,
            }
        )

        if skip_unchanged and not self._has_changed(
            EntityType.CUSTOMER, external_id, data_hash
        ):
            result.skipped += 1
            return

        existing = self._get_customer_by_splynx_id(splynx_customer.id)
        if not existing:
            local_id = self._get_synced_entity(EntityType.CUSTOMER, external_id)
            if local_id:
                existing = self.db.get(Customer, local_id)
        if not existing:
            customer_code = self._make_customer_code(splynx_customer.id)
            existing = self._get_existing_customer(customer_code)
        if not existing:
            existing = self._find_existing_customer(splynx_customer)

        customer_code = self._make_customer_code(splynx_customer.id)

        if existing:
            existing.legal_name = splynx_customer.name or splynx_customer.login
            existing.trading_name = splynx_customer.company
            existing.is_active = splynx_customer.status == "active"
            existing.primary_contact = {
                "email": splynx_customer.email,
                "phone": splynx_customer.phone,
                "splynx_id": splynx_customer.id,
            }
            existing.billing_address = {
                "street_1": splynx_customer.street_1,
                "street_2": splynx_customer.street_2,
                "city": splynx_customer.city,
                "zip_code": splynx_customer.zip_code,
            }
            if not existing.splynx_id:
                existing.splynx_id = str(splynx_customer.id)
            result.updated += 1
            self._customer_cache[splynx_customer.id] = existing.customer_id
            self._record_sync(
                EntityType.CUSTOMER,
                external_id,
                existing.customer_id,
                data_hash,
            )
        else:
            customer = Customer(
                organization_id=self.organization_id,
                customer_code=customer_code,
                customer_type=(
                    CustomerType.INDIVIDUAL
                    if not splynx_customer.company
                    else CustomerType.COMPANY
                ),
                legal_name=(splynx_customer.name or splynx_customer.login),
                trading_name=splynx_customer.company,
                is_active=splynx_customer.status == "active",
                ar_control_account_id=self.ar_control_account_id,
                default_revenue_account_id=(self.default_revenue_account_id),
                primary_contact={
                    "email": splynx_customer.email,
                    "phone": splynx_customer.phone,
                    "splynx_id": splynx_customer.id,
                },
                billing_address={
                    "street_1": splynx_customer.street_1,
                    "street_2": splynx_customer.street_2,
                    "city": splynx_customer.city,
                    "zip_code": splynx_customer.zip_code,
                },
                splynx_id=str(splynx_customer.id),
                created_by_user_id=created_by_user_id,
            )
            self.db.add(customer)
            self.db.flush()
            result.created += 1
            self._customer_cache[splynx_customer.id] = customer.customer_id
            self._record_sync(
                EntityType.CUSTOMER,
                external_id,
                customer.customer_id,
                data_hash,
            )

        # Resolve parent from Splynx partner_id.
        # A reseller carries splynx_partner_id == its own partner_id, so
        # resolving its own partner returns itself — guard against making a
        # customer its own parent (self-referential cycle).
        target = existing if existing else customer  # noqa: F821
        partner_id = getattr(splynx_customer, "partner_id", 0)
        if partner_id:
            parent_customer_id = self._resolve_partner_parent(partner_id)
            if (
                parent_customer_id
                and parent_customer_id != target.customer_id
                and target.parent_customer_id != parent_customer_id
            ):
                target.parent_customer_id = parent_customer_id

    # -----------------------------------------------------------------
    # Customer lookup helpers
    # -----------------------------------------------------------------

    def _get_customer_by_splynx_id(self, splynx_id: int) -> Customer | None:
        """Get existing customer by splynx_id column."""
        sid = str(splynx_id)
        stmt = select(Customer).where(
            Customer.organization_id == self.organization_id,
            Customer.splynx_id.is_not(None),
            or_(
                Customer.splynx_id == sid,
                Customer.splynx_id.like(f"{sid},%"),
                Customer.splynx_id.like(f"%,{sid},%"),
                Customer.splynx_id.like(f"%,{sid}"),
            ),
        )
        result: Customer | None = self.db.scalar(stmt)
        return result

    def _resolve_partner_parent(self, partner_id: int) -> UUID | None:
        """Resolve Splynx partner_id to ERP parent customer_id."""
        if partner_id <= 1:
            return None
        if partner_id in self._partner_cache:
            return self._partner_cache[partner_id]
        stmt = select(Customer).where(
            Customer.organization_id == self.organization_id,
            Customer.splynx_partner_id == str(partner_id),
        )
        parent: Customer | None = self.db.scalar(stmt)
        if parent:
            cust_id: UUID = parent.customer_id
            self._partner_cache[partner_id] = cust_id
            return cust_id
        return None

    def _find_existing_customer(
        self, splynx_customer: SplynxCustomer
    ) -> Customer | None:
        """Try to match a Splynx customer to an existing ERP customer."""
        name = (splynx_customer.name or splynx_customer.login or "").strip()
        email = (splynx_customer.email or "").strip().lower()
        phone = (splynx_customer.phone or "").strip().lower()

        if splynx_customer.id:
            try:
                stmt = select(Customer).where(
                    Customer.organization_id == self.organization_id,
                    Customer.primary_contact["splynx_id"].astext
                    == str(splynx_customer.id),
                )
                customer: Customer | None = self.db.scalar(stmt)
                if customer:
                    return customer
            except NotImplementedError:
                logger.debug(
                    "primary_contact JSON lookup not supported; "
                    "skipping splynx_id match"
                )

        contact_filters = []
        if email:
            try:
                contact_filters.append(
                    func.lower(Customer.primary_contact["email"].astext) == email
                )
            except NotImplementedError:
                logger.debug(
                    "primary_contact JSON lookup not supported; skipping email match"
                )
        if phone:
            try:
                contact_filters.append(
                    func.lower(Customer.primary_contact["phone"].astext) == phone
                )
            except NotImplementedError:
                logger.debug(
                    "primary_contact JSON lookup not supported; skipping phone match"
                )
        if contact_filters:
            stmt = select(Customer).where(
                Customer.organization_id == self.organization_id,
                or_(*contact_filters),
            )
            customer_match: Customer | None = self.db.scalar(stmt)
            if customer_match:
                return customer_match

        if name:
            stmt = select(Customer).where(
                Customer.organization_id == self.organization_id,
                or_(
                    func.lower(Customer.legal_name) == name.lower(),
                    func.lower(Customer.trading_name) == name.lower(),
                ),
            )
            matches: list[Customer] = list(self.db.scalars(stmt).all())
            if len(matches) == 1:
                return matches[0]

        return None

    def _load_customer_cache(self) -> None:
        """Load customer ID mapping cache."""
        stmt = select(Customer).where(
            Customer.organization_id == self.organization_id,
            Customer.splynx_id.is_not(None),
        )
        customers = self.db.scalars(stmt).all()
        for customer in customers:
            for sid in (customer.splynx_id or "").split(","):
                sid = sid.strip()
                if sid:
                    try:
                        self._customer_cache[int(sid)] = customer.customer_id
                    except ValueError:
                        pass

        stmt2 = select(Customer).where(
            Customer.organization_id == self.organization_id,
            Customer.customer_code.like(f"{self.SOURCE_PREFIX}-%"),
            Customer.splynx_id.is_(None),
        )
        customers2 = self.db.scalars(stmt2).all()
        for customer in customers2:
            try:
                splynx_id = int(
                    customer.customer_code.replace(f"{self.SOURCE_PREFIX}-", "")
                )
                if splynx_id not in self._customer_cache:
                    self._customer_cache[splynx_id] = customer.customer_id
            except ValueError:
                pass

    def _get_or_create_customer_id(self, splynx_customer_id: int) -> UUID | None:
        """Get ERP customer ID for Splynx customer, or None."""
        if splynx_customer_id in self._customer_cache:
            return self._customer_cache[splynx_customer_id]

        customer = self._get_customer_by_splynx_id(splynx_customer_id)
        if customer:
            self._customer_cache[splynx_customer_id] = customer.customer_id
            return customer.customer_id

        local_id: UUID | None = self._get_synced_entity(
            EntityType.CUSTOMER, str(splynx_customer_id)
        )
        if local_id:
            self._customer_cache[splynx_customer_id] = local_id
            return local_id

        customer_code = self._make_customer_code(splynx_customer_id)
        found = self._get_existing_customer(customer_code)
        if found:
            found_id: UUID = found.customer_id
            self._customer_cache[splynx_customer_id] = found_id
            return found_id

        try:
            from app.services.splynx.client import SplynxError

            splynx_customer = self.client.get_customer(splynx_customer_id)
            existing = self._find_existing_customer(splynx_customer)
            if existing:
                self._customer_cache[splynx_customer_id] = existing.customer_id
                data_hash = self._compute_hash(
                    {
                        "name": splynx_customer.name,
                        "login": splynx_customer.login,
                        "email": splynx_customer.email,
                        "phone": splynx_customer.phone,
                        "status": splynx_customer.status,
                        "company": splynx_customer.company,
                        "street_1": splynx_customer.street_1,
                        "street_2": splynx_customer.street_2,
                        "city": splynx_customer.city,
                        "zip_code": splynx_customer.zip_code,
                    }
                )
                self._record_sync(
                    EntityType.CUSTOMER,
                    str(splynx_customer_id),
                    existing.customer_id,
                    data_hash,
                )
                return existing.customer_id
        except SplynxError:
            pass

        return None
