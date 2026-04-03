"""
Strategic Objective Service — OHCSF Performance Management System.

Handles creation, listing, updating, deletion, and analysis of
StrategicObjective records.  Supports the OHCSF hierarchical goal
cascade from MDA-level objectives down to departmental/unit objectives,
and produces an alignment report showing which objectives have linked KPIs.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.people.perf.strategic_objective import StrategicObjective
from app.services.common import PaginatedResult, PaginationParams, paginate
from app.services.people.perf.performance_mode_policy import enforce_pms_write_mode

logger = logging.getLogger(__name__)

__all__ = [
    "StrategicObjectiveServiceError",
    "StrategicObjectiveNotFoundError",
    "StrategicObjectiveService",
]

# Fields that may be updated via update_objective
_UPDATABLE_FIELDS: frozenset[str] = frozenset(
    {
        "description",
        "source_document",
        "target_description",
        "weight",
        "sequence",
        "department_id",
        "parent_objective_id",
        "objective_code",
    }
)


# =============================================================================
# Error classes
# =============================================================================


class StrategicObjectiveServiceError(Exception):
    """Base error for StrategicObjectiveService."""


class StrategicObjectiveNotFoundError(StrategicObjectiveServiceError):
    """Raised when a StrategicObjective record cannot be found."""

    def __init__(self, objective_id: UUID) -> None:
        self.objective_id = objective_id
        super().__init__(f"Strategic objective {objective_id} not found")


# =============================================================================
# Service
# =============================================================================


class StrategicObjectiveService:
    """Service for managing OHCSF strategic objectives."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def _ensure_pms_write_mode(self, org_id: UUID) -> None:
        try:
            enforce_pms_write_mode(self.db, org_id)
        except ValueError as exc:
            raise StrategicObjectiveServiceError(str(exc)) from exc

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get_or_404(self, org_id: UUID, objective_id: UUID) -> StrategicObjective:
        """Fetch a StrategicObjective scoped to org or raise NotFound."""
        stmt = select(StrategicObjective).where(
            StrategicObjective.organization_id == org_id,
            StrategicObjective.objective_id == objective_id,
        )
        record = self.db.scalar(stmt)
        if record is None:
            raise StrategicObjectiveNotFoundError(objective_id)
        return record

    # ------------------------------------------------------------------
    # Query methods
    # ------------------------------------------------------------------

    def get_objective(self, org_id: UUID, objective_id: UUID) -> StrategicObjective:
        """Return a single strategic objective scoped to the organisation.

        Raises:
            StrategicObjectiveNotFoundError: if not found.
        """
        return self._get_or_404(org_id, objective_id)

    def list_objectives(
        self,
        org_id: UUID,
        *,
        cycle_id: UUID | None = None,
        department_id: UUID | None = None,
        search: str | None = None,
        pagination: PaginationParams | None = None,
    ) -> PaginatedResult[StrategicObjective]:
        """List strategic objectives for an organisation.

        Optional filters:
            cycle_id: Limit to a specific appraisal cycle.
            department_id: Limit to a specific department.
            search: Filter by objective_code or description (case-insensitive).
            pagination: Offset/limit parameters.
        """
        stmt = (
            select(StrategicObjective)
            .where(StrategicObjective.organization_id == org_id)
            .order_by(StrategicObjective.sequence, StrategicObjective.objective_code)
        )

        if cycle_id is not None:
            stmt = stmt.where(StrategicObjective.cycle_id == cycle_id)
        if department_id is not None:
            stmt = stmt.where(StrategicObjective.department_id == department_id)
        if search:
            pattern = f"%{search}%"
            stmt = stmt.where(
                StrategicObjective.description.ilike(pattern)
                | StrategicObjective.objective_code.ilike(pattern)
            )

        return paginate(
            self.db,
            stmt,
            pagination,
            count_column=StrategicObjective.objective_id,
        )

    # ------------------------------------------------------------------
    # Mutation methods
    # ------------------------------------------------------------------

    def create_objective(
        self,
        org_id: UUID,
        *,
        cycle_id: UUID,
        objective_code: str,
        description: str,
        department_id: UUID | None = None,
        parent_objective_id: UUID | None = None,
        source_document: str | None = None,
        target_description: str | None = None,
        weight: Any | None = None,
    ) -> StrategicObjective:
        """Create a new strategic objective for a cycle.

        Args:
            org_id: Organisation scope.
            cycle_id: Appraisal cycle this objective belongs to.
            objective_code: Unique code within the organisation (e.g. "SO-001").
            description: Full text description of the objective.
            department_id: Optional department scope.
            parent_objective_id: Optional parent for hierarchical decomposition.
            source_document: Reference document (e.g. state development plan).
            target_description: Quantitative or qualitative target statement.
            weight: Percentage weight (Numeric 5,2).

        Returns:
            Newly created StrategicObjective (flushed, not committed).
        """
        self._ensure_pms_write_mode(org_id)
        record = StrategicObjective(
            organization_id=org_id,
            cycle_id=cycle_id,
            objective_code=objective_code,
            description=description,
            department_id=department_id,
            parent_objective_id=parent_objective_id,
            source_document=source_document,
            target_description=target_description,
            weight=weight,
        )

        self.db.add(record)
        self.db.flush()
        logger.info(
            "Created StrategicObjective %s for org=%s cycle=%s",
            objective_code,
            org_id,
            cycle_id,
        )
        return record

    def update_objective(
        self,
        org_id: UUID,
        objective_id: UUID,
        **kwargs: Any,
    ) -> StrategicObjective:
        """Update allowed fields on a strategic objective.

        Only keys listed in _UPDATABLE_FIELDS are applied; unknown keys
        are silently ignored.

        Args:
            org_id: Organisation scope.
            objective_id: Primary key of the objective to update.
            **kwargs: Field=value pairs to update.

        Returns:
            Updated StrategicObjective (flushed, not committed).

        Raises:
            StrategicObjectiveNotFoundError: if not found.
        """
        self._ensure_pms_write_mode(org_id)
        record = self._get_or_404(org_id, objective_id)

        for field, value in kwargs.items():
            if field in _UPDATABLE_FIELDS:
                setattr(record, field, value)

        self.db.flush()
        logger.info("Updated StrategicObjective %s", objective_id)
        return record

    def delete_objective(self, org_id: UUID, objective_id: UUID) -> None:
        """Delete a strategic objective.

        Guards against deletion when:
        - The objective has child objectives (hierarchical dependants).
        - The objective has linked KPIs (via KPI.institutional_objective_id).

        Args:
            org_id: Organisation scope.
            objective_id: Primary key of the objective to delete.

        Raises:
            StrategicObjectiveNotFoundError: if not found.
            StrategicObjectiveServiceError: if children or linked KPIs exist.
        """
        self._ensure_pms_write_mode(org_id)
        record = self._get_or_404(org_id, objective_id)

        # Guard: child objectives
        child_stmt = select(StrategicObjective).where(
            StrategicObjective.organization_id == org_id,
            StrategicObjective.parent_objective_id == objective_id,
        )
        children = list(self.db.scalars(child_stmt).all())
        if children:
            raise StrategicObjectiveServiceError(
                f"Cannot delete objective {objective_id}: "
                f"{len(children)} child objective(s) exist. "
                "Remove or reassign child objectives first."
            )

        # Guard: linked KPIs
        from app.models.people.perf.kpi import KPI  # lazy import — avoids circular

        kpi_stmt = select(KPI).where(
            KPI.organization_id == org_id,
            KPI.institutional_objective_id == objective_id,
        )
        linked_kpis = list(self.db.scalars(kpi_stmt).all())
        if linked_kpis:
            raise StrategicObjectiveServiceError(
                f"Cannot delete objective {objective_id}: "
                f"{len(linked_kpis)} KPI(s) are linked to it. "
                "Unlink or delete the KPIs first."
            )

        self.db.delete(record)
        self.db.flush()
        logger.info("Deleted StrategicObjective %s", objective_id)

    # ------------------------------------------------------------------
    # Analysis methods
    # ------------------------------------------------------------------

    def get_cascade_tree(self, org_id: UUID, cycle_id: UUID) -> list[dict]:
        """Build a hierarchical tree of objectives for a cycle.

        Queries all objectives for the cycle and assembles them into a tree
        in memory.  Each node has the following keys:

            objective_id, objective_code, description, department_id,
            weight, sequence, children (list of child nodes)

        Objectives whose parent is not present in the result set (e.g. orphans
        due to data issues) are placed at the root level.

        Returns:
            List of root-level objective dicts, each with a ``children`` list
            that recursively contains the same structure.
        """
        stmt = (
            select(StrategicObjective)
            .where(
                StrategicObjective.organization_id == org_id,
                StrategicObjective.cycle_id == cycle_id,
            )
            .order_by(StrategicObjective.sequence, StrategicObjective.objective_code)
        )
        objectives = list(self.db.scalars(stmt).all())

        if not objectives:
            return []

        # Index objectives by their ID for quick lookup
        obj_index: dict[UUID, dict] = {}
        for obj in objectives:
            obj_index[obj.objective_id] = {
                "objective_id": obj.objective_id,
                "objective_code": obj.objective_code,
                "description": obj.description,
                "department_id": obj.department_id,
                "weight": obj.weight,
                "sequence": obj.sequence,
                "children": [],
            }

        roots: list[dict] = []
        for obj in objectives:
            node = obj_index[obj.objective_id]
            parent_id = obj.parent_objective_id
            if parent_id is not None and parent_id in obj_index:
                obj_index[parent_id]["children"].append(node)
            else:
                # Root objective or orphan (parent not in result set)
                roots.append(node)

        return roots

    def get_alignment_report(self, org_id: UUID, cycle_id: UUID) -> dict:
        """Produce an alignment report for a cycle.

        For each objective, counts how many KPIs are linked via
        ``KPI.institutional_objective_id``.  Objectives with zero linked KPIs
        are flagged as gaps.

        Returns a dict with:
            objectives: list of {objective_id, code, description,
                                  kpi_count, has_gap}
            total_objectives: int
            aligned_count: int  (objectives with >= 1 KPI)
            gap_count: int      (objectives with 0 KPIs)
            alignment_percentage: float  (aligned / total * 100, or 0.0)
        """
        from app.models.people.perf.kpi import KPI  # lazy import — avoids circular

        # Fetch all objectives for the cycle
        obj_stmt = (
            select(StrategicObjective)
            .where(
                StrategicObjective.organization_id == org_id,
                StrategicObjective.cycle_id == cycle_id,
            )
            .order_by(StrategicObjective.sequence, StrategicObjective.objective_code)
        )
        objectives = list(self.db.scalars(obj_stmt).all())

        if not objectives:
            return {
                "objectives": [],
                "total_objectives": 0,
                "aligned_count": 0,
                "gap_count": 0,
                "alignment_percentage": 0.0,
            }

        entries: list[dict] = []
        aligned_count = 0

        for obj in objectives:
            kpi_count_stmt = select(func.count()).select_from(
                select(KPI)
                .where(
                    KPI.organization_id == org_id,
                    KPI.institutional_objective_id == obj.objective_id,
                )
                .subquery()
            )
            kpi_count: int = self.db.scalar(kpi_count_stmt) or 0
            has_gap = kpi_count == 0

            if not has_gap:
                aligned_count += 1

            entries.append(
                {
                    "objective_id": obj.objective_id,
                    "code": obj.objective_code,
                    "description": obj.description,
                    "kpi_count": kpi_count,
                    "has_gap": has_gap,
                }
            )

        total = len(objectives)
        gap_count = total - aligned_count
        alignment_pct = (aligned_count / total * 100) if total > 0 else 0.0

        return {
            "objectives": entries,
            "total_objectives": total,
            "aligned_count": aligned_count,
            "gap_count": gap_count,
            "alignment_percentage": round(alignment_pct, 2),
        }
