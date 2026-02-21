"""
Reports Web Routes.

HTML template routes for financial reports and analytics.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.services.finance.rpt.web import reports_web_service
from app.web.deps import WebAuthContext, get_db, require_finance_access

router = APIRouter(prefix="/reports", tags=["reports-web"])


def _csv_response(content: str, filename: str) -> StreamingResponse:
    """Build a StreamingResponse for CSV download."""
    import io

    return StreamingResponse(
        io.BytesIO(content.encode("utf-8")),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def reports_dashboard(
    request: Request,
    start_date: str | None = None,
    end_date: str | None = None,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Reports dashboard/hub page."""
    return reports_web_service.dashboard_response(
        request, auth, start_date, end_date, db
    )


@router.get("/trial-balance", response_class=HTMLResponse)
def trial_balance_report(
    request: Request,
    as_of_date: str | None = None,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Trial balance report page."""
    return reports_web_service.trial_balance_response(request, auth, as_of_date, db)


@router.get("/income-statement", response_class=HTMLResponse)
def income_statement_report(
    request: Request,
    start_date: str | None = None,
    end_date: str | None = None,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Income statement report page."""
    return reports_web_service.income_statement_response(
        request, auth, start_date, end_date, db
    )


@router.get("/balance-sheet", response_class=HTMLResponse)
def balance_sheet_report(
    request: Request,
    as_of_date: str | None = None,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Balance sheet report page."""
    return reports_web_service.balance_sheet_response(request, auth, as_of_date, db)


@router.get("/ap-aging", response_class=HTMLResponse)
def ap_aging_report(
    request: Request,
    as_of_date: str | None = None,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """AP aging report page."""
    return reports_web_service.ap_aging_response(request, auth, as_of_date, db)


@router.get("/ar-aging", response_class=HTMLResponse)
def ar_aging_report(
    request: Request,
    as_of_date: str | None = None,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """AR aging report page."""
    return reports_web_service.ar_aging_response(request, auth, as_of_date, db)


@router.get("/general-ledger", response_class=HTMLResponse)
def general_ledger_report(
    request: Request,
    account_id: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """General ledger detail report page."""
    return reports_web_service.general_ledger_response(
        request,
        auth,
        account_id,
        start_date,
        end_date,
        db,
    )


@router.get("/tax-summary", response_class=HTMLResponse)
def tax_summary_report(
    request: Request,
    start_date: str | None = None,
    end_date: str | None = None,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Tax summary report page."""
    return reports_web_service.tax_summary_response(
        request, auth, start_date, end_date, db
    )


@router.get("/expense-summary", response_class=HTMLResponse)
def expense_summary_report(
    request: Request,
    start_date: str | None = None,
    end_date: str | None = None,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Expense summary report page."""
    return reports_web_service.expense_summary_response(
        request, auth, start_date, end_date, db
    )


@router.get("/cash-flow", response_class=HTMLResponse)
def cash_flow_report(
    request: Request,
    start_date: str | None = None,
    end_date: str | None = None,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Cash flow statement report page."""
    return reports_web_service.cash_flow_response(
        request, auth, start_date, end_date, db
    )


@router.get("/changes-in-equity", response_class=HTMLResponse)
def changes_in_equity_report(
    request: Request,
    start_date: str | None = None,
    end_date: str | None = None,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Changes in equity report page."""
    return reports_web_service.changes_in_equity_response(
        request, auth, start_date, end_date, db
    )


# ─────────────────── CSV Export endpoints ───────────────────


@router.get("/trial-balance/export")
def export_trial_balance(
    as_of_date: str | None = None,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Export trial balance as CSV."""
    csv = reports_web_service.export_trial_balance_csv(
        str(auth.organization_id), db, as_of_date
    )
    return _csv_response(csv, "trial_balance.csv")


@router.get("/income-statement/export")
def export_income_statement(
    start_date: str | None = None,
    end_date: str | None = None,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Export income statement as CSV."""
    csv = reports_web_service.export_income_statement_csv(
        str(auth.organization_id), db, start_date, end_date
    )
    return _csv_response(csv, "income_statement.csv")


@router.get("/balance-sheet/export")
def export_balance_sheet(
    as_of_date: str | None = None,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Export balance sheet as CSV."""
    csv = reports_web_service.export_balance_sheet_csv(
        str(auth.organization_id), db, as_of_date
    )
    return _csv_response(csv, "balance_sheet.csv")


@router.get("/general-ledger/export")
def export_general_ledger(
    account_id: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Export general ledger as CSV."""
    csv = reports_web_service.export_general_ledger_csv(
        str(auth.organization_id), db, account_id, start_date, end_date
    )
    return _csv_response(csv, "general_ledger.csv")


@router.get("/budget-vs-actual", response_class=HTMLResponse)
def budget_vs_actual_report(
    request: Request,
    start_date: str | None = None,
    end_date: str | None = None,
    budget_id: str | None = None,
    budget_code: str | None = None,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Budget vs actual report page."""
    return reports_web_service.budget_vs_actual_response(
        request, auth, start_date, end_date, budget_id, budget_code, db
    )
