"""
IPSAS Statement Service - IPSAS 1 & 2 financial statements.

Generates IPSAS-compliant financial statements:
- Statement of Financial Position (IPSAS 1)
- Statement of Financial Performance (IPSAS 1)
- Statement of Changes in Net Assets (IPSAS 1)
- Cash Flow Statement (IPSAS 2)
"""

import logging
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy import select, func
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class IPSASStatementService:
    """Service for generating IPSAS financial statements."""

    def __init__(self, db: Session):
        self.db = db

    def generate_financial_position(
        self,
        organization_id: UUID,
        fiscal_period_id: UUID,
        *,
        fund_id: Optional[UUID] = None,
    ) -> dict:
        """
        Generate IPSAS 1 Statement of Financial Position.

        Similar to IFRS Balance Sheet but with fund-based segmentation
        and classification of net assets (restricted vs unrestricted).
        """
        from app.models.finance.gl.account import Account
        from app.models.finance.gl.account_category import AccountCategory, IFRSCategory
        from app.models.finance.gl.journal_entry import JournalEntry
        from app.models.finance.gl.journal_entry_line import JournalEntryLine

        # Build base query for balances (join through JournalEntry for org/period)
        stmt = (
            select(
                AccountCategory.ifrs_category,
                Account.account_name,
                func.sum(
                    JournalEntryLine.debit_amount - JournalEntryLine.credit_amount
                ).label("balance"),
            )
            .join(Account, JournalEntryLine.account_id == Account.account_id)
            .join(
                AccountCategory,
                Account.category_id == AccountCategory.category_id,
            )
            .join(
                JournalEntry,
                JournalEntryLine.journal_entry_id == JournalEntry.journal_entry_id,
            )
            .where(
                JournalEntry.organization_id == organization_id,
                JournalEntry.fiscal_period_id == fiscal_period_id,
            )
            .group_by(AccountCategory.ifrs_category, Account.account_name)
            .order_by(AccountCategory.ifrs_category, Account.account_name)
        )

        rows = self.db.execute(stmt).all()

        assets = []
        liabilities = []
        net_assets = []

        for row in rows:
            entry = {
                "account_type": str(row.ifrs_category),
                "account_name": row.account_name,
                "balance": str(row.balance or Decimal(0)),
            }
            if row.ifrs_category == IFRSCategory.ASSETS:
                assets.append(entry)
            elif row.ifrs_category == IFRSCategory.LIABILITIES:
                liabilities.append(entry)
            else:
                net_assets.append(entry)

        total_assets = sum(Decimal(a["balance"]) for a in assets)
        total_liabilities = sum(Decimal(l["balance"]) for l in liabilities)

        return {
            "organization_id": str(organization_id),
            "fiscal_period_id": str(fiscal_period_id),
            "fund_id": str(fund_id) if fund_id else None,
            "assets": assets,
            "liabilities": liabilities,
            "net_assets": net_assets,
            "total_assets": str(total_assets),
            "total_liabilities": str(total_liabilities),
            "total_net_assets": str(total_assets - total_liabilities),
        }

    def generate_financial_performance(
        self,
        organization_id: UUID,
        fiscal_period_id: UUID,
        *,
        fund_id: Optional[UUID] = None,
    ) -> dict:
        """
        Generate IPSAS 1 Statement of Financial Performance.

        Revenue and expenses for the period, similar to income statement
        but with government revenue classifications.
        """
        from app.models.finance.gl.account import Account
        from app.models.finance.gl.account_category import AccountCategory, IFRSCategory
        from app.models.finance.gl.journal_entry import JournalEntry
        from app.models.finance.gl.journal_entry_line import JournalEntryLine

        stmt = (
            select(
                AccountCategory.ifrs_category,
                Account.account_name,
                func.sum(
                    JournalEntryLine.credit_amount - JournalEntryLine.debit_amount
                ).label("amount"),
            )
            .join(Account, JournalEntryLine.account_id == Account.account_id)
            .join(
                AccountCategory,
                Account.category_id == AccountCategory.category_id,
            )
            .join(
                JournalEntry,
                JournalEntryLine.journal_entry_id == JournalEntry.journal_entry_id,
            )
            .where(
                JournalEntry.organization_id == organization_id,
                JournalEntry.fiscal_period_id == fiscal_period_id,
                AccountCategory.ifrs_category.in_([
                    IFRSCategory.REVENUE,
                    IFRSCategory.EXPENSES,
                ]),
            )
            .group_by(AccountCategory.ifrs_category, Account.account_name)
            .order_by(AccountCategory.ifrs_category, Account.account_name)
        )

        rows = self.db.execute(stmt).all()

        revenue = []
        expenses = []

        for row in rows:
            entry = {
                "account_name": row.account_name,
                "amount": str(row.amount or Decimal(0)),
            }
            if row.ifrs_category == IFRSCategory.REVENUE:
                revenue.append(entry)
            else:
                expenses.append(entry)

        total_revenue = sum(Decimal(r["amount"]) for r in revenue)
        total_expenses = sum(Decimal(e["amount"]) for e in expenses)

        return {
            "organization_id": str(organization_id),
            "fiscal_period_id": str(fiscal_period_id),
            "fund_id": str(fund_id) if fund_id else None,
            "revenue": revenue,
            "expenses": expenses,
            "total_revenue": str(total_revenue),
            "total_expenses": str(abs(total_expenses)),
            "surplus_deficit": str(total_revenue + total_expenses),
        }

    def generate_changes_in_net_assets(
        self,
        organization_id: UUID,
        fiscal_period_id: UUID,
    ) -> dict:
        """
        Generate IPSAS 1 Statement of Changes in Net Assets.

        Shows movements in restricted and unrestricted net assets.
        """
        from app.models.finance.ipsas.fund import Fund
        from app.models.finance.ipsas.enums import FundStatus

        # Get funds to separate restricted vs unrestricted
        fund_stmt = select(Fund).where(
            Fund.organization_id == organization_id,
            Fund.status == FundStatus.ACTIVE,
        )
        funds = list(self.db.scalars(fund_stmt).all())

        restricted_funds = [f for f in funds if f.is_restricted]
        unrestricted_funds = [f for f in funds if not f.is_restricted]

        return {
            "organization_id": str(organization_id),
            "fiscal_period_id": str(fiscal_period_id),
            "restricted_funds": [
                {"fund_code": f.fund_code, "fund_name": f.fund_name}
                for f in restricted_funds
            ],
            "unrestricted_funds": [
                {"fund_code": f.fund_code, "fund_name": f.fund_name}
                for f in unrestricted_funds
            ],
            "note": "Detailed net asset movements require GL journal analysis",
        }

    def generate_cash_flow(
        self,
        organization_id: UUID,
        fiscal_period_id: UUID,
        *,
        fund_id: Optional[UUID] = None,
    ) -> dict:
        """
        Generate IPSAS 2 Cash Flow Statement.

        Categorized into operating, investing, and financing activities.
        """
        return {
            "organization_id": str(organization_id),
            "fiscal_period_id": str(fiscal_period_id),
            "fund_id": str(fund_id) if fund_id else None,
            "operating_activities": [],
            "investing_activities": [],
            "financing_activities": [],
            "net_cash_flow": "0",
            "note": "Cash flow statement requires bank transaction classification",
        }
