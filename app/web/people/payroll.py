"""
Payroll Web Routes.

HTML template routes for Salary Components, Structures, and Slips.
All business logic is delegated to the payroll_web_service.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.services.people.payroll.web import payroll_web_service
from app.services.people.payroll.web.loan_web import loan_web_service
from app.web.deps import WebAuthContext, get_db, require_hr_access

router = APIRouter(prefix="/payroll", tags=["payroll-web"])


# ─────────────────────────────────────────────────────────────────────────────
# Salary Components
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/components", response_class=HTMLResponse)
def list_salary_components(
    request: Request,
    search: str | None = None,
    component_type: str | None = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Salary component list page."""
    return payroll_web_service.list_components_response(
        request, auth, db, search, component_type, page
    )


@router.get("/components/new", response_class=HTMLResponse)
def new_component_form(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """New salary component form."""
    return payroll_web_service.component_new_form_response(request, auth, db)


@router.get("/components/{component_id}/edit", response_class=HTMLResponse)
def edit_component_form(
    request: Request,
    component_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Edit salary component form."""
    return payroll_web_service.component_edit_form_response(
        request, auth, db, component_id
    )


@router.post("/components/new")
async def create_component(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Create new salary component."""
    return await payroll_web_service.create_component_response(request, auth, db)


@router.post("/components/{component_id}/edit")
async def update_component(
    request: Request,
    component_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Update salary component."""
    return await payroll_web_service.update_component_response(
        request, auth, db, component_id
    )


@router.post("/components/{component_id}/delete")
def delete_component(
    component_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Delete salary component."""
    return payroll_web_service.delete_component_response(auth, db, component_id)


# ─────────────────────────────────────────────────────────────────────────────
# Salary Slips
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/slips", response_class=HTMLResponse)
def list_salary_slips(
    request: Request,
    search: str | None = None,
    status: str | None = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Salary slip list page."""
    return payroll_web_service.list_slips_response(
        request, auth, db, search, status, page
    )


@router.get("/slips/export")
def export_salary_slips(
    request: Request,
    search: str | None = None,
    status: str | None = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Export salary slips to CSV."""
    return payroll_web_service.export_slips_response(request, auth, db, search, status)


@router.get("/slips/new", response_class=HTMLResponse)
def new_slip_form(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """New salary slip form."""
    return payroll_web_service.slip_new_form_response(request, auth, db)


@router.post("/slips/new")
async def create_slip(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Create new salary slip."""
    return await payroll_web_service.create_slip_response(request, auth, db)


@router.get("/slips/{slip_id}", response_class=HTMLResponse)
def view_slip(
    request: Request,
    slip_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """View salary slip details."""
    return payroll_web_service.slip_detail_response(request, auth, db, slip_id)


@router.get("/slips/{slip_id}/edit", response_class=HTMLResponse)
def edit_slip_form(
    request: Request,
    slip_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Edit salary slip form."""
    return payroll_web_service.slip_edit_form_response(request, auth, db, slip_id)


@router.post("/slips/{slip_id}/submit")
def submit_slip(
    slip_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Submit salary slip for approval."""
    return payroll_web_service.submit_slip_response(auth, db, slip_id)


@router.post("/slips/{slip_id}/approve")
def approve_slip(
    slip_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Approve salary slip."""
    return payroll_web_service.approve_slip_response(auth, db, slip_id)


@router.post("/slips/{slip_id}/post")
def post_slip(
    slip_id: str,
    posting_date: str | None = Form(None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Post salary slip to GL."""
    return payroll_web_service.post_slip_response(auth, db, slip_id, posting_date)


@router.post("/slips/{slip_id}/edit")
async def update_slip(
    request: Request,
    slip_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Update salary slip."""
    return await payroll_web_service.update_slip_response(request, auth, db, slip_id)


@router.post("/slips/{slip_id}/delete")
def delete_slip(
    slip_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Delete salary slip."""
    return payroll_web_service.delete_slip_response(auth, db, slip_id)


# ─────────────────────────────────────────────────────────────────────────────
# Salary Structures
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/structures", response_class=HTMLResponse)
def list_salary_structures(
    request: Request,
    search: str | None = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Salary structure list page."""
    return payroll_web_service.list_structures_response(request, auth, db, search, page)


@router.get("/structures/new", response_class=HTMLResponse)
def new_structure_form(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """New salary structure form."""
    return payroll_web_service.structure_new_form_response(request, auth, db)


@router.get("/structures/{structure_id}/edit", response_class=HTMLResponse)
def edit_structure_form(
    request: Request,
    structure_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Edit salary structure form."""
    return payroll_web_service.structure_edit_form_response(
        request, auth, db, structure_id
    )


@router.post("/structures/new")
async def create_structure(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Create new salary structure."""
    return await payroll_web_service.create_structure_response(request, auth, db)


@router.post("/structures/{structure_id}/edit")
async def update_structure(
    request: Request,
    structure_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Update salary structure."""
    return await payroll_web_service.update_structure_response(
        request, auth, db, structure_id
    )


@router.post("/structures/{structure_id}/delete")
def delete_structure(
    structure_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Delete salary structure."""
    return payroll_web_service.delete_structure_response(auth, db, structure_id)


@router.get("/structures/{structure_id}", response_class=HTMLResponse)
def view_structure(
    request: Request,
    structure_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """View salary structure details."""
    return payroll_web_service.structure_detail_response(
        request, auth, db, structure_id
    )


# ─────────────────────────────────────────────────────────────────────────────
# Salary Structure Assignments
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/assignments", response_class=HTMLResponse)
def list_assignments(
    request: Request,
    search: str | None = None,
    bulk_created: int | None = None,
    bulk_skipped: int | None = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Salary assignments list page."""
    return payroll_web_service.list_assignments_response(
        request, auth, db, search, page, bulk_created, bulk_skipped
    )


@router.get("/assignments/new", response_class=HTMLResponse)
def new_assignment_form(
    request: Request,
    employee_id: str | None = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """New salary assignment form."""
    return payroll_web_service.assignment_new_form_response(
        request, auth, db, employee_id
    )


@router.post("/assignments/new")
async def create_assignment(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Create salary structure assignment."""
    return await payroll_web_service.create_assignment_response(request, auth, db)


@router.get("/assignments/bulk", response_class=HTMLResponse)
def bulk_assignment_form(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Bulk salary assignment form."""
    return payroll_web_service.assignment_bulk_form_response(request, auth, db)


@router.post("/assignments/bulk")
async def create_bulk_assignment(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Create bulk salary structure assignments."""
    return await payroll_web_service.create_assignment_bulk_response(request, auth, db)


@router.get("/assignments/{assignment_id}/edit", response_class=HTMLResponse)
def edit_assignment_form(
    request: Request,
    assignment_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Edit salary assignment form."""
    return payroll_web_service.assignment_edit_form_response(
        request, auth, db, assignment_id
    )


@router.post("/assignments/{assignment_id}/edit")
async def update_assignment(
    request: Request,
    assignment_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Update salary structure assignment."""
    return await payroll_web_service.update_assignment_response(
        request, auth, db, assignment_id
    )


@router.post("/assignments/{assignment_id}/end")
def end_assignment(
    assignment_id: str,
    end_date: str | None = Form(None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """End salary structure assignment."""
    return payroll_web_service.end_assignment_response(
        auth, db, assignment_id, end_date
    )


@router.post("/assignments/{assignment_id}/delete")
def delete_assignment(
    assignment_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Delete salary structure assignment."""
    return payroll_web_service.delete_assignment_response(auth, db, assignment_id)


# ─────────────────────────────────────────────────────────────────────────────
# Loans / Salary Advances
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/loans", response_class=HTMLResponse)
def list_loans(
    request: Request,
    search: str | None = None,
    status: str | None = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Employee loans list page."""
    return loan_web_service.list_loans_response(request, auth, db, search, status, page)


@router.get("/loan")
def loan_singular_redirect() -> RedirectResponse:
    """Redirect singular loan path to canonical loans listing."""
    return RedirectResponse(url="/people/payroll/loans", status_code=302)


@router.get("/loans/")
def loans_trailing_slash_redirect() -> RedirectResponse:
    """Redirect trailing slash loans path to canonical loans listing."""
    return RedirectResponse(url="/people/payroll/loans", status_code=302)


@router.get("/loans/new", response_class=HTMLResponse)
def new_loan_form(
    request: Request,
    employee_id: str | None = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """New loan application form."""
    return loan_web_service.loan_form_response(request, auth, db, employee_id)


@router.post("/loans/new")
async def create_loan(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Create a new employee loan application."""
    return await loan_web_service.create_loan_response(request, auth, db)


@router.get("/loans/{loan_id}", response_class=HTMLResponse)
def view_loan(
    request: Request,
    loan_id: UUID,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Loan details page."""
    return loan_web_service.loan_detail_response(request, auth, db, str(loan_id))


@router.post("/loans/{loan_id}/approve")
def approve_loan(
    loan_id: UUID,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Approve a pending loan."""
    return loan_web_service.approve_loan_response(auth, db, str(loan_id))


@router.post("/loans/{loan_id}/reject")
async def reject_loan(
    request: Request,
    loan_id: UUID,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Reject a pending loan."""
    return await loan_web_service.reject_loan_response(request, auth, db, str(loan_id))


@router.post("/loans/{loan_id}/disburse")
async def disburse_loan(
    request: Request,
    loan_id: UUID,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Mark an approved loan as disbursed."""
    return await loan_web_service.disburse_loan_response(
        request, auth, db, str(loan_id)
    )


@router.get("/loans/types", response_class=HTMLResponse)
def list_loan_types(
    request: Request,
    search: str | None = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Loan types list page."""
    return loan_web_service.list_loan_types_response(request, auth, db, search)


@router.get("/loans/types/")
def loan_types_trailing_slash_redirect() -> RedirectResponse:
    """Redirect trailing slash loan types path to canonical loan types listing."""
    return RedirectResponse(url="/people/payroll/loans/types", status_code=302)


@router.get("/loans/types/new", response_class=HTMLResponse)
def new_loan_type_form(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """New loan type form."""
    return loan_web_service.loan_type_form_response(request, auth, db)


@router.get("/loans/types/{loan_type_id}/edit", response_class=HTMLResponse)
def edit_loan_type_form(
    request: Request,
    loan_type_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Edit loan type form."""
    return loan_web_service.loan_type_form_response(request, auth, db, loan_type_id)


@router.post("/loans/types/new")
async def create_loan_type(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Create a new loan type."""
    return await loan_web_service.save_loan_type_response(request, auth, db)


@router.post("/loans/types/{loan_type_id}")
async def update_loan_type(
    request: Request,
    loan_type_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Update an existing loan type."""
    return await loan_web_service.save_loan_type_response(
        request, auth, db, loan_type_id
    )


# ─────────────────────────────────────────────────────────────────────────────
# Payroll Dashboard
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/dashboard", response_class=HTMLResponse)
def payroll_dashboard(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Payroll module dashboard."""
    return payroll_web_service.dashboard_response(request, auth, db)


# ─────────────────────────────────────────────────────────────────────────────
# Payroll Runs
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/runs", response_class=HTMLResponse)
def list_payroll_runs(
    request: Request,
    status: str | None = None,
    year: int | None = None,
    month: int | None = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Payroll runs list page."""
    return payroll_web_service.list_runs_response(
        request, auth, db, status, year, month, page
    )


@router.get("/runs/new", response_class=HTMLResponse)
def new_run_form(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """New payroll run form."""
    return payroll_web_service.run_new_form_response(request, auth, db)


@router.post("/runs/new")
async def create_run(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Create new payroll run."""
    return await payroll_web_service.create_run_response(request, auth, db)


@router.get("/runs/{entry_id}/copy", response_class=HTMLResponse)
def copy_run_form(
    request: Request,
    entry_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Pre-populate new payroll run form from an existing run."""
    return payroll_web_service.copy_run_form_response(request, auth, db, entry_id)


@router.get("/runs/{entry_id}", response_class=HTMLResponse)
def view_run(
    request: Request,
    entry_id: str,
    success: str | None = None,
    error: str | None = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Payroll run detail page."""
    return payroll_web_service.run_detail_response(
        request, auth, db, entry_id, success, error
    )


@router.post("/runs/{entry_id}/generate")
def generate_run(
    entry_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Generate salary slips for payroll run."""
    return payroll_web_service.generate_run_response(auth, db, entry_id)


@router.post("/runs/{entry_id}/regenerate")
def regenerate_run(
    entry_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Regenerate salary slips for payroll run."""
    return payroll_web_service.regenerate_run_response(auth, db, entry_id)


@router.post("/runs/{entry_id}/submit")
def submit_run(
    entry_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Submit payroll run for approval."""
    return payroll_web_service.submit_run_response(auth, db, entry_id)


@router.post("/runs/{entry_id}/approve")
def approve_run(
    entry_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Approve payroll run."""
    return payroll_web_service.approve_run_response(auth, db, entry_id)


@router.post("/runs/{entry_id}/post")
def post_run(
    entry_id: str,
    posting_date: str | None = Form(None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Post payroll run to GL."""
    return payroll_web_service.post_run_response(auth, db, entry_id, posting_date)


@router.post("/runs/{entry_id}/delete")
def delete_run(
    entry_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Delete payroll run."""
    return payroll_web_service.delete_run_response(auth, db, entry_id)


@router.post("/runs/{entry_id}/send-payslips")
def send_payslips(
    entry_id: str,
    force: bool = Query(default=False),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Queue payslip emails for all posted slips in a payroll run."""
    return payroll_web_service.send_payslips_response(auth, db, entry_id, force)


@router.get("/runs/{entry_id}/email-status")
def email_status(
    entry_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Get email sending progress for a payroll run."""
    return payroll_web_service.email_status_response(auth, db, entry_id)


@router.get("/runs/{entry_id}/export/paye")
def export_paye(
    entry_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Export PAYE (income tax) data for a payroll run."""
    return payroll_web_service.export_paye_response(auth, db, entry_id)


@router.get("/runs/{entry_id}/export/pension")
def export_pension(
    entry_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Export pension contribution data for a payroll run."""
    return payroll_web_service.export_pension_response(auth, db, entry_id)


@router.get("/runs/{entry_id}/export/nhf")
def export_nhf(
    entry_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Export NHF contribution data for a payroll run."""
    return payroll_web_service.export_nhf_response(auth, db, entry_id)


@router.get("/runs/{entry_id}/bank-upload")
def bank_upload(
    entry_id: str,
    source_account: str | None = Query(default=None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Download bank upload file for payroll run (Zenith format)."""
    return payroll_web_service.bank_upload_response(auth, db, entry_id, source_account)


# ─────────────────────────────────────────────────────────────────────────────
# Reports
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/reports/summary", response_class=HTMLResponse)
def report_summary(
    request: Request,
    year: int | None = None,
    month: int | None = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Payroll summary report."""
    return payroll_web_service.summary_report_response(request, auth, db, year, month)


@router.get("/reports/by-department", response_class=HTMLResponse)
def report_by_department(
    request: Request,
    year: int | None = None,
    month: int | None = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Payroll by department report."""
    return payroll_web_service.by_department_report_response(
        request, auth, db, year, month
    )


@router.get("/reports/tax-summary", response_class=HTMLResponse)
def report_tax_summary(
    request: Request,
    year: int | None = None,
    month: int | None = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Tax summary report."""
    return payroll_web_service.tax_summary_report_response(
        request, auth, db, year, month
    )


@router.get("/reports/trends", response_class=HTMLResponse)
def report_trends(
    request: Request,
    year: int | None = None,
    months: int | None = 12,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Payroll trends report."""
    return payroll_web_service.trends_report_response(request, auth, db, year, months)


# ─────────────────────────────────────────────────────────────────────────────
# Tax Bands
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/tax/bands", response_class=HTMLResponse)
def list_tax_bands(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Tax bands list page."""
    return payroll_web_service.list_tax_bands_response(request, auth, db)


@router.post("/tax/bands/seed")
def seed_tax_bands(
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Seed default tax bands."""
    return payroll_web_service.seed_tax_bands_response(auth, db)


# ─────────────────────────────────────────────────────────────────────────────
# Tax Calculator
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/tax/calculator", response_class=HTMLResponse)
def tax_calculator_form(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """PAYE tax calculator form."""
    return payroll_web_service.tax_calculator_form_response(request, auth, db)


@router.post("/tax/calculator", response_class=HTMLResponse)
async def calculate_tax(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Calculate PAYE tax."""
    return await payroll_web_service.calculate_tax_response(request, auth, db)


# ─────────────────────────────────────────────────────────────────────────────
# Tax Profiles
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/tax/profiles", response_class=HTMLResponse)
def list_tax_profiles(
    request: Request,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Tax profiles list page."""
    return payroll_web_service.list_tax_profiles_response(request, auth, db, page)


@router.get("/tax/profiles/new", response_class=HTMLResponse)
def new_tax_profile_form(
    request: Request,
    employee_id: str | None = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """New tax profile form."""
    return payroll_web_service.tax_profile_new_form_response(
        request, auth, db, employee_id
    )


@router.post("/tax/profiles/new")
async def create_tax_profile(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Create new tax profile."""
    return await payroll_web_service.create_tax_profile_response(request, auth, db)


@router.get("/tax/profiles/{employee_id}", response_class=HTMLResponse)
def view_tax_profile(
    request: Request,
    employee_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Tax profile detail page."""
    return payroll_web_service.tax_profile_detail_response(
        request, auth, db, employee_id
    )


@router.get("/tax/profiles/{employee_id}/edit", response_class=HTMLResponse)
def edit_tax_profile_form(
    request: Request,
    employee_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Edit tax profile form."""
    return payroll_web_service.tax_profile_edit_form_response(
        request, auth, db, employee_id
    )


@router.post("/tax/profiles/{employee_id}/edit")
async def update_tax_profile(
    request: Request,
    employee_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Update tax profile."""
    return await payroll_web_service.update_tax_profile_response(
        request, auth, db, employee_id
    )
