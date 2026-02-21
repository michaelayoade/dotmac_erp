"""
ApprovalWorkflowService - Multi-level approval workflows.

Manages document approval workflows with dual control, threshold evaluation,
and segregation of duties enforcement.
"""

from __future__ import annotations

import builtins
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.models.finance.audit.approval_decision import (
    ApprovalDecision,
    ApprovalDecisionAction,
)
from app.models.finance.audit.approval_request import (
    ApprovalRequest,
    ApprovalRequestStatus,
)
from app.models.finance.audit.approval_workflow import ApprovalWorkflow
from app.models.rbac import PersonRole, Role
from app.services.common import coerce_uuid
from app.services.finance.platform.authorization import AuthorizationService
from app.services.finance.platform.fx import FXService
from app.services.response import ListResponseMixin

logger = logging.getLogger(__name__)


@dataclass
class ApprovalStatus:
    """Current status of an approval request."""

    request_id: UUID
    status: str
    current_level: int
    total_levels: int
    decisions: list[dict[str, Any]]
    can_approve: bool
    can_reject: bool


class ApprovalWorkflowService(ListResponseMixin):
    """
    Service for managing approval workflows.

    Supports multi-level approvals, threshold-based routing,
    segregation of duties (SoD), and dual control.
    """

    @staticmethod
    def check_workflow_required(
        db: Session,
        organization_id: UUID,
        document_type: str,
        document_amount: Decimal | None = None,
        currency_code: str | None = None,
    ) -> UUID | None:
        """
        Check if a workflow is required and return its ID.

        Evaluates threshold conditions if applicable.

        Args:
            db: Database session
            organization_id: Organization scope
            document_type: Type of document (INVOICE, JOURNAL, etc.)
            document_amount: Optional document amount for threshold check
            currency_code: Currency of the amount

        Returns:
            Workflow ID if required, None otherwise
        """
        org_id = coerce_uuid(organization_id)

        # Find applicable workflows for this document type
        workflows = (
            db.query(ApprovalWorkflow)
            .filter(
                and_(
                    ApprovalWorkflow.organization_id == org_id,
                    ApprovalWorkflow.document_type == document_type,
                    ApprovalWorkflow.is_active == True,  # noqa: E712
                )
            )
            .order_by(ApprovalWorkflow.threshold_amount.desc().nullslast())
            .all()
        )

        if not workflows:
            return None

        fallback_workflow_id = None

        # Find the first matching workflow based on threshold
        for workflow in workflows:
            if workflow.threshold_amount is None:
                # No threshold - treat as default if no threshold matches
                fallback_workflow_id = workflow.workflow_id
                continue

            if document_amount is not None:
                amount_to_compare = document_amount
                if workflow.threshold_currency_code:
                    if not currency_code:
                        raise HTTPException(
                            status_code=400,
                            detail="currency_code is required for threshold evaluation",
                        )
                    if currency_code != workflow.threshold_currency_code:
                        conversion = FXService.convert(
                            db,
                            organization_id,
                            document_amount,
                            currency_code,
                            workflow.threshold_currency_code,
                            "SPOT",
                            datetime.now(UTC).date(),
                        )
                        amount_to_compare = conversion.converted_amount

                # Check if document amount exceeds threshold
                if amount_to_compare >= workflow.threshold_amount:
                    return workflow.workflow_id

        return fallback_workflow_id

    @staticmethod
    def submit_for_approval(
        db: Session,
        organization_id: UUID,
        workflow_id: UUID,
        document_type: str,
        document_id: UUID,
        document_reference: str,
        document_amount: Decimal | None,
        document_currency_code: str | None,
        requested_by_user_id: UUID,
        correlation_id: str | None = None,
    ) -> UUID:
        """
        Submit a document for approval.

        Args:
            db: Database session
            organization_id: Organization scope
            workflow_id: Approval workflow ID
            document_type: Type of document
            document_id: Document identifier
            document_reference: Human-readable reference
            document_amount: Optional amount
            document_currency_code: Currency of amount
            requested_by_user_id: User submitting for approval
            correlation_id: Optional correlation ID

        Returns:
            Created approval request ID

        Raises:
            HTTPException(404): If workflow not found
            HTTPException(400): If workflow not active
        """
        org_id = coerce_uuid(organization_id)
        wf_id = coerce_uuid(workflow_id)

        # Get workflow
        workflow = db.get(ApprovalWorkflow, wf_id)
        if not workflow:
            raise HTTPException(status_code=404, detail="Approval workflow not found")

        if workflow.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Approval workflow not found")

        if not workflow.is_active:
            raise HTTPException(
                status_code=400, detail="Approval workflow is not active"
            )

        # Create approval request
        request = ApprovalRequest(
            organization_id=org_id,
            workflow_id=wf_id,
            document_type=document_type,
            document_id=coerce_uuid(document_id),
            document_reference=document_reference,
            document_amount=document_amount,
            document_currency_code=document_currency_code,
            requested_by_user_id=coerce_uuid(requested_by_user_id),
            current_level=1,
            status=ApprovalRequestStatus.PENDING,
            correlation_id=correlation_id,
        )

        db.add(request)
        db.commit()
        db.refresh(request)

        return request.request_id

    @staticmethod
    def approve(
        db: Session,
        request_id: UUID,
        approver_user_id: UUID,
        comments: str | None = None,
        ip_address: str | None = None,
        mfa_verified: bool = False,
        delegated_from_user_id: UUID | None = None,
    ) -> ApprovalStatus:
        """
        Approve an approval request.

        Enforces SoD rules and advances to next level if applicable.

        Args:
            db: Database session
            request_id: Approval request ID
            approver_user_id: User approving
            comments: Optional approval comments
            ip_address: IP address of approver
            mfa_verified: Whether MFA was verified
            delegated_from_user_id: Original approver if delegated

        Returns:
            Updated ApprovalStatus

        Raises:
            HTTPException(404): If request not found
            HTTPException(403): If user cannot approve (SoD violation)
            HTTPException(400): If already completed
        """
        req_id = coerce_uuid(request_id)
        approver_id = coerce_uuid(approver_user_id)

        # Get request with workflow
        request = db.get(ApprovalRequest, req_id)
        if not request:
            raise HTTPException(status_code=404, detail="Approval request not found")

        if request.status != ApprovalRequestStatus.PENDING:
            raise HTTPException(
                status_code=400,
                detail=f"Request is not pending (status: {request.status.value})",
            )

        workflow = request.workflow
        approval_levels = workflow.approval_levels

        if request.current_level > len(approval_levels):
            raise HTTPException(
                status_code=400,
                detail="No more approval levels defined",
            )

        current_level_config = approval_levels[request.current_level - 1]
        required_count = current_level_config.get("required_count") or 1
        required_count = max(required_count, 1)

        existing_approvals = [
            d
            for d in request.decisions
            if d.level == request.current_level
            and d.action == ApprovalDecisionAction.APPROVE
        ]
        if any(d.approver_user_id == approver_id for d in existing_approvals):
            raise HTTPException(
                status_code=400,
                detail="User has already approved this level",
            )
        if len(existing_approvals) >= required_count:
            raise HTTPException(
                status_code=400,
                detail="Approval level already satisfied",
            )

        is_allowed, reason = ApprovalWorkflowService._is_user_allowed_for_level(
            db,
            approver_id,
            current_level_config,
        )
        if not is_allowed:
            raise HTTPException(status_code=403, detail=reason)

        # Check SoD rules
        sod_rule = current_level_config.get("sod_rule")
        if sod_rule:
            previous_approvers = [d.approver_user_id for d in request.decisions]
            context = {
                "created_by_user_id": request.requested_by_user_id,
                "previous_approvers": previous_approvers,
            }
            is_valid, violation = AuthorizationService.validate_sod_rule(
                db, approver_id, sod_rule, context
            )
            if not is_valid:
                raise HTTPException(status_code=403, detail=violation)

        # Create decision record
        decision = ApprovalDecision(
            request_id=req_id,
            level=request.current_level,
            approver_user_id=approver_id,
            delegated_from_user_id=coerce_uuid(delegated_from_user_id)
            if delegated_from_user_id
            else None,
            action=ApprovalDecisionAction.APPROVE,
            comments=comments,
            ip_address=ip_address,
            mfa_verified=mfa_verified,
        )
        db.add(decision)

        # Check if more levels needed
        approved_count = len(existing_approvals) + 1
        if approved_count >= required_count:
            if request.current_level >= len(approval_levels):
                # Final approval
                request.status = ApprovalRequestStatus.APPROVED
                request.completed_at = datetime.now(UTC)
                request.final_approver_user_id = approver_id
            else:
                # Move to next level
                request.current_level += 1

        db.commit()
        db.refresh(request)

        return ApprovalWorkflowService.get_approval_status(db, req_id)

    @staticmethod
    def reject(
        db: Session,
        request_id: UUID,
        rejector_user_id: UUID,
        comments: str,
        ip_address: str | None = None,
    ) -> ApprovalStatus:
        """
        Reject an approval request.

        Args:
            db: Database session
            request_id: Approval request ID
            rejector_user_id: User rejecting
            comments: Required rejection reason
            ip_address: IP address of rejector

        Returns:
            Updated ApprovalStatus

        Raises:
            HTTPException(404): If request not found
            HTTPException(400): If comments not provided
            HTTPException(400): If already completed
        """
        if not comments:
            raise HTTPException(
                status_code=400,
                detail="Rejection comments are required",
            )

        req_id = coerce_uuid(request_id)
        rejector_id = coerce_uuid(rejector_user_id)

        request = db.get(ApprovalRequest, req_id)
        if not request:
            raise HTTPException(status_code=404, detail="Approval request not found")

        if request.status != ApprovalRequestStatus.PENDING:
            raise HTTPException(
                status_code=400,
                detail=f"Request is not pending (status: {request.status.value})",
            )

        # Create decision record
        decision = ApprovalDecision(
            request_id=req_id,
            level=request.current_level,
            approver_user_id=rejector_id,
            action=ApprovalDecisionAction.REJECT,
            comments=comments,
            ip_address=ip_address,
        )
        db.add(decision)

        # Update request status
        request.status = ApprovalRequestStatus.REJECTED
        request.completed_at = datetime.now(UTC)
        request.notes = comments

        db.commit()
        db.refresh(request)

        return ApprovalWorkflowService.get_approval_status(db, req_id)

    @staticmethod
    def get_pending_approvals(
        db: Session,
        organization_id: UUID,
        approver_user_id: UUID,
        document_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """
        Get pending approvals for a user.

        Args:
            db: Database session
            organization_id: Organization scope
            approver_user_id: User to get approvals for
            document_type: Optional filter by document type
            limit: Maximum results
            offset: Pagination offset

        Returns:
            List of pending approval requests
        """
        org_id = coerce_uuid(organization_id)

        query = db.query(ApprovalRequest).filter(
            and_(
                ApprovalRequest.organization_id == org_id,
                ApprovalRequest.status == ApprovalRequestStatus.PENDING,
            )
        )

        if document_type:
            query = query.filter(ApprovalRequest.document_type == document_type)

        requests = (
            query.order_by(ApprovalRequest.requested_at.asc())
            .limit(limit)
            .offset(offset)
            .all()
        )

        results = []
        for request in requests:
            is_allowed, _ = ApprovalWorkflowService._is_user_allowed_for_request(
                db,
                request,
                approver_user_id,
            )
            if not is_allowed:
                continue
            results.append(
                {
                    "request_id": str(request.request_id),
                    "document_type": request.document_type,
                    "document_id": str(request.document_id),
                    "document_reference": request.document_reference,
                    "document_amount": float(request.document_amount)
                    if request.document_amount is not None
                    else None,
                    "document_currency_code": request.document_currency_code,
                    "requested_by_user_id": str(request.requested_by_user_id),
                    "requested_at": request.requested_at.isoformat(),
                    "current_level": request.current_level,
                    "workflow_name": request.workflow.workflow_name,
                }
            )

        return results

    @staticmethod
    def get_approval_status(
        db: Session,
        request_id: UUID,
    ) -> ApprovalStatus:
        """
        Get current status of an approval request.

        Args:
            db: Database session
            request_id: Approval request ID

        Returns:
            ApprovalStatus with current state and history

        Raises:
            HTTPException(404): If request not found
        """
        req_id = coerce_uuid(request_id)

        request = db.get(ApprovalRequest, req_id)
        if not request:
            raise HTTPException(status_code=404, detail="Approval request not found")

        workflow = request.workflow
        total_levels = len(workflow.approval_levels)

        decisions = []
        for decision in request.decisions:
            decisions.append(
                {
                    "decision_id": str(decision.decision_id),
                    "level": decision.level,
                    "approver_user_id": str(decision.approver_user_id),
                    "action": decision.action.value,
                    "comments": decision.comments,
                    "decided_at": decision.decided_at.isoformat(),
                    "mfa_verified": decision.mfa_verified,
                }
            )

        can_approve = request.status == ApprovalRequestStatus.PENDING
        can_reject = request.status == ApprovalRequestStatus.PENDING

        return ApprovalStatus(
            request_id=request.request_id,
            status=request.status.value,
            current_level=request.current_level,
            total_levels=total_levels,
            decisions=decisions,
            can_approve=can_approve,
            can_reject=can_reject,
        )

    @staticmethod
    def cancel_request(
        db: Session,
        request_id: UUID,
        cancelled_by_user_id: UUID,
        reason: str,
    ) -> ApprovalStatus:
        """
        Cancel a pending approval request.

        Only the original requester or admin can cancel.

        Args:
            db: Database session
            request_id: Approval request ID
            cancelled_by_user_id: User cancelling
            reason: Cancellation reason

        Returns:
            Updated ApprovalStatus

        Raises:
            HTTPException(404): If request not found
            HTTPException(403): If user cannot cancel
            HTTPException(400): If not in cancellable state
        """
        req_id = coerce_uuid(request_id)
        canceller_id = coerce_uuid(cancelled_by_user_id)

        request = db.get(ApprovalRequest, req_id)
        if not request:
            raise HTTPException(status_code=404, detail="Approval request not found")

        if request.status != ApprovalRequestStatus.PENDING:
            raise HTTPException(
                status_code=400,
                detail=f"Request cannot be cancelled (status: {request.status.value})",
            )

        # Check if user can cancel (must be original requester)
        if request.requested_by_user_id != canceller_id:
            raise HTTPException(
                status_code=403,
                detail="Only the original requester can cancel this request",
            )

        request.status = ApprovalRequestStatus.CANCELLED
        request.completed_at = datetime.now(UTC)
        request.notes = f"Cancelled: {reason}"

        db.commit()
        db.refresh(request)

        return ApprovalWorkflowService.get_approval_status(db, req_id)

    @staticmethod
    def _is_user_allowed_for_request(
        db: Session,
        request: ApprovalRequest,
        approver_user_id: UUID,
    ) -> tuple[bool, str | None]:
        approver_id = coerce_uuid(approver_user_id)
        workflow = request.workflow
        approval_levels = workflow.approval_levels
        if request.current_level < 1 or request.current_level > len(approval_levels):
            return (False, "Approval level is not configured")

        current_level_config = approval_levels[request.current_level - 1]

        required_count = current_level_config.get("required_count") or 1
        required_count = max(required_count, 1)

        existing_approvals = [
            d
            for d in request.decisions
            if d.level == request.current_level
            and d.action == ApprovalDecisionAction.APPROVE
        ]
        if len(existing_approvals) >= required_count:
            return (False, "Approval level already satisfied")
        if any(d.approver_user_id == approver_id for d in existing_approvals):
            return (False, "User has already approved this level")

        is_allowed, reason = ApprovalWorkflowService._is_user_allowed_for_level(
            db,
            approver_id,
            current_level_config,
        )
        if not is_allowed:
            return (False, reason)

        # Check SoD rules
        sod_rule = current_level_config.get("sod_rule")
        if sod_rule:
            previous_approvers = [d.approver_user_id for d in request.decisions]
            context = {
                "created_by_user_id": request.requested_by_user_id,
                "previous_approvers": previous_approvers,
            }
            is_valid, violation = AuthorizationService.validate_sod_rule(
                db, approver_id, sod_rule, context
            )
            if not is_valid:
                return (False, violation)

        return (True, None)

    @staticmethod
    def _is_user_allowed_for_level(
        db: Session,
        approver_user_id: UUID,
        level_config: dict[str, Any],
    ) -> tuple[bool, str | None]:
        approver_type = level_config.get("approver_type")
        approver_id = level_config.get("approver_id")

        if approver_type == "USER":
            if not approver_id:
                return (False, "Approval level is missing approver configuration")
            if coerce_uuid(approver_id) != approver_user_id:
                return (False, "User is not eligible to approve this level")
            return (True, None)

        if approver_type == "ROLE":
            if not approver_id:
                return (False, "Approval level is missing approver configuration")
            role_id = coerce_uuid(approver_id)
            has_role = (
                db.query(Role)
                .join(PersonRole, PersonRole.role_id == Role.id)
                .filter(
                    and_(
                        PersonRole.person_id == approver_user_id,
                        Role.id == role_id,
                        Role.is_active == True,  # noqa: E712
                    )
                )
                .first()
                is not None
            )
            if not has_role:
                return (False, "User is not eligible to approve this level")
            return (True, None)

        if approver_type in (None, ""):
            return (False, "Approval level is missing approver configuration")

        return (False, "Approval level uses unsupported approver type")

    @staticmethod
    def get_request(
        db: Session,
        request_id: str,
    ) -> ApprovalRequest:
        """
        Get an approval request by ID.

        Args:
            db: Database session
            request_id: Request ID

        Returns:
            ApprovalRequest

        Raises:
            HTTPException(404): If not found
        """
        request = db.get(ApprovalRequest, coerce_uuid(request_id))
        if not request:
            raise HTTPException(status_code=404, detail="Approval request not found")
        return request

    @staticmethod
    def get_workflow(
        db: Session,
        workflow_id: str,
    ) -> ApprovalWorkflow:
        """
        Get an approval workflow by ID.

        Args:
            db: Database session
            workflow_id: Workflow ID

        Returns:
            ApprovalWorkflow

        Raises:
            HTTPException(404): If not found
        """
        workflow = db.get(ApprovalWorkflow, coerce_uuid(workflow_id))
        if not workflow:
            raise HTTPException(status_code=404, detail="Approval workflow not found")
        return workflow

    @staticmethod
    def list(
        db: Session,
        organization_id: str | None = None,
        document_type: str | None = None,
        status: ApprovalRequestStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> builtins.list[ApprovalRequest]:
        """
        List approval requests (for ListResponseMixin compatibility).

        Args:
            db: Database session
            organization_id: Filter by organization
            document_type: Filter by document type
            status: Filter by status
            limit: Maximum results
            offset: Pagination offset

        Returns:
            List of ApprovalRequest objects
        """
        stmt = db.query(ApprovalRequest)

        if organization_id:
            stmt = stmt.filter(
                ApprovalRequest.organization_id == coerce_uuid(organization_id)
            )

        if document_type:
            stmt = stmt.filter(ApprovalRequest.document_type == document_type)

        if status:
            stmt = stmt.filter(ApprovalRequest.status == status)

        return (
            stmt.order_by(ApprovalRequest.requested_at.desc())
            .limit(limit)
            .offset(offset)
            .all()
        )

    @staticmethod
    def list_workflows(
        db: Session,
        organization_id: str | None = None,
        document_type: str | None = None,
        is_active: bool | None = True,
        limit: int = 50,
        offset: int = 0,
    ) -> builtins.list[ApprovalWorkflow]:
        """
        List approval workflows.

        Args:
            db: Database session
            organization_id: Filter by organization
            document_type: Filter by document type
            is_active: Filter by active status
            limit: Maximum results
            offset: Pagination offset

        Returns:
            List of ApprovalWorkflow objects
        """
        stmt = db.query(ApprovalWorkflow)

        if organization_id:
            stmt = stmt.filter(
                ApprovalWorkflow.organization_id == coerce_uuid(organization_id)
            )

        if document_type:
            stmt = stmt.filter(ApprovalWorkflow.document_type == document_type)

        if is_active is not None:
            stmt = stmt.filter(ApprovalWorkflow.is_active == is_active)

        return (
            stmt.order_by(ApprovalWorkflow.workflow_name)
            .limit(limit)
            .offset(offset)
            .all()
        )


# Module-level singleton instance
approval_workflow_service = ApprovalWorkflowService()
