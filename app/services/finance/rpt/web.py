"""
Reports web view service.

Provides view-focused data for reports web routes.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

if TYPE_CHECKING:
    from app.web.deps import WebAuthContext

import logging

from fastapi import Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.finance.ap.supplier import Supplier
from app.models.finance.ap.supplier_invoice import (
    SupplierInvoice,
    SupplierInvoiceStatus,
)
from app.models.finance.ar.customer import Customer
from app.models.finance.ar.invoice import Invoice as ARInvoice
from app.models.finance.gl.account import Account
from app.models.finance.gl.account_category import AccountCategory, IFRSCategory
from app.models.finance.gl.fiscal_period import FiscalPeriod
from app.models.finance.gl.journal_entry import JournalEntry, JournalStatus
from app.models.finance.gl.journal_entry_line import JournalEntryLine
from app.models.finance.rpt.report_definition import ReportDefinition, ReportType
from app.models.finance.rpt.report_instance import ReportInstance
from app.services.common import coerce_uuid
from app.services.finance.platform.org_context import org_context_service
from app.services.finance.rpt.analysis_cube import AnalysisCubeService
from app.services.formatters import format_currency as _format_currency
from app.services.formatters import format_date as _format_date
from app.services.formatters import parse_date as _parse_date
from app.services.inventory.valuation_reconciliation import (
    ValuationReconciliationService,
)
from app.templates import templates

logger = logging.getLogger(__name__)

# NOTE: WebAuthContext and base_context are imported lazily inside response methods
# to avoid circular imports with app.web.deps


def _iso_date(d: date) -> str:
    """Format date as YYYY-MM-DD for HTML5 date inputs."""
    return d.isoformat()


def _build_csv(headers: list[str], rows: list[list[str]]) -> str:
    """Build a CSV string from headers and rows."""
    import csv
    import io

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(headers)
    writer.writerows(rows)
    return output.getvalue()


def _ifrs_label(category: IFRSCategory | str | None) -> str:
    label_map: dict[IFRSCategory, str] = {
        IFRSCategory.ASSETS: "Assets",
        IFRSCategory.LIABILITIES: "Liabilities",
        IFRSCategory.EQUITY: "Equity",
        IFRSCategory.REVENUE: "Revenue",
        IFRSCategory.EXPENSES: "Expenses",
        IFRSCategory.OTHER_COMPREHENSIVE_INCOME: "Other Comprehensive Income",
    }
    if category is None:
        return ""
    if isinstance(category, str) and not isinstance(category, IFRSCategory):
        try:
            category = IFRSCategory(category)
        except ValueError:
            return category.replace("_", " ").title()
    if isinstance(category, IFRSCategory):
        if category in label_map:
            return label_map[category]
        return str(category.value)
    return category


def _report_type_label(report_type: ReportType) -> str:
    labels: dict[ReportType, str] = {
        ReportType.BALANCE_SHEET: "Statement of Financial Position",
        ReportType.INCOME_STATEMENT: "Statement of Profit or Loss",
        ReportType.CASH_FLOW: "Cash Flow Statement",
        ReportType.CHANGES_IN_EQUITY: "Changes in Equity",
        ReportType.TRIAL_BALANCE: "Trial Balance",
        ReportType.GENERAL_LEDGER: "General Ledger",
        ReportType.SUBLEDGER: "Subledger",
        ReportType.AGING: "Aging Report",
        ReportType.BUDGET_VS_ACTUAL: "Budget vs Actual",
        ReportType.TAX: "Tax Report",
        ReportType.REGULATORY: "Regulatory Report",
        ReportType.CUSTOM: "Custom Report",
    }
    if report_type in labels:
        return labels[report_type]
    return str(report_type.value)


class ReportsWebService:
    """View service for reports web routes."""

    @staticmethod
    def _amount_from_category(
        ifrs_category: IFRSCategory,
        debit: Decimal,
        credit: Decimal,
    ) -> Decimal:
        if ifrs_category in {IFRSCategory.ASSETS, IFRSCategory.EXPENSES}:
            return debit - credit
        return credit - debit

    @staticmethod
    def _category_balances(
        db: Session,
        organization_id: str,
        start_date: date | None = None,
        end_date: date | None = None,
        as_of_date: date | None = None,
    ) -> dict:
        org_id = coerce_uuid(organization_id)

        stmt = (
            select(
                AccountCategory.category_code,
                AccountCategory.ifrs_category,
                func.coalesce(
                    func.sum(JournalEntryLine.debit_amount_functional), 0
                ).label("debit"),
                func.coalesce(
                    func.sum(JournalEntryLine.credit_amount_functional), 0
                ).label("credit"),
            )
            .join(Account, Account.category_id == AccountCategory.category_id)
            .join(JournalEntryLine, JournalEntryLine.account_id == Account.account_id)
            .join(
                JournalEntry,
                JournalEntry.journal_entry_id == JournalEntryLine.journal_entry_id,
            )
            .where(
                JournalEntry.organization_id == org_id,
                JournalEntry.status == JournalStatus.POSTED,
            )
        )

        if as_of_date:
            stmt = stmt.where(JournalEntry.posting_date <= as_of_date)
        else:
            if start_date:
                stmt = stmt.where(JournalEntry.posting_date >= start_date)
            if end_date:
                stmt = stmt.where(JournalEntry.posting_date <= end_date)

        rows = db.execute(
            stmt.group_by(
                AccountCategory.category_code,
                AccountCategory.ifrs_category,
            )
        ).all()

        balances = {}
        for code, ifrs_category, debit, credit in rows:
            debit = Decimal(str(debit or 0))
            credit = Decimal(str(credit or 0))
            balances[code] = {
                "ifrs_category": ifrs_category,
                "amount": ReportsWebService._amount_from_category(
                    ifrs_category, debit, credit
                ),
            }

        return balances

    @staticmethod
    def _tax_totals_from_gl(
        db: Session,
        organization_id: str,
        start_date: date,
        end_date: date,
    ) -> dict:
        """Aggregate tax totals from GL by querying the TAX-L category.

        Groups accounts by name pattern:
        - VAT/Output tax → output_tax (liability, credit-normal)
        - WHT → withholding (liability, credit-normal)
        - Everything else under TAX-L → input_tax proxy
        """
        org_id = coerce_uuid(organization_id)

        rows = db.execute(
            select(
                Account.account_code,
                Account.account_name,
                func.coalesce(
                    func.sum(JournalEntryLine.debit_amount_functional), 0
                ).label("debit"),
                func.coalesce(
                    func.sum(JournalEntryLine.credit_amount_functional), 0
                ).label("credit"),
            )
            .join(AccountCategory, Account.category_id == AccountCategory.category_id)
            .join(JournalEntryLine, JournalEntryLine.account_id == Account.account_id)
            .join(
                JournalEntry,
                JournalEntry.journal_entry_id == JournalEntryLine.journal_entry_id,
            )
            .where(
                JournalEntry.organization_id == org_id,
                JournalEntry.status == JournalStatus.POSTED,
                JournalEntry.posting_date >= start_date,
                JournalEntry.posting_date <= end_date,
                AccountCategory.category_code == "TAX-L",
            )
            .group_by(Account.account_code, Account.account_name)
        ).all()

        output_tax = Decimal("0")
        input_tax = Decimal("0")
        withholding = Decimal("0")

        for _code, name, debit, credit in rows:
            debit = Decimal(str(debit or 0))
            credit = Decimal(str(credit or 0))
            balance = credit - debit  # Liability accounts are credit-normal

            name_lower = (name or "").lower()
            if "vat" in name_lower or "output" in name_lower:
                output_tax += balance
            elif "wht" in name_lower or "withholding" in name_lower:
                withholding += balance
            else:
                # Other tax liabilities (income tax, education tax, etc.)
                input_tax += balance

        net_tax = output_tax - input_tax - withholding

        return {
            "output_tax": output_tax,
            "input_tax": input_tax,
            "withholding": withholding,
            "net_tax": net_tax,
        }

    @staticmethod
    def dashboard_context(
        db: Session,
        organization_id: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict:
        """Get context for reports dashboard with key summaries."""
        from app.models.finance.tax.tax_period import TaxPeriod, TaxPeriodStatus

        org_id = coerce_uuid(organization_id)
        today = date.today()

        # Parse date filters (default to current month)
        from_date = _parse_date(start_date) or today.replace(day=1)
        to_date = _parse_date(end_date) or today

        # ========== Financial Position Summary ==========
        period = db.scalars(
            select(FiscalPeriod)
            .where(
                FiscalPeriod.organization_id == org_id,
                FiscalPeriod.start_date <= to_date,
                FiscalPeriod.end_date >= to_date,
            )
            .order_by(FiscalPeriod.start_date.desc())
        ).first()

        total_assets = Decimal("0")
        total_liabilities = Decimal("0")
        total_equity = Decimal("0")
        total_revenue = Decimal("0")
        total_expenses = Decimal("0")

        bs_rows = db.execute(
            select(
                AccountCategory.ifrs_category,
                func.coalesce(
                    func.sum(JournalEntryLine.debit_amount_functional), 0
                ).label("debit"),
                func.coalesce(
                    func.sum(JournalEntryLine.credit_amount_functional), 0
                ).label("credit"),
            )
            .join(
                JournalEntry,
                JournalEntry.journal_entry_id == JournalEntryLine.journal_entry_id,
            )
            .join(Account, Account.account_id == JournalEntryLine.account_id)
            .join(AccountCategory, Account.category_id == AccountCategory.category_id)
            .where(
                JournalEntry.organization_id == org_id,
                JournalEntry.status == JournalStatus.POSTED,
                JournalEntry.posting_date <= to_date,
                AccountCategory.ifrs_category.in_(
                    [
                        IFRSCategory.ASSETS,
                        IFRSCategory.LIABILITIES,
                        IFRSCategory.EQUITY,
                    ]
                ),
            )
            .group_by(AccountCategory.ifrs_category)
        ).all()

        for ifrs_category, debit, credit in bs_rows:
            debit = Decimal(str(debit or 0))
            credit = Decimal(str(credit or 0))

            if ifrs_category == IFRSCategory.ASSETS:
                total_assets += debit - credit
            elif ifrs_category == IFRSCategory.LIABILITIES:
                total_liabilities += credit - debit
            elif ifrs_category == IFRSCategory.EQUITY:
                total_equity += credit - debit

        # ========== AP/AR Control Balances (GL Source of Truth) ==========
        ar_control_stmt = (
            select(
                func.coalesce(
                    func.sum(
                        JournalEntryLine.debit_amount_functional
                        - JournalEntryLine.credit_amount_functional
                    ),
                    0,
                )
            )
            .join(
                JournalEntry,
                JournalEntry.journal_entry_id == JournalEntryLine.journal_entry_id,
            )
            .join(Account, Account.account_id == JournalEntryLine.account_id)
            .where(
                JournalEntry.organization_id == org_id,
                JournalEntry.status == JournalStatus.POSTED,
                Account.subledger_type == "AR",
                Account.is_active.is_(True),
            )
        )
        ar_control_stmt = ar_control_stmt.where(JournalEntry.posting_date <= to_date)
        ar_total = Decimal(str(db.scalar(ar_control_stmt) or 0))

        ap_control_stmt = (
            select(
                func.coalesce(
                    func.sum(
                        JournalEntryLine.credit_amount_functional
                        - JournalEntryLine.debit_amount_functional
                    ),
                    0,
                )
            )
            .join(
                JournalEntry,
                JournalEntry.journal_entry_id == JournalEntryLine.journal_entry_id,
            )
            .join(Account, Account.account_id == JournalEntryLine.account_id)
            .where(
                JournalEntry.organization_id == org_id,
                JournalEntry.status == JournalStatus.POSTED,
                Account.subledger_type == "AP",
                Account.is_active.is_(True),
            )
        )
        ap_control_stmt = ap_control_stmt.where(JournalEntry.posting_date <= to_date)
        ap_total = Decimal(str(db.scalar(ap_control_stmt) or 0))

        # ========== Tax Summary (GL Source of Truth) ==========
        tax_totals = ReportsWebService._tax_totals_from_gl(
            db=db,
            organization_id=organization_id,
            start_date=from_date,
            end_date=to_date,
        )
        output_tax = tax_totals["output_tax"]
        input_tax = tax_totals["input_tax"]
        net_tax = tax_totals["net_tax"]

        # Get overdue tax periods
        overdue_tax_periods = (
            db.scalar(
                select(func.count(TaxPeriod.period_id)).where(
                    TaxPeriod.organization_id == org_id,
                    TaxPeriod.status == TaxPeriodStatus.OPEN,
                    TaxPeriod.due_date < today,
                )
            )
            or 0
        )

        pl_rows = db.execute(
            select(
                AccountCategory.ifrs_category,
                func.coalesce(
                    func.sum(JournalEntryLine.debit_amount_functional), 0
                ).label("debit"),
                func.coalesce(
                    func.sum(JournalEntryLine.credit_amount_functional), 0
                ).label("credit"),
            )
            .join(
                JournalEntry,
                JournalEntry.journal_entry_id == JournalEntryLine.journal_entry_id,
            )
            .join(Account, Account.account_id == JournalEntryLine.account_id)
            .join(AccountCategory, Account.category_id == AccountCategory.category_id)
            .where(
                JournalEntry.organization_id == org_id,
                JournalEntry.status == JournalStatus.POSTED,
                JournalEntry.posting_date >= from_date,
                JournalEntry.posting_date <= to_date,
                AccountCategory.ifrs_category.in_(
                    [
                        IFRSCategory.REVENUE,
                        IFRSCategory.EXPENSES,
                    ]
                ),
            )
            .group_by(AccountCategory.ifrs_category)
        ).all()

        total_revenue = Decimal("0")
        total_expenses = Decimal("0")
        for ifrs_category, debit, credit in pl_rows:
            debit = Decimal(str(debit or 0))
            credit = Decimal(str(credit or 0))
            if ifrs_category == IFRSCategory.REVENUE:
                total_revenue = credit - debit
            elif ifrs_category == IFRSCategory.EXPENSES:
                total_expenses = debit - credit

        net_income = total_revenue - total_expenses

        # Key metrics summary
        key_metrics = {
            "start_date": _format_date(from_date),
            "start_date_iso": _iso_date(from_date),
            "end_date": _format_date(to_date),
            "end_date_iso": _iso_date(to_date),
            "total_assets": _format_currency(total_assets),
            "total_assets_raw": float(total_assets),
            "total_liabilities": _format_currency(total_liabilities),
            "total_liabilities_raw": float(total_liabilities),
            "total_equity": _format_currency(total_equity),
            "total_equity_raw": float(total_equity),
            "total_revenue": _format_currency(total_revenue),
            "total_revenue_raw": float(total_revenue),
            "total_expenses": _format_currency(total_expenses),
            "total_expenses_raw": float(total_expenses),
            "net_income": _format_currency(net_income),
            "net_income_raw": float(net_income),
            "is_profit": net_income >= 0,
            "ap_total": _format_currency(ap_total),
            "ap_total_raw": float(ap_total),
            "ar_total": _format_currency(ar_total),
            "ar_total_raw": float(ar_total),
            "output_tax": _format_currency(output_tax),
            "output_tax_raw": float(output_tax),
            "input_tax": _format_currency(input_tax),
            "input_tax_raw": float(input_tax),
            "net_tax": _format_currency(net_tax),
            "net_tax_raw": float(net_tax),
            "is_tax_payable": net_tax > 0,
            "overdue_tax_periods": overdue_tax_periods,
            "period_name": period.period_name if period else "No Active Period",
            "as_of_date": _format_date(to_date),
            "as_of_date_iso": _iso_date(to_date),
        }

        # Get report definitions
        definitions = db.scalars(
            select(ReportDefinition)
            .where(
                ReportDefinition.organization_id == org_id,
                ReportDefinition.is_active.is_(True),
            )
            .order_by(ReportDefinition.report_name)
        ).all()

        # Recent report instances
        recent_instances = db.execute(
            select(ReportInstance, ReportDefinition)
            .join(
                ReportDefinition,
                ReportInstance.report_def_id == ReportDefinition.report_def_id,
            )
            .where(ReportInstance.organization_id == org_id)
            .order_by(ReportInstance.queued_at.desc())
            .limit(10)
        ).all()

        # Group reports by category
        financial_statements = []
        operational_reports = []
        compliance_reports = []

        for defn in definitions:
            report_view = {
                "report_def_id": str(defn.report_def_id),
                "report_code": defn.report_code,
                "report_name": defn.report_name,
                "description": defn.description or "",
                "report_type": defn.report_type.value,
                "report_type_label": _report_type_label(defn.report_type),
                "category": defn.category or "general",
            }

            if defn.report_type in [
                ReportType.BALANCE_SHEET,
                ReportType.INCOME_STATEMENT,
                ReportType.CASH_FLOW,
                ReportType.CHANGES_IN_EQUITY,
                ReportType.TRIAL_BALANCE,
            ]:
                financial_statements.append(report_view)
            elif defn.report_type in [ReportType.TAX, ReportType.REGULATORY]:
                compliance_reports.append(report_view)
            else:
                operational_reports.append(report_view)

        recent_view = []
        for instance, defn in recent_instances:
            recent_view.append(
                {
                    "instance_id": str(instance.instance_id),
                    "report_name": defn.report_name,
                    "report_type": defn.report_type.value,
                    "status": instance.status.value,
                    "queued_at": instance.queued_at.strftime("%Y-%m-%d %H:%M")
                    if instance.queued_at
                    else "",
                    "output_format": instance.output_format,
                }
            )

        # Standard reports always available (using IFRS terminology)
        standard_reports = [
            {
                "name": "Trial Balance",
                "description": "View account balances as of a specific date",
                "url": "/finance/reports/trial-balance",
                "icon": "scale",
            },
            {
                "name": "Statement of Profit or Loss",
                "description": "Revenue and expenses for a period",
                "url": "/finance/reports/income-statement",
                "icon": "trending-up",
            },
            {
                "name": "Statement of Financial Position",
                "description": "Assets, liabilities, and equity",
                "url": "/finance/reports/balance-sheet",
                "icon": "layers",
            },
            {
                "name": "AP Aging",
                "description": "Accounts payable aging analysis",
                "url": "/finance/reports/ap-aging",
                "icon": "clock",
            },
            {
                "name": "AR Aging",
                "description": "Accounts receivable aging analysis",
                "url": "/finance/reports/ar-aging",
                "icon": "users",
            },
            {
                "name": "General Ledger",
                "description": "Detailed account transactions",
                "url": "/finance/reports/general-ledger",
                "icon": "book-open",
            },
            {
                "name": "Tax Summary",
                "description": "Tax collected, paid, and net position",
                "url": "/finance/reports/tax-summary",
                "icon": "receipt",
            },
            {
                "name": "Expense Summary",
                "description": "Expense breakdown by category",
                "url": "/finance/reports/expense-summary",
                "icon": "credit-card",
            },
            {
                "name": "Cash Flow Statement",
                "description": "Cash inflows, outflows, and net movement",
                "url": "/finance/reports/cash-flow",
                "icon": "activity",
            },
            {
                "name": "Changes in Equity",
                "description": "Equity roll-forward for the period",
                "url": "/finance/reports/changes-in-equity",
                "icon": "trending-up",
            },
            {
                "name": "Budget vs Actual",
                "description": "Budget performance against actuals",
                "url": "/finance/reports/budget-vs-actual",
                "icon": "bar-chart-2",
            },
        ]

        return {
            "key_metrics": key_metrics,
            "standard_reports": standard_reports,
            "financial_statements": financial_statements,
            "operational_reports": operational_reports,
            "compliance_reports": compliance_reports,
            "recent_reports": recent_view,
        }

    @staticmethod
    def trial_balance_context(
        db: Session,
        organization_id: str,
        as_of_date: str | None = None,
    ) -> dict:
        """Get context for trial balance report."""
        org_id = coerce_uuid(organization_id)
        ref_date = _parse_date(as_of_date) or date.today()

        # Find the fiscal period for the date
        period = db.scalars(
            select(FiscalPeriod)
            .where(
                FiscalPeriod.organization_id == org_id,
                FiscalPeriod.start_date <= ref_date,
                FiscalPeriod.end_date >= ref_date,
            )
            .order_by(FiscalPeriod.start_date.desc())
        ).first()

        if not period:
            period = db.scalars(
                select(FiscalPeriod)
                .where(FiscalPeriod.organization_id == org_id)
                .order_by(FiscalPeriod.end_date.desc())
            ).first()

        balances = []
        total_debit = Decimal("0")
        total_credit = Decimal("0")

        # Group by IFRS category
        assets = []
        liabilities = []
        equity = []
        revenue = []
        expenses = []

        rows = db.execute(
            select(
                Account.account_code,
                Account.account_name,
                AccountCategory.ifrs_category,
                func.coalesce(
                    func.sum(JournalEntryLine.debit_amount_functional), 0
                ).label("debit"),
                func.coalesce(
                    func.sum(JournalEntryLine.credit_amount_functional), 0
                ).label("credit"),
            )
            .join(
                JournalEntry,
                JournalEntry.journal_entry_id == JournalEntryLine.journal_entry_id,
            )
            .join(Account, Account.account_id == JournalEntryLine.account_id)
            .join(AccountCategory, Account.category_id == AccountCategory.category_id)
            .where(
                JournalEntry.organization_id == org_id,
                JournalEntry.status == JournalStatus.POSTED,
                JournalEntry.posting_date <= ref_date,
            )
            .group_by(
                Account.account_code,
                Account.account_name,
                AccountCategory.ifrs_category,
            )
            .order_by(Account.account_code)
        ).all()

        for account_code, account_name, ifrs_category, debit, credit in rows:
            debit = Decimal(str(debit or 0))
            credit = Decimal(str(credit or 0))
            total_debit += debit
            total_credit += credit

            # If account_code is truncated (exactly 20 chars = VARCHAR limit),
            # hide it and show only account_name.
            display_code = account_code if len(account_code) < 20 else ""

            entry = {
                "account_code": display_code,
                "account_name": account_name,
                "debit": _format_currency(debit) if debit else "",
                "credit": _format_currency(credit) if credit else "",
                "debit_raw": float(debit),
                "credit_raw": float(credit),
            }

            if ifrs_category == IFRSCategory.ASSETS:
                assets.append(entry)
            elif ifrs_category == IFRSCategory.LIABILITIES:
                liabilities.append(entry)
            elif ifrs_category == IFRSCategory.EQUITY:
                equity.append(entry)
            elif ifrs_category == IFRSCategory.REVENUE:
                revenue.append(entry)
            elif ifrs_category == IFRSCategory.EXPENSES:
                expenses.append(entry)
            else:
                balances.append(entry)

        return {
            "as_of_date": _format_date(ref_date),
            "as_of_date_iso": _iso_date(ref_date),
            "period_name": period.period_name if period else "No Period",
            "assets": assets,
            "liabilities": liabilities,
            "equity": equity,
            "revenue": revenue,
            "expenses": expenses,
            "other_balances": balances,
            "total_debit": _format_currency(total_debit),
            "total_credit": _format_currency(total_credit),
            "is_balanced": round(total_debit, 2) == round(total_credit, 2),
        }

    @staticmethod
    def income_statement_context(
        db: Session,
        organization_id: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict:
        """Get context for income statement report."""
        org_id = coerce_uuid(organization_id)

        # Default to current month
        today = date.today()
        from_date = _parse_date(start_date) or today.replace(day=1)
        to_date = _parse_date(end_date) or today

        # Find fiscal period
        period = db.scalars(
            select(FiscalPeriod)
            .where(
                FiscalPeriod.organization_id == org_id,
                FiscalPeriod.start_date <= to_date,
                FiscalPeriod.end_date >= from_date,
            )
            .order_by(FiscalPeriod.start_date.desc())
        ).first()

        balances = ReportsWebService._category_balances(
            db=db,
            organization_id=organization_id,
            start_date=from_date,
            end_date=to_date,
        )

        def cat_amount(code: str) -> Decimal:
            return cast(Decimal, balances.get(code, {}).get("amount", Decimal("0")))

        revenue = cat_amount("REV")
        other_income = Decimal("0")
        cogs = cat_amount("COS")
        operating_expenses = cat_amount("EXP")
        profit_for_period = revenue + other_income - cogs - operating_expenses
        oci = cat_amount("OCI")
        total_comprehensive_income = profit_for_period + oci

        income_statement_lines = [
            {
                "name": "Revenue",
                "amount": _format_currency(revenue),
                "amount_raw": float(revenue),
            },
            {
                "name": "Other Income",
                "amount": _format_currency(other_income),
                "amount_raw": float(other_income),
            },
            {
                "name": "Cost of Sales",
                "amount": _format_currency(cogs),
                "amount_raw": float(cogs),
            },
            {
                "name": "Operating Expenses",
                "amount": _format_currency(operating_expenses),
                "amount_raw": float(operating_expenses),
            },
            {
                "name": "Profit for the Period",
                "amount": _format_currency(profit_for_period),
                "amount_raw": float(profit_for_period),
            },
            {
                "name": "Other Comprehensive Income",
                "amount": _format_currency(oci),
                "amount_raw": float(oci),
            },
            {
                "name": "Total Comprehensive Income",
                "amount": _format_currency(total_comprehensive_income),
                "amount_raw": float(total_comprehensive_income),
            },
        ]

        return {
            "start_date": _format_date(from_date),
            "start_date_iso": _iso_date(from_date),
            "end_date": _format_date(to_date),
            "end_date_iso": _iso_date(to_date),
            "period_name": period.period_name if period else "No Period",
            "income_statement_lines": income_statement_lines,
            "total_revenue": _format_currency(revenue + other_income),
            "total_expenses": _format_currency(cogs + operating_expenses),
            "net_income": _format_currency(profit_for_period),
            "net_income_raw": float(profit_for_period),
            "is_profit": profit_for_period >= 0,
        }

    @staticmethod
    def balance_sheet_context(
        db: Session,
        organization_id: str,
        as_of_date: str | None = None,
    ) -> dict:
        """Get context for balance sheet report."""
        org_id = coerce_uuid(organization_id)
        ref_date = _parse_date(as_of_date) or date.today()

        # Find fiscal period
        period = db.scalars(
            select(FiscalPeriod)
            .where(
                FiscalPeriod.organization_id == org_id,
                FiscalPeriod.start_date <= ref_date,
                FiscalPeriod.end_date >= ref_date,
            )
            .order_by(FiscalPeriod.start_date.desc())
        ).first()

        if not period:
            period = db.scalars(
                select(FiscalPeriod)
                .where(FiscalPeriod.organization_id == org_id)
                .order_by(FiscalPeriod.end_date.desc())
            ).first()

        balances = ReportsWebService._category_balances(
            db=db,
            organization_id=organization_id,
            as_of_date=ref_date,
        )

        def cat_amount(code: str) -> Decimal:
            return cast(Decimal, balances.get(code, {}).get("amount", Decimal("0")))

        current_assets = [
            ("Cash and Cash Equivalents", cat_amount("CASH") + cat_amount("BANK")),
            ("Accounts Receivable", cat_amount("AR")),
            ("Inventory", cat_amount("INV")),
            ("Other Current Assets", cat_amount("AST") + cat_amount("ASSETS")),
        ]
        non_current_assets = [
            ("Property, Plant and Equipment", cat_amount("FA") + cat_amount("FA-AD")),
        ]
        current_liabilities = [
            ("Accounts Payable", cat_amount("AP")),
            ("Tax Liabilities", cat_amount("TAX-L")),
            ("Other Current Liabilities", cat_amount("LIA")),
        ]
        non_current_liabilities = [
            ("Long-term Liabilities", cat_amount("LTL")),
        ]
        equity_lines = [
            ("Share Capital", cat_amount("EQ")),
            ("Retained Earnings", cat_amount("RE")),
            ("Other Equity", cat_amount("EQT")),
        ]

        total_assets = sum(
            (amount for _, amount in current_assets), Decimal("0")
        ) + sum((amount for _, amount in non_current_assets), Decimal("0"))
        total_liabilities = sum(
            (amount for _, amount in current_liabilities), Decimal("0")
        ) + sum((amount for _, amount in non_current_liabilities), Decimal("0"))
        total_equity = sum((amount for _, amount in equity_lines), Decimal("0"))
        total_liabilities_equity = total_liabilities + total_equity

        balance_sheet_lines = {
            "current_assets": [
                {
                    "name": name,
                    "amount": _format_currency(amount),
                    "amount_raw": float(amount),
                }
                for name, amount in current_assets
            ],
            "non_current_assets": [
                {
                    "name": name,
                    "amount": _format_currency(amount),
                    "amount_raw": float(amount),
                }
                for name, amount in non_current_assets
            ],
            "current_liabilities": [
                {
                    "name": name,
                    "amount": _format_currency(amount),
                    "amount_raw": float(amount),
                }
                for name, amount in current_liabilities
            ],
            "non_current_liabilities": [
                {
                    "name": name,
                    "amount": _format_currency(amount),
                    "amount_raw": float(amount),
                }
                for name, amount in non_current_liabilities
            ],
            "equity": [
                {
                    "name": name,
                    "amount": _format_currency(amount),
                    "amount_raw": float(amount),
                }
                for name, amount in equity_lines
            ],
        }

        return {
            "as_of_date": _format_date(ref_date),
            "as_of_date_iso": _iso_date(ref_date),
            "period_name": period.period_name if period else "No Period",
            "balance_sheet_lines": balance_sheet_lines,
            "total_assets": _format_currency(total_assets),
            "total_liabilities": _format_currency(total_liabilities),
            "total_equity": _format_currency(total_equity),
            "total_liabilities_equity": _format_currency(total_liabilities_equity),
            "is_balanced": round(total_assets, 2) == round(total_liabilities_equity, 2),
        }

    @staticmethod
    def ap_aging_context(
        db: Session,
        organization_id: str,
        as_of_date: str | None = None,
    ) -> dict:
        """Get context for AP aging report."""
        org_id = coerce_uuid(organization_id)
        ref_date = _parse_date(as_of_date) or date.today()

        # Get open invoices
        invoices = db.execute(
            select(SupplierInvoice, Supplier)
            .join(Supplier, SupplierInvoice.supplier_id == Supplier.supplier_id)
            .where(
                SupplierInvoice.organization_id == org_id,
                SupplierInvoice.status.in_(
                    [
                        SupplierInvoiceStatus.POSTED,
                        SupplierInvoiceStatus.PARTIALLY_PAID,
                    ]
                ),
                SupplierInvoice.invoice_date <= ref_date,
            )
            .order_by(SupplierInvoice.due_date)
        ).all()

        # Aging buckets
        current = []
        days_1_30 = []
        days_31_60 = []
        days_61_90 = []
        over_90 = []

        total_current = Decimal("0")
        total_1_30 = Decimal("0")
        total_31_60 = Decimal("0")
        total_61_90 = Decimal("0")
        total_over_90 = Decimal("0")

        for invoice, supplier in invoices:
            due_date = invoice.due_date
            balance = invoice.balance_due or Decimal("0")

            if not due_date:
                continue

            days_overdue = (ref_date - due_date).days

            entry = {
                "invoice_number": invoice.invoice_number,
                "supplier_name": supplier.trading_name or supplier.legal_name,
                "invoice_date": _format_date(invoice.invoice_date),
                "due_date": _format_date(due_date),
                "amount": _format_currency(balance, invoice.currency_code),
                "amount_raw": float(balance),
                "days_overdue": max(0, days_overdue),
            }

            if days_overdue <= 0:
                current.append(entry)
                total_current += balance
            elif days_overdue <= 30:
                days_1_30.append(entry)
                total_1_30 += balance
            elif days_overdue <= 60:
                days_31_60.append(entry)
                total_31_60 += balance
            elif days_overdue <= 90:
                days_61_90.append(entry)
                total_61_90 += balance
            else:
                over_90.append(entry)
                total_over_90 += balance

        grand_total = (
            total_current + total_1_30 + total_31_60 + total_61_90 + total_over_90
        )

        return {
            "as_of_date": _format_date(ref_date),
            "as_of_date_iso": _iso_date(ref_date),
            "current": current,
            "days_1_30": days_1_30,
            "days_31_60": days_31_60,
            "days_61_90": days_61_90,
            "over_90": over_90,
            "total_current": _format_currency(total_current),
            "total_1_30": _format_currency(total_1_30),
            "total_31_60": _format_currency(total_31_60),
            "total_61_90": _format_currency(total_61_90),
            "total_over_90": _format_currency(total_over_90),
            "grand_total": _format_currency(grand_total),
            "summary": [
                {
                    "bucket": "Current",
                    "amount": _format_currency(total_current),
                    "amount_raw": float(total_current),
                },
                {
                    "bucket": "1-30 Days",
                    "amount": _format_currency(total_1_30),
                    "amount_raw": float(total_1_30),
                },
                {
                    "bucket": "31-60 Days",
                    "amount": _format_currency(total_31_60),
                    "amount_raw": float(total_31_60),
                },
                {
                    "bucket": "61-90 Days",
                    "amount": _format_currency(total_61_90),
                    "amount_raw": float(total_61_90),
                },
                {
                    "bucket": "Over 90 Days",
                    "amount": _format_currency(total_over_90),
                    "amount_raw": float(total_over_90),
                },
            ],
        }

    @staticmethod
    def ar_aging_context(
        db: Session,
        organization_id: str,
        as_of_date: str | None = None,
    ) -> dict:
        """Get context for AR aging report."""
        org_id = coerce_uuid(organization_id)
        ref_date = _parse_date(as_of_date) or date.today()

        # Get open invoices
        from app.models.finance.ar.invoice import InvoiceStatus as ARInvoiceStatus

        invoices = db.execute(
            select(ARInvoice, Customer)
            .join(Customer, ARInvoice.customer_id == Customer.customer_id)
            .where(
                ARInvoice.organization_id == org_id,
                ARInvoice.status.in_(
                    [
                        ARInvoiceStatus.POSTED,
                        ARInvoiceStatus.PARTIALLY_PAID,
                    ]
                ),
                ARInvoice.invoice_date <= ref_date,
            )
            .order_by(ARInvoice.due_date)
        ).all()

        # Aging buckets
        current = []
        days_1_30 = []
        days_31_60 = []
        days_61_90 = []
        over_90 = []

        total_current = Decimal("0")
        total_1_30 = Decimal("0")
        total_31_60 = Decimal("0")
        total_61_90 = Decimal("0")
        total_over_90 = Decimal("0")

        for invoice, customer in invoices:
            due_date = invoice.due_date
            balance = invoice.balance_due or Decimal("0")

            if not due_date:
                continue

            days_overdue = (ref_date - due_date).days

            entry = {
                "invoice_number": invoice.invoice_number,
                "customer_name": customer.trading_name or customer.legal_name,
                "invoice_date": _format_date(invoice.invoice_date),
                "due_date": _format_date(due_date),
                "amount": _format_currency(balance, invoice.currency_code),
                "amount_raw": float(balance),
                "days_overdue": max(0, days_overdue),
            }

            if days_overdue <= 0:
                current.append(entry)
                total_current += balance
            elif days_overdue <= 30:
                days_1_30.append(entry)
                total_1_30 += balance
            elif days_overdue <= 60:
                days_31_60.append(entry)
                total_31_60 += balance
            elif days_overdue <= 90:
                days_61_90.append(entry)
                total_61_90 += balance
            else:
                over_90.append(entry)
                total_over_90 += balance

        grand_total = (
            total_current + total_1_30 + total_31_60 + total_61_90 + total_over_90
        )

        return {
            "as_of_date": _format_date(ref_date),
            "as_of_date_iso": _iso_date(ref_date),
            "current": current,
            "days_1_30": days_1_30,
            "days_31_60": days_31_60,
            "days_61_90": days_61_90,
            "over_90": over_90,
            "total_current": _format_currency(total_current),
            "total_1_30": _format_currency(total_1_30),
            "total_31_60": _format_currency(total_31_60),
            "total_61_90": _format_currency(total_61_90),
            "total_over_90": _format_currency(total_over_90),
            "grand_total": _format_currency(grand_total),
            "summary": [
                {
                    "bucket": "Current",
                    "amount": _format_currency(total_current),
                    "amount_raw": float(total_current),
                },
                {
                    "bucket": "1-30 Days",
                    "amount": _format_currency(total_1_30),
                    "amount_raw": float(total_1_30),
                },
                {
                    "bucket": "31-60 Days",
                    "amount": _format_currency(total_31_60),
                    "amount_raw": float(total_31_60),
                },
                {
                    "bucket": "61-90 Days",
                    "amount": _format_currency(total_61_90),
                    "amount_raw": float(total_61_90),
                },
                {
                    "bucket": "Over 90 Days",
                    "amount": _format_currency(total_over_90),
                    "amount_raw": float(total_over_90),
                },
            ],
        }

    @staticmethod
    def general_ledger_context(
        db: Session,
        organization_id: str,
        account_id: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict:
        """Get context for general ledger detail report."""
        from app.models.finance.gl.journal_entry import JournalEntry

        org_id = coerce_uuid(organization_id)

        # Default to current month
        today = date.today()
        from_date = _parse_date(start_date) or today.replace(day=1)
        to_date = _parse_date(end_date) or today

        # Get accounts for dropdown
        accounts = db.scalars(
            select(Account)
            .where(
                Account.organization_id == org_id,
                Account.is_active.is_(True),
            )
            .order_by(Account.account_code)
        ).all()

        account_options = [
            {
                "account_id": str(acct.account_id),
                "account_code": acct.account_code
                if len(acct.account_code) < 20
                else "",
                "account_name": acct.account_name,
            }
            for acct in accounts
        ]

        transactions = []
        selected_account = None
        running_balance = Decimal("0")

        if account_id:
            acct_id = coerce_uuid(account_id)
            selected_account = db.get(Account, acct_id)

            if selected_account and selected_account.organization_id == org_id:
                # Get journal lines for this account
                lines = db.execute(
                    select(JournalEntryLine, JournalEntry)
                    .join(
                        JournalEntry,
                        JournalEntry.journal_entry_id
                        == JournalEntryLine.journal_entry_id,
                    )
                    .where(
                        JournalEntryLine.account_id == acct_id,
                        JournalEntry.organization_id == org_id,
                        JournalEntry.status == JournalStatus.POSTED,
                        JournalEntry.posting_date >= from_date,
                        JournalEntry.posting_date <= to_date,
                    )
                    .order_by(JournalEntry.posting_date, JournalEntry.journal_entry_id)
                ).all()

                for line, entry in lines:
                    debit = line.debit_amount_functional or Decimal("0")
                    credit = line.credit_amount_functional or Decimal("0")

                    # Calculate running balance based on normal balance
                    if selected_account.normal_balance.value == "DEBIT":
                        running_balance += debit - credit
                    else:
                        running_balance += credit - debit

                    transactions.append(
                        {
                            "date": _format_date(entry.posting_date),
                            "journal_number": entry.journal_number,
                            "description": line.description or entry.description,
                            "reference": entry.reference or "",
                            "debit": _format_currency(debit) if debit else "",
                            "credit": _format_currency(credit) if credit else "",
                            "balance": _format_currency(running_balance),
                        }
                    )

        return {
            "start_date": _format_date(from_date),
            "start_date_iso": _iso_date(from_date),
            "end_date": _format_date(to_date),
            "end_date_iso": _iso_date(to_date),
            "account_id": account_id,
            "accounts": account_options,
            "selected_account": {
                "account_code": selected_account.account_code,
                "account_name": selected_account.account_name,
            }
            if selected_account
            else None,
            "transactions": transactions,
            "ending_balance": _format_currency(running_balance),
        }

    @staticmethod
    def tax_summary_context(
        db: Session,
        organization_id: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict:
        """Get context for tax summary report."""
        today = date.today()
        from_date = _parse_date(start_date) or today.replace(day=1)
        to_date = _parse_date(end_date) or today

        tax_totals = ReportsWebService._tax_totals_from_gl(
            db=db,
            organization_id=organization_id,
            start_date=from_date,
            end_date=to_date,
        )

        output_tax = tax_totals["output_tax"]
        input_tax = tax_totals["input_tax"]
        withholding = tax_totals["withholding"]
        net_tax = tax_totals["net_tax"]
        payments = Decimal("0")

        tax_breakdown = [
            {
                "tax_type": "Output Tax",
                "output": _format_currency(output_tax),
                "input": _format_currency(Decimal("0")),
                "net": _format_currency(output_tax),
                "net_raw": float(output_tax),
            },
            {
                "tax_type": "Input Tax",
                "output": _format_currency(Decimal("0")),
                "input": _format_currency(input_tax),
                "net": _format_currency(-input_tax),
                "net_raw": float(-input_tax),
            },
            {
                "tax_type": "Withholding Tax",
                "output": _format_currency(Decimal("0")),
                "input": _format_currency(withholding),
                "net": _format_currency(-withholding),
                "net_raw": float(-withholding),
            },
        ]

        upcoming_deadlines: list[dict[str, Any]] = []

        return {
            "start_date": _format_date(from_date),
            "start_date_iso": _iso_date(from_date),
            "end_date": _format_date(to_date),
            "end_date_iso": _iso_date(to_date),
            "output_tax": _format_currency(output_tax),
            "output_tax_raw": float(output_tax),
            "input_tax": _format_currency(input_tax),
            "input_tax_raw": float(input_tax),
            "net_tax": _format_currency(net_tax),
            "net_tax_raw": float(net_tax),
            "is_payable": net_tax > 0,
            "withholding": _format_currency(withholding),
            "payments": _format_currency(payments),
            "tax_breakdown": tax_breakdown,
            "upcoming_deadlines": upcoming_deadlines,
        }

    @staticmethod
    def expense_summary_context(
        db: Session,
        organization_id: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict:
        """Get context for expense summary report."""
        org_id = coerce_uuid(organization_id)
        presentation_currency_code = org_context_service.get_presentation_currency(
            db,
            org_id,
        )
        today = date.today()
        from_date = _parse_date(start_date) or today.replace(day=1)
        to_date = _parse_date(end_date) or today

        expense_items = []
        total_expenses = Decimal("0")

        # Aggregate posted ledger lines within the date range for expense accounts.
        expense_rows = db.execute(
            select(
                Account.account_code,
                Account.account_name,
                AccountCategory.category_name,
                func.coalesce(
                    func.sum(JournalEntryLine.debit_amount_functional), 0
                ).label("debit"),
                func.coalesce(
                    func.sum(JournalEntryLine.credit_amount_functional), 0
                ).label("credit"),
            )
            .join(
                JournalEntry,
                JournalEntry.journal_entry_id == JournalEntryLine.journal_entry_id,
            )
            .join(Account, JournalEntryLine.account_id == Account.account_id)
            .join(AccountCategory, Account.category_id == AccountCategory.category_id)
            .where(
                JournalEntry.organization_id == org_id,
                JournalEntry.status == JournalStatus.POSTED,
                JournalEntry.posting_date >= from_date,
                JournalEntry.posting_date <= to_date,
                AccountCategory.ifrs_category == IFRSCategory.EXPENSES,
            )
            .group_by(
                Account.account_code,
                Account.account_name,
                AccountCategory.category_name,
            )
            .order_by(Account.account_code)
        ).all()

        for account_code, account_name, category_name, debit, credit in expense_rows:
            debit = Decimal(str(debit or 0))
            credit = Decimal(str(credit or 0))
            amount = debit - credit
            total_expenses += amount
            expense_items.append(
                {
                    "account_code": account_code,
                    "account_name": account_name,
                    "category": category_name,
                    "amount": _format_currency(amount),
                    "amount_raw": float(amount),
                }
            )

        # Sort by amount descending
        expense_items.sort(key=lambda x: x["amount_raw"], reverse=True)

        # Top 5 expense categories
        top_expenses = expense_items[:5]

        return {
            "start_date": _format_date(from_date),
            "start_date_iso": _iso_date(from_date),
            "end_date": _format_date(to_date),
            "end_date_iso": _iso_date(to_date),
            "expense_items": expense_items,
            "top_expenses": top_expenses,
            "total_expenses": _format_currency(total_expenses),
            "total_expenses_raw": float(total_expenses),
            "presentation_currency_code": presentation_currency_code,
        }

    @staticmethod
    def cash_flow_context(
        db: Session,
        organization_id: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict:
        """Get context for cash flow summary report."""
        org_id = coerce_uuid(organization_id)
        today = date.today()
        from_date = _parse_date(start_date) or today.replace(day=1)
        to_date = _parse_date(end_date) or today

        cash_category_codes = {"CASH", "BANK"}
        cash_category_ids = db.scalars(
            select(AccountCategory.category_id).where(
                AccountCategory.organization_id == org_id,
                AccountCategory.category_code.in_(cash_category_codes),
            )
        ).all()

        cash_accounts = db.execute(
            select(Account.account_id, Account.account_code, Account.account_name)
            .where(
                Account.organization_id == org_id,
                Account.category_id.in_(cash_category_ids),
                Account.is_active.is_(True),
            )
            .order_by(Account.account_code)
        ).all()
        account_ids = [row.account_id for row in cash_accounts]

        movements = []
        total_inflow = Decimal("0")
        total_outflow = Decimal("0")

        if account_ids:
            rows = db.execute(
                select(
                    Account.account_id,
                    func.coalesce(
                        func.sum(JournalEntryLine.debit_amount_functional), 0
                    ).label("debit"),
                    func.coalesce(
                        func.sum(JournalEntryLine.credit_amount_functional), 0
                    ).label("credit"),
                )
                .join(
                    JournalEntry,
                    JournalEntry.journal_entry_id == JournalEntryLine.journal_entry_id,
                )
                .join(Account, Account.account_id == JournalEntryLine.account_id)
                .where(
                    JournalEntry.organization_id == org_id,
                    JournalEntry.status == JournalStatus.POSTED,
                    JournalEntry.posting_date >= from_date,
                    JournalEntry.posting_date <= to_date,
                    JournalEntryLine.account_id.in_(account_ids),
                )
                .group_by(Account.account_id)
            ).all()

            acct_map = {row.account_id: row for row in cash_accounts}
            for account_id, debit, credit in rows:
                debit = Decimal(str(debit or 0))
                credit = Decimal(str(credit or 0))
                inflow = debit
                outflow = credit
                total_inflow += inflow
                total_outflow += outflow

                account = acct_map.get(account_id)
                movements.append(
                    {
                        "account_code": account.account_code if account else "",
                        "account_name": account.account_name if account else "",
                        "inflow": _format_currency(inflow),
                        "outflow": _format_currency(outflow),
                        "net": _format_currency(inflow - outflow),
                        "net_raw": float(inflow - outflow),
                    }
                )

        net_cash = total_inflow - total_outflow

        return {
            "start_date": _format_date(from_date),
            "start_date_iso": _iso_date(from_date),
            "end_date": _format_date(to_date),
            "end_date_iso": _iso_date(to_date),
            "cash_movements": movements,
            "total_inflow": _format_currency(total_inflow),
            "total_outflow": _format_currency(total_outflow),
            "net_cash_flow": _format_currency(net_cash),
            "net_cash_flow_raw": float(net_cash),
        }

    @staticmethod
    def changes_in_equity_context(
        db: Session,
        organization_id: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict:
        """Get context for statement of changes in equity."""
        org_id = coerce_uuid(organization_id)
        today = date.today()
        from_date = _parse_date(start_date) or today.replace(day=1)
        to_date = _parse_date(end_date) or today

        equity_rows = db.execute(
            select(
                Account.account_id,
                Account.account_code,
                Account.account_name,
                func.coalesce(
                    func.sum(JournalEntryLine.debit_amount_functional), 0
                ).label("debit"),
                func.coalesce(
                    func.sum(JournalEntryLine.credit_amount_functional), 0
                ).label("credit"),
            )
            .join(
                JournalEntry,
                JournalEntry.journal_entry_id == JournalEntryLine.journal_entry_id,
            )
            .join(Account, Account.account_id == JournalEntryLine.account_id)
            .join(AccountCategory, Account.category_id == AccountCategory.category_id)
            .where(
                JournalEntry.organization_id == org_id,
                JournalEntry.status == JournalStatus.POSTED,
                JournalEntry.posting_date >= from_date,
                JournalEntry.posting_date <= to_date,
                AccountCategory.ifrs_category == IFRSCategory.EQUITY,
            )
            .group_by(Account.account_id, Account.account_code, Account.account_name)
            .order_by(Account.account_code)
        ).all()

        opening_rows = db.execute(
            select(
                Account.account_id,
                func.coalesce(
                    func.sum(JournalEntryLine.debit_amount_functional), 0
                ).label("debit"),
                func.coalesce(
                    func.sum(JournalEntryLine.credit_amount_functional), 0
                ).label("credit"),
            )
            .join(
                JournalEntry,
                JournalEntry.journal_entry_id == JournalEntryLine.journal_entry_id,
            )
            .join(Account, Account.account_id == JournalEntryLine.account_id)
            .join(AccountCategory, Account.category_id == AccountCategory.category_id)
            .where(
                JournalEntry.organization_id == org_id,
                JournalEntry.status == JournalStatus.POSTED,
                JournalEntry.posting_date < from_date,
                AccountCategory.ifrs_category == IFRSCategory.EQUITY,
            )
            .group_by(Account.account_id)
        ).all()
        opening_map = {row.account_id: (row.debit, row.credit) for row in opening_rows}

        line_items = []
        total_opening = Decimal("0")
        total_change = Decimal("0")
        total_closing = Decimal("0")

        for account_id, code, name, debit, credit in equity_rows:
            debit = Decimal(str(debit or 0))
            credit = Decimal(str(credit or 0))
            opening_debit, opening_credit = opening_map.get(account_id, (0, 0))
            opening = Decimal(str(opening_credit or 0)) - Decimal(
                str(opening_debit or 0)
            )
            change = credit - debit
            closing = opening + change

            total_opening += opening
            total_change += change
            total_closing += closing

            line_items.append(
                {
                    "account_code": code,
                    "account_name": name,
                    "opening_balance": _format_currency(opening),
                    "change": _format_currency(change),
                    "closing_balance": _format_currency(closing),
                    "closing_balance_raw": float(closing),
                }
            )

        # Net income for the period
        revenue_expense = db.execute(
            select(
                AccountCategory.ifrs_category,
                func.coalesce(
                    func.sum(JournalEntryLine.debit_amount_functional), 0
                ).label("debit"),
                func.coalesce(
                    func.sum(JournalEntryLine.credit_amount_functional), 0
                ).label("credit"),
            )
            .join(
                JournalEntry,
                JournalEntry.journal_entry_id == JournalEntryLine.journal_entry_id,
            )
            .join(Account, Account.account_id == JournalEntryLine.account_id)
            .join(AccountCategory, Account.category_id == AccountCategory.category_id)
            .where(
                JournalEntry.organization_id == org_id,
                JournalEntry.status == JournalStatus.POSTED,
                JournalEntry.posting_date >= from_date,
                JournalEntry.posting_date <= to_date,
                AccountCategory.ifrs_category.in_(
                    [
                        IFRSCategory.REVENUE,
                        IFRSCategory.EXPENSES,
                    ]
                ),
            )
            .group_by(AccountCategory.ifrs_category)
        ).all()
        total_revenue = Decimal("0")
        total_expenses = Decimal("0")
        for ifrs_category, debit, credit in revenue_expense:
            debit = Decimal(str(debit or 0))
            credit = Decimal(str(credit or 0))
            if ifrs_category == IFRSCategory.REVENUE:
                total_revenue += credit - debit
            elif ifrs_category == IFRSCategory.EXPENSES:
                total_expenses += debit - credit

        net_income = total_revenue - total_expenses

        return {
            "start_date": _format_date(from_date),
            "start_date_iso": _iso_date(from_date),
            "end_date": _format_date(to_date),
            "end_date_iso": _iso_date(to_date),
            "equity_lines": line_items,
            "opening_equity": _format_currency(total_opening),
            "change_in_equity": _format_currency(total_change),
            "closing_equity": _format_currency(total_closing),
            "net_income": _format_currency(net_income),
            "net_income_raw": float(net_income),
        }

    @staticmethod
    def budget_vs_actual_context(
        db: Session,
        organization_id: str,
        start_date: str | None = None,
        end_date: str | None = None,
        budget_id: str | None = None,
        budget_code: str | None = None,
    ) -> dict:
        """Get context for budget vs actual report."""
        from app.models.finance.gl.budget import Budget, BudgetStatus
        from app.models.finance.gl.budget_line import BudgetLine

        org_id = coerce_uuid(organization_id)
        today = date.today()
        from_date = _parse_date(start_date) or today.replace(day=1)
        to_date = _parse_date(end_date) or today

        periods = db.scalars(
            select(FiscalPeriod).where(
                FiscalPeriod.organization_id == org_id,
                FiscalPeriod.start_date <= to_date,
                FiscalPeriod.end_date >= from_date,
            )
        ).all()
        period_ids = [p.fiscal_period_id for p in periods]

        budget_stmt = (
            select(BudgetLine, Budget, Account)
            .join(Budget, BudgetLine.budget_id == Budget.budget_id)
            .join(Account, BudgetLine.account_id == Account.account_id)
            .where(
                Budget.organization_id == org_id,
                Budget.status.in_([BudgetStatus.APPROVED, BudgetStatus.ACTIVE]),
                BudgetLine.fiscal_period_id.in_(period_ids),
            )
        )

        if budget_id:
            budget_stmt = budget_stmt.where(Budget.budget_id == coerce_uuid(budget_id))
        if budget_code:
            budget_stmt = budget_stmt.where(Budget.budget_code == budget_code)

        budget_lines = db.execute(budget_stmt).all()

        budget_totals: dict[UUID, dict[str, Any]] = {}
        for line, _budget, account in budget_lines:
            budget_totals.setdefault(
                account.account_id,
                {
                    "account_code": account.account_code,
                    "account_name": account.account_name,
                    "budget": Decimal("0"),
                    "normal_balance": account.normal_balance.value,
                },
            )
            budget_totals[account.account_id]["budget"] += Decimal(
                str(line.budget_amount or 0)
            )

        account_ids = list(budget_totals.keys())
        actual_rows: list[Any] = []
        if account_ids:
            actual_rows = list(
                db.execute(
                    select(
                        JournalEntryLine.account_id,
                        func.coalesce(
                            func.sum(JournalEntryLine.debit_amount_functional), 0
                        ).label("debit"),
                        func.coalesce(
                            func.sum(JournalEntryLine.credit_amount_functional), 0
                        ).label("credit"),
                    )
                    .join(
                        JournalEntry,
                        JournalEntry.journal_entry_id
                        == JournalEntryLine.journal_entry_id,
                    )
                    .where(
                        JournalEntry.organization_id == org_id,
                        JournalEntry.status == JournalStatus.POSTED,
                        JournalEntry.posting_date >= from_date,
                        JournalEntry.posting_date <= to_date,
                        JournalEntryLine.account_id.in_(account_ids),
                    )
                    .group_by(JournalEntryLine.account_id)
                ).all()
            )

        actual_map = {row.account_id: row for row in actual_rows}

        rows = []
        total_budget = Decimal("0")
        total_actual = Decimal("0")

        for account_id, data in budget_totals.items():
            actual_row = actual_map.get(account_id)
            debit = Decimal(str(actual_row.debit or 0)) if actual_row else Decimal("0")
            credit = (
                Decimal(str(actual_row.credit or 0)) if actual_row else Decimal("0")
            )
            if data["normal_balance"] == "DEBIT":
                actual = debit - credit
            else:
                actual = credit - debit

            budget = data["budget"]
            variance = actual - budget
            variance_pct = (
                (variance / budget * Decimal("100")) if budget else Decimal("0")
            )

            total_budget += budget
            total_actual += actual

            rows.append(
                {
                    "account_code": data["account_code"],
                    "account_name": data["account_name"],
                    "budget": _format_currency(budget),
                    "actual": _format_currency(actual),
                    "variance": _format_currency(variance),
                    "variance_percent": f"{variance_pct:.2f}%",
                    "variance_raw": float(variance),
                }
            )

        rows.sort(key=lambda x: x["account_code"])
        total_variance = total_actual - total_budget

        # Fetch budgets for dropdown
        budget_options = [
            {
                "budget_id": str(b.budget_id),
                "budget_code": b.budget_code,
                "budget_name": b.budget_name,
                "status": b.status.value if b.status else "",
            }
            for b in db.scalars(
                select(Budget)
                .where(Budget.organization_id == org_id)
                .order_by(Budget.budget_code)
            ).all()
        ]

        return {
            "start_date": _format_date(from_date),
            "start_date_iso": _iso_date(from_date),
            "end_date": _format_date(to_date),
            "end_date_iso": _iso_date(to_date),
            "budget_id": budget_id or "",
            "budget_code": budget_code or "",
            "budgets": budget_options,
            "budget_lines": rows,
            "total_budget": _format_currency(total_budget),
            "total_actual": _format_currency(total_actual),
            "total_variance": _format_currency(total_variance),
            "total_variance_raw": float(total_variance),
        }

    @staticmethod
    def inventory_valuation_reconciliation_context(
        db: Session,
        organization_id: str,
    ) -> dict:
        """Get context for inventory valuation versus GL reconciliation."""
        org_id = coerce_uuid(organization_id)
        service = ValuationReconciliationService(db)
        try:
            result = service.reconcile(org_id)
            return {
                "has_data": True,
                "fiscal_period_id": str(result.fiscal_period_id),
                "inventory_total": _format_currency(result.inventory_total),
                "gl_total": _format_currency(result.gl_total),
                "difference": _format_currency(result.difference),
                "difference_raw": float(result.difference),
                "is_balanced": result.is_balanced,
            }
        except ValueError:
            return {
                "has_data": False,
                "fiscal_period_id": "",
                "inventory_total": _format_currency(Decimal("0")),
                "gl_total": _format_currency(Decimal("0")),
                "difference": _format_currency(Decimal("0")),
                "difference_raw": 0.0,
                "is_balanced": True,
            }

    def dashboard_response(
        self,
        request: Request,
        auth: WebAuthContext,
        start_date: str | None,
        end_date: str | None,
        db: Session,
    ) -> HTMLResponse:
        from app.web.deps import base_context

        context = base_context(request, auth, "Reports", "reports")
        context.update(
            self.dashboard_context(
                db,
                str(auth.organization_id),
                start_date=start_date,
                end_date=end_date,
            )
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
    ) -> HTMLResponse:
        from app.web.deps import base_context

        context = base_context(request, auth, "Trial Balance", "reports")
        context.update(
            self.trial_balance_context(
                db,
                str(auth.organization_id),
                as_of_date=as_of_date,
            )
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
    ) -> HTMLResponse:
        from app.web.deps import base_context

        context = base_context(request, auth, "Statement of Profit or Loss", "reports")
        context.update(
            self.income_statement_context(
                db,
                str(auth.organization_id),
                start_date=start_date,
                end_date=end_date,
            )
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
        from app.web.deps import base_context

        context = base_context(
            request, auth, "Statement of Financial Position", "reports"
        )
        context.update(
            self.balance_sheet_context(
                db,
                str(auth.organization_id),
                as_of_date=as_of_date,
            )
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
            self.ap_aging_context(
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
            self.ar_aging_context(
                db,
                str(auth.organization_id),
                as_of_date=as_of_date,
            )
        )
        context["active_filters"] = active_filters
        return templates.TemplateResponse(
            request, "finance/reports/ar_aging.html", context
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
        from app.web.deps import base_context

        context = base_context(request, auth, "General Ledger", "reports")
        context.update(
            self.general_ledger_context(
                db,
                str(auth.organization_id),
                account_id=account_id,
                start_date=start_date,
                end_date=end_date,
            )
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
        from app.web.deps import base_context

        context = base_context(request, auth, "Tax Summary", "reports")
        context.update(
            self.tax_summary_context(
                db,
                str(auth.organization_id),
                start_date=start_date,
                end_date=end_date,
            )
        )
        return templates.TemplateResponse(
            request, "finance/reports/tax_summary.html", context
        )

    def expense_summary_response(
        self,
        request: Request,
        auth: WebAuthContext,
        start_date: str | None,
        end_date: str | None,
        db: Session,
    ) -> HTMLResponse:
        from app.web.deps import base_context

        context = base_context(request, auth, "Expense Summary", "reports")
        context.update(
            self.expense_summary_context(
                db,
                str(auth.organization_id),
                start_date=start_date,
                end_date=end_date,
            )
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
        from app.web.deps import base_context

        context = base_context(request, auth, "Cash Flow Statement", "reports")
        context.update(
            self.cash_flow_context(
                db,
                str(auth.organization_id),
                start_date=start_date,
                end_date=end_date,
            )
        )
        return templates.TemplateResponse(
            request, "finance/reports/cash_flow.html", context
        )

    def changes_in_equity_response(
        self,
        request: Request,
        auth: WebAuthContext,
        start_date: str | None,
        end_date: str | None,
        db: Session,
    ) -> HTMLResponse:
        from app.web.deps import base_context

        context = base_context(request, auth, "Changes in Equity", "reports")
        context.update(
            self.changes_in_equity_context(
                db,
                str(auth.organization_id),
                start_date=start_date,
                end_date=end_date,
            )
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
        from app.web.deps import base_context

        context = base_context(request, auth, "Budget vs Actual", "reports")
        context.update(
            self.budget_vs_actual_context(
                db,
                str(auth.organization_id),
                start_date=start_date,
                end_date=end_date,
                budget_id=budget_id,
                budget_code=budget_code,
            )
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
            self.inventory_valuation_reconciliation_context(
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
        cubes = AnalysisCubeService(db).list_cubes(auth.organization_id)
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
        return templates.TemplateResponse(request, "finance/reports/analysis.html", context)

    # ─────────────────── CSV Export helpers ───────────────────

    def export_trial_balance_csv(
        self,
        organization_id: str,
        db: Session,
        as_of_date: str | None = None,
    ) -> str:
        """Export trial balance as CSV."""
        ctx = self.trial_balance_context(db, organization_id, as_of_date)
        headers = ["Category", "Account Code", "Account Name", "Debit", "Credit"]
        rows: list[list[str]] = []
        for section_name, section_key in [
            ("Assets", "assets"),
            ("Liabilities", "liabilities"),
            ("Equity", "equity"),
            ("Revenue", "revenue"),
            ("Expenses", "expenses"),
        ]:
            for item in ctx.get(section_key, []):
                rows.append(
                    [
                        section_name,
                        item["account_code"],
                        item["account_name"],
                        str(item["debit_raw"]),
                        str(item["credit_raw"]),
                    ]
                )
        rows.append(["", "", "TOTAL", ctx["total_debit"], ctx["total_credit"]])
        return _build_csv(headers, rows)

    def export_income_statement_csv(
        self,
        organization_id: str,
        db: Session,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> str:
        """Export income statement as CSV."""
        ctx = self.income_statement_context(db, organization_id, start_date, end_date)
        headers = ["Line Item", "Amount"]
        rows = [
            [item["name"], str(item["amount_raw"])]
            for item in ctx.get("income_statement_lines", [])
        ]
        return _build_csv(headers, rows)

    def export_balance_sheet_csv(
        self,
        organization_id: str,
        db: Session,
        as_of_date: str | None = None,
    ) -> str:
        """Export balance sheet as CSV."""
        ctx = self.balance_sheet_context(db, organization_id, as_of_date)
        headers = ["Section", "Line Item", "Amount"]
        rows: list[list[str]] = []
        for section_name, section_key in [
            ("Current Assets", "current_assets"),
            ("Non-Current Assets", "non_current_assets"),
            ("Current Liabilities", "current_liabilities"),
            ("Non-Current Liabilities", "non_current_liabilities"),
            ("Equity", "equity"),
        ]:
            for item in ctx.get("balance_sheet_lines", {}).get(section_key, []):
                rows.append([section_name, item["name"], str(item["amount_raw"])])
        rows.append(["", "Total Assets", ctx["total_assets"]])
        rows.append(["", "Total Liabilities", ctx["total_liabilities"]])
        rows.append(["", "Total Equity", ctx["total_equity"]])
        return _build_csv(headers, rows)

    def export_general_ledger_csv(
        self,
        organization_id: str,
        db: Session,
        account_id: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> str:
        """Export general ledger as CSV."""
        ctx = self.general_ledger_context(
            db, organization_id, account_id, start_date, end_date
        )
        headers = [
            "Date",
            "Journal #",
            "Description",
            "Reference",
            "Debit",
            "Credit",
            "Balance",
        ]
        rows = [
            [
                txn["date"],
                txn["journal_number"],
                txn["description"],
                txn["reference"],
                txn["debit"],
                txn["credit"],
                txn["balance"],
            ]
            for txn in ctx.get("transactions", [])
        ]
        return _build_csv(headers, rows)


reports_web_service = ReportsWebService()
