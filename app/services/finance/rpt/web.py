"""
Reports web view service.

Provides view-focused data for reports web routes.
Delegates context building and CSV export to per-report modules.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.web.deps import WebAuthContext

from fastapi import Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.services.finance.rpt.aging import ap_aging_context, ar_aging_context
from app.services.finance.rpt.analysis_cube import AnalysisCubeService
from app.services.finance.rpt.balance_sheet import (
    balance_sheet_context,
    export_balance_sheet_csv,
)
from app.services.finance.rpt.budget_vs_actual import budget_vs_actual_context
from app.services.finance.rpt.cash_flow import cash_flow_context
from app.services.finance.rpt.changes_in_equity import changes_in_equity_context
from app.services.finance.rpt.dashboard import dashboard_context
from app.services.finance.rpt.day_books import (
    cash_book_context,
    export_cash_book_csv,
    export_cash_book_xlsx,
    export_journal_day_book_csv,
    export_journal_day_book_xlsx,
    export_purchases_day_book_csv,
    export_purchases_day_book_xlsx,
    export_purchases_returns_day_book_csv,
    export_purchases_returns_day_book_xlsx,
    export_sales_day_book_csv,
    export_sales_day_book_xlsx,
    export_sales_returns_day_book_csv,
    export_sales_returns_day_book_xlsx,
    journal_day_book_context,
    purchases_day_book_context,
    purchases_returns_day_book_context,
    sales_day_book_context,
    sales_returns_day_book_context,
)
from app.services.finance.rpt.expense_summary import expense_summary_context
from app.services.finance.rpt.general_ledger import (
    export_general_ledger_csv,
    general_ledger_context,
)
from app.services.finance.rpt.ias7_cash_flow import (
    export_ias7_cash_flow_csv,
    ias7_cash_flow_context,
)
from app.services.finance.rpt.income_statement import (
    export_income_statement_csv,
    income_statement_context,
)
from app.services.finance.rpt.inventory_valuation import (
    inventory_valuation_reconciliation_context,
)
from app.services.finance.rpt.management_accounts import (
    export_management_accounts_csv,
    management_accounts_context,
)
from app.services.finance.rpt.tax_summary import tax_summary_context
from app.services.finance.rpt.trial_balance import (
    export_trial_balance_csv,
    trial_balance_context,
)
from app.services.finance.rpt.vendor_payout_breakdown import (
    export_vendor_payout_breakdown_csv,
    vendor_payout_breakdown_context,
)
from app.templates import templates

# NOTE: WebAuthContext and base_context are imported lazily inside response methods
# to avoid circular imports with app.web.deps


class ReportsWebService:
    """View service for reports web routes.

    Delegates context building to per-report modules under
    ``app.services.finance.rpt.*``.  This class only orchestrates
    request → context → template rendering.
    """

    # ─────────────────── Response methods ───────────────────

    def dashboard_response(
        self,
        request: Request,
        auth: WebAuthContext,
        start_date: str | None,
        end_date: str | None,
        db: Session,
    ) -> HTMLResponse:
        from app.services.common_filters import build_active_filters
        from app.web.deps import base_context

        context = base_context(request, auth, "Reports", "reports")
        context.update(
            dashboard_context(
                db,
                str(auth.organization_id),
                start_date=start_date,
                end_date=end_date,
            )
        )
        context["active_filters"] = build_active_filters(
            params={"start_date": start_date, "end_date": end_date},
            labels={"start_date": "From", "end_date": "To"},
        )
        return templates.TemplateResponse(
            request, "finance/reports/dashboard.html", context
        )

    def trial_balance_response(
        self,
        request: Request,
        auth: WebAuthContext,
        as_of_date: str | None,
        db: Session,
        basis: str = "accrual",
    ) -> HTMLResponse:
        from app.services.common_filters import build_active_filters
        from app.web.deps import base_context

        is_cash = basis == "cash"
        title = "Trial Balance (Cash Basis)" if is_cash else "Trial Balance"
        context = base_context(request, auth, title, "reports")
        context.update(
            trial_balance_context(
                db,
                str(auth.organization_id),
                as_of_date=as_of_date,
                basis=basis,
            )
        )
        context["active_filters"] = build_active_filters(
            params={"as_of_date": as_of_date},
            labels={"as_of_date": "As of"},
        )
        return templates.TemplateResponse(
            request, "finance/reports/trial_balance.html", context
        )

    def income_statement_response(
        self,
        request: Request,
        auth: WebAuthContext,
        start_date: str | None,
        end_date: str | None,
        db: Session,
        basis: str = "accrual",
    ) -> HTMLResponse:
        from app.services.common_filters import build_active_filters
        from app.web.deps import base_context

        is_cash = basis == "cash"
        title = (
            "Statement of Profit or Loss (Cash Basis)"
            if is_cash
            else "Statement of Profit or Loss"
        )
        context = base_context(request, auth, title, "reports")
        context.update(
            income_statement_context(
                db,
                str(auth.organization_id),
                start_date=start_date,
                end_date=end_date,
                basis=basis,
            )
        )
        context["active_filters"] = build_active_filters(
            params={"start_date": start_date, "end_date": end_date},
            labels={"start_date": "From", "end_date": "To"},
        )
        return templates.TemplateResponse(
            request, "finance/reports/income_statement.html", context
        )

    def balance_sheet_response(
        self,
        request: Request,
        auth: WebAuthContext,
        as_of_date: str | None,
        db: Session,
    ) -> HTMLResponse:
        from app.services.common_filters import build_active_filters
        from app.web.deps import base_context

        context = base_context(
            request, auth, "Statement of Financial Position", "reports"
        )
        context.update(
            balance_sheet_context(
                db,
                str(auth.organization_id),
                as_of_date=as_of_date,
            )
        )
        context["active_filters"] = build_active_filters(
            params={"as_of_date": as_of_date},
            labels={"as_of_date": "As of"},
        )
        return templates.TemplateResponse(
            request, "finance/reports/balance_sheet.html", context
        )

    def ap_aging_response(
        self,
        request: Request,
        auth: WebAuthContext,
        as_of_date: str | None,
        db: Session,
    ) -> HTMLResponse:
        from app.services.common_filters import build_active_filters
        from app.web.deps import base_context

        active_filters = build_active_filters(
            params={"as_of_date": as_of_date},
            labels={"as_of_date": "As of"},
        )
        context = base_context(request, auth, "AP Aging Report", "reports")
        context.update(
            ap_aging_context(
                db,
                str(auth.organization_id),
                as_of_date=as_of_date,
            )
        )
        context["active_filters"] = active_filters
        return templates.TemplateResponse(
            request, "finance/reports/ap_aging.html", context
        )

    def ar_aging_response(
        self,
        request: Request,
        auth: WebAuthContext,
        as_of_date: str | None,
        db: Session,
    ) -> HTMLResponse:
        from app.services.common_filters import build_active_filters
        from app.web.deps import base_context

        active_filters = build_active_filters(
            params={"as_of_date": as_of_date},
            labels={"as_of_date": "As of"},
        )
        context = base_context(request, auth, "AR Aging Report", "reports")
        context.update(
            ar_aging_context(
                db,
                str(auth.organization_id),
                as_of_date=as_of_date,
            )
        )
        context["active_filters"] = active_filters
        return templates.TemplateResponse(
            request, "finance/reports/ar_aging.html", context
        )

    def sales_day_book_response(
        self,
        request: Request,
        auth: WebAuthContext,
        start_date: str | None,
        end_date: str | None,
        status: str | None,
        db: Session,
    ) -> HTMLResponse:
        from app.services.common_filters import build_active_filters
        from app.web.deps import base_context

        context = base_context(request, auth, "Sales Day Book", "reports")
        context.update(
            sales_day_book_context(
                db,
                str(auth.organization_id),
                start_date=start_date,
                end_date=end_date,
                status=status,
            )
        )
        context["active_filters"] = build_active_filters(
            params={
                "start_date": start_date,
                "end_date": end_date,
                "status": status,
            },
            labels={"start_date": "From", "end_date": "To", "status": "Status"},
        )
        return templates.TemplateResponse(
            request, "finance/reports/sales_day_book.html", context
        )

    def purchases_day_book_response(
        self,
        request: Request,
        auth: WebAuthContext,
        start_date: str | None,
        end_date: str | None,
        status: str | None,
        db: Session,
    ) -> HTMLResponse:
        from app.services.common_filters import build_active_filters
        from app.web.deps import base_context

        context = base_context(request, auth, "Purchases Day Book", "reports")
        context.update(
            purchases_day_book_context(
                db,
                str(auth.organization_id),
                start_date=start_date,
                end_date=end_date,
                status=status,
            )
        )
        context["active_filters"] = build_active_filters(
            params={
                "start_date": start_date,
                "end_date": end_date,
                "status": status,
            },
            labels={"start_date": "From", "end_date": "To", "status": "Status"},
        )
        return templates.TemplateResponse(
            request, "finance/reports/purchases_day_book.html", context
        )

    def cash_book_response(
        self,
        request: Request,
        auth: WebAuthContext,
        start_date: str | None,
        end_date: str | None,
        db: Session,
    ) -> HTMLResponse:
        from app.services.common_filters import build_active_filters
        from app.web.deps import base_context

        context = base_context(request, auth, "Cash Book", "reports")
        context.update(
            cash_book_context(
                db,
                str(auth.organization_id),
                start_date=start_date,
                end_date=end_date,
            )
        )
        context["active_filters"] = build_active_filters(
            params={"start_date": start_date, "end_date": end_date},
            labels={"start_date": "From", "end_date": "To"},
        )
        return templates.TemplateResponse(
            request, "finance/reports/cash_book.html", context
        )

    def journal_day_book_response(
        self,
        request: Request,
        auth: WebAuthContext,
        start_date: str | None,
        end_date: str | None,
        status: str | None,
        db: Session,
    ) -> HTMLResponse:
        from app.services.common_filters import build_active_filters
        from app.web.deps import base_context

        context = base_context(request, auth, "Journal Day Book", "reports")
        context.update(
            journal_day_book_context(
                db,
                str(auth.organization_id),
                start_date=start_date,
                end_date=end_date,
                status=status,
            )
        )
        context["active_filters"] = build_active_filters(
            params={
                "start_date": start_date,
                "end_date": end_date,
                "status": status,
            },
            labels={"start_date": "From", "end_date": "To", "status": "Status"},
        )
        return templates.TemplateResponse(
            request, "finance/reports/journal_day_book.html", context
        )

    def sales_returns_day_book_response(
        self,
        request: Request,
        auth: WebAuthContext,
        start_date: str | None,
        end_date: str | None,
        status: str | None,
        db: Session,
    ) -> HTMLResponse:
        from app.services.common_filters import build_active_filters
        from app.web.deps import base_context

        context = base_context(request, auth, "Sales Returns Day Book", "reports")
        context.update(
            sales_returns_day_book_context(
                db,
                str(auth.organization_id),
                start_date=start_date,
                end_date=end_date,
                status=status,
            )
        )
        context["active_filters"] = build_active_filters(
            params={
                "start_date": start_date,
                "end_date": end_date,
                "status": status,
            },
            labels={"start_date": "From", "end_date": "To", "status": "Status"},
        )
        return templates.TemplateResponse(
            request, "finance/reports/sales_returns_day_book.html", context
        )

    def purchases_returns_day_book_response(
        self,
        request: Request,
        auth: WebAuthContext,
        start_date: str | None,
        end_date: str | None,
        status: str | None,
        db: Session,
    ) -> HTMLResponse:
        from app.services.common_filters import build_active_filters
        from app.web.deps import base_context

        context = base_context(request, auth, "Purchases Returns Day Book", "reports")
        context.update(
            purchases_returns_day_book_context(
                db,
                str(auth.organization_id),
                start_date=start_date,
                end_date=end_date,
                status=status,
            )
        )
        context["active_filters"] = build_active_filters(
            params={
                "start_date": start_date,
                "end_date": end_date,
                "status": status,
            },
            labels={"start_date": "From", "end_date": "To", "status": "Status"},
        )
        return templates.TemplateResponse(
            request, "finance/reports/purchases_returns_day_book.html", context
        )

    def general_ledger_response(
        self,
        request: Request,
        auth: WebAuthContext,
        account_id: str | None,
        start_date: str | None,
        end_date: str | None,
        db: Session,
    ) -> HTMLResponse:
        from app.services.common_filters import build_active_filters
        from app.web.deps import base_context

        context = base_context(request, auth, "Account Ledger Report", "reports")
        context.update(
            general_ledger_context(
                db,
                str(auth.organization_id),
                account_id=account_id,
                start_date=start_date,
                end_date=end_date,
            )
        )
        context["active_filters"] = build_active_filters(
            params={
                "account_id": account_id,
                "start_date": start_date,
                "end_date": end_date,
            },
            labels={
                "account_id": "Account",
                "start_date": "From",
                "end_date": "To",
            },
            options={
                "account_id": {
                    account["account_id"]: (
                        f"{account['account_code']} - {account['account_name']}"
                        if account["account_code"]
                        else account["account_name"]
                    )
                    for account in context["accounts"]
                },
            },
        )
        return templates.TemplateResponse(
            request, "finance/reports/general_ledger.html", context
        )

    def tax_summary_response(
        self,
        request: Request,
        auth: WebAuthContext,
        start_date: str | None,
        end_date: str | None,
        db: Session,
    ) -> HTMLResponse:
        from app.services.common_filters import build_active_filters
        from app.web.deps import base_context

        context = base_context(request, auth, "Tax Summary", "reports")
        context.update(
            tax_summary_context(
                db,
                str(auth.organization_id),
                start_date=start_date,
                end_date=end_date,
            )
        )
        context["active_filters"] = build_active_filters(
            params={"start_date": start_date, "end_date": end_date},
            labels={"start_date": "From", "end_date": "To"},
        )
        return templates.TemplateResponse(
            request, "finance/reports/tax_summary.html", context
        )

    def vendor_payout_breakdown_response(
        self,
        request: Request,
        auth: WebAuthContext,
        start_date: str | None,
        end_date: str | None,
        supplier_id: str | None,
        status: str | None,
        db: Session,
    ) -> HTMLResponse:
        from app.services.common_filters import build_active_filters
        from app.web.deps import base_context

        context = base_context(request, auth, "Supplier Payout Breakdown", "reports")
        context.update(
            vendor_payout_breakdown_context(
                db,
                str(auth.organization_id),
                start_date=start_date,
                end_date=end_date,
                supplier_id=supplier_id,
                status=status,
            )
        )
        context["active_filters"] = build_active_filters(
            params={
                "start_date": start_date,
                "end_date": end_date,
                "supplier_id": supplier_id,
                "status": status,
            },
            labels={
                "start_date": "From",
                "end_date": "To",
                "supplier_id": "Supplier",
                "status": "Status",
            },
            options={
                "supplier_id": {
                    item["supplier_id"]: item["supplier_name"]
                    for item in context["supplier_options"]
                },
                "status": {
                    item["value"]: item["label"] for item in context["status_options"]
                },
            },
        )
        return templates.TemplateResponse(
            request,
            "finance/reports/vendor_payout_breakdown.html",
            context,
        )

    def expense_summary_response(
        self,
        request: Request,
        auth: WebAuthContext,
        start_date: str | None,
        end_date: str | None,
        db: Session,
    ) -> HTMLResponse:
        from app.services.common_filters import build_active_filters
        from app.web.deps import base_context

        context = base_context(request, auth, "Expense Summary", "reports")
        context.update(
            expense_summary_context(
                db,
                str(auth.organization_id),
                start_date=start_date,
                end_date=end_date,
            )
        )
        context["active_filters"] = build_active_filters(
            params={"start_date": start_date, "end_date": end_date},
            labels={"start_date": "From", "end_date": "To"},
        )
        return templates.TemplateResponse(
            request, "finance/reports/expense_summary.html", context
        )

    def cash_flow_response(
        self,
        request: Request,
        auth: WebAuthContext,
        start_date: str | None,
        end_date: str | None,
        db: Session,
    ) -> HTMLResponse:
        from app.services.common_filters import build_active_filters
        from app.web.deps import base_context

        context = base_context(request, auth, "Cash Flow Statement", "reports")
        context.update(
            cash_flow_context(
                db,
                str(auth.organization_id),
                start_date=start_date,
                end_date=end_date,
            )
        )
        context["active_filters"] = build_active_filters(
            params={"start_date": start_date, "end_date": end_date},
            labels={"start_date": "From", "end_date": "To"},
        )
        return templates.TemplateResponse(
            request, "finance/reports/cash_flow.html", context
        )

    def ias7_cash_flow_response(
        self,
        request: Request,
        auth: WebAuthContext,
        start_date: str | None,
        end_date: str | None,
        db: Session,
    ) -> HTMLResponse:
        from app.services.common_filters import build_active_filters
        from app.web.deps import base_context

        context = base_context(
            request, auth, "Statement of Cash Flows (IAS 7)", "reports"
        )
        context.update(
            ias7_cash_flow_context(
                db,
                str(auth.organization_id),
                start_date=start_date,
                end_date=end_date,
            )
        )
        context["active_filters"] = build_active_filters(
            params={"start_date": start_date, "end_date": end_date},
            labels={"start_date": "From", "end_date": "To"},
        )
        return templates.TemplateResponse(
            request, "finance/reports/ias7_cash_flow.html", context
        )

    def changes_in_equity_response(
        self,
        request: Request,
        auth: WebAuthContext,
        start_date: str | None,
        end_date: str | None,
        db: Session,
    ) -> HTMLResponse:
        from app.services.common_filters import build_active_filters
        from app.web.deps import base_context

        context = base_context(request, auth, "Changes in Equity", "reports")
        context.update(
            changes_in_equity_context(
                db,
                str(auth.organization_id),
                start_date=start_date,
                end_date=end_date,
            )
        )
        context["active_filters"] = build_active_filters(
            params={"start_date": start_date, "end_date": end_date},
            labels={"start_date": "From", "end_date": "To"},
        )
        return templates.TemplateResponse(
            request, "finance/reports/changes_in_equity.html", context
        )

    def budget_vs_actual_response(
        self,
        request: Request,
        auth: WebAuthContext,
        start_date: str | None,
        end_date: str | None,
        budget_id: str | None,
        budget_code: str | None,
        db: Session,
    ) -> HTMLResponse:
        from app.services.common_filters import build_active_filters
        from app.web.deps import base_context

        context = base_context(request, auth, "Budget vs Actual", "reports")
        context.update(
            budget_vs_actual_context(
                db,
                str(auth.organization_id),
                start_date=start_date,
                end_date=end_date,
                budget_id=budget_id,
                budget_code=budget_code,
            )
        )
        budgets: list[Any] = context.get("budgets", [])
        context["active_filters"] = build_active_filters(
            params={
                "start_date": start_date,
                "end_date": end_date,
                "budget_id": budget_id,
            },
            labels={"start_date": "From", "end_date": "To", "budget_id": "Budget"},
            options={
                "budget_id": {
                    str(b["budget_id"]): f"{b['budget_code']} - {b['budget_name']}"
                    for b in budgets
                }
            },
        )
        return templates.TemplateResponse(
            request, "finance/reports/budget_vs_actual.html", context
        )

    def inventory_valuation_reconciliation_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        from app.web.deps import base_context

        context = base_context(
            request,
            auth,
            "Inventory Valuation Reconciliation",
            "reports",
        )
        context.update(
            inventory_valuation_reconciliation_context(
                db,
                str(auth.organization_id),
            )
        )
        return templates.TemplateResponse(
            request,
            "finance/reports/inventory_valuation_reconciliation.html",
            context,
        )

    def analysis_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        from app.web.deps import base_context

        context = base_context(request, auth, "Analysis", "reports")
        org_id = auth.organization_id
        cubes = AnalysisCubeService(db).list_cubes(org_id) if org_id else []
        context["analysis_cubes"] = [
            {
                "code": cube.code,
                "name": cube.name,
                "description": cube.description,
                "dimensions": cube.dimensions or [],
                "measures": cube.measures or [],
                "default_rows": cube.default_rows or [],
                "default_measures": cube.default_measures or [],
            }
            for cube in cubes
        ]
        return templates.TemplateResponse(
            request, "finance/reports/analysis.html", context
        )

    def management_accounts_response(
        self,
        request: Request,
        auth: WebAuthContext,
        start_date: str | None,
        end_date: str | None,
        db: Session,
    ) -> HTMLResponse:
        """Render management accounts report page."""
        from app.services.common_filters import build_active_filters
        from app.web.deps import base_context

        context = base_context(request, auth, "Management Accounts", "reports")
        context.update(
            management_accounts_context(
                db,
                str(auth.organization_id),
                start_date=start_date,
                end_date=end_date,
            )
        )
        context["active_filters"] = build_active_filters(
            params={"start_date": start_date, "end_date": end_date},
            labels={"start_date": "From", "end_date": "To"},
        )
        return templates.TemplateResponse(
            request, "finance/reports/management_accounts.html", context
        )

    # ─────────────────── CSV Export helpers ───────────────────

    def export_trial_balance_csv(
        self,
        organization_id: str,
        db: Session,
        as_of_date: str | None = None,
        basis: str = "accrual",
    ) -> str:
        """Export trial balance as CSV."""
        return export_trial_balance_csv(organization_id, db, as_of_date, basis=basis)

    def export_income_statement_csv(
        self,
        organization_id: str,
        db: Session,
        start_date: str | None = None,
        end_date: str | None = None,
        basis: str = "accrual",
    ) -> str:
        """Export income statement as CSV."""
        return export_income_statement_csv(
            organization_id, db, start_date, end_date, basis=basis
        )

    def export_sales_day_book_csv(
        self,
        organization_id: str,
        db: Session,
        start_date: str | None = None,
        end_date: str | None = None,
        status: str | None = None,
    ) -> str:
        """Export the Sales Day Book as CSV."""
        return export_sales_day_book_csv(
            organization_id, db, start_date, end_date, status=status
        )

    def export_sales_day_book_xlsx(
        self,
        organization_id: str,
        db: Session,
        start_date: str | None = None,
        end_date: str | None = None,
        status: str | None = None,
    ) -> bytes:
        """Export the Sales Day Book as an Excel workbook."""
        return export_sales_day_book_xlsx(
            organization_id, db, start_date, end_date, status=status
        )

    def export_purchases_day_book_csv(
        self,
        organization_id: str,
        db: Session,
        start_date: str | None = None,
        end_date: str | None = None,
        status: str | None = None,
    ) -> str:
        """Export the Purchases Day Book as CSV."""
        return export_purchases_day_book_csv(
            organization_id, db, start_date, end_date, status=status
        )

    def export_purchases_day_book_xlsx(
        self,
        organization_id: str,
        db: Session,
        start_date: str | None = None,
        end_date: str | None = None,
        status: str | None = None,
    ) -> bytes:
        """Export the Purchases Day Book as an Excel workbook."""
        return export_purchases_day_book_xlsx(
            organization_id, db, start_date, end_date, status=status
        )

    def export_cash_book_csv(
        self,
        organization_id: str,
        db: Session,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> str:
        """Export the Cash Book as CSV."""
        return export_cash_book_csv(organization_id, db, start_date, end_date)

    def export_cash_book_xlsx(
        self,
        organization_id: str,
        db: Session,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> bytes:
        """Export the Cash Book as an Excel workbook."""
        return export_cash_book_xlsx(organization_id, db, start_date, end_date)

    def export_journal_day_book_csv(
        self,
        organization_id: str,
        db: Session,
        start_date: str | None = None,
        end_date: str | None = None,
        status: str | None = None,
    ) -> str:
        """Export the Journal day book as CSV."""
        return export_journal_day_book_csv(
            organization_id, db, start_date, end_date, status=status
        )

    def export_journal_day_book_xlsx(
        self,
        organization_id: str,
        db: Session,
        start_date: str | None = None,
        end_date: str | None = None,
        status: str | None = None,
    ) -> bytes:
        """Export the Journal day book as an Excel workbook."""
        return export_journal_day_book_xlsx(
            organization_id, db, start_date, end_date, status=status
        )

    def export_sales_returns_day_book_csv(
        self,
        organization_id: str,
        db: Session,
        start_date: str | None = None,
        end_date: str | None = None,
        status: str | None = None,
    ) -> str:
        """Export the Sales Returns Day Book as CSV."""
        return export_sales_returns_day_book_csv(
            organization_id, db, start_date, end_date, status=status
        )

    def export_sales_returns_day_book_xlsx(
        self,
        organization_id: str,
        db: Session,
        start_date: str | None = None,
        end_date: str | None = None,
        status: str | None = None,
    ) -> bytes:
        """Export the Sales Returns Day Book as an Excel workbook."""
        return export_sales_returns_day_book_xlsx(
            organization_id, db, start_date, end_date, status=status
        )

    def export_purchases_returns_day_book_csv(
        self,
        organization_id: str,
        db: Session,
        start_date: str | None = None,
        end_date: str | None = None,
        status: str | None = None,
    ) -> str:
        """Export the Purchases Returns Day Book as CSV."""
        return export_purchases_returns_day_book_csv(
            organization_id, db, start_date, end_date, status=status
        )

    def export_purchases_returns_day_book_xlsx(
        self,
        organization_id: str,
        db: Session,
        start_date: str | None = None,
        end_date: str | None = None,
        status: str | None = None,
    ) -> bytes:
        """Export the Purchases Returns Day Book as an Excel workbook."""
        return export_purchases_returns_day_book_xlsx(
            organization_id, db, start_date, end_date, status=status
        )

    def export_balance_sheet_csv(
        self,
        organization_id: str,
        db: Session,
        as_of_date: str | None = None,
    ) -> str:
        """Export balance sheet as CSV."""
        return export_balance_sheet_csv(organization_id, db, as_of_date)

    def export_general_ledger_csv(
        self,
        organization_id: str,
        db: Session,
        account_id: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> str:
        """Export general ledger as CSV."""
        return export_general_ledger_csv(
            organization_id, db, account_id, start_date, end_date
        )

    def export_management_accounts_csv(
        self,
        organization_id: str,
        db: Session,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> str:
        """Export management accounts as CSV."""
        return export_management_accounts_csv(organization_id, db, start_date, end_date)

    def export_ias7_cash_flow_csv(
        self,
        organization_id: str,
        db: Session,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> str:
        """Export IAS 7 cash flow statement as CSV."""
        return export_ias7_cash_flow_csv(organization_id, db, start_date, end_date)

    def export_vendor_payout_breakdown_csv(
        self,
        organization_id: str,
        db: Session,
        start_date: str | None = None,
        end_date: str | None = None,
        supplier_id: str | None = None,
        status: str | None = None,
    ) -> str:
        """Export supplier payout breakdown as CSV."""
        return export_vendor_payout_breakdown_csv(
            organization_id,
            db,
            start_date=start_date,
            end_date=end_date,
            supplier_id=supplier_id,
            status=status,
        )

    # ─────────────────── PDF Export helpers ───────────────────

    def _render_pdf(
        self,
        report_name: str,
        organization_id: str,
        db: Session,
        context: dict[str, Any],
    ) -> bytes:
        """Shared helper — render a named report to PDF bytes."""
        from app.services.finance.rpt.pdf import ReportPDFService

        return ReportPDFService(db).render(report_name, organization_id, context)

    def export_trial_balance_pdf(
        self,
        organization_id: str,
        db: Session,
        as_of_date: str | None = None,
        basis: str = "accrual",
    ) -> bytes:
        """Export trial balance as PDF."""
        ctx = trial_balance_context(
            db, organization_id, as_of_date=as_of_date, basis=basis
        )
        return self._render_pdf("trial_balance", organization_id, db, ctx)

    def export_income_statement_pdf(
        self,
        organization_id: str,
        db: Session,
        start_date: str | None = None,
        end_date: str | None = None,
        basis: str = "accrual",
    ) -> bytes:
        """Export income statement as PDF."""
        ctx = income_statement_context(
            db, organization_id, start_date=start_date, end_date=end_date, basis=basis
        )
        return self._render_pdf("income_statement", organization_id, db, ctx)

    def export_sales_day_book_pdf(
        self,
        organization_id: str,
        db: Session,
        start_date: str | None = None,
        end_date: str | None = None,
        status: str | None = None,
    ) -> bytes:
        """Export the Sales Day Book as PDF."""
        ctx = sales_day_book_context(
            db, organization_id, start_date=start_date, end_date=end_date, status=status
        )
        return self._render_pdf("sales_day_book", organization_id, db, ctx)

    def export_purchases_day_book_pdf(
        self,
        organization_id: str,
        db: Session,
        start_date: str | None = None,
        end_date: str | None = None,
        status: str | None = None,
    ) -> bytes:
        """Export the Purchases Day Book as PDF."""
        ctx = purchases_day_book_context(
            db, organization_id, start_date=start_date, end_date=end_date, status=status
        )
        return self._render_pdf("purchases_day_book", organization_id, db, ctx)

    def export_cash_book_pdf(
        self,
        organization_id: str,
        db: Session,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> bytes:
        """Export the Cash Book as PDF."""
        ctx = cash_book_context(
            db, organization_id, start_date=start_date, end_date=end_date
        )
        return self._render_pdf("cash_book", organization_id, db, ctx)

    def export_journal_day_book_pdf(
        self,
        organization_id: str,
        db: Session,
        start_date: str | None = None,
        end_date: str | None = None,
        status: str | None = None,
    ) -> bytes:
        """Export the Journal day book as PDF."""
        ctx = journal_day_book_context(
            db, organization_id, start_date=start_date, end_date=end_date, status=status
        )
        return self._render_pdf("journal_day_book", organization_id, db, ctx)

    def export_sales_returns_day_book_pdf(
        self,
        organization_id: str,
        db: Session,
        start_date: str | None = None,
        end_date: str | None = None,
        status: str | None = None,
    ) -> bytes:
        """Export the Sales Returns Day Book as PDF."""
        ctx = sales_returns_day_book_context(
            db, organization_id, start_date=start_date, end_date=end_date, status=status
        )
        return self._render_pdf("sales_returns_day_book", organization_id, db, ctx)

    def export_purchases_returns_day_book_pdf(
        self,
        organization_id: str,
        db: Session,
        start_date: str | None = None,
        end_date: str | None = None,
        status: str | None = None,
    ) -> bytes:
        """Export the Purchases Returns Day Book as PDF."""
        ctx = purchases_returns_day_book_context(
            db, organization_id, start_date=start_date, end_date=end_date, status=status
        )
        return self._render_pdf("purchases_returns_day_book", organization_id, db, ctx)

    def export_balance_sheet_pdf(
        self,
        organization_id: str,
        db: Session,
        as_of_date: str | None = None,
    ) -> bytes:
        """Export balance sheet as PDF."""
        ctx = balance_sheet_context(db, organization_id, as_of_date=as_of_date)
        return self._render_pdf("balance_sheet", organization_id, db, ctx)

    def export_general_ledger_pdf(
        self,
        organization_id: str,
        db: Session,
        account_id: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> bytes:
        """Export general ledger as PDF."""
        ctx = general_ledger_context(
            db,
            organization_id,
            account_id=account_id,
            start_date=start_date,
            end_date=end_date,
            all_accounts_limit=None,
        )
        return self._render_pdf("general_ledger", organization_id, db, ctx)

    def export_management_accounts_pdf(
        self,
        organization_id: str,
        db: Session,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> bytes:
        """Export management accounts as PDF."""
        ctx = management_accounts_context(
            db, organization_id, start_date=start_date, end_date=end_date
        )
        return self._render_pdf("management_accounts", organization_id, db, ctx)

    def export_ap_aging_pdf(
        self,
        organization_id: str,
        db: Session,
        as_of_date: str | None = None,
    ) -> bytes:
        """Export AP aging as PDF."""
        ctx = ap_aging_context(db, organization_id, as_of_date=as_of_date)
        return self._render_pdf("ap_aging", organization_id, db, ctx)

    def export_ar_aging_pdf(
        self,
        organization_id: str,
        db: Session,
        as_of_date: str | None = None,
    ) -> bytes:
        """Export AR aging as PDF."""
        ctx = ar_aging_context(db, organization_id, as_of_date=as_of_date)
        return self._render_pdf("ar_aging", organization_id, db, ctx)

    def export_tax_summary_pdf(
        self,
        organization_id: str,
        db: Session,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> bytes:
        """Export tax summary as PDF."""
        ctx = tax_summary_context(
            db, organization_id, start_date=start_date, end_date=end_date
        )
        return self._render_pdf("tax_summary", organization_id, db, ctx)

    def export_expense_summary_pdf(
        self,
        organization_id: str,
        db: Session,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> bytes:
        """Export expense summary as PDF."""
        ctx = expense_summary_context(
            db, organization_id, start_date=start_date, end_date=end_date
        )
        return self._render_pdf("expense_summary", organization_id, db, ctx)

    def export_cash_flow_pdf(
        self,
        organization_id: str,
        db: Session,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> bytes:
        """Export cash flow statement as PDF."""
        ctx = cash_flow_context(
            db, organization_id, start_date=start_date, end_date=end_date
        )
        return self._render_pdf("cash_flow", organization_id, db, ctx)

    def export_ias7_cash_flow_pdf(
        self,
        organization_id: str,
        db: Session,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> bytes:
        """Export IAS 7 cash flow statement as PDF."""
        ctx = ias7_cash_flow_context(
            db, organization_id, start_date=start_date, end_date=end_date
        )
        return self._render_pdf("ias7_cash_flow", organization_id, db, ctx)

    def export_changes_in_equity_pdf(
        self,
        organization_id: str,
        db: Session,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> bytes:
        """Export changes in equity as PDF."""
        ctx = changes_in_equity_context(
            db, organization_id, start_date=start_date, end_date=end_date
        )
        return self._render_pdf("changes_in_equity", organization_id, db, ctx)

    def export_budget_vs_actual_pdf(
        self,
        organization_id: str,
        db: Session,
        start_date: str | None = None,
        end_date: str | None = None,
        budget_id: str | None = None,
        budget_code: str | None = None,
    ) -> bytes:
        """Export budget vs actual as PDF."""
        ctx = budget_vs_actual_context(
            db,
            organization_id,
            start_date=start_date,
            end_date=end_date,
            budget_id=budget_id,
            budget_code=budget_code,
        )
        return self._render_pdf("budget_vs_actual", organization_id, db, ctx)

    def export_inventory_valuation_pdf(
        self,
        organization_id: str,
        db: Session,
    ) -> bytes:
        """Export inventory valuation reconciliation as PDF."""
        ctx = inventory_valuation_reconciliation_context(db, organization_id)
        return self._render_pdf(
            "inventory_valuation_reconciliation", organization_id, db, ctx
        )


reports_web_service = ReportsWebService()
