"""
Reports Web Routes.

HTML template routes for financial reports and analytics.
"""

from __future__ import annotations

import io

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    Response,
    StreamingResponse,
)
from sqlalchemy.orm import Session

from app.services.finance.rpt.async_exports import (
    get_completed_export_for_download,
    get_export_status,
    queue_general_ledger_export,
)
from app.services.finance.rpt.web import reports_web_service
from app.templates import templates
from app.web.deps import (
    get_db_for_org,
    WebAuthContext,
    base_context,
    require_finance_access,
)

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


def _xlsx_response(content: bytes, filename: str) -> StreamingResponse:
    """Build a StreamingResponse for Excel (.xlsx) download."""
    return StreamingResponse(
        io.BytesIO(content),
        media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def reports_dashboard(
    request: Request,
    start_date: str | None = None,
    end_date: str | None = None,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Reports landing page."""
    context = base_context(request, auth, "Reports", "reports", db=db)
    context.update({"start_date": start_date, "end_date": end_date})
    return templates.TemplateResponse(request, "finance/reports/index.html", context)


@router.get("/trial-balance", response_class=HTMLResponse)
def trial_balance_report(
    request: Request,
    as_of_date: str | None = None,
    basis: str = Query("accrual", pattern="^(accrual|cash)$"),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Trial balance report page."""
    return reports_web_service.trial_balance_response(
        request, auth, as_of_date, db, basis=basis
    )


@router.get("/trial-balance/export")
def export_trial_balance(
    as_of_date: str | None = None,
    basis: str = Query("accrual", pattern="^(accrual|cash)$"),
    fmt: str = Query("csv", alias="format"),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
) -> StreamingResponse:
    """Export trial balance as CSV or PDF."""
    org_id = str(auth.organization_id)
    suffix = "_cash_basis" if basis == "cash" else ""
    if fmt == "pdf":
        pdf = reports_web_service.export_trial_balance_pdf(
            org_id, db, as_of_date, basis=basis
        )
        return _pdf_response(pdf, f"trial_balance{suffix}.pdf")
    csv = reports_web_service.export_trial_balance_csv(
        org_id, db, as_of_date, basis=basis
    )
    return _csv_response(csv, f"trial_balance{suffix}.csv")


@router.get("/income-statement", response_class=HTMLResponse)
def income_statement_report(
    request: Request,
    start_date: str | None = None,
    end_date: str | None = None,
    basis: str = Query("accrual", pattern="^(accrual|cash)$"),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Income statement report page."""
    return reports_web_service.income_statement_response(
        request, auth, start_date, end_date, db, basis=basis
    )


@router.get("/income-statement/export")
def export_income_statement(
    start_date: str | None = None,
    end_date: str | None = None,
    basis: str = Query("accrual", pattern="^(accrual|cash)$"),
    fmt: str = Query("csv", alias="format"),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
) -> StreamingResponse:
    """Export income statement as CSV or PDF."""
    org_id = str(auth.organization_id)
    suffix = "_cash_basis" if basis == "cash" else ""
    if fmt == "pdf":
        pdf = reports_web_service.export_income_statement_pdf(
            org_id, db, start_date, end_date, basis=basis
        )
        return _pdf_response(pdf, f"income_statement{suffix}.pdf")
    csv = reports_web_service.export_income_statement_csv(
        org_id, db, start_date, end_date, basis=basis
    )
    return _csv_response(csv, f"income_statement{suffix}.csv")


@router.get("/balance-sheet", response_class=HTMLResponse)
def balance_sheet_report(
    request: Request,
    as_of_date: str | None = None,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Balance sheet report page."""
    return reports_web_service.balance_sheet_response(request, auth, as_of_date, db)


@router.get("/balance-sheet/export")
def export_balance_sheet(
    as_of_date: str | None = None,
    fmt: str = Query("csv", alias="format"),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
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
    db: Session = Depends(get_db_for_org),
):
    """AP aging report page."""
    return reports_web_service.ap_aging_response(request, auth, as_of_date, db)


@router.get("/ap-aging/export")
def export_ap_aging(
    as_of_date: str | None = None,
    fmt: str = Query("pdf", alias="format"),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
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
    db: Session = Depends(get_db_for_org),
):
    """AR aging report page."""
    return reports_web_service.ar_aging_response(request, auth, as_of_date, db)


@router.get("/ar-aging/export")
def export_ar_aging(
    as_of_date: str | None = None,
    fmt: str = Query("pdf", alias="format"),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
) -> StreamingResponse:
    """Export AR aging as PDF."""
    org_id = str(auth.organization_id)
    if fmt == "pdf":
        pdf = reports_web_service.export_ar_aging_pdf(org_id, db, as_of_date)
        return _pdf_response(pdf, "ar_aging.pdf")
    pdf = reports_web_service.export_ar_aging_pdf(org_id, db, as_of_date)
    return _pdf_response(pdf, "ar_aging.pdf")


@router.get("/sales-day-book", response_class=HTMLResponse)
def sales_day_book_report(
    request: Request,
    start_date: str | None = None,
    end_date: str | None = None,
    status: str | None = None,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Sales Day Book report page (AR invoices, chronological)."""
    return reports_web_service.sales_day_book_response(
        request, auth, start_date, end_date, status, db
    )


@router.get("/sales-day-book/export")
def export_sales_day_book(
    start_date: str | None = None,
    end_date: str | None = None,
    status: str | None = None,
    fmt: str = Query("csv", alias="format"),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
) -> StreamingResponse:
    """Export the Sales Day Book as Excel, PDF or CSV."""
    org_id = str(auth.organization_id)
    if fmt == "pdf":
        pdf = reports_web_service.export_sales_day_book_pdf(
            org_id, db, start_date, end_date, status=status
        )
        return _pdf_response(pdf, "sales_day_book.pdf")
    if fmt in ("xlsx", "excel"):
        xlsx = reports_web_service.export_sales_day_book_xlsx(
            org_id, db, start_date, end_date, status=status
        )
        return _xlsx_response(xlsx, "sales_day_book.xlsx")
    csv = reports_web_service.export_sales_day_book_csv(
        org_id, db, start_date, end_date, status=status
    )
    return _csv_response(csv, "sales_day_book.csv")


@router.get("/purchases-day-book", response_class=HTMLResponse)
def purchases_day_book_report(
    request: Request,
    start_date: str | None = None,
    end_date: str | None = None,
    status: str | None = None,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Purchases Day Book report page (AP invoices, chronological)."""
    return reports_web_service.purchases_day_book_response(
        request, auth, start_date, end_date, status, db
    )


@router.get("/purchases-day-book/export")
def export_purchases_day_book(
    start_date: str | None = None,
    end_date: str | None = None,
    status: str | None = None,
    fmt: str = Query("csv", alias="format"),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
) -> StreamingResponse:
    """Export the Purchases Day Book as Excel, PDF or CSV."""
    org_id = str(auth.organization_id)
    if fmt == "pdf":
        pdf = reports_web_service.export_purchases_day_book_pdf(
            org_id, db, start_date, end_date, status=status
        )
        return _pdf_response(pdf, "purchases_day_book.pdf")
    if fmt in ("xlsx", "excel"):
        xlsx = reports_web_service.export_purchases_day_book_xlsx(
            org_id, db, start_date, end_date, status=status
        )
        return _xlsx_response(xlsx, "purchases_day_book.xlsx")
    csv = reports_web_service.export_purchases_day_book_csv(
        org_id, db, start_date, end_date, status=status
    )
    return _csv_response(csv, "purchases_day_book.csv")


@router.get("/cash-book", response_class=HTMLResponse)
def cash_book_report(
    request: Request,
    start_date: str | None = None,
    end_date: str | None = None,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Cash Book report page (receipts + payments, chronological)."""
    return reports_web_service.cash_book_response(
        request, auth, start_date, end_date, db
    )


@router.get("/cash-book/export")
def export_cash_book(
    start_date: str | None = None,
    end_date: str | None = None,
    fmt: str = Query("csv", alias="format"),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
) -> StreamingResponse:
    """Export the Cash Book as Excel, PDF or CSV."""
    org_id = str(auth.organization_id)
    if fmt == "pdf":
        pdf = reports_web_service.export_cash_book_pdf(org_id, db, start_date, end_date)
        return _pdf_response(pdf, "cash_book.pdf")
    if fmt in ("xlsx", "excel"):
        xlsx = reports_web_service.export_cash_book_xlsx(
            org_id, db, start_date, end_date
        )
        return _xlsx_response(xlsx, "cash_book.xlsx")
    csv = reports_web_service.export_cash_book_csv(org_id, db, start_date, end_date)
    return _csv_response(csv, "cash_book.csv")


@router.get("/journal-day-book", response_class=HTMLResponse)
def journal_day_book_report(
    request: Request,
    start_date: str | None = None,
    end_date: str | None = None,
    status: str | None = None,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Journal day book report page (posted GL journal entries)."""
    return reports_web_service.journal_day_book_response(
        request, auth, start_date, end_date, status, db
    )


@router.get("/journal-day-book/export")
def export_journal_day_book(
    start_date: str | None = None,
    end_date: str | None = None,
    status: str | None = None,
    fmt: str = Query("csv", alias="format"),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
) -> StreamingResponse:
    """Export the Journal day book as Excel, PDF or CSV."""
    org_id = str(auth.organization_id)
    if fmt == "pdf":
        pdf = reports_web_service.export_journal_day_book_pdf(
            org_id, db, start_date, end_date, status=status
        )
        return _pdf_response(pdf, "journal_day_book.pdf")
    if fmt in ("xlsx", "excel"):
        xlsx = reports_web_service.export_journal_day_book_xlsx(
            org_id, db, start_date, end_date, status=status
        )
        return _xlsx_response(xlsx, "journal_day_book.xlsx")
    csv = reports_web_service.export_journal_day_book_csv(
        org_id, db, start_date, end_date, status=status
    )
    return _csv_response(csv, "journal_day_book.csv")


@router.get("/sales-returns-day-book", response_class=HTMLResponse)
def sales_returns_day_book_report(
    request: Request,
    start_date: str | None = None,
    end_date: str | None = None,
    status: str | None = None,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Sales Returns Day Book report page (AR credit notes)."""
    return reports_web_service.sales_returns_day_book_response(
        request, auth, start_date, end_date, status, db
    )


@router.get("/sales-returns-day-book/export")
def export_sales_returns_day_book(
    start_date: str | None = None,
    end_date: str | None = None,
    status: str | None = None,
    fmt: str = Query("csv", alias="format"),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
) -> StreamingResponse:
    """Export the Sales Returns Day Book as Excel, PDF or CSV."""
    org_id = str(auth.organization_id)
    if fmt == "pdf":
        pdf = reports_web_service.export_sales_returns_day_book_pdf(
            org_id, db, start_date, end_date, status=status
        )
        return _pdf_response(pdf, "sales_returns_day_book.pdf")
    if fmt in ("xlsx", "excel"):
        xlsx = reports_web_service.export_sales_returns_day_book_xlsx(
            org_id, db, start_date, end_date, status=status
        )
        return _xlsx_response(xlsx, "sales_returns_day_book.xlsx")
    csv = reports_web_service.export_sales_returns_day_book_csv(
        org_id, db, start_date, end_date, status=status
    )
    return _csv_response(csv, "sales_returns_day_book.csv")


@router.get("/purchases-returns-day-book", response_class=HTMLResponse)
def purchases_returns_day_book_report(
    request: Request,
    start_date: str | None = None,
    end_date: str | None = None,
    status: str | None = None,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Purchases Returns Day Book report page (supplier credit notes)."""
    return reports_web_service.purchases_returns_day_book_response(
        request, auth, start_date, end_date, status, db
    )


@router.get("/purchases-returns-day-book/export")
def export_purchases_returns_day_book(
    start_date: str | None = None,
    end_date: str | None = None,
    status: str | None = None,
    fmt: str = Query("csv", alias="format"),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
) -> StreamingResponse:
    """Export the Purchases Returns Day Book as Excel, PDF or CSV."""
    org_id = str(auth.organization_id)
    if fmt == "pdf":
        pdf = reports_web_service.export_purchases_returns_day_book_pdf(
            org_id, db, start_date, end_date, status=status
        )
        return _pdf_response(pdf, "purchases_returns_day_book.pdf")
    if fmt in ("xlsx", "excel"):
        xlsx = reports_web_service.export_purchases_returns_day_book_xlsx(
            org_id, db, start_date, end_date, status=status
        )
        return _xlsx_response(xlsx, "purchases_returns_day_book.xlsx")
    csv = reports_web_service.export_purchases_returns_day_book_csv(
        org_id, db, start_date, end_date, status=status
    )
    return _csv_response(csv, "purchases_returns_day_book.csv")


@router.get("/general-ledger", response_class=HTMLResponse)
def general_ledger_report(
    request: Request,
    account_id: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
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
    db: Session = Depends(get_db_for_org),
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


@router.post("/general-ledger/export")
def queue_general_ledger_export_route(
    account_id: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    fmt: str = Query("csv", alias="format"),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
) -> JSONResponse:
    """Queue general ledger export for background processing."""
    if not auth.organization_id or not auth.user_id:
        return JSONResponse(
            {"detail": "Authentication required"},
            status_code=401,
        )

    instance = queue_general_ledger_export(
        db,
        auth.organization_id,
        auth.user_id,
        account_id=account_id,
        start_date=start_date,
        end_date=end_date,
        output_format=fmt,
    )
    return JSONResponse(
        {
            "message": (
                "General Ledger export is processing. "
                "You will be notified when it is ready."
            ),
            "instance_id": str(instance.instance_id),
        },
        status_code=202,
    )


@router.get("/general-ledger/exports/{instance_id}/download")
def download_general_ledger_export(
    instance_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
) -> Response:
    """Download a completed queued General Ledger export."""
    if not auth.organization_id or not auth.user_id:
        return JSONResponse(
            {"detail": "Authentication required"},
            status_code=401,
        )

    body, filename, media_type, content_length = get_completed_export_for_download(
        db,
        auth.organization_id,
        auth.user_id,
        instance_id,
    )
    if hasattr(body, "__fspath__"):
        return FileResponse(body, filename=filename, media_type=media_type)

    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    if content_length is not None:
        headers["Content-Length"] = str(content_length)
    return StreamingResponse(body, media_type=media_type, headers=headers)


@router.get("/general-ledger/exports/{instance_id}/status")
def general_ledger_export_status(
    instance_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
) -> JSONResponse:
    """Return the status of a queued General Ledger export."""
    if not auth.organization_id or not auth.user_id:
        return JSONResponse(
            {"detail": "Authentication required"},
            status_code=401,
        )

    return JSONResponse(
        get_export_status(db, auth.organization_id, auth.user_id, instance_id)
    )


@router.get("/tax-summary", response_class=HTMLResponse)
def tax_summary_report(
    request: Request,
    start_date: str | None = None,
    end_date: str | None = None,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Tax summary report page."""
    return reports_web_service.tax_summary_response(
        request, auth, start_date, end_date, db
    )


@router.get("/vendor-payout-breakdown", response_class=HTMLResponse)
def vendor_payout_breakdown_report(
    request: Request,
    start_date: str | None = None,
    end_date: str | None = None,
    supplier_id: str | None = None,
    status: str | None = None,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Supplier payout report with payment-level line-item breakdown."""
    return reports_web_service.vendor_payout_breakdown_response(
        request,
        auth,
        start_date,
        end_date,
        supplier_id,
        status,
        db,
    )


@router.get("/vendor-payout-breakdown/export")
def export_vendor_payout_breakdown(
    start_date: str | None = None,
    end_date: str | None = None,
    supplier_id: str | None = None,
    status: str | None = None,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
) -> StreamingResponse:
    """Export supplier payout breakdown as CSV."""
    csv = reports_web_service.export_vendor_payout_breakdown_csv(
        str(auth.organization_id),
        db,
        start_date=start_date,
        end_date=end_date,
        supplier_id=supplier_id,
        status=status,
    )
    return _csv_response(csv, "supplier_payout_breakdown.csv")


@router.get("/tax-summary/export")
def export_tax_summary(
    start_date: str | None = None,
    end_date: str | None = None,
    fmt: str = Query("pdf", alias="format"),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
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
    db: Session = Depends(get_db_for_org),
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
    db: Session = Depends(get_db_for_org),
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
    db: Session = Depends(get_db_for_org),
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
    db: Session = Depends(get_db_for_org),
) -> StreamingResponse:
    """Export cash flow as PDF."""
    org_id = str(auth.organization_id)
    pdf = reports_web_service.export_cash_flow_pdf(org_id, db, start_date, end_date)
    return _pdf_response(pdf, "cash_flow.pdf")


@router.get("/cash-flow/ias7", response_class=HTMLResponse)
def ias7_cash_flow_report(
    request: Request,
    start_date: str | None = None,
    end_date: str | None = None,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """IAS 7 cash flow statement (indirect method)."""
    return reports_web_service.ias7_cash_flow_response(
        request, auth, start_date, end_date, db
    )


@router.get("/cash-flow/ias7/export")
def export_ias7_cash_flow(
    start_date: str | None = None,
    end_date: str | None = None,
    fmt: str = Query("csv", alias="format"),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
) -> StreamingResponse:
    """Export IAS 7 cash flow as CSV or PDF."""
    org_id = str(auth.organization_id)
    if fmt == "pdf":
        pdf = reports_web_service.export_ias7_cash_flow_pdf(
            org_id, db, start_date, end_date
        )
        return _pdf_response(pdf, "ias7_cash_flow.pdf")
    csv = reports_web_service.export_ias7_cash_flow_csv(
        org_id, db, start_date, end_date
    )
    return _csv_response(csv, "ias7_cash_flow.csv")


@router.get("/changes-in-equity", response_class=HTMLResponse)
def changes_in_equity_report(
    request: Request,
    start_date: str | None = None,
    end_date: str | None = None,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
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
    db: Session = Depends(get_db_for_org),
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
    db: Session = Depends(get_db_for_org),
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
    db: Session = Depends(get_db_for_org),
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
    db: Session = Depends(get_db_for_org),
):
    """Pivot-style analysis report page."""
    return reports_web_service.analysis_response(request, auth, db)


@router.get("/inventory-valuation-reconciliation", response_class=HTMLResponse)
def inventory_valuation_reconciliation_report(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Inventory valuation reconciliation report page."""
    return reports_web_service.inventory_valuation_reconciliation_response(
        request, auth, db
    )


@router.get("/inventory-valuation-reconciliation/export")
def export_inventory_valuation(
    fmt: str = Query("pdf", alias="format"),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
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
    db: Session = Depends(get_db_for_org),
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
    db: Session = Depends(get_db_for_org),
) -> StreamingResponse:
    """Export budget vs actual as PDF."""
    org_id = str(auth.organization_id)
    pdf = reports_web_service.export_budget_vs_actual_pdf(
        org_id, db, start_date, end_date, budget_id, budget_code
    )
    return _pdf_response(pdf, "budget_vs_actual.pdf")
