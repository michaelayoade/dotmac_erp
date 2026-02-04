"""
Procurement Web Service - Context builders for HTML routes.

Provides methods to build template context for procurement management pages.
"""

import logging
from typing import Any, Dict, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.procurement.enums import (
    ContractStatus,
    PlanItemStatus,
    PrequalificationStatus,
    ProcurementMethod,
    ProcurementPlanStatus,
    RequisitionStatus,
    RFQStatus,
    UrgencyLevel,
)
from app.services.common import coerce_uuid
from app.services.procurement.contract import ContractService
from app.services.procurement.evaluation import BidEvaluationService
from app.services.procurement.procurement_plan import ProcurementPlanService
from app.services.procurement.quotation import QuotationResponseService
from app.services.procurement.requisition import RequisitionService
from app.services.procurement.rfq import RFQService
from app.services.procurement.vendor import VendorPrequalificationService

logger = logging.getLogger(__name__)

# Status display labels
PLAN_STATUS_LABELS = {
    ProcurementPlanStatus.DRAFT: ("Draft", "slate"),
    ProcurementPlanStatus.SUBMITTED: ("Submitted", "blue"),
    ProcurementPlanStatus.APPROVED: ("Approved", "emerald"),
    ProcurementPlanStatus.ACTIVE: ("Active", "teal"),
    ProcurementPlanStatus.CLOSED: ("Closed", "gray"),
}

REQUISITION_STATUS_LABELS = {
    RequisitionStatus.DRAFT: ("Draft", "slate"),
    RequisitionStatus.SUBMITTED: ("Submitted", "blue"),
    RequisitionStatus.BUDGET_VERIFIED: ("Budget Verified", "indigo"),
    RequisitionStatus.APPROVED: ("Approved", "emerald"),
    RequisitionStatus.CONVERTED: ("Converted", "teal"),
    RequisitionStatus.REJECTED: ("Rejected", "rose"),
    RequisitionStatus.CANCELLED: ("Cancelled", "gray"),
}

RFQ_STATUS_LABELS = {
    RFQStatus.DRAFT: ("Draft", "slate"),
    RFQStatus.PUBLISHED: ("Published", "blue"),
    RFQStatus.CLOSED: ("Closed", "amber"),
    RFQStatus.EVALUATED: ("Evaluated", "indigo"),
    RFQStatus.AWARDED: ("Awarded", "emerald"),
    RFQStatus.CANCELLED: ("Cancelled", "gray"),
}

CONTRACT_STATUS_LABELS = {
    ContractStatus.DRAFT: ("Draft", "slate"),
    ContractStatus.ACTIVE: ("Active", "emerald"),
    ContractStatus.COMPLETED: ("Completed", "teal"),
    ContractStatus.TERMINATED: ("Terminated", "rose"),
    ContractStatus.EXPIRED: ("Expired", "gray"),
}

PREQUALIFICATION_STATUS_LABELS = {
    PrequalificationStatus.PENDING: ("Pending", "amber"),
    PrequalificationStatus.UNDER_REVIEW: ("Under Review", "blue"),
    PrequalificationStatus.QUALIFIED: ("Qualified", "emerald"),
    PrequalificationStatus.DISQUALIFIED: ("Disqualified", "rose"),
    PrequalificationStatus.EXPIRED: ("Expired", "gray"),
    PrequalificationStatus.BLACKLISTED: ("Blacklisted", "red"),
}


class ProcurementWebService:
    """Web service methods for procurement management pages."""

    def __init__(self, db: Session):
        self.db = db

    # ─────────────────────────────────────────────────────────────
    # Dashboard
    # ─────────────────────────────────────────────────────────────

    def dashboard_context(self, organization_id: UUID) -> Dict[str, Any]:
        """Build context for procurement dashboard page."""
        org_id = coerce_uuid(organization_id)

        plan_service = ProcurementPlanService(self.db)
        req_service = RequisitionService(self.db)
        rfq_service = RFQService(self.db)
        contract_service = ContractService(self.db)

        plan_summary = plan_service.get_summary(org_id)
        pending_reqs, _ = req_service.list_requisitions(
            org_id,
            status="SUBMITTED",
            limit=5,
        )
        open_rfqs, open_rfq_count = rfq_service.list_rfqs(
            org_id,
            status="PUBLISHED",
            limit=5,
        )
        active_contracts, active_contract_count = contract_service.list_contracts(
            org_id,
            status="ACTIVE",
            limit=5,
        )

        return {
            "plan_summary": plan_summary,
            "pending_requisitions": pending_reqs,
            "pending_req_count": len(pending_reqs),
            "open_rfqs": open_rfqs,
            "open_rfq_count": open_rfq_count,
            "active_contracts": active_contracts,
            "active_contract_count": active_contract_count,
            "status_labels": PLAN_STATUS_LABELS,
            "req_status_labels": REQUISITION_STATUS_LABELS,
            "rfq_status_labels": RFQ_STATUS_LABELS,
            "contract_status_labels": CONTRACT_STATUS_LABELS,
        }

    # ─────────────────────────────────────────────────────────────
    # Plans
    # ─────────────────────────────────────────────────────────────

    def plan_list_context(
        self,
        organization_id: UUID,
        status: Optional[str] = None,
        fiscal_year: Optional[str] = None,
        offset: int = 0,
        limit: int = 25,
    ) -> Dict[str, Any]:
        """Build context for plan list page."""
        service = ProcurementPlanService(self.db)
        plans, total = service.list_plans(
            organization_id,
            status=status,
            fiscal_year=fiscal_year,
            offset=offset,
            limit=limit,
        )
        return {
            "plans": plans,
            "total": total,
            "offset": offset,
            "limit": limit,
            "filter_status": status,
            "filter_fiscal_year": fiscal_year,
            "status_labels": PLAN_STATUS_LABELS,
            "plan_statuses": list(ProcurementPlanStatus),
        }

    def plan_detail_context(
        self,
        organization_id: UUID,
        plan_id: UUID,
    ) -> Dict[str, Any]:
        """Build context for plan detail page."""
        service = ProcurementPlanService(self.db)
        plan = service.get_by_id(organization_id, plan_id)
        if not plan:
            from app.services.common import NotFoundError

            raise NotFoundError("Procurement plan not found")

        return {
            "plan": plan,
            "items": plan.items,
            "status_labels": PLAN_STATUS_LABELS,
            "item_statuses": list(PlanItemStatus),
            "procurement_methods": list(ProcurementMethod),
        }

    def plan_form_context(
        self,
        organization_id: UUID,
    ) -> Dict[str, Any]:
        """Build context for plan create/edit form."""
        return {
            "procurement_methods": list(ProcurementMethod),
            "quarters": [1, 2, 3, 4],
            "categories": ["Goods", "Works", "Services", "Consulting"],
        }

    # ─────────────────────────────────────────────────────────────
    # Requisitions
    # ─────────────────────────────────────────────────────────────

    def requisition_list_context(
        self,
        organization_id: UUID,
        status: Optional[str] = None,
        offset: int = 0,
        limit: int = 25,
    ) -> Dict[str, Any]:
        """Build context for requisition list page."""
        service = RequisitionService(self.db)
        requisitions, total = service.list_requisitions(
            organization_id,
            status=status,
            offset=offset,
            limit=limit,
        )
        return {
            "requisitions": requisitions,
            "total": total,
            "offset": offset,
            "limit": limit,
            "filter_status": status,
            "status_labels": REQUISITION_STATUS_LABELS,
            "req_statuses": list(RequisitionStatus),
        }

    def requisition_detail_context(
        self,
        organization_id: UUID,
        requisition_id: UUID,
    ) -> Dict[str, Any]:
        """Build context for requisition detail page."""
        service = RequisitionService(self.db)
        requisition = service.get_by_id(organization_id, requisition_id)
        if not requisition:
            from app.services.common import NotFoundError

            raise NotFoundError("Requisition not found")

        return {
            "requisition": requisition,
            "lines": requisition.lines,
            "status_labels": REQUISITION_STATUS_LABELS,
            "urgency_levels": list(UrgencyLevel),
        }

    def requisition_form_context(
        self,
        organization_id: UUID,
    ) -> Dict[str, Any]:
        """Build context for requisition create/edit form."""
        return {
            "urgency_levels": list(UrgencyLevel),
        }

    # ─────────────────────────────────────────────────────────────
    # RFQs
    # ─────────────────────────────────────────────────────────────

    def rfq_list_context(
        self,
        organization_id: UUID,
        status: Optional[str] = None,
        offset: int = 0,
        limit: int = 25,
    ) -> Dict[str, Any]:
        """Build context for RFQ list page."""
        service = RFQService(self.db)
        rfqs, total = service.list_rfqs(
            organization_id,
            status=status,
            offset=offset,
            limit=limit,
        )
        return {
            "rfqs": rfqs,
            "total": total,
            "offset": offset,
            "limit": limit,
            "filter_status": status,
            "status_labels": RFQ_STATUS_LABELS,
            "rfq_statuses": list(RFQStatus),
        }

    def rfq_detail_context(
        self,
        organization_id: UUID,
        rfq_id: UUID,
    ) -> Dict[str, Any]:
        """Build context for RFQ detail page."""
        rfq_service = RFQService(self.db)
        quot_service = QuotationResponseService(self.db)

        rfq = rfq_service.get_by_id(organization_id, rfq_id)
        if not rfq:
            from app.services.common import NotFoundError

            raise NotFoundError("RFQ not found")

        responses = quot_service.list_for_rfq(organization_id, rfq_id)

        return {
            "rfq": rfq,
            "invitations": rfq.invitations,
            "responses": responses,
            "status_labels": RFQ_STATUS_LABELS,
            "procurement_methods": list(ProcurementMethod),
        }

    def rfq_form_context(
        self,
        organization_id: UUID,
    ) -> Dict[str, Any]:
        """Build context for RFQ create/edit form."""
        return {
            "procurement_methods": list(ProcurementMethod),
        }

    # ─────────────────────────────────────────────────────────────
    # Evaluations
    # ─────────────────────────────────────────────────────────────

    def evaluation_matrix_context(
        self,
        organization_id: UUID,
        rfq_id: UUID,
    ) -> Dict[str, Any]:
        """Build context for evaluation matrix page."""
        rfq_service = RFQService(self.db)
        quot_service = QuotationResponseService(self.db)
        eval_service = BidEvaluationService(self.db)

        rfq = rfq_service.get_by_id(organization_id, rfq_id)
        if not rfq:
            from app.services.common import NotFoundError

            raise NotFoundError("RFQ not found")

        responses = quot_service.list_for_rfq(organization_id, rfq_id)
        evaluations, _ = eval_service.list_evaluations(
            organization_id,
            rfq_id=rfq_id,
        )

        return {
            "rfq": rfq,
            "responses": responses,
            "evaluations": evaluations,
            "evaluation_criteria": rfq.evaluation_criteria or [],
            "status_labels": RFQ_STATUS_LABELS,
        }

    # ─────────────────────────────────────────────────────────────
    # Contracts
    # ─────────────────────────────────────────────────────────────

    def contract_list_context(
        self,
        organization_id: UUID,
        status: Optional[str] = None,
        offset: int = 0,
        limit: int = 25,
    ) -> Dict[str, Any]:
        """Build context for contract list page."""
        service = ContractService(self.db)
        contracts, total = service.list_contracts(
            organization_id,
            status=status,
            offset=offset,
            limit=limit,
        )
        return {
            "contracts": contracts,
            "total": total,
            "offset": offset,
            "limit": limit,
            "filter_status": status,
            "status_labels": CONTRACT_STATUS_LABELS,
            "contract_statuses": list(ContractStatus),
        }

    def contract_detail_context(
        self,
        organization_id: UUID,
        contract_id: UUID,
    ) -> Dict[str, Any]:
        """Build context for contract detail page."""
        service = ContractService(self.db)
        contract = service.get_by_id(organization_id, contract_id)
        if not contract:
            from app.services.common import NotFoundError

            raise NotFoundError("Contract not found")

        remaining = contract.contract_value - contract.amount_paid
        progress_pct = (
            float(contract.amount_paid / contract.contract_value * 100)
            if contract.contract_value > 0
            else 0
        )

        return {
            "contract": contract,
            "remaining_amount": remaining,
            "payment_progress_pct": round(progress_pct, 1),
            "status_labels": CONTRACT_STATUS_LABELS,
        }

    def contract_form_context(
        self,
        organization_id: UUID,
    ) -> Dict[str, Any]:
        """Build context for contract create/edit form."""
        return {}

    # ─────────────────────────────────────────────────────────────
    # Vendors
    # ─────────────────────────────────────────────────────────────

    def vendor_list_context(
        self,
        organization_id: UUID,
        status: Optional[str] = None,
        offset: int = 0,
        limit: int = 25,
    ) -> Dict[str, Any]:
        """Build context for vendor registry page."""
        service = VendorPrequalificationService(self.db)
        prequalifications, total = service.list_prequalifications(
            organization_id,
            status=status,
            offset=offset,
            limit=limit,
        )
        return {
            "prequalifications": prequalifications,
            "total": total,
            "offset": offset,
            "limit": limit,
            "filter_status": status,
            "status_labels": PREQUALIFICATION_STATUS_LABELS,
            "preq_statuses": list(PrequalificationStatus),
        }

    def prequalification_detail_context(
        self,
        organization_id: UUID,
        prequalification_id: UUID,
    ) -> Dict[str, Any]:
        """Build context for prequalification detail page."""
        service = VendorPrequalificationService(self.db)
        preq = service.get_by_id(organization_id, prequalification_id)
        if not preq:
            from app.services.common import NotFoundError

            raise NotFoundError("Prequalification not found")

        return {
            "prequalification": preq,
            "status_labels": PREQUALIFICATION_STATUS_LABELS,
        }
