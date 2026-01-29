"""
Payroll Web Service - Tax operations.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Optional

from fastapi import Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session, joinedload

from app.models.people.hr.employee import Employee, EmployeeStatus
from app.models.people.payroll.tax_band import TaxBand
from app.models.people.payroll.employee_tax_profile import EmployeeTaxProfile
from app.services.common import coerce_uuid
from app.services.people.payroll.paye_calculator import PAYECalculator
from app.templates import templates
from app.web.deps import base_context, WebAuthContext


def _get_form_str(form: Any, key: str, default: str = "") -> str:
    value = form.get(key, default) if form is not None else default
    if isinstance(value, UploadFile) or value is None:
        return default
    return str(value).strip()

from .base import (
    DEFAULT_PAGE_SIZE,
    parse_uuid,
    parse_decimal,
    parse_bool,
)


def _safe_form_text(value: object) -> str:
    """Normalize form values to text for safe parsing."""
    if value is None:
        return ""
    if isinstance(value, UploadFile):
        return ""
    if isinstance(value, str):
        return value
    return str(value)


class TaxWebService:
    """Service for tax-related web views."""

    def list_tax_bands_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Render tax bands list page."""
        org_id = coerce_uuid(auth.organization_id)

        bands = (
            db.query(TaxBand)
            .filter(TaxBand.organization_id == org_id, TaxBand.is_active == True)
            .order_by(TaxBand.sequence)
            .all()
        )

        context = base_context(request, auth, "Tax Bands (NTA 2025)", "payroll", db=db)
        context["request"] = request
        context.update({"bands": bands})
        return templates.TemplateResponse(request, "people/payroll/tax_bands.html", context)

    def seed_tax_bands_response(
        self,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        """Seed default tax bands."""
        org_id = coerce_uuid(auth.organization_id)

        # Check if bands already exist
        existing = db.query(TaxBand).filter(TaxBand.organization_id == org_id).count()
        if existing == 0:
            # NTA 2025 tax bands
            bands = [
                TaxBand(
                    organization_id=org_id,
                    sequence=1,
                    lower_limit=Decimal("0"),
                    upper_limit=Decimal("300000"),
                    rate=Decimal("0.07"),
                    is_active=True,
                ),
                TaxBand(
                    organization_id=org_id,
                    sequence=2,
                    lower_limit=Decimal("300000"),
                    upper_limit=Decimal("600000"),
                    rate=Decimal("0.11"),
                    is_active=True,
                ),
                TaxBand(
                    organization_id=org_id,
                    sequence=3,
                    lower_limit=Decimal("600000"),
                    upper_limit=Decimal("1100000"),
                    rate=Decimal("0.15"),
                    is_active=True,
                ),
                TaxBand(
                    organization_id=org_id,
                    sequence=4,
                    lower_limit=Decimal("1100000"),
                    upper_limit=Decimal("1600000"),
                    rate=Decimal("0.19"),
                    is_active=True,
                ),
                TaxBand(
                    organization_id=org_id,
                    sequence=5,
                    lower_limit=Decimal("1600000"),
                    upper_limit=Decimal("3200000"),
                    rate=Decimal("0.21"),
                    is_active=True,
                ),
                TaxBand(
                    organization_id=org_id,
                    sequence=6,
                    lower_limit=Decimal("3200000"),
                    upper_limit=None,
                    rate=Decimal("0.24"),
                    is_active=True,
                ),
            ]
            db.add_all(bands)
            db.commit()

        return RedirectResponse(url="/people/payroll/tax/bands", status_code=303)

    def tax_calculator_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Render PAYE tax calculator form."""
        org_id = coerce_uuid(auth.organization_id)

        bands = (
            db.query(TaxBand)
            .filter(TaxBand.organization_id == org_id, TaxBand.is_active == True)
            .order_by(TaxBand.sequence)
            .all()
        )

        context = base_context(request, auth, "PAYE Calculator", "payroll", db=db)
        context["request"] = request
        context.update({
            "bands": bands,
            "form_data": {},
            "result": None,
        })
        return templates.TemplateResponse(request, "people/payroll/tax_calculator.html", context)

    async def calculate_tax_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Calculate PAYE tax."""
        org_id = coerce_uuid(auth.organization_id)

        form = getattr(request.state, "csrf_form", None)
        if form is None:
            form = await request.form()

        gross_monthly = parse_decimal(_safe_form_text(form.get("gross_monthly"))) or Decimal("0")
        basic_monthly = parse_decimal(_safe_form_text(form.get("basic_monthly"))) or Decimal("0")
        annual_rent = parse_decimal(_safe_form_text(form.get("annual_rent"))) or Decimal("0")
        rent_verified = parse_bool(_safe_form_text(form.get("rent_verified")), False)
        pension_rate = parse_decimal(_safe_form_text(form.get("pension_rate"))) or Decimal("0.08")
        nhf_rate = parse_decimal(_safe_form_text(form.get("nhf_rate"))) or Decimal("0.025")
        nhis_rate = parse_decimal(_safe_form_text(form.get("nhis_rate"))) or Decimal("0")

        bands = (
            db.query(TaxBand)
            .filter(TaxBand.organization_id == org_id, TaxBand.is_active == True)
            .order_by(TaxBand.sequence)
            .all()
        )

        result = None
        if gross_monthly > 0:
            calculator = PAYECalculator(db)
            result = calculator.calculate(
                organization_id=org_id,
                gross_monthly=gross_monthly,
                basic_monthly=basic_monthly,
                annual_rent=annual_rent,
                rent_verified=rent_verified,
                pension_rate=pension_rate,
                nhf_rate=nhf_rate,
                nhis_rate=nhis_rate,
            )

        context = base_context(request, auth, "PAYE Calculator", "payroll", db=db)
        context["request"] = request
        context.update({
            "bands": bands,
            "form_data": {
                "gross_monthly": str(gross_monthly),
                "basic_monthly": str(basic_monthly),
                "annual_rent": str(annual_rent),
                "rent_verified": rent_verified,
                "pension_rate": str(pension_rate),
                "nhf_rate": str(nhf_rate),
                "nhis_rate": str(nhis_rate),
            },
            "result": result,
        })
        return templates.TemplateResponse(request, "people/payroll/tax_calculator.html", context)

    # =========================================================================
    # Tax Profiles
    # =========================================================================

    def list_tax_profiles_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        page: int = 1,
    ) -> HTMLResponse | RedirectResponse:
        """Render tax profiles list page."""
        org_id = coerce_uuid(auth.organization_id)
        per_page = DEFAULT_PAGE_SIZE
        offset = (page - 1) * per_page

        query = (
            db.query(EmployeeTaxProfile)
            .options(joinedload(EmployeeTaxProfile.employee))
            .filter(
                EmployeeTaxProfile.organization_id == org_id,
                EmployeeTaxProfile.effective_to.is_(None),
            )
        )

        total = query.count()
        profiles = query.offset(offset).limit(per_page).all()
        total_pages = (total + per_page - 1) // per_page

        context = base_context(request, auth, "Tax Profiles", "payroll", db=db)
        context["request"] = request
        context.update({
            "profiles": profiles,
            "page": page,
            "total_pages": total_pages,
            "total": total,
            "has_prev": page > 1,
            "has_next": page < total_pages,
        })
        return templates.TemplateResponse(request, "people/payroll/tax_profiles.html", context)

    def tax_profile_new_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        employee_id: Optional[str] = None,
    ) -> HTMLResponse | RedirectResponse:
        """Render new tax profile form."""
        org_id = coerce_uuid(auth.organization_id)

        selected_employee = None
        if employee_id:
            selected_employee = db.get(Employee, parse_uuid(employee_id))

        # Get employees without tax profiles
        existing_profile_ids = (
            db.query(EmployeeTaxProfile.employee_id)
            .filter(
                EmployeeTaxProfile.organization_id == org_id,
                EmployeeTaxProfile.effective_to.is_(None),
            )
        )

        employees = (
            db.query(Employee)
            .filter(
                Employee.organization_id == org_id,
                Employee.status.in_([EmployeeStatus.ACTIVE, EmployeeStatus.ON_LEAVE]),
                ~Employee.employee_id.in_(existing_profile_ids),
            )
            .order_by(Employee.employee_code)
            .all()
        )

        context = base_context(request, auth, "New Tax Profile", "payroll", db=db)
        context["request"] = request
        context.update({
            "profile": None,
            "employees": employees,
            "selected_employee": selected_employee,
            "selected_employee_id": employee_id,
            "is_edit": False,
            "form_data": {},
            "errors": {},
        })
        return templates.TemplateResponse(request, "people/payroll/tax_profile_form.html", context)

    async def create_tax_profile_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Create new tax profile."""
        org_id = coerce_uuid(auth.organization_id)

        form = getattr(request.state, "csrf_form", None)
        if form is None:
            form = await request.form()

        employee_id = _safe_form_text(form.get("employee_id")).strip()
        tin = _safe_form_text(form.get("tin")).strip()
        annual_rent = parse_decimal(_safe_form_text(form.get("annual_rent"))) or Decimal("0")
        rent_receipt_verified = parse_bool(_safe_form_text(form.get("rent_receipt_verified")), False)
        pension_rate = parse_decimal(_safe_form_text(form.get("pension_rate"))) or Decimal("0.08")
        nhf_rate = parse_decimal(_safe_form_text(form.get("nhf_rate"))) or Decimal("0.025")
        nhis_rate = parse_decimal(_safe_form_text(form.get("nhis_rate"))) or Decimal("0")
        voluntary_pension = parse_decimal(_safe_form_text(form.get("voluntary_pension"))) or Decimal("0")
        life_insurance = parse_decimal(_safe_form_text(form.get("life_insurance"))) or Decimal("0")

        try:
            profile = EmployeeTaxProfile(
                organization_id=org_id,
                employee_id=parse_uuid(employee_id),
                tin=tin or None,
                annual_rent=annual_rent,
                rent_receipt_verified=rent_receipt_verified,
                pension_rate=pension_rate,
                nhf_rate=nhf_rate,
                nhis_rate=nhis_rate,
                voluntary_pension=voluntary_pension,
                life_insurance=life_insurance,
                effective_from=date.today(),
            )

            db.add(profile)
            db.commit()
            return RedirectResponse(url=f"/people/payroll/tax/profiles/{employee_id}", status_code=303)

        except Exception as e:
            db.rollback()
            return self._render_tax_profile_form_with_error(
                request, auth, db, str(e), {
                    "employee_id": employee_id,
                    "tin": tin,
                    "annual_rent": str(annual_rent),
                    "rent_receipt_verified": rent_receipt_verified,
                    "pension_rate": str(pension_rate),
                    "nhf_rate": str(nhf_rate),
                    "nhis_rate": str(nhis_rate),
                    "voluntary_pension": str(voluntary_pension),
                    "life_insurance": str(life_insurance),
                }
            )

    def tax_profile_detail_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        employee_id: str,
    ) -> HTMLResponse | RedirectResponse:
        """Render tax profile detail page."""
        org_id = coerce_uuid(auth.organization_id)
        e_id = parse_uuid(employee_id)

        if not e_id:
            return RedirectResponse(url="/people/payroll/tax/profiles", status_code=303)

        employee = db.get(Employee, e_id)
        if not employee or employee.organization_id != org_id:
            return RedirectResponse(url="/people/payroll/tax/profiles", status_code=303)

        profile = (
            db.query(EmployeeTaxProfile)
            .filter(
                EmployeeTaxProfile.organization_id == org_id,
                EmployeeTaxProfile.employee_id == e_id,
                EmployeeTaxProfile.effective_to.is_(None),
            )
            .first()
        )

        context = base_context(request, auth, f"Tax Profile - {employee.full_name}", "payroll", db=db)
        context["request"] = request
        context.update({
            "employee": employee,
            "profile": profile,
        })
        return templates.TemplateResponse(request, "people/payroll/tax_profile_detail.html", context)

    def tax_profile_edit_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        employee_id: str,
    ) -> HTMLResponse | RedirectResponse:
        """Render edit tax profile form."""
        org_id = coerce_uuid(auth.organization_id)
        e_id = parse_uuid(employee_id)

        if not e_id:
            return RedirectResponse(url="/people/payroll/tax/profiles", status_code=303)

        employee = db.get(Employee, e_id)
        if not employee or employee.organization_id != org_id:
            return RedirectResponse(url="/people/payroll/tax/profiles", status_code=303)

        profile = (
            db.query(EmployeeTaxProfile)
            .filter(
                EmployeeTaxProfile.organization_id == org_id,
                EmployeeTaxProfile.employee_id == e_id,
                EmployeeTaxProfile.effective_to.is_(None),
            )
            .first()
        )

        if not profile:
            return RedirectResponse(url="/people/payroll/tax/profiles", status_code=303)

        context = base_context(request, auth, f"Edit Tax Profile - {employee.full_name}", "payroll", db=db)
        context["request"] = request
        context.update({
            "profile": profile,
            "employees": [employee],
            "selected_employee": employee,
            "selected_employee_id": employee_id,
            "is_edit": True,
            "form_data": {},
            "errors": {},
        })
        return templates.TemplateResponse(request, "people/payroll/tax_profile_form.html", context)

    async def update_tax_profile_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        employee_id: str,
    ) -> HTMLResponse | RedirectResponse:
        """Update tax profile."""
        org_id = coerce_uuid(auth.organization_id)
        e_id = parse_uuid(employee_id)

        if not e_id:
            return RedirectResponse(url="/people/payroll/tax/profiles", status_code=303)

        profile = (
            db.query(EmployeeTaxProfile)
            .filter(
                EmployeeTaxProfile.organization_id == org_id,
                EmployeeTaxProfile.employee_id == e_id,
                EmployeeTaxProfile.effective_to.is_(None),
            )
            .first()
        )

        if not profile:
            return RedirectResponse(url="/people/payroll/tax/profiles", status_code=303)

        form = getattr(request.state, "csrf_form", None)
        if form is None:
            form = await request.form()

        try:
            profile.tin = _get_form_str(form, "tin") or None
            profile.annual_rent = parse_decimal(_get_form_str(form, "annual_rent") or None) or Decimal("0")
            profile.rent_receipt_verified = parse_bool(
                _get_form_str(form, "rent_receipt_verified") or None, False
            )
            profile.pension_rate = parse_decimal(_get_form_str(form, "pension_rate") or None) or Decimal("0.08")
            profile.nhf_rate = parse_decimal(_get_form_str(form, "nhf_rate") or None) or Decimal("0.025")
            profile.nhis_rate = parse_decimal(_get_form_str(form, "nhis_rate") or None) or Decimal("0")

            db.commit()
            return RedirectResponse(url=f"/people/payroll/tax/profiles/{employee_id}", status_code=303)

        except Exception as e:
            db.rollback()

            employee = db.get(Employee, e_id)
            employee_name = employee.full_name if employee else "Employee"
            context = base_context(
                request, auth, f"Edit Tax Profile - {employee_name}", "payroll", db=db
            )
            context["request"] = request
            context.update({
                "profile": profile,
                "employees": [employee],
                "selected_employee": employee,
                "selected_employee_id": employee_id,
                "is_edit": True,
                "form_data": {},
                "error": str(e),
                "errors": {},
            })
            return templates.TemplateResponse(request, "people/payroll/tax_profile_form.html", context)

    def _render_tax_profile_form_with_error(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        error: str,
        form_data: dict,
    ) -> HTMLResponse | RedirectResponse:
        """Render tax profile form with error."""
        org_id = coerce_uuid(auth.organization_id)

        existing_profile_ids = (
            db.query(EmployeeTaxProfile.employee_id)
            .filter(
                EmployeeTaxProfile.organization_id == org_id,
                EmployeeTaxProfile.effective_to.is_(None),
            )
        )

        employees = (
            db.query(Employee)
            .filter(
                Employee.organization_id == org_id,
                Employee.status.in_([EmployeeStatus.ACTIVE, EmployeeStatus.ON_LEAVE]),
                ~Employee.employee_id.in_(existing_profile_ids),
            )
            .order_by(Employee.employee_code)
            .all()
        )

        context = base_context(request, auth, "New Tax Profile", "payroll", db=db)
        context["request"] = request
        context.update({
            "profile": None,
            "employees": employees,
            "selected_employee": None,
            "selected_employee_id": form_data.get("employee_id"),
            "is_edit": False,
            "form_data": form_data,
            "error": error,
            "errors": {},
        })
        return templates.TemplateResponse(request, "people/payroll/tax_profile_form.html", context)
