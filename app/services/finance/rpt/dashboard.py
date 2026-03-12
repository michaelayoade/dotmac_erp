"""Reports dashboard context builder."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.finance.gl.account import Account
from app.models.finance.gl.account_category import AccountCategory, IFRSCategory
from app.models.finance.gl.fiscal_period import FiscalPeriod
from app.models.finance.gl.journal_entry import JournalEntry, JournalStatus
from app.models.finance.gl.journal_entry_line import JournalEntryLine
from app.models.finance.rpt.report_definition import ReportDefinition, ReportType
from app.models.finance.rpt.report_instance import ReportInstance
from app.services.common import coerce_uuid
from app.services.finance.rpt.common import (
    _format_currency,
    _format_date,
    _iso_date,
    _parse_date,
    _report_type_label,
    _tax_totals_from_gl,
)


def dashboard_context(
    db: Session,
    organization_id: str,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
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
            func.coalesce(func.sum(JournalEntryLine.debit_amount_functional), 0).label(
                "debit"
            ),
            func.coalesce(func.sum(JournalEntryLine.credit_amount_functional), 0).label(
                "credit"
            ),
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
    tax_totals = _tax_totals_from_gl(
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
            func.coalesce(func.sum(JournalEntryLine.debit_amount_functional), 0).label(
                "debit"
            ),
            func.coalesce(func.sum(JournalEntryLine.credit_amount_functional), 0).label(
                "credit"
            ),
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
    key_metrics: dict[str, Any] = {
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
    financial_statements: list[dict[str, Any]] = []
    operational_reports: list[dict[str, Any]] = []
    compliance_reports: list[dict[str, Any]] = []

    for defn in definitions:
        report_view: dict[str, Any] = {
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

    recent_view: list[dict[str, Any]] = []
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
