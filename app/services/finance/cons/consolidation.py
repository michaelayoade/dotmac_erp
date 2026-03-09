"""
ConsolidationService - Consolidation run management.

Manages consolidation runs, eliminations, and consolidated balances (IFRS 10).
"""

from __future__ import annotations

import builtins
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.finance.cons.consolidated_balance import ConsolidatedBalance
from app.models.finance.cons.consolidation_run import (
    ConsolidationRun,
    ConsolidationStatus,
)
from app.models.finance.cons.elimination_entry import (
    EliminationEntry,
    EliminationType,
)
from app.models.finance.cons.intercompany_balance import IntercompanyBalance
from app.models.finance.cons.legal_entity import (
    ConsolidationMethod,
    LegalEntity,
)
from app.models.finance.cons.ownership_interest import OwnershipInterest
from app.services.common import coerce_uuid
from app.services.response import ListResponseMixin

logger = logging.getLogger(__name__)


@dataclass
class ConsolidationRunInput:
    """Input for creating a consolidation run."""

    fiscal_period_id: UUID
    reporting_currency_code: str
    run_description: str | None = None


@dataclass
class EliminationInput:
    """Input for creating an elimination entry."""

    elimination_type: EliminationType
    description: str
    currency_code: str
    debit_account_id: UUID
    debit_amount: Decimal
    credit_account_id: UUID
    credit_amount: Decimal
    entity_1_id: UUID | None = None
    entity_2_id: UUID | None = None
    source_balance_id: UUID | None = None
    nci_debit_account_id: UUID | None = None
    nci_debit_amount: Decimal = Decimal("0")
    nci_credit_account_id: UUID | None = None
    nci_credit_amount: Decimal = Decimal("0")
    is_automatic: bool = True


@dataclass
class ConsolidationSummary:
    """Summary of a consolidation run."""

    run_id: UUID
    status: ConsolidationStatus
    entities_count: int
    elimination_count: int
    total_eliminations: Decimal
    total_translation_adjustment: Decimal
    total_nci: Decimal
    intercompany_differences: Decimal


class ConsolidationService(ListResponseMixin):
    """
    Service for consolidation management.

    Handles:
    - Consolidation run lifecycle
    - Elimination entry generation
    - Consolidated balance calculation
    - NCI allocation
    - Currency translation
    """

    @staticmethod
    def create_run(
        db: Session,
        group_id: UUID,
        input: ConsolidationRunInput,
        created_by_user_id: UUID,
    ) -> ConsolidationRun:
        """
        Create a new consolidation run.

        Args:
            db: Database session
            group_id: Group identifier
            input: Run input data
            created_by_user_id: User creating the run

        Returns:
            Created ConsolidationRun
        """
        grp_id = coerce_uuid(group_id)
        user_id = coerce_uuid(created_by_user_id)

        # Get next run number for this period
        max_run = db.scalar(
            select(func.max(ConsolidationRun.run_number)).where(
                ConsolidationRun.group_id == grp_id,
                ConsolidationRun.fiscal_period_id == input.fiscal_period_id,
            )
        )
        run_number = (max_run or 0) + 1

        # Count entities
        entities = list(
            db.scalars(
                select(LegalEntity).where(
                    LegalEntity.group_id == grp_id,
                    LegalEntity.is_active == True,
                    LegalEntity.consolidation_method
                    != ConsolidationMethod.NOT_CONSOLIDATED,
                )
            ).all()
        )

        subsidiaries_count = len(
            [e for e in entities if e.consolidation_method == ConsolidationMethod.FULL]
        )
        associates_count = len(
            [
                e
                for e in entities
                if e.consolidation_method == ConsolidationMethod.EQUITY
            ]
        )

        run = ConsolidationRun(
            group_id=grp_id,
            fiscal_period_id=input.fiscal_period_id,
            run_number=run_number,
            run_description=input.run_description,
            reporting_currency_code=input.reporting_currency_code,
            status=ConsolidationStatus.DRAFT,
            entities_count=len(entities),
            subsidiaries_count=subsidiaries_count,
            associates_count=associates_count,
            created_by_user_id=user_id,
        )

        db.add(run)
        db.commit()
        db.refresh(run)

        return run

    @staticmethod
    def start_run(
        db: Session,
        group_id: UUID,
        run_id: UUID,
    ) -> ConsolidationRun:
        """
        Start a consolidation run.

        Args:
            db: Database session
            group_id: Group identifier
            run_id: Run to start

        Returns:
            Updated ConsolidationRun
        """
        grp_id = coerce_uuid(group_id)
        r_id = coerce_uuid(run_id)

        run = db.get(ConsolidationRun, r_id)
        if not run or run.group_id != grp_id:
            raise HTTPException(status_code=404, detail="Consolidation run not found")

        if run.status != ConsolidationStatus.DRAFT:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot start run in {run.status} status",
            )

        run.status = ConsolidationStatus.IN_PROGRESS
        run.started_at = datetime.now(UTC)

        db.commit()
        db.refresh(run)

        return run

    @staticmethod
    def create_elimination_entry(
        db: Session,
        group_id: UUID,
        run_id: UUID,
        input: EliminationInput,
    ) -> EliminationEntry:
        """
        Create an elimination entry.

        Args:
            db: Database session
            group_id: Group identifier
            run_id: Consolidation run
            input: Elimination input data

        Returns:
            Created EliminationEntry
        """
        grp_id = coerce_uuid(group_id)
        r_id = coerce_uuid(run_id)

        run = db.get(ConsolidationRun, r_id)
        if not run or run.group_id != grp_id:
            raise HTTPException(status_code=404, detail="Consolidation run not found")

        if run.status != ConsolidationStatus.IN_PROGRESS:
            raise HTTPException(
                status_code=400,
                detail="Eliminations can only be created for in-progress runs",
            )

        # Validate debit = credit
        total_debit = input.debit_amount + input.nci_debit_amount
        total_credit = input.credit_amount + input.nci_credit_amount
        if total_debit != total_credit:
            raise HTTPException(
                status_code=400,
                detail=f"Elimination must balance: debit {total_debit} != credit {total_credit}",
            )

        entry = EliminationEntry(
            consolidation_run_id=r_id,
            elimination_type=input.elimination_type,
            description=input.description,
            entity_1_id=input.entity_1_id,
            entity_2_id=input.entity_2_id,
            source_balance_id=input.source_balance_id,
            currency_code=input.currency_code,
            debit_account_id=input.debit_account_id,
            debit_amount=input.debit_amount,
            credit_account_id=input.credit_account_id,
            credit_amount=input.credit_amount,
            nci_debit_account_id=input.nci_debit_account_id,
            nci_debit_amount=input.nci_debit_amount,
            nci_credit_account_id=input.nci_credit_account_id,
            nci_credit_amount=input.nci_credit_amount,
            is_automatic=input.is_automatic,
        )

        db.add(entry)

        # Update run statistics
        run.elimination_entries_count += 1
        run.total_eliminations_amount += input.debit_amount
        run.total_nci += input.nci_debit_amount

        db.commit()
        db.refresh(entry)

        return entry

    @staticmethod
    def generate_intercompany_eliminations(
        db: Session,
        group_id: UUID,
        run_id: UUID,
        intercompany_elimination_account_id: UUID,
    ) -> builtins.list[EliminationEntry]:
        """
        Generate elimination entries for intercompany balances.

        Args:
            db: Database session
            group_id: Group identifier
            run_id: Consolidation run
            intercompany_elimination_account_id: GL account for eliminations

        Returns:
            List of created elimination entries
        """
        grp_id = coerce_uuid(group_id)
        r_id = coerce_uuid(run_id)

        run = db.get(ConsolidationRun, r_id)
        if not run or run.group_id != grp_id:
            raise HTTPException(status_code=404, detail="Consolidation run not found")

        # Get entities in group
        entities = list(
            db.scalars(
                select(LegalEntity).where(
                    LegalEntity.group_id == grp_id,
                    LegalEntity.is_active == True,
                )
            ).all()
        )
        entity_ids = [e.entity_id for e in entities]
        entity_map = {e.entity_id: e for e in entities}

        # Get matched intercompany balances
        balances = list(
            db.scalars(
                select(IntercompanyBalance).where(
                    IntercompanyBalance.fiscal_period_id == run.fiscal_period_id,
                    IntercompanyBalance.from_entity_id.in_(entity_ids),
                    IntercompanyBalance.is_matched == True,
                    IntercompanyBalance.is_eliminated == False,
                )
            ).all()
        )

        entries = []
        processed_pairs = set()

        for balance in balances:
            # Skip if we've already processed this pair
            pair_key = tuple(
                sorted([str(balance.from_entity_id), str(balance.to_entity_id)])
            )
            if pair_key in processed_pairs:
                continue
            processed_pairs.add(pair_key)

            from_entity = entity_map.get(balance.from_entity_id)
            to_entity = entity_map.get(balance.to_entity_id)

            if not from_entity or not to_entity:
                continue

            elimination_input = EliminationInput(
                elimination_type=EliminationType.INTERCOMPANY_BALANCE,
                description=f"Eliminate IC balance: {from_entity.entity_code} <-> {to_entity.entity_code}",
                currency_code=balance.reporting_currency_code,
                debit_account_id=balance.to_entity_gl_account_id,
                debit_amount=abs(balance.reporting_currency_amount),
                credit_account_id=balance.from_entity_gl_account_id,
                credit_amount=abs(balance.reporting_currency_amount),
                entity_1_id=balance.from_entity_id,
                entity_2_id=balance.to_entity_id,
                source_balance_id=balance.balance_id,
                is_automatic=True,
            )

            entry = ConsolidationService.create_elimination_entry(
                db, group_id, run_id, elimination_input
            )
            entries.append(entry)

            # Mark balance as eliminated
            balance.is_eliminated = True
            balance.elimination_entry_id = entry.entry_id

        # Update intercompany differences on run
        unmatched = db.scalar(
            select(func.sum(IntercompanyBalance.difference_amount)).where(
                IntercompanyBalance.fiscal_period_id == run.fiscal_period_id,
                IntercompanyBalance.from_entity_id.in_(entity_ids),
                IntercompanyBalance.is_matched == False,
            )
        )
        run.intercompany_differences = unmatched or Decimal("0")

        db.commit()
        return entries

    @staticmethod
    def generate_investment_eliminations(
        db: Session,
        group_id: UUID,
        run_id: UUID,
        investment_account_id: UUID,
        equity_account_id: UUID,
        goodwill_account_id: UUID,
        nci_account_id: UUID,
    ) -> list[EliminationEntry]:
        """
        Generate investment in subsidiary eliminations.

        Args:
            db: Database session
            group_id: Group identifier
            run_id: Consolidation run
            investment_account_id: Investment in subsidiary account
            equity_account_id: Subsidiary equity account
            goodwill_account_id: Goodwill account
            nci_account_id: NCI account

        Returns:
            List of created elimination entries
        """
        grp_id = coerce_uuid(group_id)
        r_id = coerce_uuid(run_id)

        run = db.get(ConsolidationRun, r_id)
        if not run or run.group_id != grp_id:
            raise HTTPException(status_code=404, detail="Consolidation run not found")

        # Get subsidiaries with ownership
        subsidiaries = list(
            db.scalars(
                select(LegalEntity).where(
                    LegalEntity.group_id == grp_id,
                    LegalEntity.is_active == True,
                    LegalEntity.consolidation_method == ConsolidationMethod.FULL,
                )
            ).all()
        )

        entries = []

        for subsidiary in subsidiaries:
            ownership = db.scalars(
                select(OwnershipInterest).where(
                    OwnershipInterest.investee_entity_id == subsidiary.entity_id,
                    OwnershipInterest.is_current == True,
                )
            ).first()

            if not ownership or not ownership.investment_cost:
                continue

            investment_cost = ownership.investment_cost
            goodwill = subsidiary.goodwill_at_acquisition or Decimal("0")
            nci_at_acquisition = ownership.nci_at_acquisition or Decimal("0")

            # Equity elimination = Investment cost - Goodwill - NCI at acquisition
            equity_to_eliminate = investment_cost - goodwill + nci_at_acquisition

            # Entry 1: Eliminate investment against equity
            elimination_input = EliminationInput(
                elimination_type=EliminationType.INVESTMENT_IN_SUBSIDIARY,
                description=f"Eliminate investment in {subsidiary.entity_code}",
                currency_code=run.reporting_currency_code,
                debit_account_id=equity_account_id,
                debit_amount=equity_to_eliminate,
                credit_account_id=investment_account_id,
                credit_amount=investment_cost,
                entity_1_id=ownership.investor_entity_id,
                entity_2_id=subsidiary.entity_id,
                nci_credit_account_id=nci_account_id,
                nci_credit_amount=nci_at_acquisition,
                is_automatic=True,
            )

            # Adjust for goodwill
            if goodwill > 0:
                elimination_input.debit_amount = equity_to_eliminate + goodwill
                # Need to also debit goodwill - handle separately

            entry = ConsolidationService.create_elimination_entry(
                db, group_id, run_id, elimination_input
            )
            entries.append(entry)

        db.commit()
        return entries

    @staticmethod
    def complete_run(
        db: Session,
        group_id: UUID,
        run_id: UUID,
    ) -> ConsolidationRun:
        """
        Complete a consolidation run.

        Args:
            db: Database session
            group_id: Group identifier
            run_id: Run to complete

        Returns:
            Updated ConsolidationRun
        """
        grp_id = coerce_uuid(group_id)
        r_id = coerce_uuid(run_id)

        run = db.get(ConsolidationRun, r_id)
        if not run or run.group_id != grp_id:
            raise HTTPException(status_code=404, detail="Consolidation run not found")

        if run.status != ConsolidationStatus.IN_PROGRESS:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot complete run in {run.status} status",
            )

        run.status = ConsolidationStatus.COMPLETED
        run.completed_at = datetime.now(UTC)

        db.commit()
        db.refresh(run)

        return run

    @staticmethod
    def approve_run(
        db: Session,
        group_id: UUID,
        run_id: UUID,
        approved_by_user_id: UUID,
    ) -> ConsolidationRun:
        """
        Approve a consolidation run.

        Args:
            db: Database session
            group_id: Group identifier
            run_id: Run to approve
            approved_by_user_id: Approving user

        Returns:
            Updated ConsolidationRun
        """
        grp_id = coerce_uuid(group_id)
        r_id = coerce_uuid(run_id)
        user_id = coerce_uuid(approved_by_user_id)

        run = db.get(ConsolidationRun, r_id)
        if not run or run.group_id != grp_id:
            raise HTTPException(status_code=404, detail="Consolidation run not found")

        if run.status != ConsolidationStatus.COMPLETED:
            raise HTTPException(
                status_code=400,
                detail="Can only approve completed runs",
            )

        # SoD check
        if run.created_by_user_id == user_id:
            raise HTTPException(
                status_code=400,
                detail="Segregation of duties: creator cannot approve",
            )

        run.status = ConsolidationStatus.APPROVED
        run.approved_by_user_id = user_id
        run.approved_at = datetime.now(UTC)

        db.commit()
        db.refresh(run)

        return run

    @staticmethod
    def create_consolidated_balance(
        db: Session,
        run_id: UUID,
        account_id: UUID,
        currency_code: str,
        subsidiary_balance_sum: Decimal,
        segment_id: UUID | None = None,
        equity_method_balance: Decimal = Decimal("0"),
        intercompany_eliminations: Decimal = Decimal("0"),
        investment_eliminations: Decimal = Decimal("0"),
        unrealized_profit_eliminations: Decimal = Decimal("0"),
        other_eliminations: Decimal = Decimal("0"),
        translation_adjustment: Decimal = Decimal("0"),
        nci_share: Decimal = Decimal("0"),
    ) -> ConsolidatedBalance:
        """
        Create a consolidated balance record.

        Args:
            db: Database session
            run_id: Consolidation run
            account_id: GL account
            currency_code: Reporting currency
            subsidiary_balance_sum: Sum of subsidiary balances
            segment_id: Optional segment
            equity_method_balance: Equity method investments
            intercompany_eliminations: IC eliminations
            investment_eliminations: Investment eliminations
            unrealized_profit_eliminations: Unrealized profit eliminations
            other_eliminations: Other eliminations
            translation_adjustment: CTA
            nci_share: NCI portion

        Returns:
            Created ConsolidatedBalance
        """
        r_id = coerce_uuid(run_id)

        total_eliminations = (
            intercompany_eliminations
            + investment_eliminations
            + unrealized_profit_eliminations
            + other_eliminations
        )

        consolidated_balance = (
            subsidiary_balance_sum
            + equity_method_balance
            - total_eliminations
            + translation_adjustment
        )

        parent_share = consolidated_balance - nci_share

        balance = ConsolidatedBalance(
            consolidation_run_id=r_id,
            account_id=account_id,
            segment_id=segment_id,
            currency_code=currency_code,
            subsidiary_balance_sum=subsidiary_balance_sum,
            equity_method_balance=equity_method_balance,
            intercompany_eliminations=intercompany_eliminations,
            investment_eliminations=investment_eliminations,
            unrealized_profit_eliminations=unrealized_profit_eliminations,
            other_eliminations=other_eliminations,
            total_eliminations=total_eliminations,
            translation_adjustment=translation_adjustment,
            nci_share=nci_share,
            consolidated_balance=consolidated_balance,
            parent_share=parent_share,
        )

        db.add(balance)
        db.commit()
        db.refresh(balance)

        return balance

    @staticmethod
    def get_summary(
        db: Session,
        group_id: UUID,
        run_id: UUID,
    ) -> ConsolidationSummary:
        """
        Get consolidation run summary.

        Args:
            db: Database session
            group_id: Group identifier
            run_id: Consolidation run

        Returns:
            ConsolidationSummary
        """
        grp_id = coerce_uuid(group_id)
        r_id = coerce_uuid(run_id)

        run = db.get(ConsolidationRun, r_id)
        if not run or run.group_id != grp_id:
            raise HTTPException(status_code=404, detail="Consolidation run not found")

        return ConsolidationSummary(
            run_id=run.run_id,
            status=run.status,
            entities_count=run.entities_count,
            elimination_count=run.elimination_entries_count,
            total_eliminations=run.total_eliminations_amount,
            total_translation_adjustment=run.total_translation_adjustment,
            total_nci=run.total_nci,
            intercompany_differences=run.intercompany_differences,
        )

    @staticmethod
    def get_elimination_entries(
        db: Session,
        run_id: UUID,
        elimination_type: EliminationType | None = None,
    ) -> list[EliminationEntry]:
        """Get elimination entries for a run."""
        r_id = coerce_uuid(run_id)

        query = select(EliminationEntry).where(
            EliminationEntry.consolidation_run_id == r_id
        )

        if elimination_type:
            query = query.where(EliminationEntry.elimination_type == elimination_type)

        return list(db.scalars(query.order_by(EliminationEntry.created_at)).all())

    @staticmethod
    def get_consolidated_balances(
        db: Session,
        run_id: UUID,
        segment_id: UUID | None = None,
    ) -> builtins.list[ConsolidatedBalance]:
        """Get consolidated balances for a run."""
        r_id = coerce_uuid(run_id)

        query = select(ConsolidatedBalance).where(
            ConsolidatedBalance.consolidation_run_id == r_id
        )

        if segment_id:
            query = query.where(
                ConsolidatedBalance.segment_id == coerce_uuid(segment_id)
            )

        return list(db.scalars(query).all())

    @staticmethod
    def get(
        db: Session,
        run_id: str,
        group_id: UUID | None = None,
    ) -> ConsolidationRun:
        """Get a consolidation run by ID."""
        run = db.get(ConsolidationRun, coerce_uuid(run_id))
        if not run:
            raise HTTPException(status_code=404, detail="Consolidation run not found")
        if group_id is not None and run.group_id != coerce_uuid(group_id):
            raise HTTPException(status_code=404, detail="Consolidation run not found")
        return run

    @staticmethod
    def list(
        db: Session,
        group_id: str | None = None,
        fiscal_period_id: str | None = None,
        status: ConsolidationStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> builtins.list[ConsolidationRun]:
        """List consolidation runs with optional filters."""
        query = select(ConsolidationRun)

        if group_id:
            query = query.where(ConsolidationRun.group_id == coerce_uuid(group_id))

        if fiscal_period_id:
            query = query.where(
                ConsolidationRun.fiscal_period_id == coerce_uuid(fiscal_period_id)
            )

        if status:
            query = query.where(ConsolidationRun.status == status)

        query = query.order_by(ConsolidationRun.created_at.desc())
        return list(db.scalars(query.limit(limit).offset(offset)).all())


# Module-level singleton instance
consolidation_service = ConsolidationService()
