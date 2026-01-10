"""
IntercompanyService - Intercompany balance management.

Manages intercompany balances, matching, and reconciliation (IFRS 10).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.ifrs.cons.intercompany_balance import IntercompanyBalance
from app.models.ifrs.cons.legal_entity import LegalEntity
from app.services.common import coerce_uuid
from app.services.response import ListResponseMixin


@dataclass
class IntercompanyBalanceInput:
    """Input for creating intercompany balance."""

    fiscal_period_id: UUID
    balance_date: date
    from_entity_id: UUID
    to_entity_id: UUID
    balance_type: str
    from_entity_gl_account_id: UUID
    from_entity_currency: str
    from_entity_amount: Decimal
    from_entity_functional_amount: Decimal
    to_entity_gl_account_id: UUID
    to_entity_currency: str
    to_entity_amount: Decimal
    to_entity_functional_amount: Decimal
    reporting_currency_code: str
    reporting_currency_amount: Decimal
    balance_description: Optional[str] = None


@dataclass
class MatchingResult:
    """Result of intercompany matching."""

    balance_id: UUID
    from_entity_code: str
    to_entity_code: str
    balance_type: str
    from_amount: Decimal
    to_amount: Decimal
    difference: Decimal
    is_matched: bool
    difference_reason: Optional[str]


@dataclass
class IntercompanySummary:
    """Summary of intercompany balances."""

    balance_type: str
    total_from_amount: Decimal
    total_to_amount: Decimal
    total_difference: Decimal
    matched_count: int
    unmatched_count: int


class IntercompanyService(ListResponseMixin):
    """
    Service for intercompany balance management.

    Handles:
    - Intercompany balance recording
    - Balance matching between entities
    - Difference identification
    - Reconciliation reporting
    """

    @staticmethod
    def record_balance(
        db: Session,
        group_id: UUID,
        input: IntercompanyBalanceInput,
    ) -> IntercompanyBalance:
        """
        Record intercompany balance.

        Args:
            db: Database session
            group_id: Group identifier
            input: Balance input data

        Returns:
            Created IntercompanyBalance
        """
        grp_id = coerce_uuid(group_id)

        # Validate entities belong to group
        from_entity = db.get(LegalEntity, input.from_entity_id)
        if not from_entity or from_entity.group_id != grp_id:
            raise HTTPException(status_code=404, detail="From entity not found in group")

        to_entity = db.get(LegalEntity, input.to_entity_id)
        if not to_entity or to_entity.group_id != grp_id:
            raise HTTPException(status_code=404, detail="To entity not found in group")

        # Prevent self-intercompany
        if input.from_entity_id == input.to_entity_id:
            raise HTTPException(
                status_code=400,
                detail="Cannot create intercompany balance with same entity",
            )

        # Check for existing balance
        existing = (
            db.query(IntercompanyBalance)
            .filter(
                IntercompanyBalance.fiscal_period_id == input.fiscal_period_id,
                IntercompanyBalance.from_entity_id == input.from_entity_id,
                IntercompanyBalance.to_entity_id == input.to_entity_id,
                IntercompanyBalance.balance_type == input.balance_type,
            )
            .first()
        )

        if existing:
            raise HTTPException(
                status_code=400,
                detail="Intercompany balance already exists for this period and type",
            )

        # Calculate initial difference (will be updated during matching)
        difference = input.from_entity_functional_amount + input.to_entity_functional_amount

        balance = IntercompanyBalance(
            fiscal_period_id=input.fiscal_period_id,
            balance_date=input.balance_date,
            from_entity_id=input.from_entity_id,
            to_entity_id=input.to_entity_id,
            balance_type=input.balance_type,
            balance_description=input.balance_description,
            from_entity_gl_account_id=input.from_entity_gl_account_id,
            from_entity_currency=input.from_entity_currency,
            from_entity_amount=input.from_entity_amount,
            from_entity_functional_amount=input.from_entity_functional_amount,
            to_entity_gl_account_id=input.to_entity_gl_account_id,
            to_entity_currency=input.to_entity_currency,
            to_entity_amount=input.to_entity_amount,
            to_entity_functional_amount=input.to_entity_functional_amount,
            reporting_currency_code=input.reporting_currency_code,
            reporting_currency_amount=input.reporting_currency_amount,
            is_matched=False,
            difference_amount=difference,
        )

        db.add(balance)
        db.commit()
        db.refresh(balance)

        return balance

    @staticmethod
    def perform_matching(
        db: Session,
        group_id: UUID,
        fiscal_period_id: UUID,
        tolerance: Decimal = Decimal("0.01"),
    ) -> list[MatchingResult]:
        """
        Perform intercompany balance matching.

        Args:
            db: Database session
            group_id: Group identifier
            fiscal_period_id: Period to match
            tolerance: Matching tolerance amount

        Returns:
            List of matching results
        """
        grp_id = coerce_uuid(group_id)
        period_id = coerce_uuid(fiscal_period_id)

        # Get all entities in group
        entities = (
            db.query(LegalEntity)
            .filter(
                LegalEntity.group_id == grp_id,
                LegalEntity.is_active == True,
            )
            .all()
        )
        entity_ids = [e.entity_id for e in entities]
        entity_map = {e.entity_id: e.entity_code for e in entities}

        # Get all balances for the period
        balances = (
            db.query(IntercompanyBalance)
            .filter(
                IntercompanyBalance.fiscal_period_id == period_id,
                IntercompanyBalance.from_entity_id.in_(entity_ids),
            )
            .all()
        )

        results = []

        for balance in balances:
            # Find reciprocal balance
            reciprocal = (
                db.query(IntercompanyBalance)
                .filter(
                    IntercompanyBalance.fiscal_period_id == period_id,
                    IntercompanyBalance.from_entity_id == balance.to_entity_id,
                    IntercompanyBalance.to_entity_id == balance.from_entity_id,
                    IntercompanyBalance.balance_type == balance.balance_type,
                )
                .first()
            )

            if reciprocal:
                # Calculate difference in reporting currency
                # From entity records a receivable (+), To entity records a payable (-)
                # They should net to zero if matched
                difference = abs(
                    balance.reporting_currency_amount + reciprocal.reporting_currency_amount
                )

                is_matched = difference <= tolerance
                difference_reason = None

                if not is_matched:
                    if balance.from_entity_currency != reciprocal.to_entity_currency:
                        difference_reason = "Currency mismatch"
                    elif abs(balance.from_entity_amount) != abs(reciprocal.to_entity_amount):
                        difference_reason = "Transaction amount mismatch"
                    else:
                        difference_reason = "Exchange rate difference"

                # Update both balances
                balance.is_matched = is_matched
                balance.difference_amount = difference
                balance.difference_reason = difference_reason

                reciprocal.is_matched = is_matched
                reciprocal.difference_amount = difference
                reciprocal.difference_reason = difference_reason

            else:
                # No reciprocal found
                balance.is_matched = False
                balance.difference_amount = abs(balance.reporting_currency_amount)
                balance.difference_reason = "No reciprocal balance found"

            results.append(
                MatchingResult(
                    balance_id=balance.balance_id,
                    from_entity_code=entity_map.get(balance.from_entity_id, "Unknown"),
                    to_entity_code=entity_map.get(balance.to_entity_id, "Unknown"),
                    balance_type=balance.balance_type,
                    from_amount=balance.reporting_currency_amount,
                    to_amount=reciprocal.reporting_currency_amount if reciprocal else Decimal("0"),
                    difference=balance.difference_amount,
                    is_matched=balance.is_matched,
                    difference_reason=balance.difference_reason,
                )
            )

        db.commit()
        return results

    @staticmethod
    def resolve_difference(
        db: Session,
        group_id: UUID,
        balance_id: UUID,
        resolution_reason: str,
        adjusted_amount: Optional[Decimal] = None,
    ) -> IntercompanyBalance:
        """
        Resolve intercompany difference.

        Args:
            db: Database session
            group_id: Group identifier
            balance_id: Balance to resolve
            resolution_reason: Explanation of resolution
            adjusted_amount: Adjusted amount if applicable

        Returns:
            Updated IntercompanyBalance
        """
        grp_id = coerce_uuid(group_id)
        bal_id = coerce_uuid(balance_id)

        balance = db.get(IntercompanyBalance, bal_id)
        if not balance:
            raise HTTPException(status_code=404, detail="Balance not found")

        # Verify entity belongs to group
        from_entity = db.get(LegalEntity, balance.from_entity_id)
        if not from_entity or from_entity.group_id != grp_id:
            raise HTTPException(status_code=404, detail="Balance not in group")

        balance.difference_reason = resolution_reason
        if adjusted_amount is not None:
            balance.difference_amount = adjusted_amount

        # Mark as matched if difference is now zero
        if balance.difference_amount == Decimal("0"):
            balance.is_matched = True

        db.commit()
        db.refresh(balance)

        return balance

    @staticmethod
    def get_unmatched_balances(
        db: Session,
        group_id: UUID,
        fiscal_period_id: UUID,
    ) -> list[IntercompanyBalance]:
        """
        Get unmatched intercompany balances.

        Args:
            db: Database session
            group_id: Group identifier
            fiscal_period_id: Period to query

        Returns:
            List of unmatched balances
        """
        grp_id = coerce_uuid(group_id)
        period_id = coerce_uuid(fiscal_period_id)

        # Get entities in group
        entities = (
            db.query(LegalEntity)
            .filter(
                LegalEntity.group_id == grp_id,
                LegalEntity.is_active == True,
            )
            .all()
        )
        entity_ids = [e.entity_id for e in entities]

        return (
            db.query(IntercompanyBalance)
            .filter(
                IntercompanyBalance.fiscal_period_id == period_id,
                IntercompanyBalance.from_entity_id.in_(entity_ids),
                IntercompanyBalance.is_matched == False,
            )
            .order_by(IntercompanyBalance.difference_amount.desc())
            .all()
        )

    @staticmethod
    def get_summary_by_type(
        db: Session,
        group_id: UUID,
        fiscal_period_id: UUID,
    ) -> list[IntercompanySummary]:
        """
        Get intercompany summary by balance type.

        Args:
            db: Database session
            group_id: Group identifier
            fiscal_period_id: Period to summarize

        Returns:
            List of summaries by balance type
        """
        grp_id = coerce_uuid(group_id)
        period_id = coerce_uuid(fiscal_period_id)

        # Get entities in group
        entities = (
            db.query(LegalEntity)
            .filter(
                LegalEntity.group_id == grp_id,
                LegalEntity.is_active == True,
            )
            .all()
        )
        entity_ids = [e.entity_id for e in entities]

        results = (
            db.query(
                IntercompanyBalance.balance_type,
                func.sum(IntercompanyBalance.from_entity_functional_amount).label("total_from"),
                func.sum(IntercompanyBalance.to_entity_functional_amount).label("total_to"),
                func.sum(IntercompanyBalance.difference_amount).label("total_diff"),
                func.sum(func.cast(IntercompanyBalance.is_matched, Decimal)).label("matched"),
                func.count(IntercompanyBalance.balance_id).label("total_count"),
            )
            .filter(
                IntercompanyBalance.fiscal_period_id == period_id,
                IntercompanyBalance.from_entity_id.in_(entity_ids),
            )
            .group_by(IntercompanyBalance.balance_type)
            .all()
        )

        summaries = []
        for row in results:
            matched_count = int(row.matched or 0)
            total_count = row.total_count or 0
            summaries.append(
                IntercompanySummary(
                    balance_type=row.balance_type,
                    total_from_amount=row.total_from or Decimal("0"),
                    total_to_amount=row.total_to or Decimal("0"),
                    total_difference=row.total_diff or Decimal("0"),
                    matched_count=matched_count,
                    unmatched_count=total_count - matched_count,
                )
            )

        return summaries

    @staticmethod
    def get_balances_for_elimination(
        db: Session,
        group_id: UUID,
        fiscal_period_id: UUID,
    ) -> list[IntercompanyBalance]:
        """
        Get matched balances ready for elimination.

        Args:
            db: Database session
            group_id: Group identifier
            fiscal_period_id: Period to query

        Returns:
            List of balances for elimination
        """
        grp_id = coerce_uuid(group_id)
        period_id = coerce_uuid(fiscal_period_id)

        # Get entities in group
        entities = (
            db.query(LegalEntity)
            .filter(
                LegalEntity.group_id == grp_id,
                LegalEntity.is_active == True,
            )
            .all()
        )
        entity_ids = [e.entity_id for e in entities]

        return (
            db.query(IntercompanyBalance)
            .filter(
                IntercompanyBalance.fiscal_period_id == period_id,
                IntercompanyBalance.from_entity_id.in_(entity_ids),
                IntercompanyBalance.is_matched == True,
                IntercompanyBalance.is_eliminated == False,
            )
            .all()
        )

    @staticmethod
    def mark_as_eliminated(
        db: Session,
        group_id: UUID,
        balance_ids: list[UUID],
        elimination_entry_id: UUID,
    ) -> int:
        """
        Mark balances as eliminated.

        Args:
            db: Database session
            group_id: Group identifier
            balance_ids: Balances to mark
            elimination_entry_id: Related elimination entry

        Returns:
            Number of balances updated
        """
        grp_id = coerce_uuid(group_id)
        elim_id = coerce_uuid(elimination_entry_id)

        # Get entities in group
        entities = (
            db.query(LegalEntity)
            .filter(
                LegalEntity.group_id == grp_id,
                LegalEntity.is_active == True,
            )
            .all()
        )
        entity_ids = [e.entity_id for e in entities]

        updated = 0
        for bal_id in balance_ids:
            balance = db.get(IntercompanyBalance, coerce_uuid(bal_id))
            if balance and balance.from_entity_id in entity_ids:
                balance.is_eliminated = True
                balance.elimination_entry_id = elim_id
                updated += 1

        db.commit()
        return updated

    @staticmethod
    def get(
        db: Session,
        balance_id: str,
    ) -> IntercompanyBalance:
        """Get an intercompany balance by ID."""
        balance = db.get(IntercompanyBalance, coerce_uuid(balance_id))
        if not balance:
            raise HTTPException(status_code=404, detail="Intercompany balance not found")
        return balance

    @staticmethod
    def list(
        db: Session,
        fiscal_period_id: Optional[str] = None,
        from_entity_id: Optional[str] = None,
        to_entity_id: Optional[str] = None,
        balance_type: Optional[str] = None,
        is_matched: Optional[bool] = None,
        is_eliminated: Optional[bool] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[IntercompanyBalance]:
        """List intercompany balances with optional filters."""
        query = db.query(IntercompanyBalance)

        if fiscal_period_id:
            query = query.filter(
                IntercompanyBalance.fiscal_period_id == coerce_uuid(fiscal_period_id)
            )

        if from_entity_id:
            query = query.filter(
                IntercompanyBalance.from_entity_id == coerce_uuid(from_entity_id)
            )

        if to_entity_id:
            query = query.filter(
                IntercompanyBalance.to_entity_id == coerce_uuid(to_entity_id)
            )

        if balance_type:
            query = query.filter(IntercompanyBalance.balance_type == balance_type)

        if is_matched is not None:
            query = query.filter(IntercompanyBalance.is_matched == is_matched)

        if is_eliminated is not None:
            query = query.filter(IntercompanyBalance.is_eliminated == is_eliminated)

        query = query.order_by(IntercompanyBalance.balance_date.desc())
        return query.limit(limit).offset(offset).all()


# Module-level singleton instance
intercompany_service = IntercompanyService()
