"""
Reports Web Routes.

HTML template routes for financial reports and analytics.
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.services.ifrs.rpt.web import reports_web_service
from app.templates import templates
from app.web.deps import get_db, require_web_auth, WebAuthContext, base_context


router = APIRouter(prefix="/reports", tags=["reports-web"])


# =============================================================================
# Reports Dashboard
# =============================================================================

@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def reports_dashboard(
    request: Request,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Reports dashboard/hub page."""
    context = base_context(request, auth, "Reports", "reports")
    context.update(
        reports_web_service.dashboard_context(
            db,
            str(auth.organization_id),
            start_date=start_date,
            end_date=end_date,
        )
    )
    return templates.TemplateResponse(request, "ifrs/reports/dashboard.html", context)


# =============================================================================
# Trial Balance
# =============================================================================

@router.get("/trial-balance", response_class=HTMLResponse)
def trial_balance_report(
    request: Request,
    as_of_date: Optional[str] = None,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Trial balance report page."""
    context = base_context(request, auth, "Trial Balance", "reports")
    context.update(
        reports_web_service.trial_balance_context(
            db,
            str(auth.organization_id),
            as_of_date=as_of_date,
        )
    )
    return templates.TemplateResponse(request, "ifrs/reports/trial_balance.html", context)


# =============================================================================
# Income Statement
# =============================================================================

@router.get("/income-statement", response_class=HTMLResponse)
def income_statement_report(
    request: Request,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Income statement report page."""
    context = base_context(request, auth, "Income Statement", "reports")
    context.update(
        reports_web_service.income_statement_context(
            db,
            str(auth.organization_id),
            start_date=start_date,
            end_date=end_date,
        )
    )
    return templates.TemplateResponse(request, "ifrs/reports/income_statement.html", context)


# =============================================================================
# Balance Sheet
# =============================================================================

@router.get("/balance-sheet", response_class=HTMLResponse)
def balance_sheet_report(
    request: Request,
    as_of_date: Optional[str] = None,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Balance sheet report page."""
    context = base_context(request, auth, "Balance Sheet", "reports")
    context.update(
        reports_web_service.balance_sheet_context(
            db,
            str(auth.organization_id),
            as_of_date=as_of_date,
        )
    )
    return templates.TemplateResponse(request, "ifrs/reports/balance_sheet.html", context)


# =============================================================================
# AP Aging
# =============================================================================

@router.get("/ap-aging", response_class=HTMLResponse)
def ap_aging_report(
    request: Request,
    as_of_date: Optional[str] = None,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """AP aging report page."""
    context = base_context(request, auth, "AP Aging Report", "reports")
    context.update(
        reports_web_service.ap_aging_context(
            db,
            str(auth.organization_id),
            as_of_date=as_of_date,
        )
    )
    return templates.TemplateResponse(request, "ifrs/reports/ap_aging.html", context)


# =============================================================================
# AR Aging
# =============================================================================

@router.get("/ar-aging", response_class=HTMLResponse)
def ar_aging_report(
    request: Request,
    as_of_date: Optional[str] = None,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """AR aging report page."""
    context = base_context(request, auth, "AR Aging Report", "reports")
    context.update(
        reports_web_service.ar_aging_context(
            db,
            str(auth.organization_id),
            as_of_date=as_of_date,
        )
    )
    return templates.TemplateResponse(request, "ifrs/reports/ar_aging.html", context)


# =============================================================================
# General Ledger
# =============================================================================

@router.get("/general-ledger", response_class=HTMLResponse)
def general_ledger_report(
    request: Request,
    account_id: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """General ledger detail report page."""
    context = base_context(request, auth, "General Ledger", "reports")
    context.update(
        reports_web_service.general_ledger_context(
            db,
            str(auth.organization_id),
            account_id=account_id,
            start_date=start_date,
            end_date=end_date,
        )
    )
    return templates.TemplateResponse(request, "ifrs/reports/general_ledger.html", context)


# =============================================================================
# Tax Summary Report
# =============================================================================

@router.get("/tax-summary", response_class=HTMLResponse)
def tax_summary_report(
    request: Request,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Tax summary report page."""
    context = base_context(request, auth, "Tax Summary", "reports")
    context.update(
        reports_web_service.tax_summary_context(
            db,
            str(auth.organization_id),
            start_date=start_date,
            end_date=end_date,
        )
    )
    return templates.TemplateResponse(request, "ifrs/reports/tax_summary.html", context)


# =============================================================================
# Expense Summary Report
# =============================================================================

@router.get("/expense-summary", response_class=HTMLResponse)
def expense_summary_report(
    request: Request,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Expense summary report page."""
    context = base_context(request, auth, "Expense Summary", "reports")
    context.update(
        reports_web_service.expense_summary_context(
            db,
            str(auth.organization_id),
            start_date=start_date,
            end_date=end_date,
        )
    )
    return templates.TemplateResponse(request, "ifrs/reports/expense_summary.html", context)
