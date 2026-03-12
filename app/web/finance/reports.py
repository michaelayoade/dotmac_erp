"""
Reports Web Routes.

HTML template routes for financial reports and analytics.
"""

from __future__ import annotations

import io

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.services.finance.rpt.web import reports_web_service
from app.web.deps import WebAuthContext, get_db, require_finance_access

router = APIRouter(prefix="/reports", tags=["reports-web"])


def _csv_response(content: str, filename: str) -> StreamingResponse:
    """Build a StreamingResponse for CSV download."""
    return StreamingResponse(
        io.BytesIO(content.encode("utf-8")),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _pdf_response(content: bytes, filename: str) -> StreamingResponse:
    """Build a StreamingResponse for PDF download."""
    return StreamingResponse(
        io.BytesIO(content),
        media_type="application/pdf",
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


@router.get("/trial-balance/export")
def export_trial_balance(
    as_of_date: str | None = None,
    fmt: str = Query("csv", alias="format"),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Export trial balance as CSV or PDF."""
    org_id = str(auth.organization_id)
    if fmt == "pdf":
        pdf = reports_web_service.export_trial_balance_pdf(org_id, db, as_of_date)
        return _pdf_response(pdf, "trial_balance.pdf")
    csv = reports_web_service.export_trial_balance_csv(org_id, db, as_of_date)
    return _csv_response(csv, "trial_balance.csv")


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


@router.get("/income-statement/export")
def export_income_statement(
    start_date: str | None = None,
    end_date: str | None = None,
    fmt: str = Query("csv", alias="format"),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Export income statement as CSV or PDF."""
    org_id = str(auth.organization_id)
    if fmt == "pdf":
        pdf = reports_web_service.export_income_statement_pdf(
            org_id, db, start_date, end_date
        )
        return _pdf_response(pdf, "income_statement.pdf")
    csv = reports_web_service.export_income_statement_csv(
        org_id, db, start_date, end_date
    )
    return _csv_response(csv, "income_statement.csv")


@router.get("/balance-sheet", response_class=HTMLResponse)
def balance_sheet_report(
    request: Request,
    as_of_date: str | None = None,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Balance sheet report page."""
    return reports_web_service.balance_sheet_response(request, auth, as_of_date, db)


@router.get("/balance-sheet/export")
def export_balance_sheet(
    as_of_date: str | None = None,
    fmt: str = Query("csv", alias="format"),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Export balance sheet as CSV or PDF."""
    org_id = str(auth.organization_id)
    if fmt == "pdf":
        pdf = reports_web_service.export_balance_sheet_pdf(org_id, db, as_of_date)
        return _pdf_response(pdf, "balance_sheet.pdf")
    csv = reports_web_service.export_balance_sheet_csv(org_id, db, as_of_date)
    return _csv_response(csv, "balance_sheet.csv")


@router.get("/ap-aging", response_class=HTMLResponse)
def ap_aging_report(
    request: Request,
    as_of_date: str | None = None,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """AP aging report page."""
    return reports_web_service.ap_aging_response(request, auth, as_of_date, db)


@router.get("/ap-aging/export")
def export_ap_aging(
    as_of_date: str | None = None,
    fmt: str = Query("pdf", alias="format"),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Export AP aging as PDF."""
    org_id = str(auth.organization_id)
    if fmt == "pdf":
        pdf = reports_web_service.export_ap_aging_pdf(org_id, db, as_of_date)
        return _pdf_response(pdf, "ap_aging.pdf")
    # No CSV export for aging — PDF only
    pdf = reports_web_service.export_ap_aging_pdf(org_id, db, as_of_date)
    return _pdf_response(pdf, "ap_aging.pdf")


@router.get("/ar-aging", response_class=HTMLResponse)
def ar_aging_report(
    request: Request,
    as_of_date: str | None = None,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """AR aging report page."""
    return reports_web_service.ar_aging_response(request, auth, as_of_date, db)


@router.get("/ar-aging/export")
def export_ar_aging(
    as_of_date: str | None = None,
    fmt: str = Query("pdf", alias="format"),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Export AR aging as PDF."""
    org_id = str(auth.organization_id)
    if fmt == "pdf":
        pdf = reports_web_service.export_ar_aging_pdf(org_id, db, as_of_date)
        return _pdf_response(pdf, "ar_aging.pdf")
    pdf = reports_web_service.export_ar_aging_pdf(org_id, db, as_of_date)
    return _pdf_response(pdf, "ar_aging.pdf")


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


@router.get("/general-ledger/export")
def export_general_ledger(
    account_id: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    fmt: str = Query("csv", alias="format"),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Export general ledger as CSV or PDF."""
    org_id = str(auth.organization_id)
    if fmt == "pdf":
        pdf = reports_web_service.export_general_ledger_pdf(
            org_id, db, account_id, start_date, end_date
        )
        return _pdf_response(pdf, "general_ledger.pdf")
    csv = reports_web_service.export_general_ledger_csv(
        org_id, db, account_id, start_date, end_date
    )
    return _csv_response(csv, "general_ledger.csv")


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


@router.get("/tax-summary/export")
def export_tax_summary(
    start_date: str | None = None,
    end_date: str | None = None,
    fmt: str = Query("pdf", alias="format"),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Export tax summary as PDF."""
    org_id = str(auth.organization_id)
    pdf = reports_web_service.export_tax_summary_pdf(org_id, db, start_date, end_date)
    return _pdf_response(pdf, "tax_summary.pdf")


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


@router.get("/expense-summary/export")
def export_expense_summary(
    start_date: str | None = None,
    end_date: str | None = None,
    fmt: str = Query("pdf", alias="format"),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Export expense summary as PDF."""
    org_id = str(auth.organization_id)
    pdf = reports_web_service.export_expense_summary_pdf(
        org_id, db, start_date, end_date
    )
    return _pdf_response(pdf, "expense_summary.pdf")


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


@router.get("/cash-flow/export")
def export_cash_flow(
    start_date: str | None = None,
    end_date: str | None = None,
    fmt: str = Query("pdf", alias="format"),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Export cash flow as PDF."""
    org_id = str(auth.organization_id)
    pdf = reports_web_service.export_cash_flow_pdf(org_id, db, start_date, end_date)
    return _pdf_response(pdf, "cash_flow.pdf")


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


@router.get("/changes-in-equity/export")
def export_changes_in_equity(
    start_date: str | None = None,
    end_date: str | None = None,
    fmt: str = Query("pdf", alias="format"),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Export changes in equity as PDF."""
    org_id = str(auth.organization_id)
    pdf = reports_web_service.export_changes_in_equity_pdf(
        org_id, db, start_date, end_date
    )
    return _pdf_response(pdf, "changes_in_equity.pdf")


@router.get("/management-accounts", response_class=HTMLResponse)
def management_accounts_report(
    request: Request,
    start_date: str | None = None,
    end_date: str | None = None,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Management accounts report page."""
    return reports_web_service.management_accounts_response(
        request, auth, start_date, end_date, db
    )


@router.get("/management-accounts/export")
def export_management_accounts(
    start_date: str | None = None,
    end_date: str | None = None,
    fmt: str = Query("csv", alias="format"),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Export management accounts as CSV or PDF."""
    org_id = str(auth.organization_id)
    if fmt == "pdf":
        pdf = reports_web_service.export_management_accounts_pdf(
            org_id, db, start_date, end_date
        )
        return _pdf_response(pdf, "management_accounts.pdf")
    csv = reports_web_service.export_management_accounts_csv(
        org_id, db, start_date, end_date
    )
    return _csv_response(csv, "management_accounts.csv")


@router.get("/analysis", response_class=HTMLResponse)
def analysis_report(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Pivot-style analysis report page."""
    return reports_web_service.analysis_response(request, auth, db)


@router.get("/inventory-valuation-reconciliation", response_class=HTMLResponse)
def inventory_valuation_reconciliation_report(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Inventory valuation reconciliation report page."""
    return reports_web_service.inventory_valuation_reconciliation_response(
        request, auth, db
    )


@router.get("/inventory-valuation-reconciliation/export")
def export_inventory_valuation(
    fmt: str = Query("pdf", alias="format"),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Export inventory valuation reconciliation as PDF."""
    org_id = str(auth.organization_id)
    pdf = reports_web_service.export_inventory_valuation_pdf(org_id, db)
    return _pdf_response(pdf, "inventory_valuation_reconciliation.pdf")


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


@router.get("/budget-vs-actual/export")
def export_budget_vs_actual(
    start_date: str | None = None,
    end_date: str | None = None,
    budget_id: str | None = None,
    budget_code: str | None = None,
    fmt: str = Query("pdf", alias="format"),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Export budget vs actual as PDF."""
    org_id = str(auth.organization_id)
    pdf = reports_web_service.export_budget_vs_actual_pdf(
        org_id, db, start_date, end_date, budget_id, budget_code
    )
    return _pdf_response(pdf, "budget_vs_actual.pdf")
