"""Sub expense observations delegated to ERP's expense owner."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from dotmac_kernel.db import conflict_savepoint

from app.models.expense.expense_claim import ExpenseCategory, ExpenseClaim
from app.schemas.sync.dotmac_sub import (
    SubExpenseCategoriesResponse,
    SubExpenseCategoryItem,
    SubExpenseClaimPayload,
    SubExpenseClaimResponse,
    SubExpenseClaimStatusResponse,
)
from app.services.sync.sub.base import _SubSyncBase
from app.services.sync.sub.errors import SubReplayConflictError, SubValidationError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExpenseClaimAcceptance:
    outcome: SubExpenseClaimResponse
    replayed: bool


class _ExpenseIntakeMixin(_SubSyncBase):
    def accept_expense_claim(
        self,
        organization_id: UUID,
        data: SubExpenseClaimPayload,
        created_by_person_id: UUID | None = None,
    ) -> ExpenseClaimAcceptance:
        """Accept an immutable expense request and report replay semantics."""
        replayed = self._find_claim(organization_id, data.source_request_id) is not None
        outcome = self.create_expense_claim(organization_id, data, created_by_person_id)
        return ExpenseClaimAcceptance(outcome=outcome, replayed=replayed)

    def create_expense_claim(
        self,
        organization_id: UUID,
        data: SubExpenseClaimPayload,
        created_by_person_id: UUID | None = None,
    ) -> SubExpenseClaimResponse:
        """Create and submit one immutable Sub expense request."""
        from app.services.common import ValidationError
        from app.services.expense import ExpenseService, ExpenseServiceError
        from app.services.finance.platform.org_context import org_context_service

        employee_id = self._resolve_employee_id(
            organization_id, data.requested_by_email
        )
        if employee_id is None:
            raise SubValidationError(
                f"No ERP employee matches email {data.requested_by_email}; "
                "cannot create expense claim."
            )
        claim_date = self._parse_sub_date(data.claim_date, "claim_date")
        project_id = self._resolve_project_id(organization_id, data.project_source_id)
        categories = self._resolve_expense_categories(organization_id, data)
        notes = self._build_notes(data)
        resolved_items: list[dict[str, Any]] = []
        for sequence, item in enumerate(data.items):
            resolved_items.append(
                {
                    "sequence": sequence,
                    "category_id": categories[item.category_code].category_id,
                    "expense_date": (
                        self._parse_sub_date(item.expense_date, "expense_date")
                        if item.expense_date
                        else claim_date
                    ),
                    "description": item.description,
                    "claimed_amount": item.claimed_amount,
                    "receipt_url": item.receipt_url,
                    "vendor_name": item.vendor_name,
                    "notes": item.notes,
                }
            )

        currency = data.currency_code or org_context_service.get_functional_currency(
            self.db, organization_id
        )
        incoming_fingerprint = self._fingerprint(
            employee_id=employee_id,
            claim_date=claim_date,
            purpose=data.purpose,
            project_id=project_id,
            currency_code=currency,
            notes=notes,
            items=resolved_items,
        )
        existing = self._find_claim(organization_id, data.source_request_id)
        if existing is not None:
            self._require_matching_expense_fingerprint(
                existing,
                incoming_fingerprint,
                source_request_id=data.source_request_id,
            )
            return self._expense_response(existing, data.source_request_id)

        service = ExpenseService(self.db)
        try:
            with conflict_savepoint(self.db):
                claim = service.create_claim(
                    organization_id,
                    employee_id=employee_id,
                    claim_date=claim_date,
                    purpose=data.purpose,
                    project_id=project_id,
                    ticket_id=None,
                    currency_code=data.currency_code,
                    notes=notes,
                    items=resolved_items,
                    created_by_id=created_by_person_id,
                )
                claim.source_id = data.source_request_id
                claim.external_work_reference = data.external_work_reference
                claim.last_synced_at = datetime.now(timezone.utc)
                self.db.flush()
                service.submit_claim(
                    organization_id,
                    claim.claim_id,
                    notify_approvers=True,
                    actor_id=created_by_person_id,
                )
        except (ExpenseServiceError, ValidationError) as exc:
            raise SubValidationError(str(exc)) from exc
        except IntegrityError:
            raced = self._find_claim(organization_id, data.source_request_id)
            if raced is None:
                raise
            self._require_matching_expense_fingerprint(
                raced,
                incoming_fingerprint,
                source_request_id=data.source_request_id,
            )
            return self._expense_response(raced, data.source_request_id)
        return self._expense_response(claim, data.source_request_id)

    def get_expense_claim_by_source_id(
        self, organization_id: UUID, source_request_id: str
    ) -> SubExpenseClaimStatusResponse | None:
        claim = self.db.scalar(
            select(ExpenseClaim).where(
                ExpenseClaim.organization_id == organization_id,
                ExpenseClaim.source_id == source_request_id,
            )
        )
        if claim is None:
            return None
        return SubExpenseClaimStatusResponse(
            claim_id=claim.claim_id,
            claim_number=claim.claim_number,
            status=claim.status.value.lower(),
            rejection_reason=claim.rejection_reason,
            paid_on=claim.paid_on,
            total_claimed_amount=claim.total_claimed_amount,
            total_approved_amount=claim.total_approved_amount,
            source_request_id=source_request_id,
        )

    def list_expense_categories(
        self, organization_id: UUID
    ) -> SubExpenseCategoriesResponse:
        rows = self.db.scalars(
            select(ExpenseCategory)
            .where(
                ExpenseCategory.organization_id == organization_id,
                ExpenseCategory.is_active.is_(True),
            )
            .order_by(ExpenseCategory.category_code)
        ).all()
        return SubExpenseCategoriesResponse(
            items=[
                SubExpenseCategoryItem(
                    category_code=row.category_code,
                    category_name=row.category_name,
                    requires_receipt=row.requires_receipt,
                    max_amount_per_claim=row.max_amount_per_claim,
                )
                for row in rows
            ]
        )

    def _find_claim(
        self, organization_id: UUID, source_request_id: str
    ) -> ExpenseClaim | None:
        return self.db.scalar(
            select(ExpenseClaim)
            .options(joinedload(ExpenseClaim.items))
            .where(
                ExpenseClaim.organization_id == organization_id,
                ExpenseClaim.source_id == source_request_id,
            )
        )

    def _require_matching_expense_fingerprint(
        self,
        claim: ExpenseClaim,
        incoming_fingerprint: str,
        *,
        source_request_id: str,
    ) -> None:
        persisted_fingerprint = self._fingerprint(
            employee_id=claim.employee_id,
            claim_date=claim.claim_date,
            purpose=claim.purpose,
            project_id=claim.project_id,
            currency_code=claim.currency_code,
            notes=claim.notes,
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
                for line in claim.items
            ],
        )
        if persisted_fingerprint != incoming_fingerprint:
            raise SubReplayConflictError(
                f"Sub expense claim {source_request_id} was reused with a "
                "different immutable payload."
            )

    @staticmethod
    def _build_notes(data: SubExpenseClaimPayload) -> str | None:
        parts: list[str] = []
        if data.reference_number:
            parts.append(f"Sub expense request: {data.reference_number}")
        if data.external_work_reference:
            parts.append(f"External work reference: {data.external_work_reference}")
        if data.remarks:
            parts.append(data.remarks)
        return "\n".join(parts) or None

    def _resolve_expense_categories(
        self, organization_id: UUID, data: SubExpenseClaimPayload
    ) -> dict[str, ExpenseCategory]:
        codes = {item.category_code for item in data.items}
        rows = self.db.scalars(
            select(ExpenseCategory).where(
                ExpenseCategory.organization_id == organization_id,
                ExpenseCategory.category_code.in_(codes),
                ExpenseCategory.is_active.is_(True),
            )
        ).all()
        by_code = {row.category_code: row for row in rows}
        missing = sorted(codes - set(by_code))
        if missing:
            raise SubValidationError(
                f"Unknown expense category code(s): {', '.join(missing)}"
            )
        return by_code

    @staticmethod
    def _parse_sub_date(value: str, label: str) -> date:
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(
                f"Invalid {label} format: {value}. Use YYYY-MM-DD."
            ) from exc

    @staticmethod
    def _fingerprint(
        *,
        employee_id: UUID | None,
        claim_date: date,
        purpose: str,
        project_id: UUID | None,
        currency_code: str | None,
        notes: str | None,
        items: list[dict[str, Any]],
    ) -> str:
        normalized_items = []
        for item in sorted(items, key=lambda entry: entry["sequence"]):
            amount = Decimal(str(item["claimed_amount"])).quantize(Decimal("0.01"))
            normalized_items.append(
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
            "currency_code": currency_code or "",
            "notes": notes or "",
            "items": normalized_items,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    @staticmethod
    def _expense_response(
        claim: ExpenseClaim, source_request_id: str
    ) -> SubExpenseClaimResponse:
        return SubExpenseClaimResponse(
            claim_id=claim.claim_id,
            claim_number=claim.claim_number,
            status=claim.status.value.lower(),
            source_request_id=source_request_id,
        )
