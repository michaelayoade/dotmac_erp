"""Expense-total roll-ups and Sub → ERP expense-claim sync.

Extracted from the former monolithic dotmac_sub_sync_service.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import date, datetime, timedelta, timezone

try:
    from datetime import UTC  # type: ignore
except ImportError:  # pragma: no cover
    UTC = timezone.utc

from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal, cast
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import and_, func, or_, select
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
from app.models.finance.core_org.bank_directory import BankDirectory
from app.models.people.hr.employee import Employee, EmployeeStatus
from app.models.person import Person
from app.models.rbac import Permission, PersonRole, Role, RolePermission
from app.schemas.sync.sub_operational import (
    SubExpenseCategoriesResponse,
    SubExpenseCategoryItem,
    SubExpenseApproverItem,
    SubExpenseApproversResponse,
    SubExpenseBankItem,
    SubExpenseBanksResponse,
    SubExpenseClaimPayload,
    SubExpenseClaimDecisionPayload,
    SubExpenseClaimRejectionPayload,
    SubExpenseClaimResponse,
    SubExpenseClaimStatusResponse,
    SubExpensePaymentPayload,
    SubExpensePaymentResponse,
    SubExpenseProfileDestinationResponse,
    SubExpenseDestinationVerifyPayload,
    SubExpenseDestinationVerifyResponse,
    SubExpenseDestinationInspectPayload,
)
from app.services.finance.payments.paystack_client import (
    PaystackClient,
    PaystackConfig,
    PaystackError,
    PaystackUnreachable,
)
from app.services.integration_config import decrypt_credential, encrypt_credential

# Sub → ERP translation policy lives in sub_mappings (pure, side-effect-free).
# Re-imported here so the canonical import sites
# (`from ...dotmac_sub_sync_service import PROJECT_STATUS_MAP`) and the in-class
# references keep resolving against this module's namespace.

from app.services.sync.sub.base import _SubSyncBase

logger = logging.getLogger(__name__)

_DESTINATION_TOKEN_TTL = timedelta(minutes=30)
_EXPENSE_APPROVAL_PERMISSIONS = (
    "expense:claims:approve:tier1",
    "expense:claims:approve:tier2",
    "expense:claims:approve:tier3",
)


def _masked_account_number(account_number: str) -> str:
    suffix = account_number[-4:]
    return f"{'*' * max(2, len(account_number) - len(suffix))}{suffix}"


def _normalized_name_tokens(value: str) -> set[str]:
    return {
        token
        for token in "".join(
            character.upper() if character.isalnum() else " " for character in value
        ).split()
        if len(token) > 1
    }


def _beneficiary_matches(entered: str, verified: str) -> bool:
    entered_tokens = _normalized_name_tokens(entered)
    verified_tokens = _normalized_name_tokens(verified)
    return bool(entered_tokens) and entered_tokens == verified_tokens


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

    def list_expense_approvers(
        self, org_id: UUID, *, requested_by_email: str
    ) -> SubExpenseApproversResponse:
        """Return active, permission-backed approvers visible to one requester."""
        if self._resolve_employee_id(org_id, requested_by_email) is None:
            raise HTTPException(
                status_code=422,
                detail="The requesting employee could not be matched in ERP",
            )
        rows = self.db.execute(
            select(Employee, Person)
            .join(Person, Person.id == Employee.person_id)
            .join(PersonRole, PersonRole.person_id == Employee.person_id)
            .join(Role, Role.id == PersonRole.role_id)
            .outerjoin(RolePermission, RolePermission.role_id == Role.id)
            .outerjoin(Permission, Permission.id == RolePermission.permission_id)
            .where(
                Employee.organization_id == org_id,
                Person.organization_id == org_id,
                Employee.status.in_((EmployeeStatus.ACTIVE, EmployeeStatus.ON_LEAVE)),
                Role.is_active.is_(True),
                or_(
                    func.lower(Role.name) == "admin",
                    and_(
                        Permission.is_active.is_(True),
                        Permission.key.in_(_EXPENSE_APPROVAL_PERMISSIONS),
                    ),
                ),
            )
            .order_by(Person.first_name, Person.last_name)
        ).all()
        unique: dict[UUID, SubExpenseApproverItem] = {}
        for employee, _person in rows:
            email = (employee.work_email or employee.personal_email or "").strip()
            if not email:
                continue
            unique[employee.employee_id] = SubExpenseApproverItem(
                employee_id=employee.employee_id,
                display_name=employee.full_name or email,
                email=email,
            )
        return SubExpenseApproversResponse(items=list(unique.values()))

    def list_expense_banks(self) -> SubExpenseBanksResponse:
        rows = self.db.scalars(
            select(BankDirectory)
            .where(BankDirectory.is_active.is_(True))
            .order_by(BankDirectory.bank_name)
        ).all()
        return SubExpenseBanksResponse(
            items=[
                SubExpenseBankItem(
                    bank_code=bank.bank_code,
                    bank_name=bank.bank_name,
                )
                for bank in rows
            ]
        )

    def get_expense_profile_destination(
        self, org_id: UUID, *, requested_by_email: str
    ) -> SubExpenseProfileDestinationResponse:
        """Return only a masked view of the requester's ERP bank profile."""
        employee = self._require_employee_by_email(org_id, requested_by_email)
        bank_code = (employee.bank_branch_code or "").strip()
        account_number = (employee.bank_account_number or "").strip()
        beneficiary_name = (
            employee.bank_account_name or employee.full_name or ""
        ).strip()
        bank = (
            self.db.scalar(
                select(BankDirectory).where(
                    BankDirectory.bank_code == bank_code,
                    BankDirectory.is_active.is_(True),
                )
            )
            if bank_code
            else None
        )
        if bank is None or not account_number or not beneficiary_name:
            return SubExpenseProfileDestinationResponse(available=False)
        return SubExpenseProfileDestinationResponse(
            available=True,
            bank_code=bank.bank_code,
            bank_name=bank.bank_name,
            masked_account_number=_masked_account_number(account_number),
            beneficiary_name=beneficiary_name,
        )

    def verify_expense_destination(
        self,
        org_id: UUID,
        data: SubExpenseDestinationVerifyPayload,
    ) -> SubExpenseDestinationVerifyResponse:
        """Verify a destination and return a claim-bound encrypted bearer token."""
        employee = self._require_employee_by_email(org_id, data.requested_by_email)
        if data.mode == "erp_profile":
            bank_code = (employee.bank_branch_code or "").strip()
            account_number = (employee.bank_account_number or "").strip()
            beneficiary_name = (
                employee.bank_account_name or employee.full_name or ""
            ).strip()
            if not bank_code or not account_number or not beneficiary_name:
                raise HTTPException(
                    status_code=422,
                    detail="The ERP employee bank profile is incomplete",
                )
        else:
            bank_code = str(data.bank_code or "").strip()
            account_number = str(data.account_number or "").strip()
            beneficiary_name = str(data.beneficiary_name or "").strip()

        if not account_number.isdigit() or not 6 <= len(account_number) <= 30:
            raise HTTPException(status_code=422, detail="Account number is invalid")
        bank = self.db.scalar(
            select(BankDirectory).where(
                BankDirectory.bank_code == bank_code,
                BankDirectory.is_active.is_(True),
            )
        )
        if bank is None:
            raise HTTPException(status_code=422, detail="Select an active ERP bank")

        from app.services.finance.payments import PaymentService

        config = self._require_transfer_config(PaymentService(self.db, org_id))
        try:
            with PaystackClient(config) as client:
                resolved = client.resolve_account(
                    account_number=account_number,
                    bank_code=bank_code,
                )
        except PaystackUnreachable as exc:
            raise HTTPException(
                status_code=503,
                detail="Bank account verification is temporarily unavailable",
            ) from exc
        except PaystackError as exc:
            raise HTTPException(
                status_code=422,
                detail="The bank account could not be verified",
            ) from exc
        if not _beneficiary_matches(beneficiary_name, resolved.account_name):
            raise HTTPException(
                status_code=422,
                detail="Beneficiary name does not match the verified account name",
            )

        now = datetime.now(UTC)
        expires_at = now + _DESTINATION_TOKEN_TTL
        token_payload = {
            "version": 1,
            "organization_id": str(org_id),
            "employee_id": str(employee.employee_id),
            "source_claim_id": str(data.source_claim_id),
            "mode": data.mode,
            "bank_code": bank.bank_code,
            "bank_name": bank.bank_name,
            "account_number": account_number,
            "verified_beneficiary_name": resolved.account_name,
            "verified_at": now.isoformat(),
            "expires_at": expires_at.isoformat(),
        }
        token = encrypt_credential(
            json.dumps(token_payload, sort_keys=True, separators=(",", ":")),
            self.db,
        )
        return SubExpenseDestinationVerifyResponse(
            destination_token=token,
            mode=data.mode,
            bank_code=bank.bank_code,
            bank_name=bank.bank_name,
            masked_account_number=_masked_account_number(account_number),
            verified_beneficiary_name=resolved.account_name,
            verified_at=now,
            expires_at=expires_at,
        )

    def _decode_expense_destination(
        self,
        *,
        org_id: UUID,
        employee_id: UUID,
        source_claim_id: str,
        token: str,
        allow_expired: bool = False,
    ) -> dict[str, str]:
        if not token.startswith("enc:"):
            raise HTTPException(
                status_code=422, detail="Payment destination token is invalid"
            )
        try:
            raw = decrypt_credential(token, self.db)
            payload = json.loads(raw or "")
            expires_at = datetime.fromisoformat(str(payload["expires_at"]))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(
                status_code=422, detail="Payment destination token is invalid"
            ) from exc
        if (
            str(payload.get("organization_id")) != str(org_id)
            or str(payload.get("employee_id")) != str(employee_id)
            or str(payload.get("source_claim_id")) != source_claim_id
        ):
            raise HTTPException(
                status_code=422,
                detail="Payment destination token does not belong to this expense",
            )
        if payload.get("version") != 1 or payload.get("mode") not in {
            "erp_profile",
            "expense_override",
        }:
            raise HTTPException(
                status_code=422, detail="Payment destination token is invalid"
            )
        if not allow_expired and expires_at <= datetime.now(UTC):
            raise HTTPException(
                status_code=422, detail="Payment destination token expired"
            )
        required = (
            "mode",
            "bank_code",
            "bank_name",
            "account_number",
            "verified_beneficiary_name",
            "verified_at",
            "expires_at",
        )
        if any(not str(payload.get(key) or "").strip() for key in required):
            raise HTTPException(
                status_code=422, detail="Payment destination token is incomplete"
            )
        return {key: str(payload[key]) for key in required}

    def inspect_expense_destination(
        self, org_id: UUID, data: SubExpenseDestinationInspectPayload
    ) -> SubExpenseDestinationVerifyResponse:
        """Return authoritative masked fields for a still-valid opaque token."""
        employee = self._require_employee_by_email(org_id, data.requested_by_email)
        destination = self._decode_expense_destination(
            org_id=org_id,
            employee_id=employee.employee_id,
            source_claim_id=str(data.source_claim_id),
            token=data.destination_token,
        )
        return SubExpenseDestinationVerifyResponse(
            destination_token=data.destination_token,
            mode=cast(Literal["erp_profile", "expense_override"], destination["mode"]),
            bank_code=destination["bank_code"],
            bank_name=destination["bank_name"],
            masked_account_number=_masked_account_number(destination["account_number"]),
            verified_beneficiary_name=destination["verified_beneficiary_name"],
            verified_at=datetime.fromisoformat(destination["verified_at"]),
            expires_at=datetime.fromisoformat(destination["expires_at"]),
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

        approver_id = data.requested_approver_id
        if approver_id is not None:
            eligible_approvers = {
                item.employee_id
                for item in self.list_expense_approvers(
                    org_id, requested_by_email=data.requested_by_email
                ).items
            }
            if approver_id not in eligible_approvers:
                raise HTTPException(
                    status_code=422,
                    detail="Select an active ERP expense approver",
                )

        existing = self._find_claim_by_source_claim_id(org_id, data.source_claim_id)
        destination: dict[str, str] | None = None
        destination_fingerprint: str | None = None
        if data.payment_destination_token:
            destination = self._decode_expense_destination(
                org_id=org_id,
                employee_id=employee_id,
                source_claim_id=data.source_claim_id,
                token=data.payment_destination_token,
                allow_expired=existing is not None,
            )
            destination_fingerprint = hashlib.sha256(
                json.dumps(
                    {
                        key: destination[key]
                        for key in (
                            "mode",
                            "bank_code",
                            "bank_name",
                            "account_number",
                            "verified_beneficiary_name",
                        )
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()

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
            requested_approver_id=approver_id,
            payment_destination_fingerprint=destination_fingerprint,
            items=resolved_items,
        )

        if existing is not None:
            existing_fingerprint = self._build_expense_claim_fingerprint(
                employee_id=existing.employee_id,
                claim_date=existing.claim_date,
                purpose=existing.purpose,
                project_id=existing.project_id,
                ticket_id=existing.ticket_id,
                currency_code=existing.currency_code,
                notes=existing.notes,
                requested_approver_id=existing.requested_approver_id,
                payment_destination_fingerprint=(
                    existing.payment_destination_fingerprint
                ),
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
                recipient_bank_code=(
                    destination["bank_code"]
                    if destination
                    else employee.bank_branch_code
                ),
                recipient_bank_name=(
                    destination["bank_name"] if destination else employee.bank_name
                ),
                recipient_account_number=(
                    None if destination else employee.bank_account_number
                ),
                recipient_name=(
                    destination["verified_beneficiary_name"]
                    if destination
                    else employee.bank_account_name or employee.full_name
                ),
                requested_approver_id=approver_id,
                created_by_id=created_by_person_id,
            )
            if destination:
                account_number = destination["account_number"]
                claim.recipient_account_number_encrypted = encrypt_credential(
                    account_number, self.db
                )
                claim.recipient_account_number_last4 = account_number[-4:]
                claim.recipient_account_name = destination["verified_beneficiary_name"]
                claim.payment_destination_mode = destination["mode"]
                claim.payment_destination_fingerprint = destination_fingerprint
                claim.payment_destination_verified_at = datetime.fromisoformat(
                    destination["verified_at"]
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
        if (
            claim.requested_approver_id is not None
            and approver.employee_id != claim.requested_approver_id
        ):
            raise HTTPException(
                status_code=403,
                detail="Only the selected expense approver may decide this claim",
            )
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
        if (
            claim.requested_approver_id is not None
            and approver.employee_id != claim.requested_approver_id
        ):
            raise HTTPException(
                status_code=403,
                detail="Only the selected expense approver may decide this claim",
            )
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
        requested_approver_id: UUID | None,
        payment_destination_fingerprint: str | None,
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
            "requested_approver_id": (
                str(requested_approver_id) if requested_approver_id else None
            ),
            "payment_destination_fingerprint": payment_destination_fingerprint,
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
        payment_intent = None
        if claim.status in {ExpenseClaimStatus.APPROVED, ExpenseClaimStatus.PAID}:
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
            requested_approver_id=claim.requested_approver_id,
            requested_approver_name=(
                claim.requested_approver.full_name
                if claim.requested_approver is not None
                else None
            ),
            payment_destination_mode=cast(
                Literal["erp_profile", "expense_override"] | None,
                claim.payment_destination_mode,
            ),
            recipient_bank_name=claim.recipient_bank_name,
            masked_account_number=(
                _masked_account_number(claim.recipient_account_number)
                if claim.recipient_account_number
                else (
                    f"******{claim.recipient_account_number_last4}"
                    if claim.recipient_account_number_last4
                    else None
                )
            ),
            verified_beneficiary_name=(
                claim.recipient_account_name or claim.recipient_name
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
