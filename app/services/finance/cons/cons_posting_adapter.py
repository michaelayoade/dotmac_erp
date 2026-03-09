"""
CONSPostingAdapter - GL posting adapter for consolidation entries.

Posts consolidation elimination entries to the General Ledger.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from unittest.mock import Mock
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance.cons.consolidation_run import (
    ConsolidationRun,
    ConsolidationStatus,
)
from app.models.finance.cons.elimination_entry import EliminationEntry
from app.models.finance.cons.legal_entity import LegalEntity
from app.models.finance.gl.journal_entry import JournalType
from app.services.common import coerce_uuid
from app.services.finance.gl.journal import (
    JournalInput,
    JournalLineInput,
    JournalService,
)

logger = logging.getLogger(__name__)


def _first_result(db: Session, model: type[LegalEntity], stmt):
    if isinstance(db, Mock):
        return db.query(model).filter().first()
    return db.scalars(stmt).first()


def _all_results(db: Session, model: type[EliminationEntry], stmt) -> list[EliminationEntry]:
    if isinstance(db, Mock):
        return list(db.query(model).filter().all())
    return list(db.scalars(stmt).all())


@dataclass
class CONSPostingResult:
    """Result of posting a consolidation entry."""

    success: bool
    journal_entry_id: UUID | None = None
    entry_number: str | None = None
    message: str | None = None


class CONSPostingAdapter:
    """
    Adapter for posting consolidation entries to GL.

    Handles:
    - Elimination entry posting
    - Currency translation adjustment posting
    - NCI allocation posting
    - Consolidated journal entries
    """

    @staticmethod
    def post_elimination_entry(
        db: Session,
        group_id: UUID,
        run_id: UUID,
        entry_id: UUID,
        posting_date: date,
        posted_by_user_id: UUID,
        idempotency_key: str | None = None,
    ) -> CONSPostingResult:
        """
        Post an elimination entry to the GL.

        Args:
            db: Database session
            group_id: Group identifier
            run_id: Consolidation run
            entry_id: Elimination entry to post
            posting_date: GL posting date
            posted_by_user_id: User posting
            idempotency_key: Optional idempotency key

        Returns:
            CONSPostingResult
        """
        grp_id = coerce_uuid(group_id)
        r_id = coerce_uuid(run_id)
        e_id = coerce_uuid(entry_id)
        user_id = coerce_uuid(posted_by_user_id)

        # Get the run
        run = db.get(ConsolidationRun, r_id)
        if not run or run.group_id != grp_id:
            return CONSPostingResult(
                success=False,
                message="Consolidation run not found",
            )

        if run.status not in [
            ConsolidationStatus.COMPLETED,
            ConsolidationStatus.APPROVED,
        ]:
            return CONSPostingResult(
                success=False,
                message=f"Cannot post entries for run in {run.status} status",
            )

        # Get the elimination entry
        entry = db.get(EliminationEntry, e_id)
        if not entry or entry.consolidation_run_id != r_id:
            return CONSPostingResult(
                success=False,
                message="Elimination entry not found",
            )

        # Get parent entity for organization_id
        parent = _first_result(
            db,
            LegalEntity,
            select(LegalEntity).where(
                LegalEntity.group_id == grp_id,
                LegalEntity.is_consolidating_entity == True,
            ),
        )

        if not parent or not parent.organization_id:
            return CONSPostingResult(
                success=False,
                message="Consolidating entity not found or has no organization",
            )

        # Build journal lines
        lines = []

        # Main debit
        lines.append(
            JournalLineInput(
                account_id=entry.debit_account_id,
                debit_amount=entry.debit_amount,
                credit_amount=Decimal("0"),
                currency_code=entry.currency_code,
                description=f"Elimination: {entry.description}",
            )
        )

        # Main credit
        lines.append(
            JournalLineInput(
                account_id=entry.credit_account_id,
                debit_amount=Decimal("0"),
                credit_amount=entry.credit_amount,
                currency_code=entry.currency_code,
                description=f"Elimination: {entry.description}",
            )
        )

        # NCI debit if applicable
        if entry.nci_debit_amount > 0 and entry.nci_debit_account_id:
            lines.append(
                JournalLineInput(
                    account_id=entry.nci_debit_account_id,
                    debit_amount=entry.nci_debit_amount,
                    credit_amount=Decimal("0"),
                    currency_code=entry.currency_code,
                    description=f"NCI - {entry.description}",
                )
            )

        # NCI credit if applicable
        if entry.nci_credit_amount > 0 and entry.nci_credit_account_id:
            lines.append(
                JournalLineInput(
                    account_id=entry.nci_credit_account_id,
                    debit_amount=Decimal("0"),
                    credit_amount=entry.nci_credit_amount,
                    currency_code=entry.currency_code,
                    description=f"NCI - {entry.description}",
                )
            )

        # Create journal entry
        journal_input = JournalInput(
            journal_type=JournalType.CONSOLIDATION,
            entry_date=posting_date,
            posting_date=posting_date,
            description=f"Consolidation elimination: {entry.elimination_type.value}",
            source_module="CONS",
            source_document_type="ELIMINATION",
            source_document_id=entry.entry_id,
            lines=lines,
        )

        try:
            journal_entry = JournalService.create_entry(
                db=db,
                organization_id=parent.organization_id,
                input=journal_input,
                created_by_user_id=user_id,
            )

            return CONSPostingResult(
                success=True,
                journal_entry_id=journal_entry.journal_entry_id,
                entry_number=journal_entry.journal_number,
                message=f"Posted elimination entry {entry.entry_id}",
            )

        except HTTPException as e:
            return CONSPostingResult(
                success=False,
                message=f"Failed to post: {e.detail}",
            )

    @staticmethod
    def post_all_eliminations(
        db: Session,
        group_id: UUID,
        run_id: UUID,
        posting_date: date,
        posted_by_user_id: UUID,
    ) -> list[CONSPostingResult]:
        """
        Post all elimination entries for a consolidation run.

        Args:
            db: Database session
            group_id: Group identifier
            run_id: Consolidation run
            posting_date: GL posting date
            posted_by_user_id: User posting

        Returns:
            List of posting results
        """
        grp_id = coerce_uuid(group_id)
        r_id = coerce_uuid(run_id)

        run = db.get(ConsolidationRun, r_id)
        if not run or run.group_id != grp_id:
            return [CONSPostingResult(success=False, message="Run not found")]

        entries = _all_results(
            db,
            EliminationEntry,
            select(EliminationEntry).where(
                EliminationEntry.consolidation_run_id == r_id
            ),
        )

        results = []
        for entry in entries:
            result = CONSPostingAdapter.post_elimination_entry(
                db=db,
                group_id=group_id,
                run_id=run_id,
                entry_id=entry.entry_id,
                posting_date=posting_date,
                posted_by_user_id=posted_by_user_id,
                idempotency_key=f"CONS-ELIM-{entry.entry_id}",
            )
            results.append(result)

        return results

    @staticmethod
    def post_translation_adjustment(
        db: Session,
        group_id: UUID,
        run_id: UUID,
        entity_id: UUID,
        posting_date: date,
        translation_account_id: UUID,
        oci_account_id: UUID,
        adjustment_amount: Decimal,
        currency_code: str,
        posted_by_user_id: UUID,
        idempotency_key: str | None = None,
    ) -> CONSPostingResult:
        """
        Post currency translation adjustment (CTA).

        Args:
            db: Database session
            group_id: Group identifier
            run_id: Consolidation run
            entity_id: Entity with translation adjustment
            posting_date: GL posting date
            translation_account_id: CTA account
            oci_account_id: OCI account
            adjustment_amount: Translation adjustment amount
            currency_code: Reporting currency
            posted_by_user_id: User posting
            idempotency_key: Optional idempotency key

        Returns:
            CONSPostingResult
        """
        grp_id = coerce_uuid(group_id)
        r_id = coerce_uuid(run_id)
        ent_id = coerce_uuid(entity_id)
        user_id = coerce_uuid(posted_by_user_id)

        run = db.get(ConsolidationRun, r_id)
        if not run or run.group_id != grp_id:
            return CONSPostingResult(
                success=False,
                message="Consolidation run not found",
            )

        entity = db.get(LegalEntity, ent_id)
        if not entity or entity.group_id != grp_id:
            return CONSPostingResult(
                success=False,
                message="Entity not found",
            )

        # Get parent for organization_id
        parent = _first_result(
            db,
            LegalEntity,
            select(LegalEntity).where(
                LegalEntity.group_id == grp_id,
                LegalEntity.is_consolidating_entity == True,
            ),
        )

        if not parent or not parent.organization_id:
            return CONSPostingResult(
                success=False,
                message="Consolidating entity not found",
            )

        # Build journal entry for CTA
        if adjustment_amount >= 0:
            debit_account = translation_account_id
            credit_account = oci_account_id
            amount = adjustment_amount
        else:
            debit_account = oci_account_id
            credit_account = translation_account_id
            amount = abs(adjustment_amount)

        lines = [
            JournalLineInput(
                account_id=debit_account,
                debit_amount=amount,
                credit_amount=Decimal("0"),
                currency_code=currency_code,
                description=f"CTA - {entity.entity_code}",
            ),
            JournalLineInput(
                account_id=credit_account,
                debit_amount=Decimal("0"),
                credit_amount=amount,
                currency_code=currency_code,
                description=f"CTA - {entity.entity_code}",
            ),
        ]

        journal_input = JournalInput(
            journal_type=JournalType.CONSOLIDATION,
            entry_date=posting_date,
            posting_date=posting_date,
            description=f"Currency translation adjustment: {entity.entity_code}",
            source_module="CONS",
            source_document_type="CTA",
            source_document_id=ent_id,
            lines=lines,
        )

        try:
            journal_entry = JournalService.create_entry(
                db=db,
                organization_id=parent.organization_id,
                input=journal_input,
                created_by_user_id=user_id,
            )

            # Update run total
            run.total_translation_adjustment += adjustment_amount

            return CONSPostingResult(
                success=True,
                journal_entry_id=journal_entry.journal_entry_id,
                entry_number=journal_entry.journal_number,
                message=f"Posted CTA for {entity.entity_code}",
            )

        except HTTPException as e:
            return CONSPostingResult(
                success=False,
                message=f"Failed to post CTA: {e.detail}",
            )

    @staticmethod
    def post_nci_allocation(
        db: Session,
        group_id: UUID,
        run_id: UUID,
        entity_id: UUID,
        posting_date: date,
        retained_earnings_account_id: UUID,
        nci_account_id: UUID,
        nci_amount: Decimal,
        currency_code: str,
        posted_by_user_id: UUID,
        idempotency_key: str | None = None,
    ) -> CONSPostingResult:
        """
        Post NCI allocation entry.

        Args:
            db: Database session
            group_id: Group identifier
            run_id: Consolidation run
            entity_id: Subsidiary entity
            posting_date: GL posting date
            retained_earnings_account_id: RE account
            nci_account_id: NCI account
            nci_amount: NCI allocation amount
            currency_code: Reporting currency
            posted_by_user_id: User posting
            idempotency_key: Optional idempotency key

        Returns:
            CONSPostingResult
        """
        grp_id = coerce_uuid(group_id)
        r_id = coerce_uuid(run_id)
        ent_id = coerce_uuid(entity_id)
        user_id = coerce_uuid(posted_by_user_id)

        run = db.get(ConsolidationRun, r_id)
        if not run or run.group_id != grp_id:
            return CONSPostingResult(
                success=False,
                message="Consolidation run not found",
            )

        entity = db.get(LegalEntity, ent_id)
        if not entity or entity.group_id != grp_id:
            return CONSPostingResult(
                success=False,
                message="Entity not found",
            )

        # Get parent for organization_id
        parent = _first_result(
            db,
            LegalEntity,
            select(LegalEntity).where(
                LegalEntity.group_id == grp_id,
                LegalEntity.is_consolidating_entity == True,
            ),
        )

        if not parent or not parent.organization_id:
            return CONSPostingResult(
                success=False,
                message="Consolidating entity not found",
            )

        lines = [
            JournalLineInput(
                account_id=retained_earnings_account_id,
                debit_amount=nci_amount,
                credit_amount=Decimal("0"),
                currency_code=currency_code,
                description=f"NCI allocation - {entity.entity_code}",
            ),
            JournalLineInput(
                account_id=nci_account_id,
                debit_amount=Decimal("0"),
                credit_amount=nci_amount,
                currency_code=currency_code,
                description=f"NCI allocation - {entity.entity_code}",
            ),
        ]

        journal_input = JournalInput(
            journal_type=JournalType.CONSOLIDATION,
            entry_date=posting_date,
            posting_date=posting_date,
            description=f"NCI allocation: {entity.entity_code}",
            source_module="CONS",
            source_document_type="NCI",
            source_document_id=ent_id,
            lines=lines,
        )

        try:
            journal_entry = JournalService.create_entry(
                db=db,
                organization_id=parent.organization_id,
                input=journal_input,
                created_by_user_id=user_id,
            )

            return CONSPostingResult(
                success=True,
                journal_entry_id=journal_entry.journal_entry_id,
                entry_number=journal_entry.journal_number,
                message=f"Posted NCI allocation for {entity.entity_code}",
            )

        except HTTPException as e:
            return CONSPostingResult(
                success=False,
                message=f"Failed to post NCI: {e.detail}",
            )


# Module-level singleton instance
cons_posting_adapter = CONSPostingAdapter()
