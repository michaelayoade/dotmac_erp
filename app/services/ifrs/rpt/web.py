"""
Reports web view service.

Provides view-focused data for reports web routes.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.ifrs.gl.account import Account
from app.models.ifrs.gl.account_balance import AccountBalance, BalanceType
from app.models.ifrs.gl.account_category import AccountCategory, IFRSCategory
from app.models.ifrs.gl.fiscal_period import FiscalPeriod
from app.models.ifrs.ap.supplier_invoice import SupplierInvoice, SupplierInvoiceStatus
from app.models.ifrs.ap.supplier import Supplier
from app.models.ifrs.ar.invoice import Invoice as ARInvoice
from app.models.ifrs.ar.customer import Customer
from app.models.ifrs.rpt.report_definition import ReportDefinition, ReportType
from app.models.ifrs.rpt.report_instance import ReportInstance, ReportStatus
from app.config import settings
from app.services.common import coerce_uuid
from app.services.ifrs.platform.org_context import org_context_service


def _parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _format_date(value: Optional[date]) -> str:
    return value.strftime("%Y-%m-%d") if value else ""


def _format_currency(
    amount: Optional[Decimal],
    currency: str = settings.default_presentation_currency_code,
) -> str:
    if amount is None:
        return f"{currency} 0.00"
    value = Decimal(str(amount))
    return f"{currency} {value:,.2f}"


def _ifrs_label(category: IFRSCategory) -> str:
    label_map = {
        IFRSCategory.ASSETS: "Assets",
        IFRSCategory.LIABILITIES: "Liabilities",
        IFRSCategory.EQUITY: "Equity",
        IFRSCategory.REVENUE: "Revenue",
        IFRSCategory.EXPENSES: "Expenses",
        IFRSCategory.OTHER_COMPREHENSIVE_INCOME: "Other Comprehensive Income",
    }
    return label_map.get(category, category.value)


def _report_type_label(report_type: ReportType) -> str:
    labels = {
        ReportType.BALANCE_SHEET: "Balance Sheet",
        ReportType.INCOME_STATEMENT: "Income Statement",
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
    return labels.get(report_type, report_type.value)


class ReportsWebService:
    """View service for reports web routes."""

    @staticmethod
    def dashboard_context(
        db: Session,
        organization_id: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> dict:
        """Get context for reports dashboard with key summaries."""
        from app.models.ifrs.gl.journal_entry import JournalEntry, JournalStatus
        from app.models.ifrs.ar.invoice import InvoiceStatus as ARInvoiceStatus
        from app.models.ifrs.tax.tax_transaction import TaxTransaction, TaxTransactionType
        from app.models.ifrs.tax.tax_period import TaxPeriod, TaxPeriodStatus

        org_id = coerce_uuid(organization_id)
        today = date.today()

        # Parse date filters (default to current month)
        from_date = _parse_date(start_date) or today.replace(day=1)
        to_date = _parse_date(end_date) or today

        # ========== Financial Position Summary ==========
        period = (
            db.query(FiscalPeriod)
            .filter(
                FiscalPeriod.organization_id == org_id,
                FiscalPeriod.start_date <= today,
                FiscalPeriod.end_date >= today,
            )
            .order_by(FiscalPeriod.start_date.desc())
            .first()
        )

        total_assets = Decimal("0")
        total_liabilities = Decimal("0")
        total_equity = Decimal("0")
        total_revenue = Decimal("0")
        total_expenses = Decimal("0")

        if period:
            # Get balance sheet totals
            bs_rows = (
                db.query(AccountBalance, Account, AccountCategory)
                .join(Account, AccountBalance.account_id == Account.account_id)
                .join(AccountCategory, Account.category_id == AccountCategory.category_id)
                .filter(
                    AccountBalance.organization_id == org_id,
                    AccountBalance.fiscal_period_id == period.fiscal_period_id,
                    AccountBalance.balance_type == BalanceType.ACTUAL,
                )
                .all()
            )

            for balance, account, category in bs_rows:
                debit = balance.closing_debit or Decimal("0")
                credit = balance.closing_credit or Decimal("0")

                if category.ifrs_category == IFRSCategory.ASSETS:
                    total_assets += debit - credit
                elif category.ifrs_category == IFRSCategory.LIABILITIES:
                    total_liabilities += credit - debit
                elif category.ifrs_category == IFRSCategory.EQUITY:
                    total_equity += credit - debit
                elif category.ifrs_category == IFRSCategory.REVENUE:
                    total_revenue += credit - debit
                elif category.ifrs_category == IFRSCategory.EXPENSES:
                    total_expenses += debit - credit

        net_income = total_revenue - total_expenses

        # ========== AP/AR Aging Summaries ==========
        # AP totals
        ap_invoices = (
            db.query(SupplierInvoice)
            .filter(
                SupplierInvoice.organization_id == org_id,
                SupplierInvoice.status.in_([
                    SupplierInvoiceStatus.POSTED,
                    SupplierInvoiceStatus.PARTIALLY_PAID,
                ]),
            )
            .all()
        )

        ap_current = Decimal("0")
        ap_overdue = Decimal("0")
        ap_total = Decimal("0")

        for inv in ap_invoices:
            balance = inv.balance_due or Decimal("0")
            ap_total += balance
            if inv.due_date and (today - inv.due_date).days > 0:
                ap_overdue += balance
            else:
                ap_current += balance

        # AR totals
        ar_invoices = (
            db.query(ARInvoice)
            .filter(
                ARInvoice.organization_id == org_id,
                ARInvoice.status.in_([
                    ARInvoiceStatus.POSTED,
                    ARInvoiceStatus.PARTIALLY_PAID,
                ]),
            )
            .all()
        )

        ar_current = Decimal("0")
        ar_overdue = Decimal("0")
        ar_total = Decimal("0")

        for inv in ar_invoices:
            balance = inv.balance_due or Decimal("0")
            ar_total += balance
            if inv.due_date and (today - inv.due_date).days > 0:
                ar_overdue += balance
            else:
                ar_current += balance

        # ========== Tax Summary ==========
        tax_transactions = (
            db.query(TaxTransaction)
            .filter(
                TaxTransaction.organization_id == org_id,
                TaxTransaction.transaction_date >= from_date,
                TaxTransaction.transaction_date <= to_date,
            )
            .all()
        )

        output_tax = Decimal("0")
        input_tax = Decimal("0")
        for txn in tax_transactions:
            amount = txn.tax_amount or Decimal("0")
            if txn.transaction_type == TaxTransactionType.OUTPUT:
                output_tax += amount
            elif txn.transaction_type == TaxTransactionType.INPUT:
                input_tax += amount

        net_tax = output_tax - input_tax

        # Get overdue tax periods
        overdue_tax_periods = (
            db.query(TaxPeriod)
            .filter(
                TaxPeriod.organization_id == org_id,
                TaxPeriod.status == TaxPeriodStatus.OPEN,
                TaxPeriod.due_date < today,
            )
            .count()
        )

        # Key metrics summary
        key_metrics = {
            "start_date": start_date or _format_date(from_date),
            "end_date": end_date or _format_date(to_date),
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
            "ap_current": _format_currency(ap_current),
            "ap_overdue": _format_currency(ap_overdue),
            "ap_overdue_raw": float(ap_overdue),
            "ar_total": _format_currency(ar_total),
            "ar_total_raw": float(ar_total),
            "ar_current": _format_currency(ar_current),
            "ar_overdue": _format_currency(ar_overdue),
            "ar_overdue_raw": float(ar_overdue),
            "output_tax": _format_currency(output_tax),
            "input_tax": _format_currency(input_tax),
            "net_tax": _format_currency(net_tax),
            "net_tax_raw": float(net_tax),
            "is_tax_payable": net_tax > 0,
            "overdue_tax_periods": overdue_tax_periods,
            "period_name": period.period_name if period else "No Active Period",
            "as_of_date": _format_date(today),
        }

        # Get report definitions
        definitions = (
            db.query(ReportDefinition)
            .filter(
                ReportDefinition.organization_id == org_id,
                ReportDefinition.is_active.is_(True),
            )
            .order_by(ReportDefinition.report_name)
            .all()
        )

        # Recent report instances
        recent_instances = (
            db.query(ReportInstance, ReportDefinition)
            .join(ReportDefinition, ReportInstance.report_def_id == ReportDefinition.report_def_id)
            .filter(ReportInstance.organization_id == org_id)
            .order_by(ReportInstance.queued_at.desc())
            .limit(10)
            .all()
        )

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
            recent_view.append({
                "instance_id": str(instance.report_instance_id),
                "report_name": defn.report_name,
                "report_type": defn.report_type.value,
                "status": instance.status.value,
                "queued_at": instance.queued_at.strftime("%Y-%m-%d %H:%M") if instance.queued_at else "",
                "output_format": instance.output_format,
            })

        # Standard reports always available (using IFRS terminology)
        standard_reports = [
            {
                "name": "Trial Balance",
                "description": "View account balances as of a specific date",
                "url": "/reports/trial-balance",
                "icon": "scale",
            },
            {
                "name": "Statement of Profit or Loss",
                "description": "Revenue and expenses for a period",
                "url": "/reports/income-statement",
                "icon": "trending-up",
            },
            {
                "name": "Statement of Financial Position",
                "description": "Assets, liabilities, and equity",
                "url": "/reports/balance-sheet",
                "icon": "layers",
            },
            {
                "name": "AP Aging",
                "description": "Accounts payable aging analysis",
                "url": "/reports/ap-aging",
                "icon": "clock",
            },
            {
                "name": "AR Aging",
                "description": "Accounts receivable aging analysis",
                "url": "/reports/ar-aging",
                "icon": "users",
            },
            {
                "name": "General Ledger",
                "description": "Detailed account transactions",
                "url": "/reports/general-ledger",
                "icon": "book-open",
            },
            {
                "name": "Tax Summary",
                "description": "Tax collected, paid, and net position",
                "url": "/reports/tax-summary",
                "icon": "receipt",
            },
            {
                "name": "Expense Summary",
                "description": "Expense breakdown by category",
                "url": "/reports/expense-summary",
                "icon": "credit-card",
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
        as_of_date: Optional[str] = None,
    ) -> dict:
        """Get context for trial balance report."""
        org_id = coerce_uuid(organization_id)
        ref_date = _parse_date(as_of_date) or date.today()

        # Find the fiscal period for the date
        period = (
            db.query(FiscalPeriod)
            .filter(
                FiscalPeriod.organization_id == org_id,
                FiscalPeriod.start_date <= ref_date,
                FiscalPeriod.end_date >= ref_date,
            )
            .order_by(FiscalPeriod.start_date.desc())
            .first()
        )

        if not period:
            period = (
                db.query(FiscalPeriod)
                .filter(FiscalPeriod.organization_id == org_id)
                .order_by(FiscalPeriod.end_date.desc())
                .first()
            )

        balances = []
        total_debit = Decimal("0")
        total_credit = Decimal("0")

        # Group by IFRS category
        assets = []
        liabilities = []
        equity = []
        revenue = []
        expenses = []

        if period:
            rows = (
                db.query(AccountBalance, Account, AccountCategory)
                .join(Account, AccountBalance.account_id == Account.account_id)
                .join(AccountCategory, Account.category_id == AccountCategory.category_id)
                .filter(
                    AccountBalance.organization_id == org_id,
                    AccountBalance.fiscal_period_id == period.fiscal_period_id,
                    AccountBalance.balance_type == BalanceType.ACTUAL,
                )
                .order_by(Account.account_code)
                .all()
            )

            for balance, account, category in rows:
                debit = balance.closing_debit or Decimal("0")
                credit = balance.closing_credit or Decimal("0")
                total_debit += debit
                total_credit += credit

                entry = {
                    "account_code": account.account_code,
                    "account_name": account.account_name,
                    "debit": _format_currency(debit, balance.currency_code) if debit else "",
                    "credit": _format_currency(credit, balance.currency_code) if credit else "",
                    "debit_raw": float(debit),
                    "credit_raw": float(credit),
                }

                if category.ifrs_category == IFRSCategory.ASSETS:
                    assets.append(entry)
                elif category.ifrs_category == IFRSCategory.LIABILITIES:
                    liabilities.append(entry)
                elif category.ifrs_category == IFRSCategory.EQUITY:
                    equity.append(entry)
                elif category.ifrs_category == IFRSCategory.REVENUE:
                    revenue.append(entry)
                elif category.ifrs_category == IFRSCategory.EXPENSES:
                    expenses.append(entry)
                else:
                    balances.append(entry)

        return {
            "as_of_date": as_of_date or _format_date(ref_date),
            "period_name": period.period_name if period else "No Period",
            "assets": assets,
            "liabilities": liabilities,
            "equity": equity,
            "revenue": revenue,
            "expenses": expenses,
            "other_balances": balances,
            "total_debit": _format_currency(total_debit),
            "total_credit": _format_currency(total_credit),
            "is_balanced": total_debit == total_credit,
        }

    @staticmethod
    def income_statement_context(
        db: Session,
        organization_id: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> dict:
        """Get context for income statement report."""
        org_id = coerce_uuid(organization_id)

        # Default to current month
        today = date.today()
        from_date = _parse_date(start_date) or today.replace(day=1)
        to_date = _parse_date(end_date) or today

        # Find fiscal period
        period = (
            db.query(FiscalPeriod)
            .filter(
                FiscalPeriod.organization_id == org_id,
                FiscalPeriod.start_date <= to_date,
                FiscalPeriod.end_date >= from_date,
            )
            .order_by(FiscalPeriod.start_date.desc())
            .first()
        )

        revenue_items = []
        expense_items = []
        total_revenue = Decimal("0")
        total_expenses = Decimal("0")

        if period:
            # Get revenue accounts
            revenue_rows = (
                db.query(AccountBalance, Account, AccountCategory)
                .join(Account, AccountBalance.account_id == Account.account_id)
                .join(AccountCategory, Account.category_id == AccountCategory.category_id)
                .filter(
                    AccountBalance.organization_id == org_id,
                    AccountBalance.fiscal_period_id == period.fiscal_period_id,
                    AccountBalance.balance_type == BalanceType.ACTUAL,
                    AccountCategory.ifrs_category == IFRSCategory.REVENUE,
                )
                .order_by(Account.account_code)
                .all()
            )

            for balance, account, category in revenue_rows:
                # Revenue has credit balance
                amount = (balance.closing_credit or Decimal("0")) - (balance.closing_debit or Decimal("0"))
                total_revenue += amount
                revenue_items.append({
                    "account_code": account.account_code,
                    "account_name": account.account_name,
                    "amount": _format_currency(amount),
                    "amount_raw": float(amount),
                })

            # Get expense accounts
            expense_rows = (
                db.query(AccountBalance, Account, AccountCategory)
                .join(Account, AccountBalance.account_id == Account.account_id)
                .join(AccountCategory, Account.category_id == AccountCategory.category_id)
                .filter(
                    AccountBalance.organization_id == org_id,
                    AccountBalance.fiscal_period_id == period.fiscal_period_id,
                    AccountBalance.balance_type == BalanceType.ACTUAL,
                    AccountCategory.ifrs_category == IFRSCategory.EXPENSES,
                )
                .order_by(Account.account_code)
                .all()
            )

            for balance, account, category in expense_rows:
                # Expenses have debit balance
                amount = (balance.closing_debit or Decimal("0")) - (balance.closing_credit or Decimal("0"))
                total_expenses += amount
                expense_items.append({
                    "account_code": account.account_code,
                    "account_name": account.account_name,
                    "amount": _format_currency(amount),
                    "amount_raw": float(amount),
                })

        net_income = total_revenue - total_expenses

        return {
            "start_date": start_date or _format_date(from_date),
            "end_date": end_date or _format_date(to_date),
            "period_name": period.period_name if period else "No Period",
            "revenue_items": revenue_items,
            "expense_items": expense_items,
            "total_revenue": _format_currency(total_revenue),
            "total_expenses": _format_currency(total_expenses),
            "net_income": _format_currency(net_income),
            "net_income_raw": float(net_income),
            "is_profit": net_income >= 0,
        }

    @staticmethod
    def balance_sheet_context(
        db: Session,
        organization_id: str,
        as_of_date: Optional[str] = None,
    ) -> dict:
        """Get context for balance sheet report."""
        org_id = coerce_uuid(organization_id)
        ref_date = _parse_date(as_of_date) or date.today()

        # Find fiscal period
        period = (
            db.query(FiscalPeriod)
            .filter(
                FiscalPeriod.organization_id == org_id,
                FiscalPeriod.start_date <= ref_date,
                FiscalPeriod.end_date >= ref_date,
            )
            .order_by(FiscalPeriod.start_date.desc())
            .first()
        )

        if not period:
            period = (
                db.query(FiscalPeriod)
                .filter(FiscalPeriod.organization_id == org_id)
                .order_by(FiscalPeriod.end_date.desc())
                .first()
            )

        asset_items = []
        liability_items = []
        equity_items = []
        total_assets = Decimal("0")
        total_liabilities = Decimal("0")
        total_equity = Decimal("0")

        if period:
            rows = (
                db.query(AccountBalance, Account, AccountCategory)
                .join(Account, AccountBalance.account_id == Account.account_id)
                .join(AccountCategory, Account.category_id == AccountCategory.category_id)
                .filter(
                    AccountBalance.organization_id == org_id,
                    AccountBalance.fiscal_period_id == period.fiscal_period_id,
                    AccountBalance.balance_type == BalanceType.ACTUAL,
                    AccountCategory.ifrs_category.in_([
                        IFRSCategory.ASSETS,
                        IFRSCategory.LIABILITIES,
                        IFRSCategory.EQUITY,
                    ]),
                )
                .order_by(Account.account_code)
                .all()
            )

            for balance, account, category in rows:
                debit = balance.closing_debit or Decimal("0")
                credit = balance.closing_credit or Decimal("0")

                if category.ifrs_category == IFRSCategory.ASSETS:
                    amount = debit - credit
                    total_assets += amount
                    asset_items.append({
                        "account_code": account.account_code,
                        "account_name": account.account_name,
                        "amount": _format_currency(amount),
                        "amount_raw": float(amount),
                    })
                elif category.ifrs_category == IFRSCategory.LIABILITIES:
                    amount = credit - debit
                    total_liabilities += amount
                    liability_items.append({
                        "account_code": account.account_code,
                        "account_name": account.account_name,
                        "amount": _format_currency(amount),
                        "amount_raw": float(amount),
                    })
                elif category.ifrs_category == IFRSCategory.EQUITY:
                    amount = credit - debit
                    total_equity += amount
                    equity_items.append({
                        "account_code": account.account_code,
                        "account_name": account.account_name,
                        "amount": _format_currency(amount),
                        "amount_raw": float(amount),
                    })

        total_liabilities_equity = total_liabilities + total_equity

        return {
            "as_of_date": as_of_date or _format_date(ref_date),
            "period_name": period.period_name if period else "No Period",
            "asset_items": asset_items,
            "liability_items": liability_items,
            "equity_items": equity_items,
            "total_assets": _format_currency(total_assets),
            "total_liabilities": _format_currency(total_liabilities),
            "total_equity": _format_currency(total_equity),
            "total_liabilities_equity": _format_currency(total_liabilities_equity),
            "is_balanced": total_assets == total_liabilities_equity,
        }

    @staticmethod
    def ap_aging_context(
        db: Session,
        organization_id: str,
        as_of_date: Optional[str] = None,
    ) -> dict:
        """Get context for AP aging report."""
        org_id = coerce_uuid(organization_id)
        ref_date = _parse_date(as_of_date) or date.today()

        # Get open invoices
        invoices = (
            db.query(SupplierInvoice, Supplier)
            .join(Supplier, SupplierInvoice.supplier_id == Supplier.supplier_id)
            .filter(
                SupplierInvoice.organization_id == org_id,
                SupplierInvoice.status.in_([
                    SupplierInvoiceStatus.POSTED,
                    SupplierInvoiceStatus.PARTIALLY_PAID,
                ]),
            )
            .order_by(SupplierInvoice.due_date)
            .all()
        )

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
                "supplier_name": supplier.supplier_name,
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

        grand_total = total_current + total_1_30 + total_31_60 + total_61_90 + total_over_90

        return {
            "as_of_date": as_of_date or _format_date(ref_date),
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
                {"bucket": "Current", "amount": _format_currency(total_current), "amount_raw": float(total_current)},
                {"bucket": "1-30 Days", "amount": _format_currency(total_1_30), "amount_raw": float(total_1_30)},
                {"bucket": "31-60 Days", "amount": _format_currency(total_31_60), "amount_raw": float(total_31_60)},
                {"bucket": "61-90 Days", "amount": _format_currency(total_61_90), "amount_raw": float(total_61_90)},
                {"bucket": "Over 90 Days", "amount": _format_currency(total_over_90), "amount_raw": float(total_over_90)},
            ],
        }

    @staticmethod
    def ar_aging_context(
        db: Session,
        organization_id: str,
        as_of_date: Optional[str] = None,
    ) -> dict:
        """Get context for AR aging report."""
        org_id = coerce_uuid(organization_id)
        ref_date = _parse_date(as_of_date) or date.today()

        # Get open invoices
        from app.models.ifrs.ar.invoice import InvoiceStatus as ARInvoiceStatus

        invoices = (
            db.query(ARInvoice, Customer)
            .join(Customer, ARInvoice.customer_id == Customer.customer_id)
            .filter(
                ARInvoice.organization_id == org_id,
                ARInvoice.status.in_([
                    ARInvoiceStatus.POSTED,
                    ARInvoiceStatus.PARTIALLY_PAID,
                ]),
            )
            .order_by(ARInvoice.due_date)
            .all()
        )

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
                "customer_name": customer.customer_name,
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

        grand_total = total_current + total_1_30 + total_31_60 + total_61_90 + total_over_90

        return {
            "as_of_date": as_of_date or _format_date(ref_date),
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
                {"bucket": "Current", "amount": _format_currency(total_current), "amount_raw": float(total_current)},
                {"bucket": "1-30 Days", "amount": _format_currency(total_1_30), "amount_raw": float(total_1_30)},
                {"bucket": "31-60 Days", "amount": _format_currency(total_31_60), "amount_raw": float(total_31_60)},
                {"bucket": "61-90 Days", "amount": _format_currency(total_61_90), "amount_raw": float(total_61_90)},
                {"bucket": "Over 90 Days", "amount": _format_currency(total_over_90), "amount_raw": float(total_over_90)},
            ],
        }

    @staticmethod
    def general_ledger_context(
        db: Session,
        organization_id: str,
        account_id: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> dict:
        """Get context for general ledger detail report."""
        from app.models.ifrs.gl.journal_entry import JournalEntry, JournalStatus
        from app.models.ifrs.gl.journal_entry_line import JournalEntryLine

        org_id = coerce_uuid(organization_id)

        # Default to current month
        today = date.today()
        from_date = _parse_date(start_date) or today.replace(day=1)
        to_date = _parse_date(end_date) or today

        # Get accounts for dropdown
        accounts = (
            db.query(Account)
            .filter(
                Account.organization_id == org_id,
                Account.is_active.is_(True),
            )
            .order_by(Account.account_code)
            .all()
        )

        account_options = [
            {
                "account_id": str(acct.account_id),
                "account_code": acct.account_code,
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
                lines = (
                    db.query(JournalEntryLine, JournalEntry)
                    .join(JournalEntry, JournalEntryLine.journal_entry_id == JournalEntry.journal_entry_id)
                    .filter(
                        JournalEntryLine.account_id == acct_id,
                        JournalEntry.posting_date >= from_date,
                        JournalEntry.posting_date <= to_date,
                        JournalEntry.status == JournalStatus.POSTED,
                    )
                    .order_by(JournalEntry.posting_date, JournalEntry.journal_entry_id)
                    .all()
                )

                for line, entry in lines:
                    debit = line.debit_amount or Decimal("0")
                    credit = line.credit_amount or Decimal("0")

                    # Calculate running balance based on normal balance
                    if selected_account.normal_balance.value == "DEBIT":
                        running_balance += debit - credit
                    else:
                        running_balance += credit - debit

                    transactions.append({
                        "date": _format_date(entry.posting_date),
                        "journal_number": entry.journal_number,
                        "description": line.description or entry.description,
                        "reference": entry.reference or "",
                        "debit": _format_currency(debit) if debit else "",
                        "credit": _format_currency(credit) if credit else "",
                        "balance": _format_currency(running_balance),
                    })

        return {
            "start_date": start_date or _format_date(from_date),
            "end_date": end_date or _format_date(to_date),
            "account_id": account_id,
            "accounts": account_options,
            "selected_account": {
                "account_code": selected_account.account_code,
                "account_name": selected_account.account_name,
            } if selected_account else None,
            "transactions": transactions,
            "ending_balance": _format_currency(running_balance),
        }


    @staticmethod
    def tax_summary_context(
        db: Session,
        organization_id: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> dict:
        """Get context for tax summary report."""
        from app.models.ifrs.tax.tax_transaction import TaxTransaction, TaxTransactionType
        from app.models.ifrs.tax.tax_code import TaxCode, TaxType
        from app.models.ifrs.tax.tax_period import TaxPeriod, TaxPeriodStatus

        org_id = coerce_uuid(organization_id)
        today = date.today()
        from_date = _parse_date(start_date) or today.replace(day=1)
        to_date = _parse_date(end_date) or today

        # Get tax transactions for the period
        transactions = (
            db.query(TaxTransaction, TaxCode)
            .join(TaxCode, TaxTransaction.tax_code_id == TaxCode.tax_code_id)
            .filter(
                TaxTransaction.organization_id == org_id,
                TaxTransaction.transaction_date >= from_date,
                TaxTransaction.transaction_date <= to_date,
            )
            .order_by(TaxTransaction.transaction_date.desc())
            .all()
        )

        # Calculate totals by type
        output_tax = Decimal("0")  # Tax collected (sales)
        input_tax = Decimal("0")   # Tax paid (purchases)
        withholding = Decimal("0")
        payments = Decimal("0")

        by_tax_type: dict = {}

        for txn, code in transactions:
            amount = txn.tax_amount or Decimal("0")

            if txn.transaction_type == TaxTransactionType.OUTPUT:
                output_tax += amount
            elif txn.transaction_type == TaxTransactionType.INPUT:
                input_tax += amount
            elif txn.transaction_type == TaxTransactionType.WITHHOLDING:
                withholding += amount
            elif txn.transaction_type == TaxTransactionType.PAYMENT:
                payments += amount

            # Group by tax type
            tax_type = code.tax_type.value
            if tax_type not in by_tax_type:
                by_tax_type[tax_type] = {"output": Decimal("0"), "input": Decimal("0")}
            if txn.transaction_type == TaxTransactionType.OUTPUT:
                by_tax_type[tax_type]["output"] += amount
            elif txn.transaction_type == TaxTransactionType.INPUT:
                by_tax_type[tax_type]["input"] += amount

        net_tax = output_tax - input_tax

        # Tax type breakdown
        tax_breakdown = []
        for tax_type, amounts in by_tax_type.items():
            net = amounts["output"] - amounts["input"]
            tax_breakdown.append({
                "tax_type": tax_type,
                "output": _format_currency(amounts["output"]),
                "input": _format_currency(amounts["input"]),
                "net": _format_currency(net),
                "net_raw": float(net),
            })

        # Get open tax periods
        open_periods = (
            db.query(TaxPeriod)
            .filter(
                TaxPeriod.organization_id == org_id,
                TaxPeriod.status.in_([TaxPeriodStatus.OPEN]),
            )
            .order_by(TaxPeriod.due_date)
            .limit(5)
            .all()
        )

        upcoming_deadlines = []
        for period in open_periods:
            days_until = (period.due_date - today).days if period.due_date else 0
            upcoming_deadlines.append({
                "period_name": period.period_name,
                "due_date": _format_date(period.due_date),
                "days_until": days_until,
                "is_overdue": days_until < 0,
            })

        return {
            "start_date": start_date or _format_date(from_date),
            "end_date": end_date or _format_date(to_date),
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
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
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

        # Get expense accounts (EXPENSES category)
        expense_balances = (
            db.query(AccountBalance, Account, AccountCategory)
            .join(Account, AccountBalance.account_id == Account.account_id)
            .join(AccountCategory, Account.category_id == AccountCategory.category_id)
            .filter(
                AccountBalance.organization_id == org_id,
                AccountBalance.balance_type == BalanceType.ACTUAL,
                AccountCategory.ifrs_category == IFRSCategory.EXPENSES,
            )
            .order_by(Account.account_code)
            .all()
        )

        expense_items = []
        total_expenses = Decimal("0")

        for balance, account, category in expense_balances:
            amount = (balance.closing_debit or Decimal("0")) - (balance.closing_credit or Decimal("0"))
            total_expenses += amount
            expense_items.append({
                "account_code": account.account_code,
                "account_name": account.account_name,
                "category": category.category_name,
                "amount": _format_currency(amount),
                "amount_raw": float(amount),
            })

        # Sort by amount descending
        expense_items.sort(key=lambda x: x["amount_raw"], reverse=True)

        # Top 5 expense categories
        top_expenses = expense_items[:5]

        return {
            "start_date": start_date or _format_date(from_date),
            "end_date": end_date or _format_date(to_date),
            "expense_items": expense_items,
            "top_expenses": top_expenses,
            "total_expenses": _format_currency(total_expenses),
            "total_expenses_raw": float(total_expenses),
            "presentation_currency_code": presentation_currency_code,
        }


reports_web_service = ReportsWebService()
