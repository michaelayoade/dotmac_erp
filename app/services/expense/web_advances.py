"""Expense cash-advance web responses."""

from __future__ import annotations

import logging

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from app.services.common import PaginationParams, coerce_uuid
from app.services.common_filters import build_active_filters
from app.services.expense.expense_service import ExpenseService, ExpenseServiceError
from app.templates import templates
from app.web.deps import base_context

logger = logging.getLogger(__name__)


class ExpenseAdvancesWebMixin:
    @staticmethod
    def cash_advances_list_response(
        request: Request, auth, db, status: str | None, page: int
    ) -> HTMLResponse:
        from app.models.expense.cash_advance import CashAdvanceStatus

        org_id = coerce_uuid(auth.organization_id)
        svc = ExpenseService(db)
        pagination = PaginationParams.from_page(page, 20)
        status_filter = None
        if status:
            try:
                status_filter = CashAdvanceStatus(status)
            except ValueError:
                pass

        result = svc.list_advances(org_id, status=status_filter, pagination=pagination)
        context = base_context(request, auth, "Cash Advances", "advances")
        context.update(
            {
                "advances": result.items,
                "status": status,
                "statuses": [s.value for s in CashAdvanceStatus],
                "page": page,
                "total_pages": result.total_pages,
                "total": result.total,
                "has_prev": result.has_prev,
                "has_next": result.has_next,
                "active_filters": build_active_filters(params={"status": status}),
            }
        )
        return templates.TemplateResponse(
            request, "expense/advances/list.html", context
        )

    @staticmethod
    def cash_advance_detail_response(
        request: Request, auth, db, advance_id: str
    ) -> HTMLResponse:
        from app.models.expense.cash_advance import CashAdvanceStatus
        from app.models.finance.banking.bank_account import (
            BankAccount,
            BankAccountStatus,
        )

        org_id = coerce_uuid(auth.organization_id)
        svc = ExpenseService(db)
        try:
            advance = svc.get_advance(org_id, coerce_uuid(advance_id))
        except (ExpenseServiceError, ValueError) as e:
            logger.warning("Cash advance not found: %s — %s", advance_id, e)
            context = base_context(request, auth, "Cash Advance", "advances")
            context["advance"] = None
            context["error"] = "Advance not found"
            return templates.TemplateResponse(
                request, "expense/advances/detail.html", context
            )

        bank_accounts = list(
            db.scalars(
                select(BankAccount)
                .where(
                    BankAccount.organization_id == org_id,
                    BankAccount.status == BankAccountStatus.active,
                )
                .order_by(BankAccount.account_name)
            ).all()
        )

        linked_claims = []
        if advance.status in [
            CashAdvanceStatus.DISBURSED,
            CashAdvanceStatus.PARTIALLY_SETTLED,
        ]:
            claims = svc.list_claims(
                org_id,
                employee_id=advance.employee_id,
                pagination=PaginationParams(offset=0, limit=20),
            )
            linked_claims = [
                claim
                for claim in claims.items
                if claim.cash_advance_id == advance.advance_id
            ]

        context = base_context(
            request, auth, f"Advance {advance.advance_number}", "advances"
        )
        context.update(
            {
                "advance": advance,
                "bank_accounts": bank_accounts,
                "linked_claims": linked_claims,
                "can_disburse": advance.status == CashAdvanceStatus.APPROVED,
                "can_settle": advance.status
                in [CashAdvanceStatus.DISBURSED, CashAdvanceStatus.PARTIALLY_SETTLED],
            }
        )
        return templates.TemplateResponse(
            request, "expense/advances/detail.html", context
        )

    @staticmethod
    def disburse_cash_advance_response(
        advance_id: str,
        bank_account_id: str,
        payment_mode: str,
        payment_reference: str | None,
        auth,
        db,
    ) -> RedirectResponse:
        from app.tasks.expense import post_cash_advance_disbursement

        org_id = coerce_uuid(auth.organization_id)
        svc = ExpenseService(db)
        try:
            advance = svc.disburse_advance(
                org_id, coerce_uuid(advance_id), payment_reference=payment_reference
            )
            db.flush()
            post_cash_advance_disbursement.delay(
                organization_id=str(org_id),
                advance_id=str(advance.advance_id),
                user_id=str(auth.user_id),
                bank_account_id=bank_account_id,
            )
        except ExpenseServiceError:
            db.rollback()

        return RedirectResponse(f"/expense/advances/{advance_id}", status_code=303)

    @staticmethod
    def settle_cash_advance_response(
        advance_id: str, claim_id: str, settlement_amount: str | None, auth, db
    ) -> RedirectResponse:
        from app.tasks.expense import settle_cash_advance_with_claim

        org_id = coerce_uuid(auth.organization_id)
        try:
            settle_cash_advance_with_claim.delay(
                organization_id=str(org_id),
                advance_id=advance_id,
                claim_id=claim_id,
                user_id=str(auth.user_id),
                settlement_amount=settlement_amount,
            )
            db.flush()
        except (ExpenseServiceError, ValueError, TypeError) as e:
            logger.warning("Failed to settle advance %s: %s", advance_id, e)
            db.rollback()
        except Exception:
            logger.exception("Unexpected error settling advance %s", advance_id)
            db.rollback()

        return RedirectResponse(f"/expense/advances/{advance_id}", status_code=303)
