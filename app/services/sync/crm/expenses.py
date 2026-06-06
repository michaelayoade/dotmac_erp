"""Expense-total roll-ups for CRM projects, tickets and work orders.

Extracted from the former monolithic dotmac_crm_sync_service.
"""

from __future__ import annotations

import logging
from datetime import timezone

try:
    from datetime import UTC  # type: ignore
except ImportError:  # pragma: no cover
    UTC = timezone.utc

from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import func, select


if TYPE_CHECKING:
    from app.models.finance.ap.supplier import Supplier  # noqa: F401

from app.models.expense.expense_claim import ExpenseClaim, ExpenseClaimStatus
from app.models.sync.dotmac_crm_sync import (
    CRMEntityType,
)
from app.schemas.sync.dotmac_crm import (
    ExpenseTotals,
)

# CRM → ERP translation policy lives in crm_mappings (pure, side-effect-free).
# Re-imported here so the canonical import sites
# (`from ...dotmac_crm_sync_service import PROJECT_STATUS_MAP`) and the in-class
# references keep resolving against this module's namespace.

from app.services.sync.crm.base import _CRMSyncBase

logger = logging.getLogger(__name__)


class _ExpenseTotalsMixin(_CRMSyncBase):
    def get_expense_totals_for_project(
        self,
        org_id: UUID,
        crm_id: str,
    ) -> ExpenseTotals | None:
        """Get expense totals for a CRM project."""
        mapping = self._get_mapping(org_id, CRMEntityType.PROJECT, crm_id)
        if not mapping:
            return None

        return self._calculate_expense_totals(
            org_id,
            project_id=mapping.local_entity_id,
        )

    def get_expense_totals_for_ticket(
        self,
        org_id: UUID,
        crm_id: str,
    ) -> ExpenseTotals | None:
        """Get expense totals for a CRM ticket."""
        mapping = self._get_mapping(org_id, CRMEntityType.TICKET, crm_id)
        if not mapping:
            return None

        return self._calculate_expense_totals(
            org_id,
            ticket_id=mapping.local_entity_id,
        )

    def get_expense_totals_for_work_order(
        self,
        org_id: UUID,
        crm_id: str,
    ) -> ExpenseTotals | None:
        """Get expense totals for a CRM work order."""
        mapping = self._get_mapping(org_id, CRMEntityType.WORK_ORDER, crm_id)
        if not mapping:
            return None

        return self._calculate_expense_totals(
            org_id,
            task_id=mapping.local_entity_id,
        )

    def get_batch_expense_totals(
        self,
        org_id: UUID,
        project_crm_ids: list[str],
        ticket_crm_ids: list[str],
        work_order_crm_ids: list[str],
    ) -> dict[str, ExpenseTotals]:
        """
        Get expense totals for multiple CRM entities in batched queries.

        Instead of 2 queries per CRM ID (mapping lookup + aggregation),
        resolves all mappings in up to 3 queries then aggregates in up to 3.
        """
        result: dict[str, ExpenseTotals] = {}

        # Batch-resolve mappings (up to 3 queries)
        project_map = self._batch_get_mappings(
            org_id, CRMEntityType.PROJECT, project_crm_ids
        )
        ticket_map = self._batch_get_mappings(
            org_id, CRMEntityType.TICKET, ticket_crm_ids
        )
        wo_map = self._batch_get_mappings(
            org_id, CRMEntityType.WORK_ORDER, work_order_crm_ids
        )

        # Batch-aggregate expenses (up to 3 queries)
        for crm_to_local, fk_col in [
            (project_map, ExpenseClaim.project_id),
            (ticket_map, ExpenseClaim.ticket_id),
            (wo_map, ExpenseClaim.task_id),
        ]:
            if not crm_to_local:
                continue
            local_to_crm = {v: k for k, v in crm_to_local.items()}
            local_ids = list(crm_to_local.values())

            stmt = (
                select(
                    fk_col,
                    ExpenseClaim.status,
                    func.coalesce(func.sum(ExpenseClaim.total_claimed_amount), 0).label(
                        "total"
                    ),
                )
                .where(
                    ExpenseClaim.organization_id == org_id,
                    fk_col.in_(local_ids),
                )
                .group_by(fk_col, ExpenseClaim.status)
            )
            rows = self.db.execute(stmt).all()

            # Group by local_id
            grouped: dict[UUID, ExpenseTotals] = {}
            for local_id, status, total in rows:
                if local_id not in grouped:
                    grouped[local_id] = ExpenseTotals()
                amount = Decimal(str(total)) if total else Decimal("0.00")
                totals = grouped[local_id]
                if status == ExpenseClaimStatus.DRAFT:
                    totals.draft = amount
                elif status == ExpenseClaimStatus.SUBMITTED:
                    totals.submitted = amount
                elif status in (
                    ExpenseClaimStatus.APPROVED,
                    ExpenseClaimStatus.PENDING_APPROVAL,
                ):
                    totals.approved += amount
                elif status == ExpenseClaimStatus.PAID:
                    totals.paid = amount

            for local_id, totals in grouped.items():
                crm_id = local_to_crm.get(local_id)
                if crm_id:
                    result[crm_id] = totals

        return result

    def _calculate_expense_totals(
        self,
        org_id: UUID,
        project_id: UUID | None = None,
        ticket_id: UUID | None = None,
        task_id: UUID | None = None,
    ) -> ExpenseTotals:
        """Calculate expense totals grouped by status."""
        # Build base query
        stmt = select(
            ExpenseClaim.status,
            func.coalesce(func.sum(ExpenseClaim.total_claimed_amount), 0).label(
                "total"
            ),
        ).where(ExpenseClaim.organization_id == org_id)

        if project_id:
            stmt = stmt.where(ExpenseClaim.project_id == project_id)
        if ticket_id:
            stmt = stmt.where(ExpenseClaim.ticket_id == ticket_id)
        if task_id:
            stmt = stmt.where(ExpenseClaim.task_id == task_id)

        stmt = stmt.group_by(ExpenseClaim.status)
        results = self.db.execute(stmt).all()

        totals = ExpenseTotals()
        for status, total in results:
            amount = Decimal(str(total)) if total else Decimal("0.00")
            if status == ExpenseClaimStatus.DRAFT:
                totals.draft = amount
            elif status == ExpenseClaimStatus.SUBMITTED:
                totals.submitted = amount
            elif status in (
                ExpenseClaimStatus.APPROVED,
                ExpenseClaimStatus.PENDING_APPROVAL,
            ):
                totals.approved += amount
            elif status == ExpenseClaimStatus.PAID:
                totals.paid = amount

        return totals
