"""
Payroll Web Service - Salary Component operations.
"""

from __future__ import annotations

import logging

from fastapi import Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from app.models.finance.gl.account import Account
from app.models.finance.gl.account_category import AccountCategory, IFRSCategory
from app.models.people.payroll.salary_component import (
    SalaryComponent,
    SalaryComponentType,
)
from app.models.people.payroll.salary_slip import SalarySlipDeduction, SalarySlipEarning
from app.models.people.payroll.salary_structure import (
    SalaryStructureDeduction,
    SalaryStructureEarning,
)
from app.services.common import coerce_uuid
from app.templates import templates
from app.web.deps import WebAuthContext, base_context

from .base import (
    COMPONENT_TYPES,
    DEFAULT_PAGE_SIZE,
    parse_bool,
    parse_component_type,
    parse_uuid,
)

logger = logging.getLogger(__name__)


def _safe_form_text(value: object) -> str:
    """Normalize form values to text for safe parsing."""
    if value is None:
        return ""
    if isinstance(value, UploadFile):
        return ""
    if isinstance(value, str):
        return value
    return str(value)


class ComponentWebService:
    """Service for salary component web views."""

    def list_components_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        search: str | None = None,
        component_type: str | None = None,
        page: int = 1,
    ) -> Response:
        """Render salary components list page."""
        org_id = coerce_uuid(auth.organization_id)
        per_page = DEFAULT_PAGE_SIZE
        offset = (page - 1) * per_page

        query = db.query(SalaryComponent).filter(
            SalaryComponent.organization_id == org_id
        )

        if search:
            query = query.filter(
                SalaryComponent.component_name.ilike(f"%{search}%")
                | SalaryComponent.component_code.ilike(f"%{search}%")
            )

        type_enum = parse_component_type(component_type)
        if type_enum:
            query = query.filter(SalaryComponent.component_type == type_enum)

        total = query.count()
        components = (
            query.order_by(SalaryComponent.display_order)
            .offset(offset)
            .limit(per_page)
            .all()
        )
        total_pages = (total + per_page - 1) // per_page

        context = base_context(request, auth, "Salary Components", "payroll", db=db)
        context["request"] = request
        context.update(
            {
                "components": components,
                "search": search,
                "component_type": component_type,
                "page": page,
                "total_pages": total_pages,
                "total": total,
                "has_prev": page > 1,
                "has_next": page < total_pages,
            }
        )
        return templates.TemplateResponse(
            request, "people/payroll/components.html", context
        )

    def component_new_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Render new salary component form."""
        org_id = coerce_uuid(auth.organization_id)

        # Get expense accounts for dropdown
        expense_accounts = (
            db.query(Account)
            .join(AccountCategory, Account.category_id == AccountCategory.category_id)
            .filter(
                Account.organization_id == org_id,
                AccountCategory.ifrs_category == IFRSCategory.EXPENSES,
                Account.is_active.is_(True),
                AccountCategory.is_active.is_(True),
            )
            .order_by(Account.account_code)
            .all()
        )

        # Get liability accounts for dropdown
        liability_accounts = (
            db.query(Account)
            .join(AccountCategory, Account.category_id == AccountCategory.category_id)
            .filter(
                Account.organization_id == org_id,
                AccountCategory.ifrs_category == IFRSCategory.LIABILITIES,
                Account.is_active.is_(True),
                AccountCategory.is_active.is_(True),
            )
            .order_by(Account.account_code)
            .all()
        )

        context = base_context(request, auth, "New Salary Component", "payroll", db=db)
        context["request"] = request
        context.update(
            {
                "component": None,
                "expense_accounts": expense_accounts,
                "liability_accounts": liability_accounts,
                "component_types": COMPONENT_TYPES,
                "form_data": {},
                "errors": {},
            }
        )
        return templates.TemplateResponse(
            request, "people/payroll/component_form.html", context
        )

    def component_edit_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        component_id: str,
    ) -> HTMLResponse | RedirectResponse:
        """Render edit salary component form."""
        org_id = coerce_uuid(auth.organization_id)
        c_id = parse_uuid(component_id)

        if not c_id:
            return RedirectResponse(url="/people/payroll/components", status_code=303)

        component = db.get(SalaryComponent, c_id)
        if not component or component.organization_id != org_id:
            return RedirectResponse(url="/people/payroll/components", status_code=303)

        expense_accounts = (
            db.query(Account)
            .join(AccountCategory, Account.category_id == AccountCategory.category_id)
            .filter(
                Account.organization_id == org_id,
                AccountCategory.ifrs_category == IFRSCategory.EXPENSES,
                Account.is_active.is_(True),
                AccountCategory.is_active.is_(True),
            )
            .order_by(Account.account_code)
            .all()
        )

        liability_accounts = (
            db.query(Account)
            .join(AccountCategory, Account.category_id == AccountCategory.category_id)
            .filter(
                Account.organization_id == org_id,
                AccountCategory.ifrs_category == IFRSCategory.LIABILITIES,
                Account.is_active.is_(True),
                AccountCategory.is_active.is_(True),
            )
            .order_by(Account.account_code)
            .all()
        )

        context = base_context(request, auth, "Edit Salary Component", "payroll", db=db)
        context["request"] = request
        context.update(
            {
                "component": component,
                "expense_accounts": expense_accounts,
                "liability_accounts": liability_accounts,
                "component_types": COMPONENT_TYPES,
                "form_data": {},
                "errors": {},
            }
        )
        return templates.TemplateResponse(
            request, "people/payroll/component_form.html", context
        )

    async def create_component_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Create new salary component."""
        org_id = coerce_uuid(auth.organization_id)

        form = getattr(request.state, "csrf_form", None)
        if form is None:
            form = await request.form()

        component_code = _safe_form_text(form.get("component_code")).strip()
        component_name = _safe_form_text(form.get("component_name")).strip()
        component_type = _safe_form_text(form.get("component_type")).strip()
        abbr = _safe_form_text(form.get("abbr")).strip()
        description = _safe_form_text(form.get("description")).strip()
        expense_account_id = _safe_form_text(form.get("expense_account_id")).strip()
        liability_account_id = _safe_form_text(form.get("liability_account_id")).strip()
        is_tax_applicable = parse_bool(
            _safe_form_text(form.get("is_tax_applicable")), False
        )
        is_statutory = parse_bool(_safe_form_text(form.get("is_statutory")), False)
        depends_on_payment_days = parse_bool(
            _safe_form_text(form.get("depends_on_payment_days")), True
        )

        try:
            component = SalaryComponent(
                organization_id=org_id,
                component_code=component_code,
                component_name=component_name,
                component_type=SalaryComponentType(component_type.upper()),
                abbr=abbr or None,
                description=description or None,
                expense_account_id=parse_uuid(expense_account_id),
                liability_account_id=parse_uuid(liability_account_id),
                is_tax_applicable=is_tax_applicable,
                is_statutory=is_statutory,
                depends_on_payment_days=depends_on_payment_days,
                is_active=True,
            )

            db.add(component)
            db.commit()
            return RedirectResponse(url="/people/payroll/components", status_code=303)

        except Exception as e:
            db.rollback()

            expense_accounts = (
                db.query(Account)
                .join(
                    AccountCategory, Account.category_id == AccountCategory.category_id
                )
                .filter(
                    Account.organization_id == org_id,
                    AccountCategory.ifrs_category == IFRSCategory.EXPENSES,
                    Account.is_active.is_(True),
                )
                .order_by(Account.account_code)
                .all()
            )

            liability_accounts = (
                db.query(Account)
                .join(
                    AccountCategory, Account.category_id == AccountCategory.category_id
                )
                .filter(
                    Account.organization_id == org_id,
                    AccountCategory.ifrs_category == IFRSCategory.LIABILITIES,
                    Account.is_active.is_(True),
                )
                .order_by(Account.account_code)
                .all()
            )

            context = base_context(
                request, auth, "New Salary Component", "payroll", db=db
            )
            context["request"] = request
            context.update(
                {
                    "component": None,
                    "expense_accounts": expense_accounts,
                    "liability_accounts": liability_accounts,
                    "component_types": COMPONENT_TYPES,
                    "form_data": {
                        "component_code": component_code,
                        "component_name": component_name,
                        "component_type": component_type,
                        "abbr": abbr,
                        "description": description,
                        "expense_account_id": expense_account_id,
                        "liability_account_id": liability_account_id,
                        "is_tax_applicable": is_tax_applicable,
                        "is_statutory": is_statutory,
                        "depends_on_payment_days": depends_on_payment_days,
                    },
                    "error": str(e),
                    "errors": {},
                }
            )
            return templates.TemplateResponse(
                request, "people/payroll/component_form.html", context
            )

    async def update_component_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        component_id: str,
    ) -> HTMLResponse | RedirectResponse:
        """Update salary component."""
        org_id = coerce_uuid(auth.organization_id)
        c_id = parse_uuid(component_id)

        if not c_id:
            return RedirectResponse(url="/people/payroll/components", status_code=303)

        component = db.get(SalaryComponent, c_id)
        if not component or component.organization_id != org_id:
            return RedirectResponse(url="/people/payroll/components", status_code=303)

        form = getattr(request.state, "csrf_form", None)
        if form is None:
            form = await request.form()

        component_code = _safe_form_text(form.get("component_code")).strip()
        component_name = _safe_form_text(form.get("component_name")).strip()
        component_type = _safe_form_text(form.get("component_type")).strip()
        abbr = _safe_form_text(form.get("abbr")).strip()
        description = _safe_form_text(form.get("description")).strip()
        expense_account_id = _safe_form_text(form.get("expense_account_id")).strip()
        liability_account_id = _safe_form_text(form.get("liability_account_id")).strip()
        is_tax_applicable = parse_bool(
            _safe_form_text(form.get("is_tax_applicable")), False
        )
        is_statutory = parse_bool(_safe_form_text(form.get("is_statutory")), False)
        depends_on_payment_days = parse_bool(
            _safe_form_text(form.get("depends_on_payment_days")), True
        )

        try:
            component.component_code = component_code
            component.component_name = component_name
            component.component_type = SalaryComponentType(component_type.upper())
            component.abbr = abbr or None
            component.description = description or None
            component.expense_account_id = parse_uuid(expense_account_id)
            component.liability_account_id = parse_uuid(liability_account_id)
            component.is_tax_applicable = is_tax_applicable
            component.is_statutory = is_statutory
            component.depends_on_payment_days = depends_on_payment_days

            db.commit()
            return RedirectResponse(url="/people/payroll/components", status_code=303)

        except Exception as e:
            db.rollback()

            expense_accounts = (
                db.query(Account)
                .join(
                    AccountCategory, Account.category_id == AccountCategory.category_id
                )
                .filter(
                    Account.organization_id == org_id,
                    AccountCategory.ifrs_category == IFRSCategory.EXPENSES,
                    Account.is_active.is_(True),
                )
                .order_by(Account.account_code)
                .all()
            )

            liability_accounts = (
                db.query(Account)
                .join(
                    AccountCategory, Account.category_id == AccountCategory.category_id
                )
                .filter(
                    Account.organization_id == org_id,
                    AccountCategory.ifrs_category == IFRSCategory.LIABILITIES,
                    Account.is_active.is_(True),
                )
                .order_by(Account.account_code)
                .all()
            )

            context = base_context(
                request, auth, "Edit Salary Component", "payroll", db=db
            )
            context["request"] = request
            context.update(
                {
                    "component": component,
                    "expense_accounts": expense_accounts,
                    "liability_accounts": liability_accounts,
                    "component_types": COMPONENT_TYPES,
                    "form_data": {
                        "component_code": component_code,
                        "component_name": component_name,
                        "component_type": component_type,
                        "abbr": abbr,
                        "description": description,
                        "expense_account_id": expense_account_id,
                        "liability_account_id": liability_account_id,
                        "is_tax_applicable": is_tax_applicable,
                        "is_statutory": is_statutory,
                        "depends_on_payment_days": depends_on_payment_days,
                    },
                    "error": str(e),
                    "errors": {},
                }
            )
            return templates.TemplateResponse(
                request, "people/payroll/component_form.html", context
            )

    def delete_component_response(
        self,
        auth: WebAuthContext,
        db: Session,
        component_id: str,
    ) -> RedirectResponse:
        """Delete or deactivate a salary component."""
        org_id = coerce_uuid(auth.organization_id)
        c_id = parse_uuid(component_id)

        if not c_id:
            return RedirectResponse(url="/people/payroll/components", status_code=303)

        component = db.get(SalaryComponent, c_id)
        if not component or component.organization_id != org_id:
            return RedirectResponse(url="/people/payroll/components", status_code=303)

        in_structure = (
            db.query(SalaryStructureEarning)
            .filter(SalaryStructureEarning.component_id == c_id)
            .first()
            is not None
        ) or (
            db.query(SalaryStructureDeduction)
            .filter(SalaryStructureDeduction.component_id == c_id)
            .first()
            is not None
        )
        in_slips = (
            db.query(SalarySlipEarning)
            .filter(SalarySlipEarning.component_id == c_id)
            .first()
            is not None
        ) or (
            db.query(SalarySlipDeduction)
            .filter(SalarySlipDeduction.component_id == c_id)
            .first()
            is not None
        )

        try:
            if in_structure or in_slips:
                component.is_active = False
            else:
                db.delete(component)
            db.commit()
        except Exception:
            db.rollback()

        return RedirectResponse(url="/people/payroll/components", status_code=303)
