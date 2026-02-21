"""
DisclosureChecklistService - IFRS disclosure checklist management.

Manages IFRS disclosure requirements tracking and completion status.
"""

from __future__ import annotations

import builtins
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import Integer, func, select
from sqlalchemy.orm import Session

from app.models.finance.rpt.disclosure_checklist import (
    DisclosureChecklist,
    DisclosureStatus,
)
from app.services.common import coerce_uuid
from app.services.response import ListResponseMixin

logger = logging.getLogger(__name__)


@dataclass
class DisclosureItemInput:
    """Input for creating a disclosure checklist item."""

    fiscal_period_id: UUID
    disclosure_code: str
    disclosure_name: str
    ifrs_standard: str
    sequence_number: int
    paragraph_reference: str | None = None
    description: str | None = None
    parent_checklist_id: UUID | None = None
    indent_level: int = 0
    is_mandatory: bool = True
    applicability_criteria: str | None = None


@dataclass
class DisclosureCompletionInput:
    """Input for completing a disclosure item."""

    disclosure_location: str | None = None
    notes: str | None = None


@dataclass
class DisclosureSummary:
    """Summary of disclosure completion status."""

    total_items: int
    not_started: int
    in_progress: int
    completed: int
    not_applicable: int
    reviewed: int
    completion_percentage: Decimal
    mandatory_incomplete: int


@dataclass
class StandardSummary:
    """Summary by IFRS standard."""

    ifrs_standard: str
    total_items: int
    completed: int
    completion_percentage: Decimal


class DisclosureChecklistService(ListResponseMixin):
    """
    Service for IFRS disclosure checklist management.

    Handles:
    - Disclosure item tracking
    - Completion status management
    - Review workflow
    - Compliance reporting
    """

    @staticmethod
    def create_item(
        db: Session,
        organization_id: UUID,
        input: DisclosureItemInput,
    ) -> DisclosureChecklist:
        """
        Create a disclosure checklist item.

        Args:
            db: Database session
            organization_id: Organization scope
            input: Item input data

        Returns:
            Created DisclosureChecklist
        """
        org_id = coerce_uuid(organization_id)

        # Check for duplicate
        existing = db.scalar(
            select(DisclosureChecklist).where(
                DisclosureChecklist.organization_id == org_id,
                DisclosureChecklist.fiscal_period_id == input.fiscal_period_id,
                DisclosureChecklist.disclosure_code == input.disclosure_code,
            )
        )
        if existing:
            raise HTTPException(
                status_code=400,
                detail=f"Disclosure {input.disclosure_code} already exists for this period",
            )

        # Validate parent if specified
        if input.parent_checklist_id:
            parent = db.get(DisclosureChecklist, input.parent_checklist_id)
            if not parent or parent.organization_id != org_id:
                raise HTTPException(status_code=404, detail="Parent item not found")

        item = DisclosureChecklist(
            organization_id=org_id,
            fiscal_period_id=input.fiscal_period_id,
            disclosure_code=input.disclosure_code,
            disclosure_name=input.disclosure_name,
            description=input.description,
            ifrs_standard=input.ifrs_standard,
            paragraph_reference=input.paragraph_reference,
            parent_checklist_id=input.parent_checklist_id,
            sequence_number=input.sequence_number,
            indent_level=input.indent_level,
            is_mandatory=input.is_mandatory,
            applicability_criteria=input.applicability_criteria,
            status=DisclosureStatus.NOT_STARTED,
        )

        db.add(item)
        db.commit()
        db.refresh(item)

        return item

    @staticmethod
    def start_item(
        db: Session,
        organization_id: UUID,
        checklist_id: UUID,
    ) -> DisclosureChecklist:
        """
        Mark disclosure item as in progress.

        Args:
            db: Database session
            organization_id: Organization scope
            checklist_id: Item to start

        Returns:
            Updated DisclosureChecklist
        """
        org_id = coerce_uuid(organization_id)
        chk_id = coerce_uuid(checklist_id)

        item = db.get(DisclosureChecklist, chk_id)
        if not item or item.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Disclosure item not found")

        if item.status not in [
            DisclosureStatus.NOT_STARTED,
            DisclosureStatus.IN_PROGRESS,
        ]:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot start item in {item.status} status",
            )

        item.status = DisclosureStatus.IN_PROGRESS
        db.commit()
        db.refresh(item)

        return item

    @staticmethod
    def complete_item(
        db: Session,
        organization_id: UUID,
        checklist_id: UUID,
        completed_by_user_id: UUID,
        input: DisclosureCompletionInput,
    ) -> DisclosureChecklist:
        """
        Mark disclosure item as completed.

        Args:
            db: Database session
            organization_id: Organization scope
            checklist_id: Item to complete
            completed_by_user_id: User completing the item
            input: Completion details

        Returns:
            Updated DisclosureChecklist
        """
        org_id = coerce_uuid(organization_id)
        chk_id = coerce_uuid(checklist_id)
        user_id = coerce_uuid(completed_by_user_id)

        item = db.get(DisclosureChecklist, chk_id)
        if not item or item.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Disclosure item not found")

        if item.status == DisclosureStatus.REVIEWED:
            raise HTTPException(
                status_code=400,
                detail="Cannot modify reviewed items",
            )

        item.status = DisclosureStatus.COMPLETED
        item.disclosure_location = input.disclosure_location
        item.notes = input.notes
        item.completed_by_user_id = user_id
        item.completed_at = datetime.now(UTC)

        db.commit()
        db.refresh(item)

        return item

    @staticmethod
    def mark_not_applicable(
        db: Session,
        organization_id: UUID,
        checklist_id: UUID,
        reason: str,
        marked_by_user_id: UUID,
    ) -> DisclosureChecklist:
        """
        Mark disclosure item as not applicable.

        Args:
            db: Database session
            organization_id: Organization scope
            checklist_id: Item to mark
            reason: Reason for N/A
            marked_by_user_id: User marking the item

        Returns:
            Updated DisclosureChecklist
        """
        org_id = coerce_uuid(organization_id)
        chk_id = coerce_uuid(checklist_id)
        user_id = coerce_uuid(marked_by_user_id)

        item = db.get(DisclosureChecklist, chk_id)
        if not item or item.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Disclosure item not found")

        if item.status == DisclosureStatus.REVIEWED:
            raise HTTPException(
                status_code=400,
                detail="Cannot modify reviewed items",
            )

        item.status = DisclosureStatus.NOT_APPLICABLE
        item.notes = reason
        item.completed_by_user_id = user_id
        item.completed_at = datetime.now(UTC)

        db.commit()
        db.refresh(item)

        return item

    @staticmethod
    def review_item(
        db: Session,
        organization_id: UUID,
        checklist_id: UUID,
        reviewed_by_user_id: UUID,
    ) -> DisclosureChecklist:
        """
        Mark disclosure item as reviewed.

        Args:
            db: Database session
            organization_id: Organization scope
            checklist_id: Item to review
            reviewed_by_user_id: User reviewing

        Returns:
            Updated DisclosureChecklist
        """
        org_id = coerce_uuid(organization_id)
        chk_id = coerce_uuid(checklist_id)
        user_id = coerce_uuid(reviewed_by_user_id)

        item = db.get(DisclosureChecklist, chk_id)
        if not item or item.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Disclosure item not found")

        if item.status not in [
            DisclosureStatus.COMPLETED,
            DisclosureStatus.NOT_APPLICABLE,
        ]:
            raise HTTPException(
                status_code=400,
                detail=f"Can only review completed or N/A items, current: {item.status}",
            )

        # SoD check
        if item.completed_by_user_id == user_id:
            raise HTTPException(
                status_code=400,
                detail="Segregation of duties: completer cannot review",
            )

        item.status = DisclosureStatus.REVIEWED
        item.reviewed_by_user_id = user_id
        item.reviewed_at = datetime.now(UTC)

        db.commit()
        db.refresh(item)

        return item

    @staticmethod
    def reopen_item(
        db: Session,
        organization_id: UUID,
        checklist_id: UUID,
    ) -> DisclosureChecklist:
        """
        Reopen a completed item for revision.

        Args:
            db: Database session
            organization_id: Organization scope
            checklist_id: Item to reopen

        Returns:
            Updated DisclosureChecklist
        """
        org_id = coerce_uuid(organization_id)
        chk_id = coerce_uuid(checklist_id)

        item = db.get(DisclosureChecklist, chk_id)
        if not item or item.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Disclosure item not found")

        if item.status == DisclosureStatus.REVIEWED:
            raise HTTPException(
                status_code=400,
                detail="Cannot reopen reviewed items without manager approval",
            )

        item.status = DisclosureStatus.IN_PROGRESS
        item.completed_by_user_id = None
        item.completed_at = None

        db.commit()
        db.refresh(item)

        return item

    @staticmethod
    def get_summary(
        db: Session,
        organization_id: str,
        fiscal_period_id: str,
    ) -> DisclosureSummary:
        """
        Get disclosure completion summary for a period.

        Args:
            db: Database session
            organization_id: Organization scope
            fiscal_period_id: Fiscal period

        Returns:
            DisclosureSummary
        """
        org_id = coerce_uuid(organization_id)
        period_id = coerce_uuid(fiscal_period_id)

        items = list(
            db.scalars(
                select(DisclosureChecklist).where(
                    DisclosureChecklist.organization_id == org_id,
                    DisclosureChecklist.fiscal_period_id == period_id,
                )
            )
        )

        total = len(items)
        not_started = len(
            [i for i in items if i.status == DisclosureStatus.NOT_STARTED]
        )
        in_progress = len(
            [i for i in items if i.status == DisclosureStatus.IN_PROGRESS]
        )
        completed = len([i for i in items if i.status == DisclosureStatus.COMPLETED])
        not_applicable = len(
            [i for i in items if i.status == DisclosureStatus.NOT_APPLICABLE]
        )
        reviewed = len([i for i in items if i.status == DisclosureStatus.REVIEWED])

        # Mandatory items not completed
        mandatory_incomplete = len(
            [
                i
                for i in items
                if i.is_mandatory
                and i.status
                in [DisclosureStatus.NOT_STARTED, DisclosureStatus.IN_PROGRESS]
            ]
        )

        # Completion % = (completed + reviewed + N/A) / total
        completion_count = completed + reviewed + not_applicable
        completion_pct = (
            Decimal(completion_count * 100 / total).quantize(Decimal("0.01"))
            if total > 0
            else Decimal("0")
        )

        return DisclosureSummary(
            total_items=total,
            not_started=not_started,
            in_progress=in_progress,
            completed=completed,
            not_applicable=not_applicable,
            reviewed=reviewed,
            completion_percentage=completion_pct,
            mandatory_incomplete=mandatory_incomplete,
        )

    @staticmethod
    def get_summary_by_standard(
        db: Session,
        organization_id: str,
        fiscal_period_id: str,
    ) -> list[StandardSummary]:
        """
        Get summary grouped by IFRS standard.

        Args:
            db: Database session
            organization_id: Organization scope
            fiscal_period_id: Fiscal period

        Returns:
            List of StandardSummary
        """
        org_id = coerce_uuid(organization_id)
        period_id = coerce_uuid(fiscal_period_id)

        results = list(
            db.execute(
                select(
                    DisclosureChecklist.ifrs_standard,
                    func.count(DisclosureChecklist.checklist_id).label("total"),
                    func.sum(
                        func.cast(
                            DisclosureChecklist.status.in_(
                                [
                                    DisclosureStatus.COMPLETED,
                                    DisclosureStatus.REVIEWED,
                                    DisclosureStatus.NOT_APPLICABLE,
                                ]
                            ),
                            Integer,
                        )
                    ).label("completed"),
                )
                .where(
                    DisclosureChecklist.organization_id == org_id,
                    DisclosureChecklist.fiscal_period_id == period_id,
                )
                .group_by(DisclosureChecklist.ifrs_standard)
                .order_by(DisclosureChecklist.ifrs_standard)
            )
        )

        summaries = []
        for row in results:
            total = row.total or 0
            completed = row.completed or 0
            pct = (
                Decimal(completed * 100 / total).quantize(Decimal("0.01"))
                if total > 0
                else Decimal("0")
            )
            summaries.append(
                StandardSummary(
                    ifrs_standard=row.ifrs_standard,
                    total_items=total,
                    completed=completed,
                    completion_percentage=pct,
                )
            )

        return summaries

    @staticmethod
    def get_incomplete_mandatory(
        db: Session,
        organization_id: str,
        fiscal_period_id: str,
    ) -> builtins.list[DisclosureChecklist]:
        """
        Get incomplete mandatory disclosure items.

        Args:
            db: Database session
            organization_id: Organization scope
            fiscal_period_id: Fiscal period

        Returns:
            List of incomplete mandatory items
        """
        org_id = coerce_uuid(organization_id)
        period_id = coerce_uuid(fiscal_period_id)

        return list(
            db.scalars(
                select(DisclosureChecklist)
                .where(
                    DisclosureChecklist.organization_id == org_id,
                    DisclosureChecklist.fiscal_period_id == period_id,
                    DisclosureChecklist.is_mandatory == True,
                    DisclosureChecklist.status.in_(
                        [
                            DisclosureStatus.NOT_STARTED,
                            DisclosureStatus.IN_PROGRESS,
                        ]
                    ),
                )
                .order_by(
                    DisclosureChecklist.ifrs_standard,
                    DisclosureChecklist.sequence_number,
                )
            )
        )

    @staticmethod
    def copy_checklist_to_period(
        db: Session,
        organization_id: UUID,
        source_period_id: UUID,
        target_period_id: UUID,
    ) -> builtins.list[DisclosureChecklist]:
        """
        Copy checklist from one period to another.

        Args:
            db: Database session
            organization_id: Organization scope
            source_period_id: Source period
            target_period_id: Target period

        Returns:
            Created checklist items
        """
        org_id = coerce_uuid(organization_id)
        src_period = coerce_uuid(source_period_id)
        tgt_period = coerce_uuid(target_period_id)

        # Get source items
        source_items = list(
            db.scalars(
                select(DisclosureChecklist)
                .where(
                    DisclosureChecklist.organization_id == org_id,
                    DisclosureChecklist.fiscal_period_id == src_period,
                )
                .order_by(DisclosureChecklist.sequence_number)
            )
        )

        # Map old IDs to new IDs
        id_map: dict[UUID, UUID] = {}
        created_items = []

        for source in source_items:
            new_item = DisclosureChecklist(
                organization_id=org_id,
                fiscal_period_id=tgt_period,
                disclosure_code=source.disclosure_code,
                disclosure_name=source.disclosure_name,
                description=source.description,
                ifrs_standard=source.ifrs_standard,
                paragraph_reference=source.paragraph_reference,
                sequence_number=source.sequence_number,
                indent_level=source.indent_level,
                is_mandatory=source.is_mandatory,
                applicability_criteria=source.applicability_criteria,
                status=DisclosureStatus.NOT_STARTED,
            )

            db.add(new_item)
            db.flush()

            id_map[source.checklist_id] = new_item.checklist_id
            created_items.append(new_item)

        # Update parent references
        for i, source in enumerate(source_items):
            if source.parent_checklist_id and source.parent_checklist_id in id_map:
                created_items[i].parent_checklist_id = id_map[
                    source.parent_checklist_id
                ]

        db.commit()

        for item in created_items:
            db.refresh(item)

        return created_items

    @staticmethod
    def record_completion(
        db: Session,
        organization_id: UUID,
        checklist_id: UUID,
        user_id: UUID,
        is_complete: bool,
        evidence_reference: str | None = None,
        notes: str | None = None,
    ) -> DisclosureChecklist:
        """Record disclosure item completion or mark as not applicable.

        Delegates to ``complete_item`` when *is_complete* is ``True``,
        otherwise delegates to ``mark_not_applicable``.

        Args:
            db: Database session
            organization_id: Organization scope
            checklist_id: Disclosure checklist item ID
            user_id: User recording the completion
            is_complete: True to complete, False to mark N/A
            evidence_reference: Location of disclosure evidence
            notes: Additional notes

        Returns:
            Updated DisclosureChecklist
        """
        input_data = DisclosureCompletionInput(
            disclosure_location=evidence_reference,
            notes=notes,
        )
        if is_complete:
            return DisclosureChecklistService.complete_item(
                db=db,
                organization_id=organization_id,
                checklist_id=checklist_id,
                completed_by_user_id=user_id,
                input=input_data,
            )
        return DisclosureChecklistService.mark_not_applicable(
            db=db,
            organization_id=organization_id,
            checklist_id=checklist_id,
            reason=notes or "Not applicable",
            marked_by_user_id=user_id,
        )

    @staticmethod
    def get(
        db: Session,
        checklist_id: str,
        organization_id: UUID | None = None,
    ) -> DisclosureChecklist:
        """Get a disclosure checklist item by ID."""
        item = db.get(DisclosureChecklist, coerce_uuid(checklist_id))
        if not item:
            raise HTTPException(status_code=404, detail="Disclosure item not found")
        if organization_id is not None and item.organization_id != coerce_uuid(
            organization_id
        ):
            raise HTTPException(status_code=404, detail="Disclosure item not found")
        return item

    @staticmethod
    def list(
        db: Session,
        organization_id: str | None = None,
        fiscal_period_id: str | None = None,
        ifrs_standard: str | None = None,
        status: DisclosureStatus | None = None,
        is_mandatory: bool | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[DisclosureChecklist]:
        """List disclosure checklist items with optional filters."""
        stmt = select(DisclosureChecklist)

        if organization_id:
            stmt = stmt.where(
                DisclosureChecklist.organization_id == coerce_uuid(organization_id)
            )

        if fiscal_period_id:
            stmt = stmt.where(
                DisclosureChecklist.fiscal_period_id == coerce_uuid(fiscal_period_id)
            )

        if ifrs_standard:
            stmt = stmt.where(DisclosureChecklist.ifrs_standard == ifrs_standard)

        if status:
            stmt = stmt.where(DisclosureChecklist.status == status)

        if is_mandatory is not None:
            stmt = stmt.where(DisclosureChecklist.is_mandatory == is_mandatory)

        stmt = stmt.order_by(
            DisclosureChecklist.ifrs_standard,
            DisclosureChecklist.sequence_number,
        )
        stmt = stmt.limit(limit).offset(offset)
        return list(db.scalars(stmt))


# Module-level singleton instance
disclosure_checklist_service = DisclosureChecklistService()
