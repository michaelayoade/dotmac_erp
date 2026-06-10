"""
Cross-module pending-approvals aggregator for the self-service API.

Fans out to each approvable category the caller is permitted to action and
returns a uniform "approval item" shape so mobile/API clients can render one
inbox instead of polling every module list endpoint.

Category gates mirror the permission checks on the corresponding approve
endpoints; an item should only appear here if the caller could actually
approve it.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

LEAVE_APPROVAL_PERMISSIONS = {
    "leave:applications:approve:tier1",
    "leave:applications:approve:tier2",
    "leave:applications:approve:tier3",
}

EXPENSE_CLAIM_APPROVE_PREFIX = "expense:claims:approve"
EXPENSE_ADVANCE_APPROVE_PREFIX = "expense:advances:approve"
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
        items: list[dict[str, Any]] = []
        counts: dict[str, int] = {}

        fetchers: list[tuple[str, bool, Any]] = [
            (
                "leave",
                is_admin or bool(scopes & LEAVE_APPROVAL_PERMISSIONS),
                self._leave_items,
            ),
            (
                "expense",
                is_admin
                or any(s.startswith(EXPENSE_CLAIM_APPROVE_PREFIX) for s in scopes),
                self._expense_claim_items,
            ),
            (
                "cash_advance",
                is_admin
                or any(s.startswith(EXPENSE_ADVANCE_APPROVE_PREFIX) for s in scopes),
                self._cash_advance_items,
            ),
            (
                "ap_invoice",
                is_admin or AP_INVOICE_APPROVE_PERMISSION in scopes,
                self._ap_invoice_items,
            ),
            (
                "ap_payment_batch",
                is_admin or AP_PAYMENT_BATCH_APPROVE_PERMISSION in scopes,
                self._ap_payment_batch_items,
            ),
            # The requisition approve endpoint has no dedicated permission, so
            # the aggregator stays conservative and surfaces it to admins only.
            ("requisition", is_admin, self._requisition_items),
        ]

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

    # ------------------------------------------------------------------
    # Per-category fetchers (lazy imports avoid circular dependencies)
    # ------------------------------------------------------------------

    def _leave_items(
        self, organization_id: UUID, person_id: UUID, limit: int
    ) -> list[dict[str, Any]]:
        from app.models.people.leave import LeaveApplicationStatus
        from app.services.common import PaginationParams
        from app.services.people.hr.employee_types import EmployeeFilters
        from app.services.people.hr.employees import EmployeeService
        from app.services.people.leave import LeaveService

        employee_svc = EmployeeService(self.db, organization_id)
        manager = employee_svc.get_employee_by_person(person_id)
        if not manager:
            return []
        # Mirrors the guard in approve_team_leave: only direct reports
        # (reports_to_id) are approvable via the team endpoint.
        reports = employee_svc.list_employees(
            filters=EmployeeFilters(reports_to_id=manager.employee_id),
            pagination=PaginationParams(offset=0, limit=1000),
        ).items
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
        items: list[dict[str, Any]] = []
        for status in (
            ExpenseClaimStatus.SUBMITTED,
            ExpenseClaimStatus.PENDING_APPROVAL,
        ):
            result = svc.list_claims(
                org_id=organization_id,
                status=status,
                pagination=PaginationParams(offset=0, limit=limit),
            )
            for claim in result.items:
                employee = getattr(claim, "employee", None)
                items.append(
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
        return items[:limit]

    def _cash_advance_items(
        self, organization_id: UUID, person_id: UUID, limit: int
    ) -> list[dict[str, Any]]:
        from app.models.people.exp import CashAdvanceStatus
        from app.services.common import PaginationParams
        from app.services.people.expense import ExpenseService

        svc = ExpenseService(self.db)
        items: list[dict[str, Any]] = []
        for status in (
            CashAdvanceStatus.SUBMITTED,
            CashAdvanceStatus.PENDING_APPROVAL,
        ):
            result = svc.list_advances(
                org_id=organization_id,
                status=status,
                pagination=PaginationParams(offset=0, limit=limit),
            )
            for advance in result.items:
                employee = getattr(advance, "employee", None)
                items.append(
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
        return items[:limit]

    def _ap_invoice_items(
        self, organization_id: UUID, person_id: UUID, limit: int
    ) -> list[dict[str, Any]]:
        from app.models.finance.ap.supplier_invoice import SupplierInvoiceStatus
        from app.services.finance.ap.supplier_invoice import supplier_invoice_service

        items: list[dict[str, Any]] = []
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
            for invoice in invoices:
                items.append(
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
                )
        return items[:limit]

    def _ap_payment_batch_items(
        self, organization_id: UUID, person_id: UUID, limit: int
    ) -> list[dict[str, Any]]:
        from app.models.finance.ap.payment_batch import (
            APBatchStatus,  # pragma: allowlist secret — enum import, not a credential
        )
        from app.services.finance.ap.payment_batch import payment_batch_service

        batches = payment_batch_service.list(
            self.db,
            organization_id=str(organization_id),
            status=APBatchStatus.DRAFT,
            limit=limit,
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
            for batch in batches
        ]

    def _requisition_items(
        self, organization_id: UUID, person_id: UUID, limit: int
    ) -> list[dict[str, Any]]:
        from app.models.procurement.enums import RequisitionStatus
        from app.services.procurement.requisition import RequisitionService

        svc = RequisitionService(self.db)
        items: list[dict[str, Any]] = []
        for status in (
            RequisitionStatus.SUBMITTED,
            RequisitionStatus.BUDGET_VERIFIED,
        ):
            reqs, _total = svc.list_requisitions(
                organization_id,
                status=status.value,
                limit=limit,
            )
            for req in reqs:
                items.append(
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
                )
        return items[:limit]

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
