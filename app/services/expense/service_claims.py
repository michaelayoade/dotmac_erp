"""Expense-claim workflow operations."""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.orm import joinedload

from app.models.expense import (
    ExpenseCategory,
    ExpenseClaim,
    ExpenseClaimAction,
    ExpenseClaimActionStatus,
    ExpenseClaimActionType,
    ExpenseClaimItem,
    ExpenseClaimStatus,
)
from app.models.finance.audit.audit_log import AuditAction
from app.services.audit_dispatcher import fire_audit_event
from app.services.common import PaginatedResult, PaginationParams
from app.services.expense.service_common import (
    ApproverAuthorityError,
    ExpenseCategoryNotFoundError,
    ExpenseClaimNotFoundError,
    ExpenseClaimStatusError,
    ExpenseLimitBlockedError,
    ExpenseServiceBase,
    ExpenseServiceError,
    SubmitClaimResult,
)

logger = logging.getLogger(__name__)


class ExpenseClaimMixin(ExpenseServiceBase):
    def list_claims(
        self,
        org_id: UUID,
        *,
        employee_id: UUID | None = None,
        status: ExpenseClaimStatus | None = None,
        from_date: date | None = None,
        to_date: date | None = None,
        search: str | None = None,
        pagination: PaginationParams | None = None,
    ) -> PaginatedResult[ExpenseClaim]:
        query = select(ExpenseClaim).where(ExpenseClaim.organization_id == org_id)

        if employee_id:
            query = query.where(ExpenseClaim.employee_id == employee_id)
        if status:
            query = query.where(ExpenseClaim.status == status)
        if from_date:
            query = query.where(ExpenseClaim.claim_date >= from_date)
        if to_date:
            query = query.where(ExpenseClaim.claim_date <= to_date)
        if search:
            search_term = f"%{search}%"
            query = query.where(
                or_(
                    ExpenseClaim.claim_number.ilike(search_term),
                    ExpenseClaim.purpose.ilike(search_term),
                )
            )

        query = query.options(joinedload(ExpenseClaim.items)).order_by(
            ExpenseClaim.claim_date.desc()
        )
        total = self.db.scalar(select(func.count()).select_from(query.subquery())) or 0

        if pagination:
            query = query.offset(pagination.offset).limit(pagination.limit)

        items = list(self.db.scalars(query).unique().all())
        return PaginatedResult(
            items=items,
            total=total,
            offset=pagination.offset if pagination else 0,
            limit=pagination.limit if pagination else len(items),
        )

    def get_claim(self, org_id: UUID, claim_id: UUID) -> ExpenseClaim:
        claim = self.db.scalar(
            select(ExpenseClaim)
            .options(joinedload(ExpenseClaim.items))
            .where(
                ExpenseClaim.claim_id == claim_id,
                ExpenseClaim.organization_id == org_id,
            )
        )
        if not claim:
            raise ExpenseClaimNotFoundError(claim_id)
        return claim

    def create_claim(
        self,
        org_id: UUID,
        *,
        employee_id: UUID | None = None,
        claim_date: date,
        purpose: str,
        expense_period_start: date | None = None,
        expense_period_end: date | None = None,
        project_id: UUID | None = None,
        ticket_id: UUID | None = None,
        task_id: UUID | None = None,
        currency_code: str = "NGN",
        cost_center_id: UUID | None = None,
        recipient_bank_code: str | None = None,
        recipient_bank_name: str | None = None,
        recipient_account_number: str | None = None,
        recipient_name: str | None = None,
        requested_approver_id: UUID | None = None,
        notes: str | None = None,
        items: list[dict] | None = None,
    ) -> ExpenseClaim:
        claim = ExpenseClaim(
            organization_id=org_id,
            employee_id=employee_id,
            claim_number=self._next_claim_number(org_id),
            claim_date=claim_date,
            purpose=purpose,
            expense_period_start=expense_period_start,
            expense_period_end=expense_period_end,
            project_id=project_id,
            ticket_id=ticket_id,
            task_id=task_id,
            currency_code=currency_code,
            cost_center_id=cost_center_id,
            recipient_bank_code=recipient_bank_code,
            recipient_bank_name=recipient_bank_name,
            recipient_account_number=recipient_account_number,
            recipient_name=recipient_name,
            requested_approver_id=requested_approver_id,
            notes=notes,
            status=ExpenseClaimStatus.DRAFT,
            total_claimed_amount=Decimal("0"),
            advance_adjusted=Decimal("0"),
        )
        self.db.add(claim)
        self.db.flush()

        total_amount = Decimal("0")
        if items:
            for idx, item_data in enumerate(items):
                category = self.db.scalar(
                    select(ExpenseCategory).where(
                        ExpenseCategory.organization_id == org_id,
                        ExpenseCategory.category_id == item_data["category_id"],
                    )
                )
                if not category:
                    raise ExpenseCategoryNotFoundError(item_data["category_id"])
                if (
                    category.max_amount_per_claim is not None
                    and item_data["claimed_amount"] > category.max_amount_per_claim
                ):
                    raise ExpenseServiceError("Claimed amount exceeds category limit")
                item = ExpenseClaimItem(
                    organization_id=org_id,
                    claim_id=claim.claim_id,
                    expense_date=item_data["expense_date"],
                    category_id=item_data["category_id"],
                    description=item_data["description"],
                    claimed_amount=item_data["claimed_amount"],
                    expense_account_id=item_data.get("expense_account_id"),
                    cost_center_id=item_data.get("cost_center_id"),
                    receipt_url=item_data.get("receipt_url"),
                    receipt_number=item_data.get("receipt_number"),
                    vendor_name=item_data.get("vendor_name"),
                    is_travel_expense=item_data.get("is_travel_expense", False),
                    travel_from=item_data.get("travel_from"),
                    travel_to=item_data.get("travel_to"),
                    distance_km=item_data.get("distance_km"),
                    notes=item_data.get("notes"),
                    sequence=idx,
                )
                self.db.add(item)
                total_amount += item_data["claimed_amount"]

        claim.total_claimed_amount = total_amount
        self.db.flush()

        fire_audit_event(
            db=self.db,
            organization_id=org_id,
            table_schema="expense",
            table_name="expense_claim",
            record_id=str(claim.claim_id),
            action=AuditAction.INSERT,
            new_values={
                "claim_number": claim.claim_number,
                "status": ExpenseClaimStatus.DRAFT.value,
                "total_claimed_amount": str(claim.total_claimed_amount),
                "purpose": claim.purpose,
            },
        )
        return claim

    def add_claim_item(
        self, org_id: UUID, claim_id: UUID, **item_data
    ) -> ExpenseClaimItem:
        claim = self.get_claim(org_id, claim_id)
        if claim.status != ExpenseClaimStatus.DRAFT:
            raise ExpenseClaimStatusError(claim.status.value, "add item")

        category = self.get_category(org_id, item_data["category_id"])
        if (
            category.max_amount_per_claim is not None
            and item_data["claimed_amount"] > category.max_amount_per_claim
        ):
            raise ExpenseServiceError("Claimed amount exceeds category limit")

        max_seq = self.db.scalar(
            select(func.max(ExpenseClaimItem.sequence)).where(
                ExpenseClaimItem.claim_id == claim_id
            )
        )
        item = ExpenseClaimItem(
            organization_id=org_id,
            claim_id=claim_id,
            expense_date=item_data["expense_date"],
            category_id=item_data["category_id"],
            description=item_data["description"],
            claimed_amount=item_data["claimed_amount"],
            expense_account_id=item_data.get("expense_account_id"),
            cost_center_id=item_data.get("cost_center_id"),
            receipt_url=item_data.get("receipt_url"),
            receipt_number=item_data.get("receipt_number"),
            vendor_name=item_data.get("vendor_name"),
            is_travel_expense=item_data.get("is_travel_expense", False),
            travel_from=item_data.get("travel_from"),
            travel_to=item_data.get("travel_to"),
            distance_km=item_data.get("distance_km"),
            notes=item_data.get("notes"),
            sequence=(max_seq or 0) + 1,
        )
        self.db.add(item)
        claim.total_claimed_amount += item.claimed_amount
        self.db.flush()
        return item

    def update_claim_item(
        self,
        org_id: UUID,
        *,
        claim_id: UUID,
        item_id: UUID,
        expense_date: date,
        category_id: UUID,
        description: str,
        claimed_amount: Decimal,
        receipt_number: str | None = None,
        receipt_url: str | None = None,
    ) -> ExpenseClaimItem:
        claim = self.get_claim(org_id, claim_id)
        if claim.status != ExpenseClaimStatus.DRAFT:
            raise ExpenseClaimStatusError(claim.status.value, "update item")

        item = self.db.scalar(
            select(ExpenseClaimItem).where(
                ExpenseClaimItem.item_id == item_id,
                ExpenseClaimItem.claim_id == claim_id,
            )
        )
        if not item:
            raise ExpenseServiceError(f"Claim item {item_id} not found")

        category = self.get_category(org_id, category_id)
        if (
            category.max_amount_per_claim is not None
            and claimed_amount > category.max_amount_per_claim
        ):
            raise ExpenseServiceError("Claimed amount exceeds category limit")

        claim.total_claimed_amount = (
            claim.total_claimed_amount - item.claimed_amount + claimed_amount
        )
        item.expense_date = expense_date
        item.category_id = category_id
        item.description = description
        item.claimed_amount = claimed_amount
        item.receipt_number = receipt_number
        item.receipt_url = receipt_url
        self.db.flush()
        return item

    def submit_claim(
        self,
        org_id: UUID,
        claim_id: UUID,
        *,
        skip_limit_check: bool = False,
        skip_receipt_validation: bool = False,
        notify_approvers: bool = True,
    ) -> SubmitClaimResult:
        from app.models.expense import LimitResultType
        from app.services.expense.approval_service import ExpenseApprovalService
        from app.services.expense.limit_service import ExpenseLimitService

        claim = self.get_claim(org_id, claim_id)
        if claim.status in {
            ExpenseClaimStatus.SUBMITTED,
            ExpenseClaimStatus.APPROVED,
            ExpenseClaimStatus.PAID,
        }:
            return SubmitClaimResult(claim=claim)
        if claim.status != ExpenseClaimStatus.DRAFT:
            raise ExpenseClaimStatusError(
                claim.status.value, ExpenseClaimStatus.SUBMITTED.value
            )
        if not claim.items:
            raise ExpenseServiceError("Cannot submit claim with no items")
        if not self._begin_action(org_id, claim_id, ExpenseClaimActionType.SUBMIT):
            return SubmitClaimResult(claim=claim)

        try:
            receipt_warnings = []
            if not skip_receipt_validation:
                approval_service = ExpenseApprovalService(self.db, self.ctx)
                validation_result = approval_service.validate_receipt_requirements(
                    claim
                )
                if not validation_result.is_valid:
                    raise ExpenseServiceError(
                        f"Missing required receipts: {'; '.join(validation_result.missing_receipts)}"
                    )
                receipt_warnings = validation_result.warnings

            evaluation_result = None
            if not skip_limit_check:
                limit_service = ExpenseLimitService(self.db, self.ctx)
                evaluation_result = limit_service.evaluate_claim(claim)
                if evaluation_result.result == LimitResultType.BLOCKED:
                    raise ExpenseLimitBlockedError(
                        evaluation_result.message,
                        evaluation_result.triggered_rule,
                    )
                if evaluation_result.result in [
                    LimitResultType.APPROVAL_REQUIRED,
                    LimitResultType.MULTI_APPROVAL_REQUIRED,
                    LimitResultType.ESCALATED,
                ]:
                    return self._finalize_submitted_claim(
                        org_id,
                        claim,
                        receipt_warnings=receipt_warnings,
                        evaluation_result=evaluation_result,
                        eligible_approvers=evaluation_result.eligible_approvers,
                        notify_approvers=notify_approvers,
                        requires_approval=True,
                    )
                if evaluation_result.result == LimitResultType.WARNING:
                    result = self._finalize_submitted_claim(
                        org_id,
                        claim,
                        receipt_warnings=[evaluation_result.message, *receipt_warnings],
                        evaluation_result=evaluation_result,
                        notify_approvers=notify_approvers,
                        requires_approval=False,
                    )
                    result.requires_approval = (
                        result.claim.status == ExpenseClaimStatus.PENDING_APPROVAL
                    )
                    return result

            return self._finalize_submitted_claim(
                org_id,
                claim,
                receipt_warnings=receipt_warnings,
                evaluation_result=evaluation_result,
                notify_approvers=notify_approvers,
                requires_approval=False,
            )
        except Exception:
            self._set_action_status(
                org_id,
                claim_id,
                ExpenseClaimActionType.SUBMIT,
                ExpenseClaimActionStatus.FAILED,
            )
            raise

    def _finalize_submitted_claim(
        self,
        org_id: UUID,
        claim: ExpenseClaim,
        *,
        receipt_warnings: list[str],
        evaluation_result=None,
        eligible_approvers=None,
        notify_approvers: bool,
        requires_approval: bool,
    ) -> SubmitClaimResult:
        from app.services.expense.approval_service import ExpenseApprovalService

        approval_service = ExpenseApprovalService(self.db, self.ctx)
        chain = approval_service.initialize_approval_chain(claim)
        claim.status = (
            ExpenseClaimStatus.PENDING_APPROVAL
            if chain.steps
            else ExpenseClaimStatus.SUBMITTED
        )
        self.db.flush()

        fire_audit_event(
            db=self.db,
            organization_id=org_id,
            table_schema="expense",
            table_name="expense_claim",
            record_id=str(claim.claim_id),
            action=AuditAction.UPDATE,
            old_values={"status": ExpenseClaimStatus.DRAFT.value},
            new_values={"status": claim.status.value},
        )

        if notify_approvers and eligible_approvers:
            self._notify_approvers(claim, eligible_approvers)
        self._notify_submission_confirmed(claim)

        try:
            from app.services.finance.automation.event_dispatcher import (
                fire_workflow_event,
            )

            fire_workflow_event(
                db=self.db,
                organization_id=org_id,
                entity_type="EXPENSE",
                entity_id=claim.claim_id,
                event="ON_STATUS_CHANGE",
                old_values={"status": "DRAFT"},
                new_values={"status": claim.status.value},
                user_id=claim.employee_id,
            )
        except Exception as exc:
            logger.exception(
                "Workflow event failed for claim %s: %s", claim.claim_id, exc
            )

        self._set_action_status(
            org_id,
            claim.claim_id,
            ExpenseClaimActionType.SUBMIT,
            ExpenseClaimActionStatus.COMPLETED,
        )
        warning_message = "; ".join(receipt_warnings) if receipt_warnings else None
        return SubmitClaimResult(
            claim=claim,
            evaluation_result=evaluation_result,
            requires_approval=requires_approval
            or claim.status == ExpenseClaimStatus.PENDING_APPROVAL,
            eligible_approvers=eligible_approvers or [],
            warning_message=warning_message,
        )

    def _notify_submission_confirmed(self, claim: ExpenseClaim) -> None:
        if not claim.employee or not claim.employee.person_id:
            return
        try:
            from app.models.notification import (
                EntityType,
                NotificationChannel,
                NotificationType,
            )
            from app.services.notification import NotificationService

            NotificationService().create(
                self.db,
                organization_id=claim.organization_id,
                recipient_id=claim.employee.person_id,
                entity_type=EntityType.EXPENSE,
                entity_id=claim.claim_id,
                notification_type=NotificationType.STATUS_CHANGE,
                title=f"Expense {claim.claim_number} submitted",
                message="Your expense claim has been submitted for approval.",
                channel=NotificationChannel.IN_APP,
                action_url=f"/expense/claims/{claim.claim_id}",
                actor_id=claim.employee.person_id,
            )
        except Exception as exc:
            logger.exception("Submission confirmation notification failed: %s", exc)

    def _notify_approvers(self, claim: ExpenseClaim, approvers) -> None:
        from app.models.people.hr.employee import Employee
        from app.services.expense.expense_notifications import (
            ExpenseNotificationService,
        )
        from app.services.notification import NotificationService

        email_service = ExpenseNotificationService(self.db)
        inapp_service = NotificationService()
        submitter_name = claim.employee.full_name if claim.employee else ""

        for approver_info in approvers[:3]:
            approver = self.db.get(Employee, approver_info.employee_id)
            if not approver:
                continue
            try:
                email_service.notify_approval_needed(claim, approver)
            except Exception as exc:
                logger.exception(
                    "Email notification failed for approver %s: %s",
                    approver.employee_id,
                    exc,
                )
            if approver.person_id:
                try:
                    inapp_service.notify_expense_submitted(
                        self.db,
                        organization_id=claim.organization_id,
                        claim_id=claim.claim_id,
                        claim_number=claim.claim_number,
                        recipient_id=approver.person_id,
                        submitter_name=submitter_name,
                        amount=str(claim.total_claimed_amount),
                        actor_id=claim.employee.person_id if claim.employee else None,
                    )
                except Exception as exc:
                    logger.exception(
                        "In-app notification failed for approver %s: %s",
                        approver.employee_id,
                        exc,
                    )

    def approve_claim(
        self,
        org_id: UUID,
        claim_id: UUID,
        *,
        approver_id: UUID | None = None,
        approved_amounts: list[dict] | None = None,
        corrections: list[dict] | None = None,
        notes: str | None = None,
        auto_post_gl: bool = False,
        create_supplier_invoice: bool = False,
        send_notification: bool = True,
    ) -> ExpenseClaim:
        from app.models.people.hr.employee import Employee
        from app.services.expense.approval_service import ExpenseApprovalService

        claim = self.get_claim(org_id, claim_id)
        old_status = claim.status.value
        if claim.status in {ExpenseClaimStatus.APPROVED, ExpenseClaimStatus.PAID}:
            return claim
        if (
            approver_id is None
            and isinstance(claim, ExpenseClaim)
            and claim.status == ExpenseClaimStatus.PENDING_APPROVAL
        ):
            raise ExpenseServiceError("Approver must be linked to an employee record")
        if claim.status not in {
            ExpenseClaimStatus.SUBMITTED,
            ExpenseClaimStatus.PENDING_APPROVAL,
        }:
            raise ExpenseClaimStatusError(
                claim.status.value, ExpenseClaimStatus.APPROVED.value
            )

        try:
            if approver_id is not None:
                self._validate_approver_authority(org_id, claim, approver_id)
                self._validate_approver_weekly_budget(org_id, claim, approver_id)

            approver = (
                self.db.get(Employee, approver_id) if approver_id is not None else None
            )
            if approver_id is not None and approver is None:
                raise ExpenseServiceError(
                    "Approver must be linked to an employee record"
                )
            claimant = (
                self.db.get(Employee, claim.employee_id) if claim.employee_id else None
            )
            if (
                approver
                and claimant
                and approver.person_id
                and claimant.person_id
                and approver.person_id == claimant.person_id
            ):
                raise ExpenseServiceError("Cannot approve your own expense claim")

            if approver_id is not None and isinstance(claim, ExpenseClaim):
                chain = ExpenseApprovalService(
                    self.db, self.ctx
                ).process_approval_decision(
                    claim,
                    approver_id,
                    "APPROVED",
                    notes=notes,
                    approved_amounts=approved_amounts,
                )
                if not chain.is_complete:
                    claim.status = ExpenseClaimStatus.PENDING_APPROVAL
                    self.db.flush()
                    return claim
            if not self._begin_action(org_id, claim_id, ExpenseClaimActionType.APPROVE):
                return claim

            claim.status = ExpenseClaimStatus.APPROVED
            claim.approver_id = approver_id
            claim.approved_on = date.today()
            if notes:
                claim.approval_notes = notes

            corrections_map: dict[str, dict[str, Any]] = {}
            correction_audit: list[dict[str, str]] = []
            if corrections:
                for correction_entry in corrections:
                    corrections_map[str(correction_entry["item_id"])] = correction_entry

            total_approved = Decimal("0")
            for item in claim.items:
                item_key = str(item.item_id)
                correction_data = corrections_map.get(item_key)
                if correction_data:
                    amount = Decimal(str(correction_data["approved_amount"]))
                    changed = False
                    audit_entry: dict[str, str] = {"item_id": item_key}
                    if amount != item.claimed_amount:
                        item.original_claimed_amount = item.claimed_amount
                        audit_entry["amount"] = f"{item.claimed_amount} -> {amount}"
                        changed = True
                    item.approved_amount = amount
                    new_cat_id = correction_data.get("category_id")
                    if new_cat_id and str(new_cat_id) != str(item.category_id):
                        item.original_category_id = item.category_id
                        item.category_id = (
                            new_cat_id
                            if isinstance(new_cat_id, UUID)
                            else UUID(str(new_cat_id))
                        )
                        audit_entry["category_changed"] = "true"
                        changed = True
                    new_desc = correction_data.get("description")
                    if new_desc and new_desc.strip() != item.description:
                        item.original_description = item.description
                        item.description = new_desc.strip()
                        audit_entry["description_changed"] = "true"
                        changed = True
                    if changed:
                        item.was_corrected = True
                        correction_audit.append(audit_entry)
                    total_approved += amount
                elif approved_amounts:
                    matched = next(
                        (
                            value
                            for value in approved_amounts
                            if str(value["item_id"]) == item_key
                        ),
                        None,
                    )
                    item.approved_amount = (
                        matched["approved_amount"] if matched else item.claimed_amount
                    )
                    total_approved += item.approved_amount
                else:
                    item.approved_amount = item.claimed_amount
                    total_approved += item.claimed_amount

            claim.total_approved_amount = total_approved
            claim.net_payable_amount = total_approved - claim.advance_adjusted
            self.db.flush()

            if auto_post_gl and approver_id:
                from app.services.expense.expense_posting_adapter import (
                    ExpensePostingAdapter,
                )

                posting_result = ExpensePostingAdapter.post_expense_claim(
                    self.db,
                    org_id,
                    claim_id,
                    date.today(),
                    approver_id,
                    auto_post=True,
                )
                if not posting_result.success:
                    logger.warning(
                        "GL posting failed for claim %s: %s",
                        claim_id,
                        posting_result.message,
                    )

            if create_supplier_invoice and approver_id:
                from app.services.expense.expense_posting_adapter import (
                    ExpensePostingAdapter,
                )

                invoice_result = (
                    ExpensePostingAdapter.create_supplier_invoice_from_expense(
                        self.db,
                        org_id,
                        claim_id,
                        approver_id,
                    )
                )
                if not invoice_result.success:
                    logger.warning(
                        "Supplier invoice creation failed for claim %s: %s",
                        claim_id,
                        invoice_result.message,
                    )

            if send_notification:
                self._notify_claim_approved(claim, org_id, approver_id)

            self.db.flush()
            audit_new_values: dict[str, str | list[dict[str, str]]] = {
                "status": ExpenseClaimStatus.APPROVED.value,
                "total_approved_amount": str(claim.total_approved_amount),
            }
            if correction_audit:
                audit_new_values["corrections"] = correction_audit
            if notes:
                audit_new_values["approval_notes"] = notes
            fire_audit_event(
                db=self.db,
                organization_id=org_id,
                table_schema="expense",
                table_name="expense_claim",
                record_id=str(claim.claim_id),
                action=AuditAction.UPDATE,
                old_values={"status": old_status},
                new_values=audit_new_values,
            )
            try:
                from app.services.finance.automation.event_dispatcher import (
                    fire_workflow_event,
                )

                fire_workflow_event(
                    db=self.db,
                    organization_id=org_id,
                    entity_type="EXPENSE",
                    entity_id=claim.claim_id,
                    event="ON_APPROVAL",
                    old_values={"status": old_status},
                    new_values={
                        "status": "APPROVED",
                        "total_approved_amount": str(claim.total_approved_amount),
                    },
                    user_id=approver_id,
                )
            except Exception as exc:
                logger.exception(
                    "Workflow event failed for claim %s: %s", claim_id, exc
                )

            self._set_action_status(
                org_id,
                claim_id,
                ExpenseClaimActionType.APPROVE,
                ExpenseClaimActionStatus.COMPLETED,
            )
            return claim
        except Exception:
            record = self.db.scalar(
                select(ExpenseClaimAction).where(
                    ExpenseClaimAction.organization_id == org_id,
                    ExpenseClaimAction.claim_id == claim_id,
                    ExpenseClaimAction.action_type == ExpenseClaimActionType.APPROVE,
                )
            )
            if record:
                self._set_action_status(
                    org_id,
                    claim_id,
                    ExpenseClaimActionType.APPROVE,
                    ExpenseClaimActionStatus.FAILED,
                )
            raise

    def _notify_claim_approved(
        self,
        claim: ExpenseClaim,
        org_id: UUID,
        approver_id: UUID | None,
    ) -> None:
        from app.models.people.hr.employee import Employee
        from app.services.expense.expense_notifications import (
            ExpenseNotificationService,
        )
        from app.services.notification import NotificationService

        approver = (
            self.db.get(Employee, approver_id) if approver_id is not None else None
        )
        approver_name = approver.full_name if approver else None
        if claim.employee and claim.employee.work_email:
            try:
                ExpenseNotificationService(self.db).notify_claim_approved(
                    claim, approver_name=approver_name
                )
            except Exception as exc:
                logger.exception("Email approval notification failed: %s", exc)
        if claim.employee and claim.employee.person_id:
            try:
                NotificationService().notify_expense_approved(
                    self.db,
                    organization_id=org_id,
                    claim_id=claim.claim_id,
                    claim_number=claim.claim_number,
                    recipient_id=claim.employee.person_id,
                    approver_name=approver_name or "Manager",
                    actor_id=approver.person_id if approver else None,
                )
            except Exception as exc:
                logger.exception("In-app approval notification failed: %s", exc)

    @staticmethod
    def ensure_gl_posted(
        db, claim: ExpenseClaim, posted_by_user_id: UUID | None = None
    ) -> bool:
        postable_statuses = {
            ExpenseClaimStatus.APPROVED,
            ExpenseClaimStatus.PAID,
        }
        if claim.status not in postable_statuses or claim.journal_entry_id is not None:
            return False
        if claim.total_approved_amount == Decimal("0"):
            return False
        try:
            from app.services.expense.expense_posting_adapter import (
                ExpensePostingAdapter,
            )

            user_id = (
                posted_by_user_id
                or claim.created_by_id
                or UUID("00000000-0000-0000-0000-000000000000")
            )
            result = ExpensePostingAdapter.post_expense_claim(
                db=db,
                organization_id=claim.organization_id,
                claim_id=claim.claim_id,
                posting_date=claim.claim_date,
                posted_by_user_id=user_id,
                auto_post=True,
                idempotency_key=f"ensure-gl-exp-{claim.claim_id}",
            )
            if result.success and result.journal_entry_id is not None:
                claim.journal_entry_id = result.journal_entry_id
                logger.info(
                    "Auto-posted expense claim %s (journal %s)",
                    claim.claim_id,
                    result.journal_entry_id,
                )
                return True
            if result.success and result.journal_entry_id is None:
                logger.warning(
                    "Auto-post returned success without journal for expense claim %s: %s",
                    claim.claim_id,
                    result.message,
                )
                return False
            logger.warning(
                "Auto-post failed for expense claim %s: %s",
                claim.claim_id,
                result.message,
            )
            return False
        except Exception as exc:
            logger.exception(
                "Error auto-posting expense claim %s: %s", claim.claim_id, exc
            )
            return False

    def _validate_approver_authority(
        self, org_id: UUID, claim: ExpenseClaim, approver_id: UUID
    ) -> None:
        from app.models.people.hr.employee import Employee
        from app.services.expense.approval_service import ExpenseApprovalService

        approver = self.db.get(Employee, approver_id)
        if not approver:
            return
        approval_svc = ExpenseApprovalService(self.db, self.ctx)
        max_amount = approval_svc._get_approver_max_amount(org_id, approver)
        if max_amount is None:
            return
        claim_amount = claim.total_claimed_amount or Decimal("0")
        if claim_amount > max_amount:
            logger.warning(
                "Approver %s authority (%s) insufficient for claim %s amount (%s)",
                approver_id,
                max_amount,
                claim.claim_id,
                claim_amount,
            )
            raise ApproverAuthorityError(claim_amount, max_amount)

    def _validate_approver_monthly_budget(
        self, org_id: UUID, claim: ExpenseClaim, approver_id: UUID
    ) -> None:
        from app.services.expense.limit_service import ExpenseLimitService

        ExpenseLimitService(self.db, self.ctx).check_approver_monthly_budget(
            org_id,
            approver_id,
            claim.total_claimed_amount or Decimal("0"),
            claim.claim_date or date.today(),
        )

    def _validate_approver_weekly_budget(
        self, org_id: UUID, claim: ExpenseClaim, approver_id: UUID
    ) -> None:
        from app.services.expense.limit_service import ExpenseLimitService

        ExpenseLimitService(self.db, self.ctx).check_approver_weekly_budget(
            org_id,
            approver_id,
            claim.total_claimed_amount or Decimal("0"),
        )

    def reject_claim(
        self,
        org_id: UUID,
        claim_id: UUID,
        *,
        approver_id: UUID | None = None,
        reason: str,
        send_notification: bool = True,
    ) -> ExpenseClaim:
        from app.models.people.hr.employee import Employee
        from app.services.expense.approval_service import ExpenseApprovalService

        claim = self.get_claim(org_id, claim_id)
        old_status = claim.status.value
        if claim.status == ExpenseClaimStatus.REJECTED:
            return claim
        if approver_id is None:
            raise ExpenseServiceError("Approver must be linked to an employee record")
        if claim.status not in {
            ExpenseClaimStatus.SUBMITTED,
            ExpenseClaimStatus.PENDING_APPROVAL,
        }:
            raise ExpenseClaimStatusError(
                claim.status.value, ExpenseClaimStatus.REJECTED.value
            )
        try:
            approver = self.db.get(Employee, approver_id)
            claimant = (
                self.db.get(Employee, claim.employee_id) if claim.employee_id else None
            )
            if (
                approver
                and claimant
                and approver.person_id
                and claimant.person_id
                and approver.person_id == claimant.person_id
            ):
                raise ExpenseServiceError("Cannot reject your own expense claim")

            ExpenseApprovalService(self.db, self.ctx).process_approval_decision(
                claim, approver_id, "REJECTED", notes=reason
            )
            if not self._begin_action(org_id, claim_id, ExpenseClaimActionType.REJECT):
                return claim

            claim.status = ExpenseClaimStatus.REJECTED
            claim.approver_id = approver_id
            claim.rejection_reason = reason

            if send_notification:
                self._notify_claim_rejected(claim, org_id, approver, reason)

            self.db.flush()
            fire_audit_event(
                db=self.db,
                organization_id=org_id,
                table_schema="expense",
                table_name="expense_claim",
                record_id=str(claim.claim_id),
                action=AuditAction.UPDATE,
                old_values={"status": old_status},
                new_values={
                    "status": ExpenseClaimStatus.REJECTED.value,
                    "rejection_reason": reason,
                },
            )
            try:
                from app.services.finance.automation.event_dispatcher import (
                    fire_workflow_event,
                )

                fire_workflow_event(
                    db=self.db,
                    organization_id=org_id,
                    entity_type="EXPENSE",
                    entity_id=claim.claim_id,
                    event="ON_REJECTION",
                    old_values={"status": old_status},
                    new_values={"status": "REJECTED", "rejection_reason": reason},
                    user_id=approver_id,
                )
            except Exception as exc:
                logger.exception(
                    "Workflow event failed for claim %s: %s", claim_id, exc
                )

            self._set_action_status(
                org_id,
                claim_id,
                ExpenseClaimActionType.REJECT,
                ExpenseClaimActionStatus.COMPLETED,
            )
            return claim
        except Exception:
            record = self.db.scalar(
                select(ExpenseClaimAction).where(
                    ExpenseClaimAction.organization_id == org_id,
                    ExpenseClaimAction.claim_id == claim_id,
                    ExpenseClaimAction.action_type == ExpenseClaimActionType.REJECT,
                )
            )
            if record:
                self._set_action_status(
                    org_id,
                    claim_id,
                    ExpenseClaimActionType.REJECT,
                    ExpenseClaimActionStatus.FAILED,
                )
            raise

    def _notify_claim_rejected(
        self, claim: ExpenseClaim, org_id: UUID, approver, reason: str
    ) -> None:
        from app.services.expense.expense_notifications import (
            ExpenseNotificationService,
        )
        from app.services.notification import NotificationService

        approver_name = approver.full_name if approver else None
        if claim.employee and claim.employee.work_email:
            try:
                ExpenseNotificationService(self.db).notify_claim_rejected(
                    claim,
                    reason,
                    approver_name=approver_name,
                )
            except Exception as exc:
                logger.exception("Email rejection notification failed: %s", exc)
        if claim.employee and claim.employee.person_id:
            try:
                NotificationService().notify_expense_rejected(
                    self.db,
                    organization_id=org_id,
                    claim_id=claim.claim_id,
                    claim_number=claim.claim_number,
                    recipient_id=claim.employee.person_id,
                    rejector_name=approver_name or "Manager",
                    reason=reason,
                    actor_id=approver.person_id if approver else None,
                )
            except Exception as exc:
                logger.exception("In-app rejection notification failed: %s", exc)

    def update_claim(self, org_id: UUID, claim_id: UUID, **kwargs) -> ExpenseClaim:
        claim = self.get_claim(org_id, claim_id)
        if claim.status != ExpenseClaimStatus.DRAFT:
            raise ExpenseClaimStatusError(claim.status.value, "update")
        for key, value in kwargs.items():
            if value is not None and hasattr(claim, key):
                setattr(claim, key, value)
        self.db.flush()
        return claim

    def delete_claim(self, org_id: UUID, claim_id: UUID) -> None:
        claim = self.get_claim(org_id, claim_id)
        if claim.status != ExpenseClaimStatus.DRAFT:
            raise ExpenseClaimStatusError(claim.status.value, "delete")
        self.db.execute(
            select(ExpenseClaimItem).where(ExpenseClaimItem.claim_id == claim_id)
        )
        for item in claim.items:
            self.db.delete(item)
        self.db.delete(claim)
        self.db.flush()

    def remove_claim_item(self, org_id: UUID, claim_id: UUID, item_id: UUID) -> None:
        claim = self.get_claim(org_id, claim_id)
        if claim.status != ExpenseClaimStatus.DRAFT:
            raise ExpenseClaimStatusError(claim.status.value, "remove item")
        item = self.db.scalar(
            select(ExpenseClaimItem).where(
                ExpenseClaimItem.item_id == item_id,
                ExpenseClaimItem.claim_id == claim_id,
            )
        )
        if not item:
            raise ExpenseServiceError(f"Claim item {item_id} not found")
        claim.total_claimed_amount -= item.claimed_amount
        self.db.delete(item)
        self.db.flush()

    def mark_paid(
        self,
        org_id: UUID,
        claim_id: UUID,
        *,
        payment_reference: str | None = None,
        payment_date: date | None = None,
        send_notification: bool = True,
    ) -> ExpenseClaim:
        claim = self.get_claim(org_id, claim_id)
        if claim.status == ExpenseClaimStatus.PAID:
            return claim
        if claim.status != ExpenseClaimStatus.APPROVED:
            raise ExpenseClaimStatusError(
                claim.status.value, ExpenseClaimStatus.PAID.value
            )
        if not self._begin_action(org_id, claim_id, ExpenseClaimActionType.MARK_PAID):
            return claim
        try:
            claim.status = ExpenseClaimStatus.PAID
            claim.paid_on = payment_date or date.today()
            claim.payment_reference = payment_reference
            if send_notification and claim.employee and claim.employee.work_email:
                from app.services.expense.expense_notifications import (
                    ExpenseNotificationService,
                )

                ExpenseNotificationService(self.db).notify_claim_paid(
                    claim,
                    payment_reference=payment_reference,
                    payment_date=claim.paid_on,
                )
            self.db.flush()
            self._set_action_status(
                org_id,
                claim_id,
                ExpenseClaimActionType.MARK_PAID,
                ExpenseClaimActionStatus.COMPLETED,
            )
            return claim
        except Exception:
            self._set_action_status(
                org_id,
                claim_id,
                ExpenseClaimActionType.MARK_PAID,
                ExpenseClaimActionStatus.FAILED,
            )
            raise

    def cancel_claim(
        self,
        org_id: UUID,
        claim_id: UUID,
        *,
        reason: str | None = None,
    ) -> ExpenseClaim:
        claim = self.get_claim(org_id, claim_id)
        if claim.status == ExpenseClaimStatus.CANCELLED:
            return claim
        if claim.status not in {
            ExpenseClaimStatus.DRAFT,
            ExpenseClaimStatus.SUBMITTED,
            ExpenseClaimStatus.PENDING_APPROVAL,
        }:
            raise ExpenseClaimStatusError(
                claim.status.value, ExpenseClaimStatus.CANCELLED.value
            )
        if not self._begin_action(org_id, claim_id, ExpenseClaimActionType.CANCEL):
            return claim
        try:
            old_status = claim.status.value
            claim.status = ExpenseClaimStatus.CANCELLED
            if reason:
                claim.notes = (
                    f"{claim.notes}\n\nCancelled: {reason}"
                    if claim.notes
                    else f"Cancelled: {reason}"
                )
            self.db.flush()
            fire_audit_event(
                db=self.db,
                organization_id=org_id,
                table_schema="expense",
                table_name="expense_claim",
                record_id=str(claim.claim_id),
                action=AuditAction.UPDATE,
                old_values={"status": old_status},
                new_values={"status": ExpenseClaimStatus.CANCELLED.value},
            )
            self._set_action_status(
                org_id,
                claim_id,
                ExpenseClaimActionType.CANCEL,
                ExpenseClaimActionStatus.COMPLETED,
            )
            logger.info("Cancelled expense claim %s (was %s)", claim_id, old_status)
            return claim
        except Exception:
            self._set_action_status(
                org_id,
                claim_id,
                ExpenseClaimActionType.CANCEL,
                ExpenseClaimActionStatus.FAILED,
            )
            raise

    def resubmit_claim(self, org_id: UUID, claim_id: UUID) -> ExpenseClaim:
        claim = self.get_claim(org_id, claim_id)
        if claim.status != ExpenseClaimStatus.REJECTED:
            raise ExpenseClaimStatusError(
                claim.status.value, ExpenseClaimStatus.DRAFT.value
            )
        old_status = claim.status.value
        claim.status = ExpenseClaimStatus.DRAFT
        claim.rejection_reason = None
        claim.approver_id = None
        claim.approved_on = None
        claim.approval_notes = None
        claim.total_approved_amount = None
        claim.net_payable_amount = None
        for item in claim.items:
            item.approved_amount = None
        self._reset_workflow_action_markers(org_id, claim_id)
        self.db.flush()
        fire_audit_event(
            db=self.db,
            organization_id=org_id,
            table_schema="expense",
            table_name="expense_claim",
            record_id=str(claim.claim_id),
            action=AuditAction.UPDATE,
            old_values={"status": old_status},
            new_values={"status": ExpenseClaimStatus.DRAFT.value},
        )
        logger.info("Resubmit: reset claim %s to DRAFT", claim_id)
        return claim

    def link_advance(
        self,
        org_id: UUID,
        claim_id: UUID,
        advance_id: UUID,
        amount_to_adjust: Decimal,
    ) -> ExpenseClaim:
        claim = self.get_claim(org_id, claim_id)
        self.get_advance(org_id, advance_id)
        if claim.status not in {ExpenseClaimStatus.DRAFT, ExpenseClaimStatus.SUBMITTED}:
            raise ExpenseClaimStatusError(claim.status.value, "link advance")
        if not self._begin_action(
            org_id, claim_id, ExpenseClaimActionType.LINK_ADVANCE
        ):
            return claim
        try:
            claim.cash_advance_id = advance_id
            claim.advance_adjusted = amount_to_adjust
            if claim.total_approved_amount:
                claim.net_payable_amount = (
                    claim.total_approved_amount - amount_to_adjust
                )
            self.db.flush()
            self._set_action_status(
                org_id,
                claim_id,
                ExpenseClaimActionType.LINK_ADVANCE,
                ExpenseClaimActionStatus.COMPLETED,
            )
            return claim
        except Exception:
            self._set_action_status(
                org_id,
                claim_id,
                ExpenseClaimActionType.LINK_ADVANCE,
                ExpenseClaimActionStatus.FAILED,
            )
            raise
