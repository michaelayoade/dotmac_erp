"""
Cross-module pending-approvals aggregator for the self-service API.

Fans out to each approvable category the caller is permitted to action and
returns a uniform "approval item" shape so mobile/API clients can render one
inbox instead of polling every module list endpoint.

Visibility gates mirror the permission checks on the corresponding approve
endpoints (including the role->permission DB fallback used by
``require_tenant_permission``). The approve endpoints remain authoritative:
business-rule guards evaluated at approval time (approver authority limits,
budget checks) can still reject an item shown here.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

LEAVE_APPROVAL_PERMISSIONS = {
    "leave:applications:approve:tier1",
    "leave:applications:approve:tier2",
    "leave:applications:approve:tier3",
}

# Exact permission keys required by each category's approve endpoint.
EXPENSE_CLAIM_APPROVE_PERMISSION = "expense:claims:approve:tier1"
EXPENSE_ADVANCE_APPROVE_PERMISSION = "expense:advances:approve:tier1"
AP_INVOICE_APPROVE_PERMISSION = "ap:invoices:approve"
AP_PAYMENT_BATCH_APPROVE_PERMISSION = "ap:payment_batches:approve"


def _sort_ts(value: datetime | date | None) -> datetime:
    """Normalize mixed date/datetime/None into a sortable datetime."""
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, date):
        return datetime.combine(value, time.min)
    return datetime.min


def _amount_str(value: Decimal | None) -> str | None:
    return None if value is None else f"{value:.2f}"


class ApprovalsAggregatorService:
    """Aggregates pending approvals across modules for one approver."""

    def __init__(self, db: Session):
        self.db = db

    def _has_permission(
        self,
        person_id: UUID,
        roles: set[str],
        scopes: set[str],
        permission_key: str,
    ) -> bool:
        """Mirror of ``require_tenant_permission``: admin role, token scope,
        or an active role->permission grant in the database."""
        if "admin" in roles or permission_key in scopes:
            return True
        from app.models.rbac import Permission, PersonRole, Role, RolePermission

        permission = self.db.scalar(
            select(Permission)
            .where(Permission.key == permission_key)
            .where(Permission.is_active.is_(True))
        )
        if not permission:
            return False
        grant = self.db.scalar(
            select(RolePermission)
            .join(Role, RolePermission.role_id == Role.id)
            .join(PersonRole, PersonRole.role_id == Role.id)
            .where(PersonRole.person_id == person_id)
            .where(RolePermission.permission_id == permission.id)
            .where(Role.is_active.is_(True))
            .limit(1)
        )
        return grant is not None

    def list_pending(
        self,
        *,
        organization_id: UUID,
        person_id: UUID,
        roles: set[str],
        scopes: set[str],
        limit_per_type: int = 10,
    ) -> dict[str, Any]:
        """Return pending approval items for every category the caller may action."""
        is_admin = "admin" in roles

        def mirrors(permission_key: str) -> bool:
            return self._has_permission(person_id, roles, scopes, permission_key)

        fetchers: list[tuple[str, bool, Any]] = [
            (
                # approve_team_leave gates on admin role or tier scopes only
                # (no DB fallback) — mirror exactly.
                "leave",
                is_admin or bool(scopes & LEAVE_APPROVAL_PERMISSIONS),
                self._leave_items,
            ),
            (
                "expense",
                mirrors(EXPENSE_CLAIM_APPROVE_PERMISSION),
                self._expense_claim_items,
            ),
            (
                "cash_advance",
                mirrors(EXPENSE_ADVANCE_APPROVE_PERMISSION),
                self._cash_advance_items,
            ),
            (
                "ap_invoice",
                mirrors(AP_INVOICE_APPROVE_PERMISSION),
                self._ap_invoice_items,
            ),
            (
                "ap_payment_batch",
                mirrors(AP_PAYMENT_BATCH_APPROVE_PERMISSION),
                self._ap_payment_batch_items,
            ),
            # The requisition approve endpoint has no dedicated permission, so
            # the aggregator stays conservative and surfaces it to admins only.
            ("requisition", is_admin, self._requisition_items),
        ]

        items: list[dict[str, Any]] = []
        counts: dict[str, int] = {}
        for key, permitted, fetch in fetchers:
            if not permitted:
                continue
            try:
                category_items = fetch(organization_id, person_id, limit_per_type)
            except Exception:
                # One broken category must not take down the whole inbox.
                logger.exception("Approvals aggregator: %s fetch failed", key)
                continue
            counts[key] = len(category_items)
            items.extend(category_items)

        items.sort(key=lambda i: i["_sort_ts"], reverse=True)
        for item in items:
            item.pop("_sort_ts", None)

        return {
            "items": items,
            "counts": counts,
            "total": sum(counts.values()),
        }

    @staticmethod
    def _merge_newest(
        batches: list[list[dict[str, Any]]], limit: int
    ) -> list[dict[str, Any]]:
        """Merge per-status batches newest-first BEFORE truncating, so one
        status filling the limit cannot starve the others."""
        merged = [item for batch in batches for item in batch]
        merged.sort(key=lambda i: i["_sort_ts"], reverse=True)
        return merged[:limit]

    # ------------------------------------------------------------------
    # Per-category fetchers (lazy imports avoid circular dependencies)
    # ------------------------------------------------------------------

    def _leave_items(
        self, organization_id: UUID, person_id: UUID, limit: int
    ) -> list[dict[str, Any]]:
        from app.models.people.leave import LeaveApplicationStatus
        from app.services.common import PaginationParams
        from app.services.people.hr.employees import EmployeeService
        from app.services.people.hr.org_resolver import OrgResolver
        from app.services.people.leave import LeaveService

        employee_svc = EmployeeService(self.db, organization_id)
        manager = employee_svc.get_employee_by_person(person_id)
        if not manager:
            return []
        # Position-based hierarchy via OrgResolver (hr-hierarchy rule);
        # the /me/team approve endpoints use the same resolution.
        reports = OrgResolver(self.db).get_direct_reports(
            manager.employee_id, organization_id
        )
        report_ids = [emp.employee_id for emp in reports]
        if not report_ids:
            return []

        result = LeaveService(self.db).list_team_applications(
            org_id=organization_id,
            employee_ids=report_ids,
            status=LeaveApplicationStatus.SUBMITTED,
            pagination=PaginationParams(offset=0, limit=limit),
        )
        items = []
        for app in result.items:
            employee = getattr(app, "employee", None)
            leave_type = getattr(app, "leave_type", None)
            requester = getattr(employee, "full_name", None)
            type_name = getattr(leave_type, "name", None) or "Leave"
            items.append(
                self._item(
                    type_key="leave",
                    entity_id=app.application_id,
                    reference=app.application_number,
                    title=f"{type_name} — {app.total_leave_days} day(s)",
                    requester=requester,
                    amount=None,
                    currency=None,
                    status=app.status.value,
                    submitted=getattr(app, "created_at", None),
                )
            )
        return items

    def _expense_claim_items(
        self, organization_id: UUID, person_id: UUID, limit: int
    ) -> list[dict[str, Any]]:
        from app.models.people.exp import ExpenseClaimStatus
        from app.services.common import PaginationParams
        from app.services.people.expense import ExpenseService

        svc = ExpenseService(self.db)
        batches: list[list[dict[str, Any]]] = []
        for status in (
            ExpenseClaimStatus.SUBMITTED,
            ExpenseClaimStatus.PENDING_APPROVAL,
        ):
            result = svc.list_claims(
                org_id=organization_id,
                status=status,
                pagination=PaginationParams(offset=0, limit=limit),
            )
            batch = []
            for claim in result.items:
                employee = getattr(claim, "employee", None)
                batch.append(
                    self._item(
                        type_key="expense",
                        entity_id=claim.claim_id,
                        reference=claim.claim_number,
                        title=claim.purpose,
                        requester=getattr(employee, "full_name", None),
                        amount=_amount_str(claim.total_claimed_amount),
                        currency=claim.currency_code,
                        status=claim.status.value,
                        submitted=getattr(claim, "created_at", None),
                    )
                )
            batches.append(batch)
        return self._merge_newest(batches, limit)

    def _cash_advance_items(
        self, organization_id: UUID, person_id: UUID, limit: int
    ) -> list[dict[str, Any]]:
        from app.models.people.exp import CashAdvanceStatus
        from app.services.common import PaginationParams
        from app.services.people.expense import ExpenseService

        svc = ExpenseService(self.db)
        batches: list[list[dict[str, Any]]] = []
        for status in (
            CashAdvanceStatus.SUBMITTED,
            CashAdvanceStatus.PENDING_APPROVAL,
        ):
            result = svc.list_advances(
                org_id=organization_id,
                status=status,
                pagination=PaginationParams(offset=0, limit=limit),
            )
            batch = []
            for advance in result.items:
                employee = getattr(advance, "employee", None)
                batch.append(
                    self._item(
                        type_key="cash_advance",
                        entity_id=advance.advance_id,
                        reference=advance.advance_number,
                        title=advance.purpose,
                        requester=getattr(employee, "full_name", None),
                        amount=_amount_str(advance.requested_amount),
                        currency=advance.currency_code,
                        status=advance.status.value,
                        submitted=getattr(advance, "created_at", None),
                    )
                )
            batches.append(batch)
        return self._merge_newest(batches, limit)

    def _ap_invoice_items(
        self, organization_id: UUID, person_id: UUID, limit: int
    ) -> list[dict[str, Any]]:
        from app.models.finance.ap.supplier_invoice import SupplierInvoiceStatus
        from app.services.finance.ap.supplier_invoice import supplier_invoice_service

        batches: list[list[dict[str, Any]]] = []
        for status in (
            SupplierInvoiceStatus.SUBMITTED,
            SupplierInvoiceStatus.PENDING_APPROVAL,
        ):
            invoices = supplier_invoice_service.list(
                db=self.db,
                organization_id=str(organization_id),
                status=status,
                limit=limit,
            )
            batches.append(
                [
                    self._item(
                        type_key="ap_invoice",
                        entity_id=invoice.invoice_id,
                        reference=invoice.invoice_number,
                        title=f"Supplier invoice {invoice.invoice_number}",
                        requester=None,
                        amount=_amount_str(invoice.total_amount),
                        currency=invoice.currency_code,
                        status=invoice.status.value,
                        submitted=invoice.submitted_at
                        or getattr(invoice, "created_at", None),
                    )
                    for invoice in invoices
                ]
            )
        return self._merge_newest(batches, limit)

    def _ap_payment_batch_items(
        self, organization_id: UUID, person_id: UUID, limit: int
    ) -> list[dict[str, Any]]:
        from app.models.finance.ap.payment_batch import (
            APBatchStatus,  # pragma: allowlist secret (detect-secrets misreads "AP…Status" as an Artifactory token)
        )
        from app.services.finance.ap.payment_batch import payment_batch_service

        batches = payment_batch_service.list(
            self.db,
            organization_id=str(organization_id),
            status=APBatchStatus.DRAFT,
            limit=limit + 1,  # headroom: SoD filter below may drop own batches
        )
        return [
            self._item(
                type_key="ap_payment_batch",
                entity_id=batch.batch_id,
                reference=batch.batch_number,
                title=f"Payment batch — {batch.total_payments} payment(s)",
                requester=None,
                amount=_amount_str(batch.total_amount),
                currency=batch.currency_code,
                status=batch.status.value,
                submitted=getattr(batch, "created_at", None) or batch.batch_date,
            )
            # Segregation of duties: approve_batch rejects the batch creator,
            # so don't show callers their own batches.
            for batch in batches
            if batch.created_by_user_id != person_id
        ][:limit]

    def _requisition_items(
        self, organization_id: UUID, person_id: UUID, limit: int
    ) -> list[dict[str, Any]]:
        from app.models.procurement.enums import RequisitionStatus
        from app.services.procurement.requisition import RequisitionService

        svc = RequisitionService(self.db)
        batches: list[list[dict[str, Any]]] = []
        for status in (
            RequisitionStatus.SUBMITTED,
            RequisitionStatus.BUDGET_VERIFIED,
        ):
            reqs, _total = svc.list_requisitions(
                organization_id,
                status=status.value,
                limit=limit,
            )
            batches.append(
                [
                    self._item(
                        type_key="requisition",
                        entity_id=req.requisition_id,
                        reference=req.requisition_number,
                        title=req.justification
                        or f"Requisition {req.requisition_number}",
                        requester=None,
                        amount=_amount_str(req.total_estimated_amount),
                        currency=req.currency_code,
                        status=req.status.value,
                        submitted=getattr(req, "created_at", None)
                        or req.requisition_date,
                    )
                    for req in reqs
                ]
            )
        return self._merge_newest(batches, limit)

    # ------------------------------------------------------------------

    @staticmethod
    def _item(
        *,
        type_key: str,
        entity_id: UUID,
        reference: str,
        title: str,
        requester: str | None,
        amount: str | None,
        currency: str | None,
        status: str,
        submitted: datetime | date | None,
    ) -> dict[str, Any]:
        return {
            "type": type_key,
            "id": str(entity_id),
            "reference": reference,
            "title": title,
            "requester": requester,
            "amount": amount,
            "currency": currency,
            "status": status,
            "submitted_at": submitted,
            "_sort_ts": _sort_ts(submitted),
        }
