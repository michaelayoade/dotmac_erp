"""Expense-total roll-ups and CRM → ERP expense-claim sync.

Extracted from the former monolithic dotmac_crm_sync_service.
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
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload


if TYPE_CHECKING:
    from app.models.finance.ap.supplier import Supplier  # noqa: F401

from app.models.expense.expense_claim import (
    ExpenseCategory,
    ExpenseClaim,
    ExpenseClaimStatus,
)
from app.models.sync.dotmac_crm_sync import (
    CRMEntityType,
)
from app.schemas.sync.dotmac_crm import (
    CRMExpenseCategoriesResponse,
    CRMExpenseCategoryItem,
    CRMExpenseClaimPayload,
    CRMExpenseClaimResponse,
    CRMExpenseClaimStatusResponse,
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

    # ------------------------------------------------------------------
    # CRM → ERP expense-claim sync (field-technician expense requests)
    # ------------------------------------------------------------------

    def _find_claim_by_omni_id(self, org_id: UUID, omni_id: str) -> ExpenseClaim | None:
        """Load an existing claim (with items) for this CRM omni_id, if any."""
        return self.db.scalar(
            select(ExpenseClaim)
            .options(joinedload(ExpenseClaim.items))
            .where(
                ExpenseClaim.organization_id == org_id,
                ExpenseClaim.crm_id == omni_id,
            )
        )

    def create_expense_claim(
        self,
        org_id: UUID,
        data: CRMExpenseClaimPayload,
        created_by_person_id: UUID | None = None,
    ) -> CRMExpenseClaimResponse:
        """
        Create-and-submit an expense claim from a CRM expense request.

        Immutable idempotency by omni_id (mirrors material requests):
        - first send creates the claim and submits it into the approval flow
        - identical resend returns the existing claim unchanged
        - changed resend is rejected (CRM must create a new expense request)

        Raises:
            HTTPException 422: unknown employee email / category codes, or
                submit-side validation failures (missing receipts, limits).
            HTTPException 409: changed resend of an existing omni_id.
            ValueError: malformed dates.
        """
        from app.services.expense import ExpenseService, ExpenseServiceError
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

        claim_date_val = self._parse_crm_date(data.claim_date, "claim_date")

        # Optional cross-references — ignore when unmapped, never fail.
        project_id = self._resolve_project_id(org_id, data.project_crm_id)
        ticket_id = self._resolve_ticket_id(org_id, data.ticket_crm_id)

        categories = self._resolve_expense_categories(org_id, data)

        notes_parts: list[str] = []
        if data.reference_number:
            notes_parts.append(f"CRM expense request: {data.reference_number}")
        if data.remarks:
            notes_parts.append(data.remarks)
        notes = "\n".join(notes_parts) or None

        resolved_items: list[dict[str, Any]] = []
        for seq, item in enumerate(data.items):
            expense_date_val = (
                self._parse_crm_date(item.expense_date, "expense_date")
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

        existing = self._find_claim_by_omni_id(org_id, data.omni_id)
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
                        "create a new CRM expense request."
                    ),
                )
            logger.info(
                "CRM expense claim duplicate accepted unchanged "
                "(omni_id=%s, claim_number=%s)",
                data.omni_id,
                existing.claim_number,
            )
            return CRMExpenseClaimResponse(
                claim_id=existing.claim_id,
                claim_number=existing.claim_number,
                status=existing.status.value.lower(),
                omni_id=data.omni_id,
            )

        service = ExpenseService(self.db)
        # Create inside a savepoint so a concurrent first-send of the same
        # omni_id — where both requests passed the existence check above —
        # degrades gracefully: the loser hits uq_expense_claim_org_crm_id, rolls
        # back its own partial insert, and returns the winner's claim instead of
        # a 500. Both requests carry the same CRM expense request, so returning
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
                created_by_id=created_by_person_id,
            )
            claim.crm_id = data.omni_id
            claim.last_synced_at = datetime.now(UTC)
            self.db.flush()
            service.submit_claim(
                org_id,
                claim.claim_id,
                notify_approvers=True,
                actor_id=created_by_person_id,
            )
            savepoint.commit()
        except (ExpenseServiceError, ValidationError) as exc:
            # Surface a readable validation error (missing receipts, category
            # limits, blocked limit rules, …) so the CRM records the reason.
            savepoint.rollback()
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except IntegrityError:
            # Lost the create race for this omni_id — return the winner's claim.
            savepoint.rollback()
            raced = self.db.scalar(
                select(ExpenseClaim).where(
                    ExpenseClaim.organization_id == org_id,
                    ExpenseClaim.crm_id == data.omni_id,
                )
            )
            if raced is None:
                raise  # not the crm_id collision we anticipated — surface it
            logger.info(
                "CRM expense claim create raced; returning existing "
                "(omni_id=%s, claim_number=%s)",
                data.omni_id,
                raced.claim_number,
            )
            return CRMExpenseClaimResponse(
                claim_id=raced.claim_id,
                claim_number=raced.claim_number,
                status=raced.status.value.lower(),
                omni_id=data.omni_id,
            )

        logger.info(
            "CRM expense claim %s created (omni_id=%s, status=%s, items=%d)",
            claim.claim_number,
            data.omni_id,
            claim.status.value,
            len(resolved_items),
        )
        return CRMExpenseClaimResponse(
            claim_id=claim.claim_id,
            claim_number=claim.claim_number,
            status=claim.status.value.lower(),
            omni_id=data.omni_id,
        )

    def _resolve_expense_categories(
        self,
        org_id: UUID,
        data: CRMExpenseClaimPayload,
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
    def _parse_crm_date(value: str, label: str) -> date:
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

    def get_expense_claim_by_crm_id(
        self,
        org_id: UUID,
        omni_id: str,
    ) -> CRMExpenseClaimStatusResponse | None:
        """Get expense claim status by CRM omni_id (None when not found)."""
        claim = self.db.scalar(
            select(ExpenseClaim).where(
                ExpenseClaim.organization_id == org_id,
                ExpenseClaim.crm_id == omni_id,
            )
        )
        if not claim:
            return None
        return CRMExpenseClaimStatusResponse(
            claim_id=claim.claim_id,
            claim_number=claim.claim_number,
            status=claim.status.value.lower(),
            rejection_reason=claim.rejection_reason,
            paid_on=claim.paid_on,
            total_claimed_amount=claim.total_claimed_amount,
            total_approved_amount=claim.total_approved_amount,
            omni_id=omni_id,
        )

    def list_expense_categories(self, org_id: UUID) -> CRMExpenseCategoriesResponse:
        """List active expense categories (ordered by code) for the CRM form."""
        rows = self.db.scalars(
            select(ExpenseCategory)
            .where(
                ExpenseCategory.organization_id == org_id,
                ExpenseCategory.is_active.is_(True),
            )
            .order_by(ExpenseCategory.category_code)
        ).all()
        return CRMExpenseCategoriesResponse(
            items=[
                CRMExpenseCategoryItem(
                    category_code=category.category_code,
                    category_name=category.category_name,
                    requires_receipt=category.requires_receipt,
                    max_amount_per_claim=category.max_amount_per_claim,
                )
                for category in rows
            ]
        )
