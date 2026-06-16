"""
Customer account-family resolution for consolidated (reseller) accounts.

The AR customer hierarchy is a flat two-level tree: a reseller/parent account
(``splynx_partner_id`` set) has sub-accounts that point at it via
``parent_customer_id``. A *consolidated parent* is any customer that has
sub-accounts. Viewing a parent rolls up the whole family (parent + its
sub-accounts); viewing a sub-account directly shows only that account, so its
own detail/drill-down is never consolidated.

This resolver is the single source of truth for "which customer ids make up
this account family" — every consolidated read (detail, list, statement,
payments) goes through it so the rollup definition can never drift.
"""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance.ar.customer import Customer

logger = logging.getLogger(__name__)


class CustomerFamilyResolver:
    """Resolve consolidated account families (reseller parent + sub-accounts)."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def child_ids(self, org_id: UUID, customer_id: UUID) -> list[UUID]:
        """Return the ids of all sub-accounts of ``customer_id`` (org-scoped)."""
        stmt = select(Customer.customer_id).where(
            Customer.organization_id == org_id,
            Customer.parent_customer_id == customer_id,
        )
        return list(self.db.scalars(stmt).all())

    def family_ids(self, org_id: UUID, customer_id: UUID) -> list[UUID]:
        """
        Return the customer ids that make up this account family.

        For a parent: ``[customer_id] + sub-account ids``. For a standalone
        customer or a sub-account (neither has children), this returns just
        ``[customer_id]`` — so a non-parent view is naturally un-consolidated
        without any special-casing at the call site.
        """
        return [customer_id, *self.child_ids(org_id, customer_id)]

    def is_consolidated_parent(self, org_id: UUID, customer_id: UUID) -> bool:
        """True if ``customer_id`` has at least one sub-account."""
        return (
            self.db.scalar(
                select(Customer.customer_id)
                .where(
                    Customer.organization_id == org_id,
                    Customer.parent_customer_id == customer_id,
                )
                .limit(1)
            )
            is not None
        )

    def attribution_map(
        self, org_id: UUID, family_ids: list[UUID]
    ) -> dict[UUID, dict[str, str]]:
        """
        Map each family member id to its ``{code, name}`` for per-row
        attribution in consolidated tables ("which sub-account is this for?").
        """
        if not family_ids:
            return {}
        rows = self.db.execute(
            select(
                Customer.customer_id,
                Customer.customer_code,
                Customer.legal_name,
            ).where(
                Customer.organization_id == org_id,
                Customer.customer_id.in_(family_ids),
            )
        ).all()
        return {
            row.customer_id: {"code": row.customer_code, "name": row.legal_name}
            for row in rows
        }
