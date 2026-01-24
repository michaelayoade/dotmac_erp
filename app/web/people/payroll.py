"""
Payroll Web Routes.

HTML template routes for Salary Components, Structures, and Slips.
"""

from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Optional

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.models.people.payroll.salary_component import SalaryComponent, SalaryComponentType
from app.models.people.payroll.salary_structure import SalaryStructure, PayrollFrequency
from app.models.people.payroll.salary_slip import SalarySlip, SalarySlipStatus
from app.models.people.payroll.salary_assignment import SalaryStructureAssignment
from app.models.people.payroll.payroll_entry import PayrollEntry, PayrollEntryStatus
from app.models.people.payroll.tax_band import TaxBand
from app.models.people.payroll.employee_tax_profile import EmployeeTaxProfile
from app.models.people.hr.employee import Employee, EmployeeStatus
from app.models.people.hr.department import Department
from app.models.people.hr.designation import Designation
from app.services.people.payroll import (
    salary_slip_service,
    SalarySlipInput,
    payroll_gl_adapter,
)
from app.services.people.payroll.payroll_service import PayrollService, PayrollServiceError
from app.services.people.payroll.paye_calculator import PAYECalculator
from app.services.people.payroll.payroll_web import (
    payroll_web_service,
    AssignmentCreateData,
    AssignmentUpdateData,
)
from app.services.common import coerce_uuid
from app.web.deps import base_context, get_db, require_hr_access, WebAuthContext
from app.templates import templates


router = APIRouter(prefix="/payroll", tags=["payroll-web"])

def _parse_bool(value: Optional[str], default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).lower() in {"1", "true", "on", "yes"}


def _parse_decimal(value: str) -> Optional[Decimal]:
    if not value:
        return None
    try:
        return Decimal(value)
    except (InvalidOperation, ValueError):
        return None

# =============================================================================
# Salary Components
# =============================================================================


@router.get("/components", response_class=HTMLResponse)
def list_salary_components(
    request: Request,
    search: Optional[str] = None,
    component_type: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Salary component list page."""
    org_id = coerce_uuid(auth.organization_id)
    per_page = 20
    offset = (page - 1) * per_page

    query = db.query(SalaryComponent).filter(SalaryComponent.organization_id == org_id)

    if search:
        query = query.filter(
            SalaryComponent.component_name.ilike(f"%{search}%")
            | SalaryComponent.component_code.ilike(f"%{search}%")
        )

    if component_type:
        try:
            type_enum = SalaryComponentType(component_type.upper())
            query = query.filter(SalaryComponent.component_type == type_enum)
        except ValueError:
            pass

    total = query.count()
    components = query.order_by(SalaryComponent.display_order).offset(offset).limit(per_page).all()

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
    return templates.TemplateResponse(request, "people/payroll/components.html", context)


@router.get("/components/new", response_class=HTMLResponse)
def new_component_form(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """New salary component form."""
    from app.models.finance.gl.account import Account
    from app.models.finance.gl.account_category import AccountCategory, IFRSCategory

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
            "component_types": [t.value for t in SalaryComponentType],
        }
    )
    return templates.TemplateResponse(request, "people/payroll/component_form.html", context)


@router.post("/components/new")
async def create_component(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Create new salary component."""
    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()

    component_code = (form.get("component_code") or "").strip()
    component_name = (form.get("component_name") or "").strip()
    component_type = (form.get("component_type") or "").strip()
    abbr = (form.get("abbr") or "").strip()
    description = (form.get("description") or "").strip()
    expense_account_id = (form.get("expense_account_id") or "").strip()
    liability_account_id = (form.get("liability_account_id") or "").strip()
    is_tax_applicable = _parse_bool(form.get("is_tax_applicable"), False)
    is_statutory = _parse_bool(form.get("is_statutory"), False)
    depends_on_payment_days = _parse_bool(form.get("depends_on_payment_days"), True)

    org_id = coerce_uuid(auth.organization_id)

    component = SalaryComponent(
        organization_id=org_id,
        component_code=component_code,
        component_name=component_name,
        component_type=SalaryComponentType(component_type.upper()),
        abbr=abbr or None,
        description=description or None,
        expense_account_id=coerce_uuid(expense_account_id) if expense_account_id else None,
        liability_account_id=coerce_uuid(liability_account_id) if liability_account_id else None,
        is_tax_applicable=is_tax_applicable,
        is_statutory=is_statutory,
        depends_on_payment_days=depends_on_payment_days,
        is_active=True,
    )

    db.add(component)
    db.commit()

    return RedirectResponse(url="/people/payroll/components", status_code=303)


# =============================================================================
# Salary Slips
# =============================================================================


@router.get("/slips", response_class=HTMLResponse)
def list_salary_slips(
    request: Request,
    search: Optional[str] = None,
    status: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Salary slip list page."""
    org_id = coerce_uuid(auth.organization_id)
    per_page = 20
    offset = (page - 1) * per_page

    query = db.query(SalarySlip).filter(SalarySlip.organization_id == org_id)

    if search:
        query = query.filter(
            SalarySlip.slip_number.ilike(f"%{search}%")
            | SalarySlip.employee_name.ilike(f"%{search}%")
        )

    if status:
        try:
            status_enum = SalarySlipStatus(status.upper())
            query = query.filter(SalarySlip.status == status_enum)
        except ValueError:
            pass

    total = query.count()
    slips = query.order_by(SalarySlip.created_at.desc()).offset(offset).limit(per_page).all()

    total_pages = (total + per_page - 1) // per_page

    # Get counts by status
    status_counts = {}
    for s in SalarySlipStatus:
        count = (
            db.query(SalarySlip)
            .filter(SalarySlip.organization_id == org_id, SalarySlip.status == s)
            .count()
        )
        status_counts[s.value] = count

    context = base_context(request, auth, "Salary Slips", "payroll", db=db)
    context["request"] = request
    context.update(
        {
            "slips": slips,
            "search": search,
            "status": status,
            "page": page,
            "total_pages": total_pages,
            "total": total,
            "has_prev": page > 1,
            "has_next": page < total_pages,
            "status_counts": status_counts,
            "statuses": [s.value for s in SalarySlipStatus],
        }
    )
    return templates.TemplateResponse(request, "people/payroll/slips.html", context)


@router.get("/slips/new", response_class=HTMLResponse)
def new_slip_form(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """New salary slip form."""
    org_id = coerce_uuid(auth.organization_id)

    # Get active employees
    employees = (
        db.query(Employee)
        .filter(
            Employee.organization_id == org_id,
            Employee.status.in_([EmployeeStatus.ACTIVE, EmployeeStatus.ON_LEAVE]),
        )
        .order_by(Employee.employee_code)
        .all()
    )

    context = base_context(request, auth, "New Salary Slip", "payroll", db=db)
    context["request"] = request
    context.update(
        {
            "slip": None,
            "employees": employees,
        }
    )
    return templates.TemplateResponse(request, "people/payroll/slip_form.html", context)


@router.post("/slips/new")
async def create_slip(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Create new salary slip."""
    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()

    employee_id = (form.get("employee_id") or "").strip()
    start_date = (form.get("start_date") or "").strip()
    end_date = (form.get("end_date") or "").strip()
    posting_date = (form.get("posting_date") or "").strip()
    total_working_days = (form.get("total_working_days") or "").strip()
    absent_days = (form.get("absent_days") or "0").strip()
    leave_without_pay = (form.get("leave_without_pay") or "0").strip()

    org_id = coerce_uuid(auth.organization_id)
    user_id = coerce_uuid(auth.user_id)

    if not employee_id or not start_date or not end_date:
        employees = (
            db.query(Employee)
            .filter(
                Employee.organization_id == org_id,
                Employee.status.in_([EmployeeStatus.ACTIVE, EmployeeStatus.ON_LEAVE]),
            )
            .order_by(Employee.employee_code)
            .all()
        )
        context = base_context(request, auth, "New Salary Slip", "payroll", db=db)
        context["request"] = request
        context.update(
            {
                "slip": None,
                "employees": employees,
                "error": "Employee, period start, and period end are required.",
            }
        )
        return templates.TemplateResponse(request, "people/payroll/slip_form.html", context)

    # Parse dates
    start = datetime.strptime(start_date, "%Y-%m-%d").date()
    end = datetime.strptime(end_date, "%Y-%m-%d").date()
    posting = datetime.strptime(posting_date, "%Y-%m-%d").date() if posting_date else None

    input = SalarySlipInput(
        employee_id=coerce_uuid(employee_id),
        start_date=start,
        end_date=end,
        posting_date=posting,
        total_working_days=Decimal(total_working_days) if total_working_days else None,
        absent_days=Decimal(absent_days),
        leave_without_pay=Decimal(leave_without_pay),
    )

    try:
        slip = salary_slip_service.create_salary_slip(
            db=db,
            organization_id=org_id,
            input=input,
            created_by_user_id=user_id,
        )
        return RedirectResponse(url=f"/people/payroll/slips/{slip.slip_id}", status_code=303)
    except Exception as e:
        # Re-render form with error
        employees = (
            db.query(Employee)
            .filter(
                Employee.organization_id == org_id,
                Employee.status.in_([EmployeeStatus.ACTIVE, EmployeeStatus.ON_LEAVE]),
            )
            .order_by(Employee.employee_code)
            .all()
        )

        context = base_context(request, auth, "New Salary Slip", "payroll", db=db)
        context["request"] = request
        context.update(
            {
                "slip": None,
                "employees": employees,
                "error": str(e),
            }
        )
        return templates.TemplateResponse(request, "people/payroll/slip_form.html", context)


@router.get("/slips/{slip_id}", response_class=HTMLResponse)
def view_slip(
    request: Request,
    slip_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """View salary slip details."""
    return payroll_web_service.slip_detail_response(request, auth, db, slip_id)


@router.post("/slips/{slip_id}/submit")
def submit_slip(
    request: Request,
    slip_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Submit salary slip for approval."""
    org_id = coerce_uuid(auth.organization_id)
    user_id = coerce_uuid(auth.user_id)

    salary_slip_service.submit_salary_slip(
        db=db,
        organization_id=org_id,
        slip_id=coerce_uuid(slip_id),
        submitted_by_user_id=user_id,
    )

    return RedirectResponse(url=f"/people/payroll/slips/{slip_id}", status_code=303)


@router.post("/slips/{slip_id}/approve")
def approve_slip(
    request: Request,
    slip_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Approve salary slip."""
    org_id = coerce_uuid(auth.organization_id)
    user_id = coerce_uuid(auth.user_id)

    salary_slip_service.approve_salary_slip(
        db=db,
        organization_id=org_id,
        slip_id=coerce_uuid(slip_id),
        approved_by_user_id=user_id,
    )

    return RedirectResponse(url=f"/people/payroll/slips/{slip_id}", status_code=303)


@router.post("/slips/{slip_id}/post")
def post_slip(
    request: Request,
    slip_id: str,
    posting_date: str = Form(None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Post salary slip to GL."""
    org_id = coerce_uuid(auth.organization_id)
    user_id = coerce_uuid(auth.user_id)

    post_date = datetime.strptime(posting_date, "%Y-%m-%d").date() if posting_date else date.today()

    result = payroll_gl_adapter.post_salary_slip(
        db=db,
        organization_id=org_id,
        slip_id=coerce_uuid(slip_id),
        posting_date=post_date,
        posted_by_user_id=user_id,
    )

    # Redirect back to slip detail (flash message would show result)
    return RedirectResponse(url=f"/people/payroll/slips/{slip_id}", status_code=303)


# =============================================================================
# Salary Structures
# =============================================================================


@router.get("/structures", response_class=HTMLResponse)
def list_salary_structures(
    request: Request,
    search: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Salary structure list page."""
    org_id = coerce_uuid(auth.organization_id)
    per_page = 20
    offset = (page - 1) * per_page

    query = db.query(SalaryStructure).filter(SalaryStructure.organization_id == org_id)

    if search:
        query = query.filter(
            SalaryStructure.structure_name.ilike(f"%{search}%")
            | SalaryStructure.structure_code.ilike(f"%{search}%")
        )

    total = query.count()
    structures = query.order_by(SalaryStructure.structure_name).offset(offset).limit(per_page).all()

    total_pages = (total + per_page - 1) // per_page

    context = base_context(request, auth, "Salary Structures", "payroll", db=db)
    context["request"] = request
    context.update(
        {
            "structures": structures,
            "search": search,
            "page": page,
            "total_pages": total_pages,
            "total": total,
            "has_prev": page > 1,
            "has_next": page < total_pages,
        }
    )
    return templates.TemplateResponse(request, "people/payroll/structures.html", context)


@router.get("/structures/new", response_class=HTMLResponse)
def new_structure_form(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """New salary structure form."""
    from app.models.people.payroll.salary_structure import PayrollFrequency

    org_id = coerce_uuid(auth.organization_id)

    # Get components for dropdown
    components = (
        db.query(SalaryComponent)
        .filter(SalaryComponent.organization_id == org_id, SalaryComponent.is_active == True)
        .order_by(SalaryComponent.display_order, SalaryComponent.component_name)
        .all()
    )

    context = base_context(request, auth, "New Salary Structure", "payroll", db=db)
    context["request"] = request
    context.update(
        {
            "structure": None,
            "components": components,
            "frequencies": [f.value for f in PayrollFrequency],
        }
    )
    return templates.TemplateResponse(request, "people/payroll/structure_form.html", context)


@router.post("/structures/new")
async def create_structure(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Create new salary structure."""
    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()

    structure_code = (form.get("structure_code") or "").strip()
    structure_name = (form.get("structure_name") or "").strip()
    description = (form.get("description") or "").strip()
    payroll_frequency = (form.get("payroll_frequency") or "MONTHLY").strip()
    is_active = _parse_bool(form.get("is_active"), True)

    earning_components = form.getlist("earning_component[]") if hasattr(form, "getlist") else []
    earning_formulas = form.getlist("earning_formula[]") if hasattr(form, "getlist") else []
    deduction_components = form.getlist("deduction_component[]") if hasattr(form, "getlist") else []
    deduction_formulas = form.getlist("deduction_formula[]") if hasattr(form, "getlist") else []

    def _build_lines(components: list[str], formulas: list[str]) -> list[dict]:
        lines: list[dict] = []
        for idx, comp_id in enumerate(components):
            if not comp_id:
                continue
            formula = (formulas[idx] if idx < len(formulas) else "").strip()
            amount = _parse_decimal(formula)
            if amount is not None:
                lines.append(
                    {
                        "component_id": coerce_uuid(comp_id),
                        "amount": amount,
                        "amount_based_on_formula": False,
                        "formula": None,
                        "display_order": idx,
                    }
                )
            else:
                lines.append(
                    {
                        "component_id": coerce_uuid(comp_id),
                        "amount": Decimal("0"),
                        "amount_based_on_formula": bool(formula),
                        "formula": formula or None,
                        "display_order": idx,
                    }
                )
        return lines

    earnings = _build_lines(earning_components, earning_formulas)
    deductions = _build_lines(deduction_components, deduction_formulas)

    org_id = coerce_uuid(auth.organization_id)
    svc = PayrollService(db)

    try:
        frequency_enum = PayrollFrequency(payroll_frequency.upper())
    except ValueError:
        frequency_enum = PayrollFrequency.MONTHLY

    structure = svc.create_salary_structure(
        org_id,
        structure_code=structure_code,
        structure_name=structure_name,
        description=description or None,
        payroll_frequency=frequency_enum,
        earnings=earnings,
        deductions=deductions,
    )
    structure.is_active = is_active
    db.commit()

    return RedirectResponse(
        url=f"/people/payroll/structures/{structure.structure_id}",
        status_code=303,
    )

@router.get("/structures/{structure_id}", response_class=HTMLResponse)
def view_structure(
    request: Request,
    structure_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """View salary structure details."""
    org_id = coerce_uuid(auth.organization_id)
    s_id = coerce_uuid(structure_id)

    structure = db.get(SalaryStructure, s_id)
    if not structure or structure.organization_id != org_id:
        return RedirectResponse(url="/people/payroll/structures", status_code=303)

    context = base_context(request, auth, "Salary Structure", "payroll", db=db)
    context["request"] = request
    context.update({"structure": structure})
    return templates.TemplateResponse(request, "people/payroll/structure_detail.html", context)


# =============================================================================
# Salary Structure Assignments
# =============================================================================


@router.get("/assignments", response_class=HTMLResponse)
def list_assignments(
    request: Request,
    search: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Salary structure assignments list page."""
    return payroll_web_service.list_assignments_response(request, auth, db, search, page)


@router.get("/assignments/new", response_class=HTMLResponse)
def new_assignment_form(
    request: Request,
    employee_id: Optional[str] = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """New salary structure assignment form."""
    return payroll_web_service.assignment_form_response(request, auth, db, employee_id=employee_id)


@router.post("/assignments/new")
async def create_assignment(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Create new salary structure assignment."""
    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()

    employee_id = (form.get("employee_id") or "").strip()
    structure_id = (form.get("structure_id") or "").strip()
    from_date_str = (form.get("from_date") or "").strip()
    to_date_str = (form.get("to_date") or "").strip()
    base_amount = (form.get("base") or "0").strip()
    variable_amount = (form.get("variable") or "0").strip()
    income_tax_slab = (form.get("income_tax_slab") or "").strip()

    org_id = coerce_uuid(auth.organization_id)

    # Parse dates
    from_date = datetime.strptime(from_date_str, "%Y-%m-%d").date() if from_date_str else date.today()
    to_date = datetime.strptime(to_date_str, "%Y-%m-%d").date() if to_date_str else None

    data = AssignmentCreateData(
        employee_id=coerce_uuid(employee_id),
        structure_id=coerce_uuid(structure_id),
        from_date=from_date,
        to_date=to_date,
        base=_parse_decimal(base_amount) or Decimal("0"),
        variable=_parse_decimal(variable_amount) or Decimal("0"),
        income_tax_slab=income_tax_slab or None,
    )

    payroll_web_service.create_assignment(db, org_id, data)
    db.commit()

    return RedirectResponse(url=f"/people/hr/employees/{employee_id}", status_code=303)


@router.get("/assignments/{assignment_id}/edit", response_class=HTMLResponse)
def edit_assignment_form(
    request: Request,
    assignment_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Edit salary structure assignment form."""
    return payroll_web_service.assignment_form_response(request, auth, db, assignment_id=assignment_id)


@router.post("/assignments/{assignment_id}/edit")
async def update_assignment(
    request: Request,
    assignment_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Update salary structure assignment."""
    org_id = coerce_uuid(auth.organization_id)
    a_id = coerce_uuid(assignment_id)

    # Get the assignment first to capture employee_id for redirect
    assignment = db.get(SalaryStructureAssignment, a_id)
    if not assignment or assignment.organization_id != org_id:
        return RedirectResponse(url="/people/payroll/assignments", status_code=303)

    employee_id = assignment.employee_id

    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()

    structure_id = (form.get("structure_id") or "").strip()
    from_date_str = (form.get("from_date") or "").strip()
    to_date_str = (form.get("to_date") or "").strip()
    base_amount = (form.get("base") or "0").strip()
    variable_amount = (form.get("variable") or "0").strip()
    income_tax_slab = (form.get("income_tax_slab") or "").strip()

    # Parse dates
    from_date = datetime.strptime(from_date_str, "%Y-%m-%d").date() if from_date_str else assignment.from_date
    to_date = datetime.strptime(to_date_str, "%Y-%m-%d").date() if to_date_str else None

    data = AssignmentUpdateData(
        structure_id=coerce_uuid(structure_id),
        from_date=from_date,
        to_date=to_date,
        base=_parse_decimal(base_amount) or Decimal("0"),
        variable=_parse_decimal(variable_amount) or Decimal("0"),
        income_tax_slab=income_tax_slab or None,
    )

    payroll_web_service.update_assignment(db, org_id, a_id, data)
    db.commit()

    return RedirectResponse(url=f"/people/hr/employees/{employee_id}", status_code=303)


@router.post("/assignments/{assignment_id}/end")
async def end_assignment(
    request: Request,
    assignment_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """End a salary structure assignment (set to_date to today)."""
    org_id = coerce_uuid(auth.organization_id)
    a_id = coerce_uuid(assignment_id)

    # Get employee_id first for redirect
    assignment = db.get(SalaryStructureAssignment, a_id)
    if not assignment or assignment.organization_id != org_id:
        return RedirectResponse(url="/people/payroll/assignments", status_code=303)

    employee_id = assignment.employee_id

    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()

    end_date_str = (form.get("end_date") or "").strip()
    end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date() if end_date_str else date.today()

    payroll_web_service.end_assignment(db, org_id, a_id, end_date)
    db.commit()

    return RedirectResponse(url=f"/people/hr/employees/{employee_id}", status_code=303)


# =============================================================================
# Payroll Runs (Entries)
# =============================================================================


@router.get("/runs", response_class=HTMLResponse)
def list_payroll_runs(
    request: Request,
    status: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Payroll run list page."""
    org_id = coerce_uuid(auth.organization_id)
    per_page = 20
    offset = (page - 1) * per_page

    query = db.query(PayrollEntry).filter(PayrollEntry.organization_id == org_id)

    if status:
        try:
            status_enum = PayrollEntryStatus(status.upper())
            query = query.filter(PayrollEntry.status == status_enum)
        except ValueError:
            pass

    if from_date:
        query = query.filter(
            PayrollEntry.start_date >= datetime.strptime(from_date, "%Y-%m-%d").date()
        )

    if to_date:
        query = query.filter(
            PayrollEntry.end_date <= datetime.strptime(to_date, "%Y-%m-%d").date()
        )

    total = query.count()
    runs = query.order_by(PayrollEntry.start_date.desc()).offset(offset).limit(per_page).all()

    total_pages = (total + per_page - 1) // per_page

    # Get counts by status
    status_counts = {}
    for s in PayrollEntryStatus:
        count = (
            db.query(PayrollEntry)
            .filter(PayrollEntry.organization_id == org_id, PayrollEntry.status == s)
            .count()
        )
        status_counts[s.value] = count

    context = base_context(request, auth, "Payroll Runs", "payroll", db=db)
    context["request"] = request
    context.update(
        {
            "runs": runs,
            "status": status,
            "from_date": from_date,
            "to_date": to_date,
            "page": page,
            "total_pages": total_pages,
            "total": total,
            "has_prev": page > 1,
            "has_next": page < total_pages,
            "status_counts": status_counts,
            "statuses": [s.value for s in PayrollEntryStatus],
        }
    )
    return templates.TemplateResponse(request, "people/payroll/runs.html", context)


@router.get("/runs/new", response_class=HTMLResponse)
def new_run_form(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """New payroll run form."""
    org_id = coerce_uuid(auth.organization_id)

    # Get departments and designations for filters
    departments = (
        db.query(Department)
        .filter(Department.organization_id == org_id, Department.is_active == True)
        .order_by(Department.department_name)
        .all()
    )

    designations = (
        db.query(Designation)
        .filter(Designation.organization_id == org_id, Designation.is_active == True)
        .order_by(Designation.designation_name)
        .all()
    )

    # Get count of employees with salary assignments
    assigned_count = (
        db.query(SalaryStructureAssignment)
        .filter(SalaryStructureAssignment.organization_id == org_id)
        .join(Employee, SalaryStructureAssignment.employee_id == Employee.employee_id)
        .filter(Employee.status.in_([EmployeeStatus.ACTIVE, EmployeeStatus.ON_LEAVE]))
        .count()
    )

    # Default to current month
    today = date.today()
    default_start = today.replace(day=1)
    if today.month == 12:
        default_end = today.replace(year=today.year + 1, month=1, day=1)
    else:
        default_end = today.replace(month=today.month + 1, day=1)
    default_end = default_end.replace(day=1) - timedelta(days=1)

    context = base_context(request, auth, "New Payroll Run", "payroll", db=db)
    context["request"] = request
    context.update(
        {
            "run": None,
            "departments": departments,
            "designations": designations,
            "frequencies": [f.value for f in PayrollFrequency],
            "default_start": default_start.isoformat(),
            "default_end": default_end.isoformat(),
            "default_posting": today.isoformat(),
            "assigned_count": assigned_count,
        }
    )
    return templates.TemplateResponse(request, "people/payroll/run_form.html", context)


@router.post("/runs/new")
def create_run(
    request: Request,
    start_date: str = Form(...),
    end_date: str = Form(...),
    posting_date: str = Form(...),
    payroll_frequency: str = Form("MONTHLY"),
    currency_code: str = Form("NGN"),
    department_id: str = Form(None),
    designation_id: str = Form(None),
    notes: str = Form(None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Create new payroll run."""
    org_id = coerce_uuid(auth.organization_id)

    try:
        start = datetime.strptime(start_date, "%Y-%m-%d").date()
        end = datetime.strptime(end_date, "%Y-%m-%d").date()
        posting = datetime.strptime(posting_date, "%Y-%m-%d").date()

        service = PayrollService(db)
        entry = service.create_payroll_entry(
            org_id,
            posting_date=posting,
            start_date=start,
            end_date=end,
            payroll_frequency=PayrollFrequency(payroll_frequency.upper()),
            currency_code=currency_code,
            department_id=coerce_uuid(department_id) if department_id else None,
            designation_id=coerce_uuid(designation_id) if designation_id else None,
            notes=notes or None,
        )
        db.commit()

        return RedirectResponse(url=f"/people/payroll/runs/{entry.entry_id}", status_code=303)
    except Exception as e:
        db.rollback()
        # Re-render form with error
        departments = (
            db.query(Department)
            .filter(Department.organization_id == org_id, Department.is_active == True)
            .order_by(Department.department_name)
            .all()
        )
        designations = (
            db.query(Designation)
            .filter(Designation.organization_id == org_id, Designation.is_active == True)
            .order_by(Designation.designation_name)
            .all()
        )

        context = base_context(request, auth, "New Payroll Run", "payroll", db=db)
        context["request"] = request
        context.update(
            {
                "run": None,
                "departments": departments,
                "designations": designations,
                "frequencies": [f.value for f in PayrollFrequency],
                "default_start": start_date,
                "default_end": end_date,
                "default_posting": posting_date,
                "error": str(e),
            }
        )
        return templates.TemplateResponse(request, "people/payroll/run_form.html", context)


@router.get("/runs/{entry_id}", response_class=HTMLResponse)
def view_run(
    request: Request,
    entry_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """View payroll run details."""
    org_id = coerce_uuid(auth.organization_id)
    e_id = coerce_uuid(entry_id)

    entry = db.get(PayrollEntry, e_id)
    if not entry or entry.organization_id != org_id:
        return RedirectResponse(url="/people/payroll/runs", status_code=303)

    # Get salary slips for this entry
    slips = (
        db.query(SalarySlip)
        .filter(SalarySlip.payroll_entry_id == e_id)
        .order_by(SalarySlip.employee_name)
        .all()
    )

    # Get slip status counts
    slip_status_counts = {}
    for s in SalarySlipStatus:
        count = len([slip for slip in slips if slip.status == s])
        if count > 0:
            slip_status_counts[s.value] = count

    context = base_context(request, auth, "Payroll Run", "payroll", db=db)
    context["request"] = request
    context.update(
        {
            "entry": entry,
            "slips": slips,
            "slip_status_counts": slip_status_counts,
        }
    )
    return templates.TemplateResponse(request, "people/payroll/run_detail.html", context)


@router.post("/runs/{entry_id}/generate")
def generate_run_slips(
    request: Request,
    entry_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Generate salary slips for a payroll run."""
    org_id = coerce_uuid(auth.organization_id)
    user_id = coerce_uuid(auth.user_id)
    e_id = coerce_uuid(entry_id)

    try:
        service = PayrollService(db)
        result = service.generate_salary_slips(org_id, e_id, created_by_id=user_id)
        db.commit()
        # Store result in session or flash message
    except PayrollServiceError as e:
        db.rollback()
        # Flash error message

    return RedirectResponse(url=f"/people/payroll/runs/{entry_id}", status_code=303)


@router.post("/runs/{entry_id}/regenerate")
def regenerate_run_slips(
    request: Request,
    entry_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Regenerate salary slips for a payroll run."""
    org_id = coerce_uuid(auth.organization_id)
    user_id = coerce_uuid(auth.user_id)
    e_id = coerce_uuid(entry_id)

    try:
        service = PayrollService(db)
        result = service.regenerate_salary_slips(org_id, e_id, created_by_id=user_id)
        db.commit()
    except PayrollServiceError as e:
        db.rollback()

    return RedirectResponse(url=f"/people/payroll/runs/{entry_id}", status_code=303)


@router.post("/runs/{entry_id}/submit")
def submit_run(
    request: Request,
    entry_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Submit all draft slips in a payroll run."""
    org_id = coerce_uuid(auth.organization_id)
    user_id = coerce_uuid(auth.user_id)
    e_id = coerce_uuid(entry_id)

    entry = db.get(PayrollEntry, e_id)
    if entry and entry.organization_id == org_id:
        # Submit all draft slips
        for slip in entry.salary_slips:
            if slip.status == SalarySlipStatus.DRAFT:
                salary_slip_service.submit_salary_slip(
                    db=db,
                    organization_id=org_id,
                    slip_id=slip.slip_id,
                    submitted_by_user_id=user_id,
                )

        entry.status = PayrollEntryStatus.SUBMITTED
        entry.salary_slips_submitted = True
        db.commit()

    return RedirectResponse(url=f"/people/payroll/runs/{entry_id}", status_code=303)


@router.post("/runs/{entry_id}/approve")
def approve_run(
    request: Request,
    entry_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Approve all submitted slips in a payroll run."""
    org_id = coerce_uuid(auth.organization_id)
    user_id = coerce_uuid(auth.user_id)
    e_id = coerce_uuid(entry_id)

    entry = db.get(PayrollEntry, e_id)
    if entry and entry.organization_id == org_id:
        # Approve all submitted slips
        for slip in entry.salary_slips:
            if slip.status == SalarySlipStatus.SUBMITTED:
                salary_slip_service.approve_salary_slip(
                    db=db,
                    organization_id=org_id,
                    slip_id=slip.slip_id,
                    approved_by_user_id=user_id,
                )

        entry.status = PayrollEntryStatus.APPROVED
        db.commit()

    return RedirectResponse(url=f"/people/payroll/runs/{entry_id}", status_code=303)


@router.post("/runs/{entry_id}/post")
def post_run(
    request: Request,
    entry_id: str,
    posting_date: str = Form(None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Post payroll run to GL."""
    org_id = coerce_uuid(auth.organization_id)
    user_id = coerce_uuid(auth.user_id)
    e_id = coerce_uuid(entry_id)

    post_date = datetime.strptime(posting_date, "%Y-%m-%d").date() if posting_date else date.today()

    try:
        service = PayrollService(db)
        result = service.handoff_payroll_to_books(
            org_id, e_id, posting_date=post_date, user_id=user_id
        )

        entry = db.get(PayrollEntry, e_id)
        if entry:
            entry.status = PayrollEntryStatus.POSTED
        db.commit()
    except Exception as e:
        db.rollback()

    return RedirectResponse(url=f"/people/payroll/runs/{entry_id}", status_code=303)


@router.post("/runs/{entry_id}/delete")
def delete_run(
    request: Request,
    entry_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Delete a payroll run (only if no slips created)."""
    org_id = coerce_uuid(auth.organization_id)
    e_id = coerce_uuid(entry_id)

    try:
        service = PayrollService(db)
        service.delete_payroll_entry(org_id, e_id)
        db.commit()
        return RedirectResponse(url="/people/payroll/runs", status_code=303)
    except PayrollServiceError:
        db.rollback()
        return RedirectResponse(url=f"/people/payroll/runs/{entry_id}", status_code=303)


# =============================================================================
# Reports
# =============================================================================


def _parse_date(date_str: Optional[str]) -> Optional[date]:
    """Parse date string to date object."""
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return None


@router.get("/reports/summary", response_class=HTMLResponse)
def payroll_summary_report(
    request: Request,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Payroll summary report page."""
    org_id = coerce_uuid(auth.organization_id)
    service = PayrollService(db)

    report = service.get_payroll_summary_report(
        org_id,
        start_date=_parse_date(start_date),
        end_date=_parse_date(end_date),
    )

    context = base_context(request, auth, "Payroll Summary Report", "payroll", db=db)
    context.update({
        "report": report,
        "start_date": start_date or report["start_date"].isoformat(),
        "end_date": end_date or report["end_date"].isoformat(),
    })
    return templates.TemplateResponse(request, "people/payroll/reports/summary.html", context)


@router.get("/reports/by-department", response_class=HTMLResponse)
def payroll_by_department_report(
    request: Request,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Payroll by department report page."""
    org_id = coerce_uuid(auth.organization_id)
    service = PayrollService(db)

    report = service.get_payroll_by_department_report(
        org_id,
        start_date=_parse_date(start_date),
        end_date=_parse_date(end_date),
    )

    context = base_context(request, auth, "Payroll by Department", "payroll", db=db)
    context.update({
        "report": report,
        "start_date": start_date or report["start_date"].isoformat(),
        "end_date": end_date or report["end_date"].isoformat(),
    })
    return templates.TemplateResponse(request, "people/payroll/reports/by_department.html", context)


@router.get("/reports/tax-summary", response_class=HTMLResponse)
def payroll_tax_summary_report(
    request: Request,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Payroll tax/deductions summary report page."""
    org_id = coerce_uuid(auth.organization_id)
    service = PayrollService(db)

    report = service.get_payroll_tax_summary_report(
        org_id,
        start_date=_parse_date(start_date),
        end_date=_parse_date(end_date),
    )

    context = base_context(request, auth, "Tax & Deductions Summary", "payroll", db=db)
    context.update({
        "report": report,
        "start_date": start_date or report["start_date"].isoformat(),
        "end_date": end_date or report["end_date"].isoformat(),
    })
    return templates.TemplateResponse(request, "people/payroll/reports/tax_summary.html", context)


@router.get("/reports/trends", response_class=HTMLResponse)
def payroll_trends_report(
    request: Request,
    months: int = Query(default=12, ge=3, le=24),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Payroll trends report page."""
    org_id = coerce_uuid(auth.organization_id)
    service = PayrollService(db)

    report = service.get_payroll_trends_report(org_id, months=months)

    context = base_context(request, auth, "Payroll Trends Report", "payroll", db=db)
    context.update({
        "report": report,
        "months": months,
    })
    return templates.TemplateResponse(request, "people/payroll/reports/trends.html", context)


# =============================================================================
# PAYE Tax (NTA 2025)
# =============================================================================


@router.get("/tax/bands", response_class=HTMLResponse)
def list_tax_bands(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Tax bands list page - NTA 2025 progressive tax bands."""
    org_id = coerce_uuid(auth.organization_id)

    bands = (
        db.query(TaxBand)
        .filter(TaxBand.organization_id == org_id)
        .order_by(TaxBand.sequence)
        .all()
    )

    context = base_context(request, auth, "Tax Bands (NTA 2025)", "payroll", db=db)
    context["request"] = request
    context.update({
        "bands": bands,
        "has_bands": len(bands) > 0,
    })
    return templates.TemplateResponse(request, "people/payroll/tax_bands.html", context)


@router.post("/tax/bands/seed")
def seed_tax_bands(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Seed default NTA 2025 tax bands."""
    org_id = coerce_uuid(auth.organization_id)
    user_id = coerce_uuid(auth.user_id)

    calculator = PAYECalculator(db)
    calculator.seed_nta_2025_bands(
        organization_id=org_id,
        created_by_id=user_id,
    )
    db.commit()

    return RedirectResponse(url="/people/payroll/tax/bands", status_code=303)


@router.get("/tax/calculator", response_class=HTMLResponse)
def tax_calculator_page(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """PAYE calculator page - interactive tax calculation tool."""
    org_id = coerce_uuid(auth.organization_id)

    # Check if tax bands exist
    bands = (
        db.query(TaxBand)
        .filter(TaxBand.organization_id == org_id, TaxBand.is_active.is_(True))
        .order_by(TaxBand.sequence)
        .all()
    )

    context = base_context(request, auth, "PAYE Calculator (NTA 2025)", "payroll", db=db)
    context["request"] = request
    context.update({
        "bands": bands,
        "has_bands": len(bands) > 0,
        "default_pension_rate": "8.0",
        "default_nhf_rate": "2.5",
        "default_nhis_rate": "0.0",
        "rent_relief_rate": "20",
        "rent_relief_max": "500000",
    })
    return templates.TemplateResponse(request, "people/payroll/tax_calculator.html", context)


@router.post("/tax/calculator", response_class=HTMLResponse)
async def calculate_paye(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Calculate PAYE tax from form submission."""
    org_id = coerce_uuid(auth.organization_id)

    form = await request.form()

    # Parse form values
    gross_monthly = _parse_decimal(form.get("gross_monthly") or "0") or Decimal("0")
    basic_monthly = _parse_decimal(form.get("basic_monthly") or "0") or Decimal("0")
    annual_rent = _parse_decimal(form.get("annual_rent") or "0") or Decimal("0")
    rent_verified = _parse_bool(form.get("rent_verified"), False)
    pension_rate = _parse_decimal(form.get("pension_rate") or "8") or Decimal("8")
    nhf_rate = _parse_decimal(form.get("nhf_rate") or "2.5") or Decimal("2.5")
    nhis_rate = _parse_decimal(form.get("nhis_rate") or "0") or Decimal("0")

    # Convert percentage rates to decimals
    pension_rate_decimal = pension_rate / 100
    nhf_rate_decimal = nhf_rate / 100
    nhis_rate_decimal = nhis_rate / 100

    calculator = PAYECalculator(db)
    breakdown = calculator.calculate(
        organization_id=org_id,
        gross_monthly=gross_monthly,
        basic_monthly=basic_monthly,
        annual_rent=annual_rent,
        rent_verified=rent_verified,
        pension_rate=pension_rate_decimal,
        nhf_rate=nhf_rate_decimal,
        nhis_rate=nhis_rate_decimal,
    )

    # Get bands for display
    bands = (
        db.query(TaxBand)
        .filter(TaxBand.organization_id == org_id, TaxBand.is_active.is_(True))
        .order_by(TaxBand.sequence)
        .all()
    )

    context = base_context(request, auth, "PAYE Calculator (NTA 2025)", "payroll", db=db)
    context["request"] = request
    context.update({
        "bands": bands,
        "has_bands": len(bands) > 0,
        "breakdown": breakdown,
        "gross_monthly": str(gross_monthly),
        "basic_monthly": str(basic_monthly),
        "annual_rent": str(annual_rent),
        "rent_verified": rent_verified,
        "default_pension_rate": str(pension_rate),
        "default_nhf_rate": str(nhf_rate),
        "default_nhis_rate": str(nhis_rate),
        "rent_relief_rate": "20",
        "rent_relief_max": "500000",
    })
    return templates.TemplateResponse(request, "people/payroll/tax_calculator.html", context)


@router.get("/tax/profiles", response_class=HTMLResponse)
def list_tax_profiles(
    request: Request,
    search: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Employee tax profiles list page."""
    org_id = coerce_uuid(auth.organization_id)
    per_page = 20
    offset = (page - 1) * per_page

    query = (
        db.query(EmployeeTaxProfile)
        .filter(EmployeeTaxProfile.organization_id == org_id)
        .join(Employee, EmployeeTaxProfile.employee_id == Employee.employee_id)
    )

    if search:
        query = query.filter(
            Employee.full_name.ilike(f"%{search}%")
            | EmployeeTaxProfile.tin.ilike(f"%{search}%")
        )

    total = query.count()
    profiles = (
        query
        .order_by(Employee.full_name)
        .offset(offset)
        .limit(per_page)
        .all()
    )

    total_pages = (total + per_page - 1) // per_page

    context = base_context(request, auth, "Employee Tax Profiles", "payroll", db=db)
    context["request"] = request
    context.update({
        "profiles": profiles,
        "search": search,
        "page": page,
        "total_pages": total_pages,
        "total": total,
        "has_prev": page > 1,
        "has_next": page < total_pages,
    })
    return templates.TemplateResponse(request, "people/payroll/tax_profiles.html", context)


@router.get("/tax/profiles/new", response_class=HTMLResponse)
def new_tax_profile_form(
    request: Request,
    employee_id: Optional[str] = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """New employee tax profile form."""
    org_id = coerce_uuid(auth.organization_id)

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
        .order_by(Employee.full_name)
        .all()
    )

    # Nigerian states for PAYE remittance
    nigeria_states = [
        "Abia", "Adamawa", "Akwa Ibom", "Anambra", "Bauchi", "Bayelsa", "Benue",
        "Borno", "Cross River", "Delta", "Ebonyi", "Edo", "Ekiti", "Enugu", "FCT",
        "Gombe", "Imo", "Jigawa", "Kaduna", "Kano", "Katsina", "Kebbi", "Kogi",
        "Kwara", "Lagos", "Nasarawa", "Niger", "Ogun", "Ondo", "Osun", "Oyo",
        "Plateau", "Rivers", "Sokoto", "Taraba", "Yobe", "Zamfara",
    ]

    context = base_context(request, auth, "New Tax Profile", "payroll", db=db)
    context["request"] = request
    context.update({
        "profile": None,
        "employees": employees,
        "selected_employee_id": employee_id,
        "nigeria_states": nigeria_states,
        "default_pension_rate": "8.0",
        "default_nhf_rate": "2.5",
        "default_nhis_rate": "0.0",
    })
    return templates.TemplateResponse(request, "people/payroll/tax_profile_form.html", context)


@router.post("/tax/profiles/new")
async def create_tax_profile(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Create new employee tax profile."""
    org_id = coerce_uuid(auth.organization_id)
    user_id = coerce_uuid(auth.user_id)

    form = await request.form()

    employee_id = coerce_uuid(form.get("employee_id"))
    tin = (form.get("tin") or "").strip() or None
    tax_state = (form.get("tax_state") or "").strip() or None
    annual_rent = _parse_decimal(form.get("annual_rent") or "0") or Decimal("0")
    rent_verified = _parse_bool(form.get("rent_receipt_verified"), False)
    pension_rate = (_parse_decimal(form.get("pension_rate") or "8") or Decimal("8")) / 100
    nhf_rate = (_parse_decimal(form.get("nhf_rate") or "2.5") or Decimal("2.5")) / 100
    nhis_rate = (_parse_decimal(form.get("nhis_rate") or "0") or Decimal("0")) / 100
    is_exempt = _parse_bool(form.get("is_tax_exempt"), False)
    exemption_reason = (form.get("exemption_reason") or "").strip() or None
    effective_from_str = form.get("effective_from")
    effective_from = (
        datetime.strptime(effective_from_str, "%Y-%m-%d").date()
        if effective_from_str
        else date.today()
    )

    profile = EmployeeTaxProfile(
        organization_id=org_id,
        employee_id=employee_id,
        tin=tin,
        tax_state=tax_state,
        annual_rent=annual_rent,
        rent_receipt_verified=rent_verified,
        pension_rate=pension_rate,
        nhf_rate=nhf_rate,
        nhis_rate=nhis_rate,
        is_tax_exempt=is_exempt,
        exemption_reason=exemption_reason if is_exempt else None,
        effective_from=effective_from,
        created_by_id=user_id,
    )
    profile.update_rent_relief()

    db.add(profile)
    db.commit()

    return RedirectResponse(url="/people/payroll/tax/profiles", status_code=303)


@router.get("/tax/profiles/{employee_id}", response_class=HTMLResponse)
def view_tax_profile(
    request: Request,
    employee_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """View employee tax profile."""
    org_id = coerce_uuid(auth.organization_id)
    emp_id = coerce_uuid(employee_id)

    profile = (
        db.query(EmployeeTaxProfile)
        .filter(
            EmployeeTaxProfile.organization_id == org_id,
            EmployeeTaxProfile.employee_id == emp_id,
            EmployeeTaxProfile.effective_to.is_(None),
        )
        .first()
    )

    if not profile:
        return RedirectResponse(url="/people/payroll/tax/profiles", status_code=303)

    employee = db.get(Employee, emp_id)

    context = base_context(request, auth, "Tax Profile", "payroll", db=db)
    context["request"] = request
    context.update({
        "profile": profile,
        "employee": employee,
    })
    return templates.TemplateResponse(request, "people/payroll/tax_profile_detail.html", context)


@router.get("/tax/profiles/{employee_id}/edit", response_class=HTMLResponse)
def edit_tax_profile_form(
    request: Request,
    employee_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Edit employee tax profile form."""
    org_id = coerce_uuid(auth.organization_id)
    emp_id = coerce_uuid(employee_id)

    profile = (
        db.query(EmployeeTaxProfile)
        .filter(
            EmployeeTaxProfile.organization_id == org_id,
            EmployeeTaxProfile.employee_id == emp_id,
            EmployeeTaxProfile.effective_to.is_(None),
        )
        .first()
    )

    if not profile:
        return RedirectResponse(url="/people/payroll/tax/profiles", status_code=303)

    employee = db.get(Employee, emp_id)

    # Nigerian states
    nigeria_states = [
        "Abia", "Adamawa", "Akwa Ibom", "Anambra", "Bauchi", "Bayelsa", "Benue",
        "Borno", "Cross River", "Delta", "Ebonyi", "Edo", "Ekiti", "Enugu", "FCT",
        "Gombe", "Imo", "Jigawa", "Kaduna", "Kano", "Katsina", "Kebbi", "Kogi",
        "Kwara", "Lagos", "Nasarawa", "Niger", "Ogun", "Ondo", "Osun", "Oyo",
        "Plateau", "Rivers", "Sokoto", "Taraba", "Yobe", "Zamfara",
    ]

    context = base_context(request, auth, "Edit Tax Profile", "payroll", db=db)
    context["request"] = request
    context.update({
        "profile": profile,
        "employee": employee,
        "nigeria_states": nigeria_states,
    })
    return templates.TemplateResponse(request, "people/payroll/tax_profile_form.html", context)


@router.post("/tax/profiles/{employee_id}/edit")
async def update_tax_profile(
    request: Request,
    employee_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Update employee tax profile."""
    org_id = coerce_uuid(auth.organization_id)
    emp_id = coerce_uuid(employee_id)
    user_id = coerce_uuid(auth.user_id)

    profile = (
        db.query(EmployeeTaxProfile)
        .filter(
            EmployeeTaxProfile.organization_id == org_id,
            EmployeeTaxProfile.employee_id == emp_id,
            EmployeeTaxProfile.effective_to.is_(None),
        )
        .first()
    )

    if not profile:
        return RedirectResponse(url="/people/payroll/tax/profiles", status_code=303)

    form = await request.form()

    profile.tin = (form.get("tin") or "").strip() or None
    profile.tax_state = (form.get("tax_state") or "").strip() or None
    profile.annual_rent = _parse_decimal(form.get("annual_rent") or "0") or Decimal("0")
    profile.rent_receipt_verified = _parse_bool(form.get("rent_receipt_verified"), False)
    profile.pension_rate = (_parse_decimal(form.get("pension_rate") or "8") or Decimal("8")) / 100
    profile.nhf_rate = (_parse_decimal(form.get("nhf_rate") or "2.5") or Decimal("2.5")) / 100
    profile.nhis_rate = (_parse_decimal(form.get("nhis_rate") or "0") or Decimal("0")) / 100
    profile.is_tax_exempt = _parse_bool(form.get("is_tax_exempt"), False)
    profile.exemption_reason = (
        (form.get("exemption_reason") or "").strip()
        if profile.is_tax_exempt
        else None
    )
    profile.updated_by_id = user_id

    profile.update_rent_relief()

    db.commit()

    return RedirectResponse(url=f"/people/payroll/tax/profiles/{employee_id}", status_code=303)
