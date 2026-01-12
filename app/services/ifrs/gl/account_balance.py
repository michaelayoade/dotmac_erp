"""
AccountBalanceService - Account balance management and caching.

Updates balance cache on posting events and provides balance queries.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Optional, TYPE_CHECKING
from uuid import UUID
import uuid as uuid_lib

from fastapi import HTTPException
from sqlalchemy import and_, func
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.ifrs.gl.account_balance import AccountBalance, BalanceType
from app.models.ifrs.gl.posted_ledger_line import PostedLedgerLine
from app.models.ifrs.gl.fiscal_period import FiscalPeriod
from app.models.ifrs.gl.account import Account
from app.services.common import coerce_uuid
from app.services.response import ListResponseMixin
from app.services.ifrs.platform.org_context import org_context_service

if TYPE_CHECKING:
    from app.schemas.ifrs.gl import TrialBalanceRead

logger = logging.getLogger(__name__)


@dataclass
class BalanceSummary:
    """Summary of account balances."""

    account_id: UUID
    account_code: str
    fiscal_period_id: UUID
    balance_type: str
    currency_code: str
    opening_balance: Decimal
    period_debit: Decimal
    period_credit: Decimal
    closing_balance: Decimal
    net_balance: Decimal
    transaction_count: int


class AccountBalanceService(ListResponseMixin):
    """
    Service for account balance management.

    Maintains pre-aggregated balances for efficient reporting.
    Updated via posting events or on-demand rebuild.
    """

    @staticmethod
    def update_balance_for_posting(
        db: Session,
        organization_id: UUID,
        account_id: UUID,
        fiscal_period_id: UUID,
        debit_amount: Decimal,
        credit_amount: Decimal,
        currency_code: Optional[str] = None,
        balance_type: BalanceType = BalanceType.ACTUAL,
        business_unit_id: Optional[UUID] = None,
        cost_center_id: Optional[UUID] = None,
        project_id: Optional[UUID] = None,
        segment_id: Optional[UUID] = None,
    ) -> AccountBalance:
        """
        Update balance for a posting.

        Creates or updates the balance record for the given dimensions.

        Args:
            db: Database session
            organization_id: Organization scope
            account_id: Account being posted to
            fiscal_period_id: Period of posting
            debit_amount: Debit amount (functional currency)
            credit_amount: Credit amount (functional currency)
            currency_code: Currency code
            balance_type: Type of balance
            business_unit_id: Optional business unit
            cost_center_id: Optional cost center
            project_id: Optional project
            segment_id: Optional segment

        Returns:
            Updated AccountBalance
        """
        org_id = coerce_uuid(organization_id)
        acct_id = coerce_uuid(account_id)
        period_id = coerce_uuid(fiscal_period_id)

        if not currency_code:
            currency_code = org_context_service.get_functional_currency(db, org_id)

        # Find existing balance
        balance = (
            db.query(AccountBalance)
            .filter(
                and_(
                    AccountBalance.organization_id == org_id,
                    AccountBalance.account_id == acct_id,
                    AccountBalance.fiscal_period_id == period_id,
                    AccountBalance.balance_type == balance_type,
                    AccountBalance.currency_code == currency_code,
                    AccountBalance.business_unit_id == (coerce_uuid(business_unit_id) if business_unit_id else None),
                    AccountBalance.cost_center_id == (coerce_uuid(cost_center_id) if cost_center_id else None),
                    AccountBalance.project_id == (coerce_uuid(project_id) if project_id else None),
                    AccountBalance.segment_id == (coerce_uuid(segment_id) if segment_id else None),
                )
            )
            .first()
        )

        if balance:
            # Update existing
            balance.period_debit += debit_amount
            balance.period_credit += credit_amount
            balance.closing_debit = balance.opening_debit + balance.period_debit
            balance.closing_credit = balance.opening_credit + balance.period_credit
            balance.net_balance = balance.closing_debit - balance.closing_credit
            balance.transaction_count += 1
            balance.last_updated_at = datetime.now(timezone.utc)
        else:
            # Create new
            balance = AccountBalance(
                organization_id=org_id,
                account_id=acct_id,
                fiscal_period_id=period_id,
                balance_type=balance_type,
                currency_code=currency_code,
                business_unit_id=coerce_uuid(business_unit_id) if business_unit_id else None,
                cost_center_id=coerce_uuid(cost_center_id) if cost_center_id else None,
                project_id=coerce_uuid(project_id) if project_id else None,
                segment_id=coerce_uuid(segment_id) if segment_id else None,
                opening_debit=Decimal("0"),
                opening_credit=Decimal("0"),
                period_debit=debit_amount,
                period_credit=credit_amount,
                closing_debit=debit_amount,
                closing_credit=credit_amount,
                net_balance=debit_amount - credit_amount,
                transaction_count=1,
            )
            db.add(balance)

        db.commit()
        db.refresh(balance)

        return balance

    @staticmethod
    def get_balance(
        db: Session,
        organization_id: UUID,
        account_id: UUID,
        fiscal_period_id: UUID,
        balance_type: BalanceType = BalanceType.ACTUAL,
        currency_code: Optional[str] = None,
        business_unit_id: Optional[UUID] = None,
        cost_center_id: Optional[UUID] = None,
        project_id: Optional[UUID] = None,
        segment_id: Optional[UUID] = None,
    ) -> Optional[AccountBalance]:
        """
        Get balance for specific dimensions.

        Args:
            db: Database session
            organization_id: Organization scope
            account_id: Account ID
            fiscal_period_id: Period ID
            balance_type: Type of balance
            currency_code: Currency code
            business_unit_id: Business unit filter
            cost_center_id: Cost center filter
            project_id: Project filter
            segment_id: Segment filter

        Returns:
            AccountBalance or None if not found
        """
        org_id = coerce_uuid(organization_id)

        if not currency_code:
            currency_code = org_context_service.get_functional_currency(db, org_id)

        return (
            db.query(AccountBalance)
            .filter(
                and_(
                    AccountBalance.organization_id == org_id,
                    AccountBalance.account_id == coerce_uuid(account_id),
                    AccountBalance.fiscal_period_id == coerce_uuid(fiscal_period_id),
                    AccountBalance.balance_type == balance_type,
                    AccountBalance.currency_code == currency_code,
                    AccountBalance.business_unit_id == (coerce_uuid(business_unit_id) if business_unit_id else None),
                    AccountBalance.cost_center_id == (coerce_uuid(cost_center_id) if cost_center_id else None),
                    AccountBalance.project_id == (coerce_uuid(project_id) if project_id else None),
                    AccountBalance.segment_id == (coerce_uuid(segment_id) if segment_id else None),
                )
            )
            .first()
        )

    @staticmethod
    def get_account_balances(
        db: Session,
        organization_id: UUID,
        fiscal_period_id: UUID,
        account_ids: Optional[list[UUID]] = None,
        balance_type: BalanceType = BalanceType.ACTUAL,
        aggregate_dimensions: bool = True,
    ) -> list[BalanceSummary]:
        """
        Get balances for accounts in a period.

        Args:
            db: Database session
            organization_id: Organization scope
            fiscal_period_id: Period ID
            account_ids: Optional list of account IDs to filter
            balance_type: Type of balance
            aggregate_dimensions: If True, aggregate across dimensions

        Returns:
            List of BalanceSummary objects
        """
        org_id = coerce_uuid(organization_id)
        period_id = coerce_uuid(fiscal_period_id)

        if aggregate_dimensions:
            # Aggregate balances across dimensions
            query = (
                db.query(
                    AccountBalance.account_id,
                    func.sum(AccountBalance.opening_debit - AccountBalance.opening_credit).label("opening_balance"),
                    func.sum(AccountBalance.period_debit).label("period_debit"),
                    func.sum(AccountBalance.period_credit).label("period_credit"),
                    func.sum(AccountBalance.closing_debit - AccountBalance.closing_credit).label("closing_balance"),
                    func.sum(AccountBalance.net_balance).label("net_balance"),
                    func.sum(AccountBalance.transaction_count).label("transaction_count"),
                )
                .filter(
                    and_(
                        AccountBalance.organization_id == org_id,
                        AccountBalance.fiscal_period_id == period_id,
                        AccountBalance.balance_type == balance_type,
                    )
                )
                .group_by(AccountBalance.account_id)
            )

            if account_ids:
                query = query.filter(AccountBalance.account_id.in_([coerce_uuid(a) for a in account_ids]))

            results = query.all()

            # Get account codes
            acct_ids = [r.account_id for r in results]
            accounts = db.query(Account).filter(Account.account_id.in_(acct_ids)).all()
            acct_map = {a.account_id: a.account_code for a in accounts}

            return [
                BalanceSummary(
                    account_id=r.account_id,
                    account_code=acct_map.get(r.account_id, ""),
                    fiscal_period_id=period_id,
                    balance_type=balance_type.value,
                    currency_code=org_context_service.get_functional_currency(db, org_id),
                    opening_balance=r.opening_balance or Decimal("0"),
                    period_debit=r.period_debit or Decimal("0"),
                    period_credit=r.period_credit or Decimal("0"),
                    closing_balance=r.closing_balance or Decimal("0"),
                    net_balance=r.net_balance or Decimal("0"),
                    transaction_count=r.transaction_count or 0,
                )
                for r in results
            ]
        else:
            # Return individual balance records
            query = db.query(AccountBalance).filter(
                and_(
                    AccountBalance.organization_id == org_id,
                    AccountBalance.fiscal_period_id == period_id,
                    AccountBalance.balance_type == balance_type,
                )
            )

            if account_ids:
                query = query.filter(AccountBalance.account_id.in_([coerce_uuid(a) for a in account_ids]))

            balances = query.all()

            # Get account codes
            acct_ids = [b.account_id for b in balances]
            accounts = db.query(Account).filter(Account.account_id.in_(acct_ids)).all()
            acct_map = {a.account_id: a.account_code for a in accounts}

            return [
                BalanceSummary(
                    account_id=b.account_id,
                    account_code=acct_map.get(b.account_id, ""),
                    fiscal_period_id=b.fiscal_period_id,
                    balance_type=b.balance_type.value,
                    currency_code=b.currency_code,
                    opening_balance=b.opening_debit - b.opening_credit,
                    period_debit=b.period_debit,
                    period_credit=b.period_credit,
                    closing_balance=b.closing_debit - b.closing_credit,
                    net_balance=b.net_balance,
                    transaction_count=b.transaction_count,
                )
                for b in balances
            ]

    @staticmethod
    def rebuild_balances_for_period(
        db: Session,
        organization_id: UUID,
        fiscal_period_id: UUID,
        balance_type: BalanceType = BalanceType.ACTUAL,
    ) -> int:
        """
        Rebuild balance cache from posted_ledger_line.

        Deletes existing balances and recalculates from source.

        Args:
            db: Database session
            organization_id: Organization scope
            fiscal_period_id: Period to rebuild
            balance_type: Type of balance to rebuild

        Returns:
            Number of balance records created
        """
        org_id = coerce_uuid(organization_id)
        period_id = coerce_uuid(fiscal_period_id)

        # 1. Delete existing balances for this period
        db.query(AccountBalance).filter(
            and_(
                AccountBalance.organization_id == org_id,
                AccountBalance.fiscal_period_id == period_id,
                AccountBalance.balance_type == balance_type,
            )
        ).delete()

        # 2. Aggregate from posted_ledger_line
        results = (
            db.query(
                PostedLedgerLine.account_id,
                PostedLedgerLine.business_unit_id,
                PostedLedgerLine.cost_center_id,
                PostedLedgerLine.project_id,
                PostedLedgerLine.segment_id,
                func.sum(PostedLedgerLine.debit_amount).label("total_debit"),
                func.sum(PostedLedgerLine.credit_amount).label("total_credit"),
                func.count().label("tx_count"),
            )
            .filter(
                and_(
                    PostedLedgerLine.organization_id == org_id,
                    PostedLedgerLine.fiscal_period_id == period_id,
                )
            )
            .group_by(
                PostedLedgerLine.account_id,
                PostedLedgerLine.business_unit_id,
                PostedLedgerLine.cost_center_id,
                PostedLedgerLine.project_id,
                PostedLedgerLine.segment_id,
            )
            .all()
        )

        # 3. Create balance records
        count = 0
        try:
            for r in results:
                debit = r.total_debit or Decimal("0")
                credit = r.total_credit or Decimal("0")

                balance = AccountBalance(
                    organization_id=org_id,
                    account_id=r.account_id,
                    fiscal_period_id=period_id,
                    balance_type=balance_type,
                    currency_code=org_context_service.get_functional_currency(db, org_id),
                    business_unit_id=r.business_unit_id,
                    cost_center_id=r.cost_center_id,
                    project_id=r.project_id,
                    segment_id=r.segment_id,
                    opening_debit=Decimal("0"),  # Will be set by period rollover
                    opening_credit=Decimal("0"),
                    period_debit=debit,
                    period_credit=credit,
                    closing_debit=debit,
                    closing_credit=credit,
                    net_balance=debit - credit,
                    transaction_count=r.tx_count,
                )
                db.add(balance)
                count += 1

            db.commit()
        except (SQLAlchemyError, InvalidOperation) as e:
            db.rollback()
            logger.error(f"Failed to rebuild balances for period {period_id}: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to rebuild balances: {str(e)}",
            )

        return count

    @staticmethod
    def rollover_opening_balances(
        db: Session,
        organization_id: UUID,
        from_period_id: UUID,
        to_period_id: UUID,
        balance_type: BalanceType = BalanceType.ACTUAL,
    ) -> int:
        """
        Roll over closing balances to opening balances for next period.

        Args:
            db: Database session
            organization_id: Organization scope
            from_period_id: Source period
            to_period_id: Target period
            balance_type: Type of balance

        Returns:
            Number of balance records updated
        """
        org_id = coerce_uuid(organization_id)
        from_id = coerce_uuid(from_period_id)
        to_id = coerce_uuid(to_period_id)

        # Get source period closing balances
        source_balances = (
            db.query(AccountBalance)
            .filter(
                and_(
                    AccountBalance.organization_id == org_id,
                    AccountBalance.fiscal_period_id == from_id,
                    AccountBalance.balance_type == balance_type,
                )
            )
            .all()
        )

        count = 0
        for source in source_balances:
            # Find or create target balance
            target = (
                db.query(AccountBalance)
                .filter(
                    and_(
                        AccountBalance.organization_id == org_id,
                        AccountBalance.account_id == source.account_id,
                        AccountBalance.fiscal_period_id == to_id,
                        AccountBalance.balance_type == balance_type,
                        AccountBalance.currency_code == source.currency_code,
                        AccountBalance.business_unit_id == source.business_unit_id,
                        AccountBalance.cost_center_id == source.cost_center_id,
                        AccountBalance.project_id == source.project_id,
                        AccountBalance.segment_id == source.segment_id,
                    )
                )
                .first()
            )

            if target:
                target.opening_debit = source.closing_debit
                target.opening_credit = source.closing_credit
                target.closing_debit = target.opening_debit + target.period_debit
                target.closing_credit = target.opening_credit + target.period_credit
                target.net_balance = target.closing_debit - target.closing_credit
            else:
                target = AccountBalance(
                    organization_id=org_id,
                    account_id=source.account_id,
                    fiscal_period_id=to_id,
                    balance_type=balance_type,
                    currency_code=source.currency_code,
                    business_unit_id=source.business_unit_id,
                    cost_center_id=source.cost_center_id,
                    project_id=source.project_id,
                    segment_id=source.segment_id,
                    opening_debit=source.closing_debit,
                    opening_credit=source.closing_credit,
                    period_debit=Decimal("0"),
                    period_credit=Decimal("0"),
                    closing_debit=source.closing_debit,
                    closing_credit=source.closing_credit,
                    net_balance=source.closing_debit - source.closing_credit,
                    transaction_count=0,
                )
                db.add(target)

            count += 1

        db.commit()

        return count

    @staticmethod
    def get_ytd_balance(
        db: Session,
        organization_id: UUID,
        account_id: UUID,
        fiscal_year_id: UUID,
        up_to_period_id: UUID,
        balance_type: BalanceType = BalanceType.ACTUAL,
    ) -> Decimal:
        """
        Get year-to-date balance for an account.

        Args:
            db: Database session
            organization_id: Organization scope
            account_id: Account ID
            fiscal_year_id: Fiscal year
            up_to_period_id: Calculate up to this period
            balance_type: Type of balance

        Returns:
            YTD net balance
        """
        org_id = coerce_uuid(organization_id)
        acct_id = coerce_uuid(account_id)
        year_id = coerce_uuid(fiscal_year_id)
        period_id = coerce_uuid(up_to_period_id)

        # Get target period
        target_period = db.get(FiscalPeriod, period_id)
        if not target_period:
            return Decimal("0")

        # Get all periods in the year up to target
        periods = (
            db.query(FiscalPeriod)
            .filter(
                and_(
                    FiscalPeriod.fiscal_year_id == year_id,
                    FiscalPeriod.organization_id == org_id,
                    FiscalPeriod.period_number <= target_period.period_number,
                )
            )
            .all()
        )

        period_ids = [p.fiscal_period_id for p in periods]

        # Sum balances across periods
        result = (
            db.query(func.sum(AccountBalance.net_balance))
            .filter(
                and_(
                    AccountBalance.organization_id == org_id,
                    AccountBalance.account_id == acct_id,
                    AccountBalance.fiscal_period_id.in_(period_ids),
                    AccountBalance.balance_type == balance_type,
                )
            )
            .scalar()
        )

        return result or Decimal("0")

    @staticmethod
    def list(
        db: Session,
        organization_id: Optional[str] = None,
        fiscal_period_id: Optional[str] = None,
        account_id: Optional[str] = None,
        balance_type: Optional[BalanceType] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[AccountBalance]:
        """
        List account balances.

        Args:
            db: Database session
            organization_id: Filter by organization
            fiscal_period_id: Filter by period
            account_id: Filter by account
            balance_type: Filter by balance type
            limit: Maximum results
            offset: Pagination offset

        Returns:
            List of AccountBalance objects
        """
        query = db.query(AccountBalance)

        if organization_id:
            query = query.filter(
                AccountBalance.organization_id == coerce_uuid(organization_id)
            )

        if fiscal_period_id:
            query = query.filter(
                AccountBalance.fiscal_period_id == coerce_uuid(fiscal_period_id)
            )

        if account_id:
            query = query.filter(AccountBalance.account_id == coerce_uuid(account_id))

        if balance_type:
            query = query.filter(AccountBalance.balance_type == balance_type)

        query = query.order_by(AccountBalance.last_updated_at.desc())
        return query.limit(limit).offset(offset).all()

    @staticmethod
    def get_trial_balance(
        db: Session,
        organization_id: str,
        fiscal_period_id: str,
        as_of_date: Optional[datetime] = None,
    ) -> "TrialBalanceRead":
        """
        Get trial balance for a fiscal period.

        Args:
            db: Database session
            organization_id: Organization scope
            fiscal_period_id: Period ID
            as_of_date: Optional as-of date

        Returns:
            TrialBalanceResult object
        """
        from app.schemas.ifrs.gl import TrialBalanceRead, TrialBalanceLineRead
        from datetime import date as date_type

        org_id = coerce_uuid(organization_id)
        period_id = coerce_uuid(fiscal_period_id)

        # Get fiscal period info
        period = db.get(FiscalPeriod, period_id)
        if not period:
            raise HTTPException(status_code=404, detail="Fiscal period not found")

        # Get balances
        balances = AccountBalanceService.get_account_balances(
            db=db,
            organization_id=org_id,
            fiscal_period_id=period_id,
        )

        # Get account details for all accounts
        acct_ids = [b.account_id for b in balances]
        accounts = db.query(Account).filter(Account.account_id.in_(acct_ids)).all()
        acct_map = {a.account_id: a for a in accounts}

        # Build trial balance lines
        lines = []
        total_debit = Decimal("0")
        total_credit = Decimal("0")

        for balance in balances:
            acct = acct_map.get(balance.account_id)
            if not acct:
                continue

            closing = balance.closing_balance
            if closing >= 0:
                debit_balance = closing
                credit_balance = Decimal("0")
                total_debit += closing
            else:
                debit_balance = Decimal("0")
                credit_balance = abs(closing)
                total_credit += abs(closing)

            lines.append(TrialBalanceLineRead(
                account_id=balance.account_id,
                account_code=acct.account_code,
                account_name=acct.account_name,
                account_type=str(acct.account_type.value) if acct.account_type else "POSTING",
                debit_balance=debit_balance,
                credit_balance=credit_balance,
            ))

        return TrialBalanceRead(
            fiscal_period_id=period_id,
            period_name=period.period_name,
            as_of_date=as_of_date or date_type.today(),
            lines=lines,
            total_debit=total_debit,
            total_credit=total_credit,
            is_balanced=abs(total_debit - total_credit) < Decimal("0.01"),
        )


# Module-level singleton instance
account_balance_service = AccountBalanceService()
