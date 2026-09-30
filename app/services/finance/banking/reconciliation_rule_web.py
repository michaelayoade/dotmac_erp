"""
Reconciliation Match Rule Web Service.

Builds template context and handles form submissions for the
match rules management UI.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.requests import Request
from starlette.responses import Response

from app.models.finance.banking.bank_account import BankAccount, BankAccountStatus
from app.models.finance.gl.account import Account, AccountType
from app.services.common import NotFoundError
from app.services.finance.banking.reconciliation_rule_service import (
    ReconciliationRuleService,
)
from app.web.deps import WebAuthContext, base_context, templates

logger = logging.getLogger(__name__)

# Source doc type choices for the form dropdown
SOURCE_DOC_TYPE_CHOICES = [
    ("CUSTOMER_PAYMENT", "Customer Payment"),
    ("SUPPLIER_PAYMENT", "Supplier Payment (AP Invoice)"),
    ("PAYMENT_INTENT", "Payment Intent (Paystack Customer)"),
    ("BANK_FEE", "Bank Fee"),
    ("INTER_BANK", "Inter-Bank Transfer"),
    ("INVOICE", "Invoice (Direct Match)"),
    ("EXPENSE", "Expense Reimbursement"),
]

# Condition field choices
CONDITION_FIELD_CHOICES = [
    ("DESCRIPTION", "Description"),
    ("REFERENCE", "Reference"),
    ("BANK_REFERENCE", "Bank Reference"),
    ("PAYEE", "Payee / Payer"),
    ("BANK_CATEGORY", "Bank Category"),
    ("BANK_CODE", "Bank Code"),
]

# Condition operator choices
CONDITION_OPERATOR_CHOICES = [
    ("EQUALS", "Equals"),
    ("CONTAINS", "Contains"),
    ("STARTS_WITH", "Starts With"),
    ("REGEX", "Regex Pattern"),
    ("BETWEEN", "Between (min,max)"),
    ("GREATER_THAN", "Greater Than"),
    ("LESS_THAN", "Less Than"),
]

# Action type choices
ACTION_TYPE_CHOICES = [
    ("MATCH", "Match (auto-reconcile)"),
    ("CREATE_JOURNAL", "Create Journal Entry"),
    ("SUGGEST", "Suggest (manual review)"),
]


def _org_id(auth: WebAuthContext) -> UUID:
    """Extract organization_id or raise."""
    org_id = auth.organization_id
    if org_id is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=401, detail="Authentication required")
    return org_id


def _get_rule_for_org(
    service: ReconciliationRuleService, rule_id: str, org_id: UUID
) -> Any:
    """Fetch a rule by ID and verify it belongs to the given org."""
    rule = service.get_by_id(UUID(rule_id))
    if not rule:
        raise NotFoundError("Rule not found")
    if rule.organization_id != org_id:
        raise NotFoundError("Rule not found")
    return rule


class ReconciliationRuleWebService:
    """Web service for reconciliation match rule pages."""

    def list_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        page: int = 1,
        limit: int = 25,
    ) -> HTMLResponse:
        """Match rules list page."""
        org_id = _org_id(auth)
        context = base_context(request, auth, "Match Rules", "banking", db=db)
        service = ReconciliationRuleService(db)
        rules = service.list_for_org(org_id)

        # Paginate
        total = len(rules)
        start = (page - 1) * limit
        paginated = rules[start : start + limit]
        bank_accounts = list(
            db.scalars(
                select(BankAccount).where(BankAccount.organization_id == org_id)
            ).all()
        )

        context.update(
            {
                "rules": paginated,
                "bank_account_names": {
                    account.bank_account_id: account.account_name
                    for account in bank_accounts
                },
                "total": total,
                "page": page,
                "limit": limit,
                "total_pages": (total + limit - 1) // limit if total else 1,
                "source_doc_types": dict(SOURCE_DOC_TYPE_CHOICES),
            }
        )
        return templates.TemplateResponse(
            request, "finance/banking/rules/match_rules.html", context
        )

    def detail_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        rule_id: str,
    ) -> HTMLResponse:
        """Match rule detail page."""
        org_id = _org_id(auth)
        context = base_context(request, auth, "Match Rule", "banking", db=db)
        service = ReconciliationRuleService(db)
        rule = _get_rule_for_org(service, rule_id, org_id)
        bank_account = (
            db.get(BankAccount, rule.bank_account_id)
            if rule.bank_account_id is not None
            else None
        )
        if bank_account and bank_account.organization_id != org_id:
            bank_account = None
        writeoff_account = (
            db.get(Account, rule.writeoff_account_id)
            if rule.writeoff_account_id is not None
            else None
        )
        if writeoff_account and writeoff_account.organization_id != org_id:
            writeoff_account = None

        # Get recent match log for this rule
        match_log = service.get_match_log(org_id, rule_id=rule.rule_id, limit=20)

        context.update(
            {
                "rule": rule,
                "bank_account": bank_account,
                "writeoff_account": writeoff_account,
                "match_log": match_log,
                "source_doc_types": dict(SOURCE_DOC_TYPE_CHOICES),
                "condition_fields": dict(CONDITION_FIELD_CHOICES),
                "condition_operators": dict(CONDITION_OPERATOR_CHOICES),
                "action_types": dict(ACTION_TYPE_CHOICES),
            }
        )
        return templates.TemplateResponse(
            request, "finance/banking/rules/match_rule_detail.html", context
        )

    def new_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        """New match rule form page."""
        org_id = _org_id(auth)
        context = base_context(request, auth, "New Match Rule", "banking", db=db)
        context.update(self._form_context(db, org_id))
        return templates.TemplateResponse(
            request, "finance/banking/rules/match_rule_form.html", context
        )

    def edit_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        rule_id: str,
    ) -> HTMLResponse:
        """Edit match rule form page."""
        org_id = _org_id(auth)
        context = base_context(request, auth, "Edit Match Rule", "banking", db=db)
        service = ReconciliationRuleService(db)
        rule = _get_rule_for_org(service, rule_id, org_id)
        context.update(self._form_context(db, org_id))
        context["rule"] = rule
        context["editing"] = True
        return templates.TemplateResponse(
            request, "finance/banking/rules/match_rule_form.html", context
        )

    def create_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        form_data: dict[str, Any],
    ) -> Response:
        """Create a new match rule from form data."""
        org_id = _org_id(auth)
        try:
            service = ReconciliationRuleService(db)
            data = self._build_rule_data(form_data)
            rule = service.create(org_id, data)
            db.flush()
            return RedirectResponse(
                url=f"/finance/banking/match-rules/{rule.rule_id}?saved=1",
                status_code=303,
            )
        except (ValueError, KeyError) as e:
            logger.warning("Failed to create match rule: %s", e)
            context = base_context(request, auth, "New Match Rule", "banking", db=db)
            context.update(self._form_context(db, org_id))
            context["error"] = str(e)
            context["form_data"] = form_data
            return templates.TemplateResponse(
                request, "finance/banking/rules/match_rule_form.html", context
            )

    def update_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        rule_id: str,
        form_data: dict[str, Any],
    ) -> Response:
        """Update an existing match rule."""
        org_id = _org_id(auth)
        try:
            service = ReconciliationRuleService(db)
            _get_rule_for_org(service, rule_id, org_id)
            data = self._build_rule_data(form_data)
            service.update(UUID(rule_id), data)
            db.flush()
            return RedirectResponse(
                url=f"/finance/banking/match-rules/{rule_id}?saved=1",
                status_code=303,
            )
        except (ValueError, KeyError) as e:
            logger.warning("Failed to update match rule %s: %s", rule_id, e)
            context = base_context(request, auth, "Edit Match Rule", "banking", db=db)
            context.update(self._form_context(db, org_id))
            context["error"] = str(e)
            context["form_data"] = form_data
            context["editing"] = True
            rule = ReconciliationRuleService(db).get_by_id(UUID(rule_id))
            context["rule"] = rule
            return templates.TemplateResponse(
                request, "finance/banking/rules/match_rule_form.html", context
            )

    def toggle_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        rule_id: str,
    ) -> RedirectResponse:
        """Toggle a rule's active state."""
        org_id = _org_id(auth)
        service = ReconciliationRuleService(db)
        rule = _get_rule_for_org(service, rule_id, org_id)
        service.update(rule.rule_id, {"is_active": not rule.is_active})
        db.flush()
        return RedirectResponse(url="/finance/banking/match-rules", status_code=303)

    def delete_response(
        self,
        auth: WebAuthContext,
        db: Session,
        rule_id: str,
    ) -> RedirectResponse:
        """Delete a match rule."""
        org_id = _org_id(auth)
        service = ReconciliationRuleService(db)
        _get_rule_for_org(service, rule_id, org_id)
        service.delete(UUID(rule_id))
        db.flush()
        return RedirectResponse(url="/finance/banking/match-rules", status_code=303)

    def match_log_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        rule_id: str | None = None,
        page: int = 1,
        limit: int = 50,
    ) -> HTMLResponse:
        """Match audit log page."""
        org_id = _org_id(auth)
        context = base_context(request, auth, "Match Log", "banking", db=db)
        service = ReconciliationRuleService(db)

        offset = (page - 1) * limit
        logs = service.get_match_log(
            org_id,
            rule_id=UUID(rule_id) if rule_id else None,
            limit=limit,
            offset=offset,
        )
        total = service.count_match_log(
            org_id,
            rule_id=UUID(rule_id) if rule_id else None,
        )

        # Get all rules for filter dropdown
        rules = service.list_for_org(org_id)

        context.update(
            {
                "logs": logs,
                "rules": rules,
                "selected_rule_id": rule_id,
                "total": total,
                "page": page,
                "limit": limit,
                "total_pages": (total + limit - 1) // limit if total else 1,
                "source_doc_types": dict(SOURCE_DOC_TYPE_CHOICES),
            }
        )
        return templates.TemplateResponse(
            request, "finance/banking/rules/match_log.html", context
        )

    # ── Private helpers ──────────────────────────────────────────────

    @staticmethod
    def _form_context(db: Session, org_id: UUID) -> dict[str, Any]:
        """Return shared form context (dropdowns, defaults)."""
        bank_accounts = list(
            db.scalars(
                select(BankAccount)
                .where(
                    BankAccount.organization_id == org_id,
                    BankAccount.status == BankAccountStatus.active,
                )
                .order_by(BankAccount.account_name, BankAccount.account_number)
            ).all()
        )
        writeoff_accounts = list(
            db.scalars(
                select(Account)
                .where(
                    Account.organization_id == org_id,
                    Account.account_type == AccountType.POSTING,
                    Account.is_active.is_(True),
                    Account.is_posting_allowed.is_(True),
                )
                .order_by(Account.account_code, Account.account_name)
            ).all()
        )
        return {
            "bank_accounts": bank_accounts,
            "writeoff_accounts": writeoff_accounts,
            "source_doc_type_choices": SOURCE_DOC_TYPE_CHOICES,
            "condition_field_choices": CONDITION_FIELD_CHOICES,
            "condition_operator_choices": CONDITION_OPERATOR_CHOICES,
            "action_type_choices": ACTION_TYPE_CHOICES,
        }

    @staticmethod
    def _build_rule_data(form_data: dict[str, Any]) -> dict[str, Any]:
        """Parse form submission into rule data dict."""
        # Build conditions from dynamic form rows
        conditions: list[dict[str, str]] = []
        i = 0
        while f"cond_field_{i}" in form_data:
            field = str(form_data[f"cond_field_{i}"])
            operator = str(form_data[f"cond_operator_{i}"])
            value = str(form_data[f"cond_value_{i}"])
            if field and operator and value:
                conditions.append(
                    {"field": field, "operator": operator, "value": value}
                )
            i += 1

        return {
            "bank_account_id": (
                UUID(str(form_data["bank_account_id"]))
                if form_data.get("bank_account_id")
                else None
            ),
            "name": str(form_data.get("name", "")).strip(),
            "description": str(form_data.get("description", "")).strip() or None,
            "source_doc_type": str(form_data.get("source_doc_type", "")),
            "priority": int(form_data.get("priority", 100)),
            "is_active": form_data.get("is_active") == "on",
            "conditions": conditions,
            "match_debit": form_data.get("match_debit") == "on",
            "match_credit": form_data.get("match_credit") == "on",
            "amount_tolerance_cents": (
                int(form_data["amount_tolerance_cents"])
                if form_data.get("amount_tolerance_cents")
                else None
            ),
            "date_window_days": (
                int(form_data["date_window_days"])
                if form_data.get("date_window_days")
                else None
            ),
            "action_type": str(form_data.get("action_type", "MATCH")),
            "min_confidence": int(form_data.get("min_confidence", 90)),
            "writeoff_account_id": (
                UUID(str(form_data["writeoff_account_id"]))
                if form_data.get("writeoff_account_id")
                else None
            ),
            "journal_label_template": (
                str(form_data["journal_label_template"]).strip()
                if form_data.get("journal_label_template")
                else None
            ),
        }


# Module-level singleton
recon_rule_web_service = ReconciliationRuleWebService()
