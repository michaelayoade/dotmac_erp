"""Expense-total roll-ups and Sub → ERP expense-claim sync.

Extracted from the former monolithic dotmac_sub_sync_service.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import date, datetime, timezone

try:
    from datetime import UTC  # type: ignore
except ImportError:  # pragma: no cover
    UTC = timezone.utc

from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload


if TYPE_CHECKING:
    from app.models.finance.ap.supplier import Supplier  # noqa: F401
    from app.services.finance.payments.payment_service import PaymentService

from app.models.expense.expense_claim import (
    ExpenseCategory,
    ExpenseClaim,
    ExpenseClaimStatus,
)
from app.models.finance.payments.payment_intent import (
    PaymentIntent,
    PaymentIntentStatus,
)
from app.models.people.hr.employee import Employee
from app.schemas.sync.sub_operational import (
    SubExpenseCategoriesResponse,
    SubExpenseCategoryItem,
    SubExpenseClaimPayload,
    SubExpenseClaimDecisionPayload,
    SubExpenseClaimRejectionPayload,
    SubExpenseClaimResponse,
    SubExpenseClaimStatusResponse,
    SubExpensePaymentPayload,
    SubExpensePaymentResponse,
)
from app.services.finance.payments.paystack_client import PaystackConfig, PaystackError

# Sub → ERP translation policy lives in sub_mappings (pure, side-effect-free).
# Re-imported here so the canonical import sites
# (`from ...dotmac_sub_sync_service import PROJECT_STATUS_MAP`) and the in-class
# references keep resolving against this module's namespace.

from app.services.sync.sub.base import _SubSyncBase

logger = logging.getLogger(__name__)


class _ExpenseSyncMixin(_SubSyncBase):
    # ------------------------------------------------------------------
    # Sub → ERP expense-claim sync (field-technician expense requests)
    # ------------------------------------------------------------------

    def _find_claim_by_source_claim_id(
        self, org_id: UUID, source_claim_id: str
    ) -> ExpenseClaim | None:
        """Load an existing claim (with items) for this Sub source_claim_id, if any."""
        return self.db.scalar(
            select(ExpenseClaim)
            .options(joinedload(ExpenseClaim.items))
            .where(
                ExpenseClaim.organization_id == org_id,
                ExpenseClaim.source_system == "sub",
                ExpenseClaim.source_reference == source_claim_id,
            )
        )

    def create_expense_claim(
        self,
        org_id: UUID,
        data: SubExpenseClaimPayload,
        created_by_person_id: UUID | None = None,
    ) -> SubExpenseClaimResponse:
        """
        Create-and-submit an expense claim from a Sub expense request.

        Immutable idempotency by source_claim_id (mirrors material requests):
        - first send creates the claim and submits it into the approval flow
        - identical resend returns the existing claim unchanged
        - changed resend is rejected (Sub must create a new expense request)

        Raises:
            HTTPException 422: unknown employee email / category codes, or
                submit-side validation failures (missing receipts, limits).
            HTTPException 409: changed resend of an existing source_claim_id.
            ValueError: malformed dates.
        """
        from app.services.expense import ExpenseService, ExpenseServiceError
        from app.services.expense.service_claims import ExpenseClaimApprovalSource
        from app.services.common import ValidationError
        from app.services.finance.platform.org_context import org_context_service

        employee_id = self._resolve_employee_id(org_id, data.requested_by_email)
        if employee_id is None:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"No ERP employee matches email {data.requested_by_email}; "
                    "cannot create expense claim."
                ),
            )

        claim_date_val = self._parse_sub_date(data.claim_date, "claim_date")

        # Optional cross-references — ignore when unmapped, never fail.
        project_id = self._resolve_project_id(org_id, data.project_source_reference)
        ticket_id = self._resolve_ticket_id(org_id, data.ticket_source_reference)

        categories = self._resolve_expense_categories(org_id, data)

        notes_parts: list[str] = []
        if data.reference_number:
            notes_parts.append(f"Sub expense request: {data.reference_number}")
        if data.remarks:
            notes_parts.append(data.remarks)
        notes = "\n".join(notes_parts) or None

        resolved_items: list[dict[str, Any]] = []
        for seq, item in enumerate(data.items):
            expense_date_val = (
                self._parse_sub_date(item.expense_date, "expense_date")
                if item.expense_date
                else claim_date_val
            )
            resolved_items.append(
                {
                    "sequence": seq,
                    "category_id": categories[item.category_code].category_id,
                    "expense_date": expense_date_val,
                    "description": item.description,
                    "claimed_amount": item.claimed_amount,
                    "receipt_url": item.receipt_url,
                    "vendor_name": item.vendor_name,
                    "notes": item.notes,
                }
            )

        effective_currency = (
            data.currency_code
            or org_context_service.get_functional_currency(self.db, org_id)
        )
        incoming_fingerprint = self._build_expense_claim_fingerprint(
            employee_id=employee_id,
            claim_date=claim_date_val,
            purpose=data.purpose,
            project_id=project_id,
            ticket_id=ticket_id,
            currency_code=effective_currency,
            notes=notes,
            items=resolved_items,
        )

        existing = self._find_claim_by_source_claim_id(org_id, data.source_claim_id)
        if existing is not None:
            existing_fingerprint = self._build_expense_claim_fingerprint(
                employee_id=existing.employee_id,
                claim_date=existing.claim_date,
                purpose=existing.purpose,
                project_id=existing.project_id,
                ticket_id=existing.ticket_id,
                currency_code=existing.currency_code,
                notes=existing.notes,
                items=[
                    {
                        "sequence": line.sequence,
                        "category_id": line.category_id,
                        "expense_date": line.expense_date,
                        "description": line.description,
                        "claimed_amount": line.claimed_amount,
                        "receipt_url": line.receipt_url,
                        "vendor_name": line.vendor_name,
                        "notes": line.notes,
                    }
                    for line in existing.items
                ],
            )
            if incoming_fingerprint != existing_fingerprint:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Expense claim already exists and cannot be modified; "
                        "create a new Sub expense request."
                    ),
                )
            logger.info(
                "Sub expense claim duplicate accepted unchanged "
                "(source_claim_id=%s, claim_number=%s)",
                data.source_claim_id,
                existing.claim_number,
            )
            return SubExpenseClaimResponse(
                claim_id=existing.claim_id,
                claim_number=existing.claim_number,
                status=existing.status.value.lower(),
                source_claim_id=data.source_claim_id,
            )

        employee = self.db.scalar(
            select(Employee).where(
                Employee.organization_id == org_id,
                Employee.employee_id == employee_id,
            )
        )
        if employee is None:
            raise HTTPException(status_code=422, detail="ERP employee was not found")

        service = ExpenseService(self.db)
        # Create inside a savepoint so a concurrent first-send of the same
        # source_claim_id — where both requests passed the existence check above —
        # degrades gracefully: the loser hits uq_expense_claim_org_source_reference, rolls
        # back its own partial insert, and returns the winner's claim instead of
        # a 500. Both requests carry the same Sub expense request, so returning
        # the existing claim matches the immutable-idempotent contract. Mirrors
        # the race handling in _get_or_create_default_project.
        savepoint = self.db.begin_nested()
        try:
            claim = service.create_claim(
                org_id,
                employee_id=employee_id,
                claim_date=claim_date_val,
                purpose=data.purpose,
                project_id=project_id,
                ticket_id=ticket_id,
                currency_code=data.currency_code,
                notes=notes,
                items=resolved_items,
                recipient_bank_code=employee.bank_branch_code,
                recipient_bank_name=employee.bank_name,
                recipient_account_number=employee.bank_account_number,
                recipient_name=employee.bank_account_name or employee.full_name,
                created_by_id=created_by_person_id,
            )
            claim.source_reference = data.source_claim_id
            claim.source_system = "sub"
            claim.last_synced_at = datetime.now(UTC)
            self.db.flush()
            service.submit_claim(
                org_id,
                claim.claim_id,
                notify_approvers=True,
                actor_id=created_by_person_id,
                approval_source=ExpenseClaimApprovalSource.TRUSTED_SUB_MANAGER,
            )
            savepoint.commit()
        except (ExpenseServiceError, ValidationError) as exc:
            # Surface a readable validation error (missing receipts, category
            # limits, blocked limit rules, …) so the Sub records the reason.
            savepoint.rollback()
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except IntegrityError:
            # Lost the create race for this source_claim_id — return the winner's claim.
            savepoint.rollback()
            raced = self.db.scalar(
                select(ExpenseClaim).where(
                    ExpenseClaim.organization_id == org_id,
                    ExpenseClaim.source_system == "sub",
                    ExpenseClaim.source_reference == data.source_claim_id,
                )
            )
            if raced is None:
                raise  # not the source_reference collision we anticipated — surface it
            logger.info(
                "Sub expense claim create raced; returning existing "
                "(source_claim_id=%s, claim_number=%s)",
                data.source_claim_id,
                raced.claim_number,
            )
            return SubExpenseClaimResponse(
                claim_id=raced.claim_id,
                claim_number=raced.claim_number,
                status=raced.status.value.lower(),
                source_claim_id=data.source_claim_id,
            )

        logger.info(
            "Sub expense claim %s created (source_claim_id=%s, status=%s, items=%d)",
            claim.claim_number,
            data.source_claim_id,
            claim.status.value,
            len(resolved_items),
        )
        return SubExpenseClaimResponse(
            claim_id=claim.claim_id,
            claim_number=claim.claim_number,
            status=claim.status.value.lower(),
            source_claim_id=data.source_claim_id,
        )

    def approve_expense_claim(
        self,
        org_id: UUID,
        source_claim_id: str,
        data: SubExpenseClaimDecisionPayload,
    ) -> SubExpenseClaimResponse:
        """Apply a manager approval already authorized and recorded by Sub."""
        from app.services.expense import ExpenseService, ExpenseServiceError
        from app.services.expense.service_claims import ExpenseClaimApprovalSource

        claim = self._require_sub_expense_claim(org_id, source_claim_id)
        approver = self._require_employee_by_email(org_id, data.decided_by_email)
        evidence = self._decision_evidence(data.decision_id, data.decided_at)
        supplied_notes = (data.notes or "").strip()
        notes = f"{supplied_notes}\n{evidence}" if supplied_notes else evidence
        try:
            claim = ExpenseService(self.db).approve_claim(
                org_id,
                claim.claim_id,
                approver_id=approver.employee_id,
                notes=notes,
                actor_id=approver.person_id,
                approval_source=ExpenseClaimApprovalSource.TRUSTED_SUB_MANAGER,
            )
        except ExpenseServiceError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return self._claim_response(claim, source_claim_id)

    def reject_expense_claim(
        self,
        org_id: UUID,
        source_claim_id: str,
        data: SubExpenseClaimRejectionPayload,
    ) -> SubExpenseClaimResponse:
        """Apply a manager rejection already authorized and recorded by Sub."""
        from app.services.expense import ExpenseService, ExpenseServiceError
        from app.services.expense.service_claims import ExpenseClaimApprovalSource

        claim = self._require_sub_expense_claim(org_id, source_claim_id)
        approver = self._require_employee_by_email(org_id, data.decided_by_email)
        evidence = self._decision_evidence(data.decision_id, data.decided_at)
        try:
            claim = ExpenseService(self.db).reject_claim(
                org_id,
                claim.claim_id,
                approver_id=approver.employee_id,
                reason=data.reason.strip(),
                actor_id=approver.person_id,
                approval_source=ExpenseClaimApprovalSource.TRUSTED_SUB_MANAGER,
            )
        except ExpenseServiceError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        claim.approval_notes = evidence
        self.db.flush()
        return self._claim_response(claim, source_claim_id)

    def initiate_expense_payment(
        self,
        org_id: UUID,
        source_claim_id: str,
        data: SubExpensePaymentPayload,
    ) -> SubExpensePaymentResponse:
        """Create or reuse an intent and execute one manager-authorized payout."""
        from app.services.finance.payments import PaymentService
        from app.services.finance.payments.payment_service import (
            TransferOutcomeUnknown,
        )

        claim = self._require_sub_expense_claim(org_id, source_claim_id)
        initiator = self._require_employee_by_email(org_id, data.initiated_by_email)
        if claim.status != ExpenseClaimStatus.APPROVED:
            raise HTTPException(
                status_code=409,
                detail=f"Only an approved expense can be paid (status={claim.status.value})",
            )
        if (
            initiator.person_id
            and claim.employee
            and claim.employee.person_id == initiator.person_id
        ):
            raise HTTPException(
                status_code=403, detail="Cannot pay your own expense claim"
            )

        command_id = str(data.command_id)
        intent = self.db.scalar(
            select(PaymentIntent)
            .where(
                PaymentIntent.organization_id == org_id,
                PaymentIntent.source_type == "EXPENSE_CLAIM",
                PaymentIntent.source_id == claim.claim_id,
                PaymentIntent.intent_metadata["sub_payment_command_id"].as_string()
                == command_id,
            )
            .order_by(PaymentIntent.created_at.desc())
        )
        payment_service = PaymentService(self.db, org_id)
        if intent is None:
            config = self._require_transfer_config(payment_service)
            intent = payment_service.create_expense_payment_intent(
                expense_claim_id=claim.claim_id,
                paystack_config=config,
                metadata={
                    "sub_payment_command_id": command_id,
                    "sub_initiated_by_email": data.initiated_by_email.lower(),
                    "sub_initiated_at": data.initiated_at.astimezone(UTC).isoformat(),
                },
            )
        if intent.status == PaymentIntentStatus.PENDING:
            config = self._require_transfer_config(payment_service)
            try:
                intent = payment_service.initiate_expense_transfer(
                    intent=intent,
                    paystack_config=config,
                )
            except TransferOutcomeUnknown:
                # The payment owner has already persisted INDETERMINATE. Return
                # that fact as an accepted command so Sub never auto-retries a
                # transfer whose outcome is unknown.
                self.db.refresh(intent)
            except PaystackError as exc:
                # A normal PaystackError is an affirmative provider refusal,
                # unlike TransferOutcomeUnknown. Persist FAILED so a manager
                # may make an explicit new attempt instead of leaving a stale
                # PENDING intent that blocks reimbursement forever.
                payment_service.mark_transfer_failed(intent, str(exc))
        refreshed_claim = self._require_sub_expense_claim(org_id, source_claim_id)
        return SubExpensePaymentResponse(
            claim_id=refreshed_claim.claim_id,
            claim_number=refreshed_claim.claim_number,
            claim_status=refreshed_claim.status.value.lower(),
            source_claim_id=source_claim_id,
            payment_intent_id=intent.intent_id,
            payment_status=intent.status.value.lower(),
            retryable=intent.status == PaymentIntentStatus.FAILED,
        )

    def _require_sub_expense_claim(
        self, org_id: UUID, source_claim_id: str
    ) -> ExpenseClaim:
        claim = self._find_claim_by_source_claim_id(org_id, source_claim_id)
        if claim is None:
            raise HTTPException(
                status_code=404,
                detail=f"Expense claim not found: {source_claim_id}",
            )
        return claim

    def _require_employee_by_email(self, org_id: UUID, email: str) -> Employee:
        employee_id = self._resolve_employee_id(org_id, email)
        employee = (
            self.db.scalar(
                select(Employee).where(
                    Employee.organization_id == org_id,
                    Employee.employee_id == employee_id,
                )
            )
            if employee_id is not None
            else None
        )
        if employee is None:
            raise HTTPException(
                status_code=422,
                detail=f"No ERP employee matches email {email}",
            )
        return employee

    @staticmethod
    def _claim_response(
        claim: ExpenseClaim, source_claim_id: str
    ) -> SubExpenseClaimResponse:
        return SubExpenseClaimResponse(
            claim_id=claim.claim_id,
            claim_number=claim.claim_number,
            status=claim.status.value.lower(),
            source_claim_id=source_claim_id,
        )

    @staticmethod
    def _decision_evidence(decision_id: UUID, decided_at: datetime) -> str:
        return (
            f"Sub manager decision {decision_id} at "
            f"{decided_at.astimezone(UTC).isoformat()}"
        )

    @staticmethod
    def _require_transfer_config(payment_service: PaymentService) -> PaystackConfig:
        config = payment_service.resolve_transfer_polling_config()
        if config is None:
            raise HTTPException(
                status_code=503,
                detail="Paystack transfer configuration is unavailable",
            )
        return config

    def _resolve_expense_categories(
        self,
        org_id: UUID,
        data: SubExpenseClaimPayload,
    ) -> dict[str, ExpenseCategory]:
        """Resolve payload category codes to active org categories (batched)."""
        codes = {item.category_code for item in data.items}
        rows = self.db.scalars(
            select(ExpenseCategory).where(
                ExpenseCategory.organization_id == org_id,
                ExpenseCategory.category_code.in_(codes),
                ExpenseCategory.is_active.is_(True),
            )
        ).all()
        by_code = {category.category_code: category for category in rows}
        missing = sorted(codes - set(by_code))
        if missing:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown expense category code(s): {', '.join(missing)}",
            )
        return by_code

    @staticmethod
    def _parse_sub_date(value: str, label: str) -> date:
        """Parse a YYYY-MM-DD payload date, raising a readable ValueError."""
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(
                f"Invalid {label} format: {value}. Use YYYY-MM-DD."
            ) from exc

    @staticmethod
    def _build_expense_claim_fingerprint(
        *,
        employee_id: UUID | None,
        claim_date: date,
        purpose: str,
        project_id: UUID | None,
        ticket_id: UUID | None,
        currency_code: str | None,
        notes: str | None,
        items: list[dict[str, Any]],
    ) -> str:
        """Deterministic fingerprint over effective claim values.

        Built identically from the inbound payload (resolved values) and from
        a persisted claim + items, so identical resends compare equal while
        any material change is detected. Status is deliberately excluded —
        the claim advances through the approval workflow after creation.
        """
        items_payload = []
        for item in sorted(items, key=lambda entry: entry["sequence"]):
            amount = Decimal(str(item["claimed_amount"])).quantize(Decimal("0.01"))
            items_payload.append(
                {
                    "sequence": item["sequence"],
                    "category_id": str(item["category_id"]),
                    "expense_date": item["expense_date"].isoformat(),
                    "description": item["description"],
                    "claimed_amount": str(amount),
                    "receipt_url": item.get("receipt_url") or "",
                    "vendor_name": item.get("vendor_name") or "",
                    "notes": item.get("notes") or "",
                }
            )
        payload = {
            "employee_id": str(employee_id) if employee_id else None,
            "claim_date": claim_date.isoformat(),
            "purpose": purpose,
            "project_id": str(project_id) if project_id else None,
            "ticket_id": str(ticket_id) if ticket_id else None,
            "currency_code": currency_code or "",
            "notes": notes or "",
            "items": items_payload,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def get_expense_claim_by_source_reference(
        self,
        org_id: UUID,
        source_claim_id: str,
    ) -> SubExpenseClaimStatusResponse | None:
        """Get expense claim status by Sub source_claim_id (None when not found)."""
        claim = self.db.scalar(
            select(ExpenseClaim).where(
                ExpenseClaim.organization_id == org_id,
                ExpenseClaim.source_system == "sub",
                ExpenseClaim.source_reference == source_claim_id,
            )
        )
        if not claim:
            return None
        payment_intent = self.db.scalar(
            select(PaymentIntent)
            .where(
                PaymentIntent.organization_id == org_id,
                PaymentIntent.source_type == "EXPENSE_CLAIM",
                PaymentIntent.source_id == claim.claim_id,
            )
            .order_by(PaymentIntent.created_at.desc())
        )
        return SubExpenseClaimStatusResponse(
            claim_id=claim.claim_id,
            claim_number=claim.claim_number,
            status=claim.status.value.lower(),
            rejection_reason=claim.rejection_reason,
            paid_on=claim.paid_on,
            total_claimed_amount=claim.total_claimed_amount,
            total_approved_amount=claim.total_approved_amount,
            payment_intent_id=(
                payment_intent.intent_id if payment_intent is not None else None
            ),
            payment_status=(
                payment_intent.status.value.lower()
                if payment_intent is not None
                else None
            ),
            source_claim_id=source_claim_id,
        )

    def list_expense_categories(self, org_id: UUID) -> SubExpenseCategoriesResponse:
        """List active expense categories (ordered by code) for the Sub form."""
        rows = self.db.scalars(
            select(ExpenseCategory)
            .where(
                ExpenseCategory.organization_id == org_id,
                ExpenseCategory.is_active.is_(True),
            )
            .order_by(ExpenseCategory.category_code)
        ).all()
        return SubExpenseCategoriesResponse(
            items=[
                SubExpenseCategoryItem(
                    category_code=category.category_code,
                    category_name=category.category_name,
                    requires_receipt=category.requires_receipt,
                    max_amount_per_claim=category.max_amount_per_claim,
                )
                for category in rows
            ]
        )
