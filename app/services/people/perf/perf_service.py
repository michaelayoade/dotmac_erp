"""Performance management service implementation.

Handles appraisal cycles, KRAs, KPIs, appraisals, and scorecards.
Adapted from DotMac People for the unified ERP platform.
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING, TypedDict
from uuid import UUID

from sqlalchemy import and_, false, func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.models.people.perf import (
    KPI,
    KRA,
    Appraisal,
    AppraisalCycle,
    AppraisalCycleStatus,
    AppraisalFeedback,
    AppraisalKRAScore,
    AppraisalStatus,
    AppraisalTemplate,
    AppraisalTemplateKRA,
    KPIStatus,
    Scorecard,
    ScorecardItem,
)
from app.services.common import PaginatedResult, PaginationParams

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from app.web.deps import WebAuthContext

__all__ = ["PerformanceService"]


class PerformanceServiceError(Exception):
    """Base error for performance service."""

    pass


class AppraisalCycleNotFoundError(PerformanceServiceError):
    """Appraisal cycle not found."""

    def __init__(self, cycle_id: UUID):
        self.cycle_id = cycle_id
        super().__init__(f"Appraisal cycle {cycle_id} not found")


class KRANotFoundError(PerformanceServiceError):
    """KRA not found."""

    def __init__(self, kra_id: UUID):
        self.kra_id = kra_id
        super().__init__(f"KRA {kra_id} not found")


class KPINotFoundError(PerformanceServiceError):
    """KPI not found."""

    def __init__(self, kpi_id: UUID):
        self.kpi_id = kpi_id
        super().__init__(f"KPI {kpi_id} not found")


class AppraisalNotFoundError(PerformanceServiceError):
    """Appraisal not found."""

    def __init__(self, appraisal_id: UUID):
        self.appraisal_id = appraisal_id
        super().__init__(f"Appraisal {appraisal_id} not found")


class ScorecardNotFoundError(PerformanceServiceError):
    """Scorecard not found."""

    def __init__(self, scorecard_id: UUID):
        self.scorecard_id = scorecard_id
        super().__init__(f"Scorecard {scorecard_id} not found")


class AppraisalStatusError(PerformanceServiceError):
    """Invalid appraisal status transition."""

    def __init__(self, current: str, target: str):
        self.current = current
        self.target = target
        super().__init__(f"Cannot transition from {current} to {target}")


# Valid status transitions for appraisals
APPRAISAL_STATUS_TRANSITIONS = {
    AppraisalStatus.DRAFT: {
        AppraisalStatus.SELF_ASSESSMENT,
        AppraisalStatus.CANCELLED,
    },
    AppraisalStatus.SELF_ASSESSMENT: {
        AppraisalStatus.PENDING_REVIEW,
        AppraisalStatus.DRAFT,
    },
    AppraisalStatus.PENDING_REVIEW: {
        AppraisalStatus.UNDER_REVIEW,
    },
    AppraisalStatus.UNDER_REVIEW: {
        AppraisalStatus.PENDING_CALIBRATION,
        AppraisalStatus.SELF_ASSESSMENT,
    },
    AppraisalStatus.PENDING_CALIBRATION: {
        AppraisalStatus.CALIBRATION,
    },
    AppraisalStatus.CALIBRATION: {
        AppraisalStatus.COMPLETED,
        AppraisalStatus.UNDER_REVIEW,
    },
    AppraisalStatus.COMPLETED: set(),  # Terminal state
    AppraisalStatus.CANCELLED: set(),  # Terminal state
}


class PerformanceService:
    """Service for performance management operations.

    Handles:
    - Appraisal cycle management
    - Key Result Areas (KRAs)
    - Key Performance Indicators (KPIs)
    - Employee appraisals with multi-stage workflow
    - Balanced scorecards
    """

    def __init__(
        self,
        db: Session,
        ctx: WebAuthContext | None = None,
    ) -> None:
        self.db = db
        self.ctx = ctx

    # =========================================================================
    # Appraisal Cycles
    # =========================================================================

    def list_cycles(
        self,
        org_id: UUID,
        *,
        search: str | None = None,
        status: AppraisalCycleStatus | None = None,
        year: int | None = None,
        pagination: PaginationParams | None = None,
    ) -> PaginatedResult[AppraisalCycle]:
        """List appraisal cycles."""
        query = select(AppraisalCycle).where(AppraisalCycle.organization_id == org_id)

        if search:
            search_term = f"%{search}%"
            query = query.where(
                or_(
                    AppraisalCycle.cycle_name.ilike(search_term),
                    AppraisalCycle.cycle_code.ilike(search_term),
                )
            )

        if status:
            query = query.where(AppraisalCycle.status == status)

        if year:
            year_start = date(year, 1, 1)
            year_end = date(year, 12, 31)
            query = query.where(
                and_(
                    AppraisalCycle.start_date >= year_start,
                    AppraisalCycle.start_date <= year_end,
                )
            )

        query = query.order_by(AppraisalCycle.start_date.desc())

        # Count total
        count_query = select(func.count()).select_from(query.subquery())
        total = self.db.scalar(count_query) or 0

        # Apply pagination
        if pagination:
            query = query.offset(pagination.offset).limit(pagination.limit)

        items = list(self.db.scalars(query).all())

        return PaginatedResult(
            items=items,
            total=total,
            offset=pagination.offset if pagination else 0,
            limit=pagination.limit if pagination else len(items),
        )

    def list_appraisal_cycles(
        self,
        org_id: UUID,
        *,
        search: str | None = None,
        status: AppraisalCycleStatus | None = None,
        year: int | None = None,
        pagination: PaginationParams | None = None,
    ) -> PaginatedResult[AppraisalCycle]:
        """Compatibility wrapper for appraisal cycles listing."""
        return self.list_cycles(
            org_id=org_id,
            search=search,
            status=status,
            year=year,
            pagination=pagination,
        )

    def get_cycle(self, org_id: UUID, cycle_id: UUID) -> AppraisalCycle:
        """Get an appraisal cycle by ID."""
        cycle = self.db.scalar(
            select(AppraisalCycle).where(
                AppraisalCycle.cycle_id == cycle_id,
                AppraisalCycle.organization_id == org_id,
            )
        )
        if not cycle:
            raise AppraisalCycleNotFoundError(cycle_id)
        return cycle

    def create_cycle(
        self,
        org_id: UUID,
        *,
        cycle_code: str,
        cycle_name: str,
        review_period_start: date,
        review_period_end: date,
        start_date: date,
        end_date: date,
        self_assessment_deadline: date | None = None,
        manager_review_deadline: date | None = None,
        calibration_deadline: date | None = None,
        include_probation_employees: bool = False,
        min_tenure_months: int = 3,
        description: str | None = None,
    ) -> AppraisalCycle:
        """Create a new appraisal cycle."""
        cycle = AppraisalCycle(
            organization_id=org_id,
            cycle_code=cycle_code,
            cycle_name=cycle_name,
            review_period_start=review_period_start,
            review_period_end=review_period_end,
            start_date=start_date,
            end_date=end_date,
            self_assessment_deadline=self_assessment_deadline,
            manager_review_deadline=manager_review_deadline,
            calibration_deadline=calibration_deadline,
            include_probation_employees=include_probation_employees,
            min_tenure_months=min_tenure_months,
            description=description,
            status=AppraisalCycleStatus.DRAFT,
        )

        self.db.add(cycle)
        self.db.flush()
        return cycle

    def update_cycle(
        self,
        org_id: UUID,
        cycle_id: UUID,
        **kwargs,
    ) -> AppraisalCycle:
        """Update an appraisal cycle."""
        cycle = self.get_cycle(org_id, cycle_id)

        for key, value in kwargs.items():
            if value is not None and hasattr(cycle, key):
                setattr(cycle, key, value)

        self.db.flush()
        return cycle

    def delete_cycle(self, org_id: UUID, cycle_id: UUID) -> None:
        """Delete an appraisal cycle."""
        cycle = self.get_cycle(org_id, cycle_id)
        self.db.delete(cycle)
        self.db.flush()

    def start_cycle(self, org_id: UUID, cycle_id: UUID) -> AppraisalCycle:
        """Start an appraisal cycle."""
        cycle = self.get_cycle(org_id, cycle_id)
        cycle.status = AppraisalCycleStatus.ACTIVE
        self.db.flush()
        return cycle

    def close_cycle(self, org_id: UUID, cycle_id: UUID) -> AppraisalCycle:
        """Close an appraisal cycle."""
        cycle = self.get_cycle(org_id, cycle_id)
        cycle.status = AppraisalCycleStatus.COMPLETED
        self.db.flush()
        return cycle

    # =========================================================================
    # Key Result Areas (KRAs)
    # =========================================================================

    def list_kras(
        self,
        org_id: UUID,
        *,
        department_id: UUID | None = None,
        designation_id: UUID | None = None,
        is_active: bool | None = None,
        category: str | None = None,
        search: str | None = None,
        pagination: PaginationParams | None = None,
    ) -> PaginatedResult[KRA]:
        """List KRAs."""
        query = select(KRA).where(KRA.organization_id == org_id)

        if department_id:
            query = query.where(KRA.department_id == department_id)

        if designation_id:
            query = query.where(KRA.designation_id == designation_id)

        if is_active is not None:
            query = query.where(KRA.is_active == is_active)

        if category:
            query = query.where(KRA.category == category)

        if search:
            search_term = f"%{search}%"
            query = query.where(
                or_(
                    KRA.kra_code.ilike(search_term),
                    KRA.kra_name.ilike(search_term),
                )
            )

        query = query.order_by(KRA.kra_name)

        # Count total
        count_query = select(func.count()).select_from(query.subquery())
        total = self.db.scalar(count_query) or 0

        # Apply pagination
        if pagination:
            query = query.offset(pagination.offset).limit(pagination.limit)

        items = list(self.db.scalars(query).all())

        return PaginatedResult(
            items=items,
            total=total,
            offset=pagination.offset if pagination else 0,
            limit=pagination.limit if pagination else len(items),
        )

    def get_kra(self, org_id: UUID, kra_id: UUID) -> KRA:
        """Get a KRA by ID."""
        kra = self.db.scalar(
            select(KRA).where(
                KRA.kra_id == kra_id,
                KRA.organization_id == org_id,
            )
        )
        if not kra:
            raise KRANotFoundError(kra_id)
        return kra

    def create_kra(
        self,
        org_id: UUID,
        *,
        kra_code: str,
        kra_name: str,
        department_id: UUID | None = None,
        designation_id: UUID | None = None,
        default_weightage: Decimal = Decimal("0"),
        category: str | None = None,
        measurement_criteria: str | None = None,
        is_active: bool = True,
        description: str | None = None,
    ) -> KRA:
        """Create a new KRA."""
        kra = KRA(
            organization_id=org_id,
            kra_code=kra_code,
            kra_name=kra_name,
            department_id=department_id,
            designation_id=designation_id,
            default_weightage=default_weightage,
            category=category,
            measurement_criteria=measurement_criteria,
            is_active=is_active,
            description=description,
        )

        self.db.add(kra)
        self.db.flush()
        return kra

    def update_kra(
        self,
        org_id: UUID,
        kra_id: UUID,
        **kwargs,
    ) -> KRA:
        """Update a KRA."""
        kra = self.get_kra(org_id, kra_id)

        for key, value in kwargs.items():
            if value is not None and hasattr(kra, key):
                setattr(kra, key, value)

        self.db.flush()
        return kra

    def delete_kra(self, org_id: UUID, kra_id: UUID) -> None:
        """Delete a KRA."""
        kra = self.get_kra(org_id, kra_id)
        self.db.delete(kra)
        self.db.flush()

    # =========================================================================
    # Appraisal Templates
    # =========================================================================

    def list_templates(
        self,
        org_id: UUID,
        *,
        department_id: UUID | None = None,
        designation_id: UUID | None = None,
        is_active: bool | None = None,
        search: str | None = None,
        pagination: PaginationParams | None = None,
    ) -> PaginatedResult[AppraisalTemplate]:
        """List appraisal templates."""
        query = select(AppraisalTemplate).where(
            AppraisalTemplate.organization_id == org_id
        )

        if department_id:
            query = query.where(AppraisalTemplate.department_id == department_id)

        if designation_id:
            query = query.where(AppraisalTemplate.designation_id == designation_id)

        if is_active is not None:
            query = query.where(AppraisalTemplate.is_active == is_active)

        if search:
            search_term = f"%{search}%"
            query = query.where(
                or_(
                    AppraisalTemplate.template_code.ilike(search_term),
                    AppraisalTemplate.template_name.ilike(search_term),
                )
            )

        query = query.options(
            joinedload(AppraisalTemplate.kras).joinedload(AppraisalTemplateKRA.kra)
        )
        query = query.order_by(AppraisalTemplate.template_name)

        count_query = select(func.count()).select_from(query.subquery())
        total = self.db.scalar(count_query) or 0

        if pagination:
            query = query.offset(pagination.offset).limit(pagination.limit)

        items = list(self.db.scalars(query).unique().all())

        return PaginatedResult(
            items=items,
            total=total,
            offset=pagination.offset if pagination else 0,
            limit=pagination.limit if pagination else len(items),
        )

    def get_template(self, org_id: UUID, template_id: UUID) -> AppraisalTemplate:
        """Get an appraisal template by ID."""
        template = self.db.scalar(
            select(AppraisalTemplate)
            .options(
                joinedload(AppraisalTemplate.kras).joinedload(AppraisalTemplateKRA.kra)
            )
            .where(
                AppraisalTemplate.template_id == template_id,
                AppraisalTemplate.organization_id == org_id,
            )
        )
        if not template:
            raise PerformanceServiceError(f"Template {template_id} not found")
        return template

    def create_template(
        self,
        org_id: UUID,
        *,
        template_code: str,
        template_name: str,
        description: str | None = None,
        department_id: UUID | None = None,
        designation_id: UUID | None = None,
        rating_scale_max: int = 5,
        is_active: bool = True,
        kras: list[dict] | None = None,
    ) -> AppraisalTemplate:
        """Create a new appraisal template."""
        template = AppraisalTemplate(
            organization_id=org_id,
            template_code=template_code,
            template_name=template_name,
            description=description,
            department_id=department_id,
            designation_id=designation_id,
            rating_scale_max=rating_scale_max,
            is_active=is_active,
        )
        self.db.add(template)
        self.db.flush()

        if kras:
            for idx, kra in enumerate(kras):
                self.db.add(
                    AppraisalTemplateKRA(
                        organization_id=org_id,
                        template_id=template.template_id,
                        kra_id=kra["kra_id"],
                        weightage=kra["weightage"],
                        sequence=kra.get("sequence", idx),
                    )
                )
        self.db.flush()
        return template

    def update_template(
        self,
        org_id: UUID,
        template_id: UUID,
        **kwargs,
    ) -> AppraisalTemplate:
        """Update an appraisal template."""
        kras = kwargs.pop("kras", None)
        template = self.get_template(org_id, template_id)

        for key, value in kwargs.items():
            if value is not None and hasattr(template, key):
                setattr(template, key, value)

        if kras is not None:
            self.db.query(AppraisalTemplateKRA).filter(
                AppraisalTemplateKRA.template_id == template_id
            ).delete()
            for idx, kra in enumerate(kras):
                self.db.add(
                    AppraisalTemplateKRA(
                        organization_id=org_id,
                        template_id=template_id,
                        kra_id=kra["kra_id"],
                        weightage=kra["weightage"],
                        sequence=kra.get("sequence", idx),
                    )
                )

        self.db.flush()
        return template

    def delete_template(self, org_id: UUID, template_id: UUID) -> None:
        """Delete an appraisal template."""
        template = self.get_template(org_id, template_id)
        self.db.query(AppraisalTemplateKRA).filter(
            AppraisalTemplateKRA.template_id == template_id
        ).delete()
        self.db.delete(template)
        self.db.flush()

    # =========================================================================
    # Key Performance Indicators (KPIs)
    # =========================================================================

    def list_kpis(
        self,
        org_id: UUID,
        *,
        employee_id: UUID | None = None,
        kra_id: UUID | None = None,
        status: KPIStatus | None = None,
        search: str | None = None,
        is_active: bool | None = None,
        from_date: date | None = None,
        to_date: date | None = None,
        pagination: PaginationParams | None = None,
    ) -> PaginatedResult[KPI]:
        """List KPIs."""
        query = select(KPI).where(KPI.organization_id == org_id)

        if employee_id:
            query = query.where(KPI.employee_id == employee_id)

        if kra_id:
            query = query.where(KPI.kra_id == kra_id)

        if status:
            query = query.where(KPI.status == status)

        if search:
            search_term = f"%{search}%"
            query = query.where(
                or_(
                    KPI.kpi_name.ilike(search_term),
                    KPI.description.ilike(search_term),
                )
            )

        if is_active is not None:
            if is_active:
                query = query.where(KPI.status == KPIStatus.ACTIVE)
            else:
                query = query.where(KPI.status != KPIStatus.ACTIVE)

        if from_date:
            query = query.where(KPI.period_start >= from_date)

        if to_date:
            query = query.where(KPI.period_end <= to_date)

        query = query.order_by(KPI.period_start.desc())

        # Count total
        count_query = select(func.count()).select_from(query.subquery())
        total = self.db.scalar(count_query) or 0

        # Apply pagination
        if pagination:
            query = query.offset(pagination.offset).limit(pagination.limit)

        items = list(self.db.scalars(query).all())

        return PaginatedResult(
            items=items,
            total=total,
            offset=pagination.offset if pagination else 0,
            limit=pagination.limit if pagination else len(items),
        )

    def get_kpi(self, org_id: UUID, kpi_id: UUID) -> KPI:
        """Get a KPI by ID."""
        kpi = self.db.scalar(
            select(KPI).where(
                KPI.kpi_id == kpi_id,
                KPI.organization_id == org_id,
            )
        )
        if not kpi:
            raise KPINotFoundError(kpi_id)
        return kpi

    def create_kpi(
        self,
        org_id: UUID,
        *,
        employee_id: UUID,
        kra_id: UUID | None = None,
        kpi_name: str,
        period_start: date,
        period_end: date,
        target_value: Decimal,
        unit_of_measure: str | None = None,
        threshold_value: Decimal | None = None,
        stretch_value: Decimal | None = None,
        weightage: Decimal = Decimal("0"),
        notes: str | None = None,
        description: str | None = None,
    ) -> KPI:
        """Create a new KPI."""
        kpi = KPI(
            organization_id=org_id,
            employee_id=employee_id,
            kra_id=kra_id,
            kpi_name=kpi_name,
            period_start=period_start,
            period_end=period_end,
            target_value=target_value,
            unit_of_measure=unit_of_measure,
            threshold_value=threshold_value,
            stretch_value=stretch_value,
            weightage=weightage,
            notes=notes,
            description=description,
            status=KPIStatus.DRAFT,
        )

        self.db.add(kpi)
        self.db.flush()
        return kpi

    def update_kpi(
        self,
        org_id: UUID,
        kpi_id: UUID,
        **kwargs,
    ) -> KPI:
        """Update a KPI."""
        kpi = self.get_kpi(org_id, kpi_id)
        for key, value in kwargs.items():
            if value is not None and hasattr(kpi, key):
                setattr(kpi, key, value)
        self.db.flush()
        return kpi

    def update_kpi_progress(
        self,
        org_id: UUID,
        kpi_id: UUID,
        *,
        actual_value: Decimal,
        evidence: str | None = None,
        notes: str | None = None,
    ) -> KPI:
        """Update KPI progress."""
        kpi = self.get_kpi(org_id, kpi_id)

        kpi.actual_value = actual_value
        if evidence:
            kpi.evidence = evidence
        if notes:
            kpi.notes = notes

        # Calculate achievement percentage
        if kpi.target_value and kpi.target_value > 0:
            kpi.achievement_percentage = (
                actual_value / kpi.target_value * 100
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        # Update status based on achievement
        if kpi.achievement_percentage:
            if kpi.achievement_percentage >= 100:
                kpi.status = KPIStatus.ACHIEVED
            elif kpi.achievement_percentage >= 80:
                kpi.status = KPIStatus.ON_TRACK
            else:
                kpi.status = KPIStatus.AT_RISK

        self.db.flush()
        return kpi

    def delete_kpi(self, org_id: UUID, kpi_id: UUID) -> None:
        """Delete a KPI."""
        kpi = self.get_kpi(org_id, kpi_id)
        self.db.delete(kpi)
        self.db.flush()

    # =========================================================================
    # Appraisals
    # =========================================================================

    def list_appraisals(
        self,
        org_id: UUID,
        *,
        employee_id: UUID | None = None,
        cycle_id: UUID | None = None,
        manager_id: UUID | None = None,
        status: AppraisalStatus | None = None,
        pagination: PaginationParams | None = None,
    ) -> PaginatedResult[Appraisal]:
        """List appraisals."""
        query = select(Appraisal).where(Appraisal.organization_id == org_id)

        if employee_id:
            query = query.where(Appraisal.employee_id == employee_id)

        if cycle_id:
            query = query.where(Appraisal.cycle_id == cycle_id)

        if manager_id:
            query = query.where(Appraisal.manager_id == manager_id)

        if status:
            query = query.where(Appraisal.status == status)

        query = query.options(joinedload(Appraisal.kra_scores))
        query = query.order_by(Appraisal.created_at.desc())

        # Count total
        count_query = select(func.count()).select_from(query.subquery())
        total = self.db.scalar(count_query) or 0

        # Apply pagination
        if pagination:
            query = query.offset(pagination.offset).limit(pagination.limit)

        items = list(self.db.scalars(query).unique().all())

        return PaginatedResult(
            items=items,
            total=total,
            offset=pagination.offset if pagination else 0,
            limit=pagination.limit if pagination else len(items),
        )

    def get_appraisal(self, org_id: UUID, appraisal_id: UUID) -> Appraisal:
        """Get an appraisal by ID."""
        appraisal = self.db.scalar(
            select(Appraisal)
            .options(joinedload(Appraisal.kra_scores))
            .where(
                Appraisal.appraisal_id == appraisal_id,
                Appraisal.organization_id == org_id,
            )
        )
        if not appraisal:
            raise AppraisalNotFoundError(appraisal_id)
        return appraisal

    def create_appraisal(
        self,
        org_id: UUID,
        *,
        employee_id: UUID,
        cycle_id: UUID,
        manager_id: UUID,
        template_id: UUID | None = None,
        kra_scores: list[dict] | None = None,
    ) -> Appraisal:
        """Create a new appraisal."""
        # Verify cycle exists
        self.get_cycle(org_id, cycle_id)

        appraisal = Appraisal(
            organization_id=org_id,
            employee_id=employee_id,
            cycle_id=cycle_id,
            manager_id=manager_id,
            template_id=template_id,
            status=AppraisalStatus.DRAFT,
        )

        self.db.add(appraisal)
        self.db.flush()

        # Add KRA scores
        if kra_scores:
            for score_data in kra_scores:
                score = AppraisalKRAScore(
                    organization_id=org_id,
                    appraisal_id=appraisal.appraisal_id,
                    kra_id=score_data["kra_id"],
                    weightage=score_data.get("weightage", Decimal("0")),
                )
                self.db.add(score)

        self.db.flush()
        return appraisal

    def update_appraisal(
        self,
        org_id: UUID,
        appraisal_id: UUID,
        **kwargs,
    ) -> Appraisal:
        """Update an appraisal."""
        appraisal = self.get_appraisal(org_id, appraisal_id)
        for key, value in kwargs.items():
            if value is not None and hasattr(appraisal, key):
                setattr(appraisal, key, value)
        self.db.flush()
        return appraisal

    def delete_appraisal(self, org_id: UUID, appraisal_id: UUID) -> None:
        """Delete an appraisal if still draft."""
        appraisal = self.get_appraisal(org_id, appraisal_id)
        if appraisal.status != AppraisalStatus.DRAFT:
            raise AppraisalStatusError(
                appraisal.status.value, AppraisalStatus.DRAFT.value
            )
        self.db.delete(appraisal)
        self.db.flush()

    def submit_self_assessment(
        self,
        org_id: UUID,
        appraisal_id: UUID,
        *,
        self_overall_rating: int,
        self_summary: str | None = None,
        achievements: str | None = None,
        challenges: str | None = None,
        development_needs: str | None = None,
        kra_ratings: list[dict] | None = None,
    ) -> Appraisal:
        """Submit employee self-assessment."""
        appraisal = self.get_appraisal(org_id, appraisal_id)

        if appraisal.status not in {
            AppraisalStatus.DRAFT,
            AppraisalStatus.SELF_ASSESSMENT,
        }:
            raise AppraisalStatusError(
                appraisal.status.value, AppraisalStatus.SELF_ASSESSMENT.value
            )

        appraisal.self_assessment_date = date.today()
        appraisal.self_overall_rating = self_overall_rating
        appraisal.self_summary = self_summary
        appraisal.achievements = achievements
        appraisal.challenges = challenges
        appraisal.development_needs = development_needs
        appraisal.status = AppraisalStatus.UNDER_REVIEW

        # Update KRA self ratings
        if kra_ratings:
            for rating in kra_ratings:
                score = self.db.get(AppraisalKRAScore, rating["score_id"])
                if score and score.appraisal_id == appraisal_id:
                    score.self_rating = rating["rating"]
                    score.self_comments = rating.get("comments")

        self.db.flush()
        return appraisal

    def submit_manager_review(
        self,
        org_id: UUID,
        appraisal_id: UUID,
        *,
        manager_overall_rating: int,
        manager_summary: str | None = None,
        manager_recommendations: str | None = None,
        kra_ratings: list[dict] | None = None,
    ) -> Appraisal:
        """Submit manager review."""
        appraisal = self.get_appraisal(org_id, appraisal_id)

        if appraisal.status != AppraisalStatus.UNDER_REVIEW:
            raise AppraisalStatusError(
                appraisal.status.value, AppraisalStatus.CALIBRATION.value
            )

        appraisal.manager_review_date = date.today()
        appraisal.manager_overall_rating = manager_overall_rating
        appraisal.manager_summary = manager_summary
        appraisal.manager_recommendations = manager_recommendations
        appraisal.status = AppraisalStatus.CALIBRATION

        # Update KRA manager ratings
        if kra_ratings:
            for rating in kra_ratings:
                score = self.db.get(AppraisalKRAScore, rating["score_id"])
                if score and score.appraisal_id == appraisal_id:
                    score.manager_rating = rating["rating"]
                    score.manager_comments = rating.get("comments")

        self.db.flush()
        return appraisal

    def submit_calibration(
        self,
        org_id: UUID,
        appraisal_id: UUID,
        *,
        calibrated_rating: int,
        calibration_notes: str | None = None,
        rating_label: str | None = None,
    ) -> Appraisal:
        """Submit HR calibration."""
        appraisal = self.get_appraisal(org_id, appraisal_id)

        if appraisal.status != AppraisalStatus.CALIBRATION:
            raise AppraisalStatusError(
                appraisal.status.value, AppraisalStatus.COMPLETED.value
            )

        appraisal.calibration_date = date.today()
        appraisal.calibrated_rating = calibrated_rating
        appraisal.calibration_notes = calibration_notes
        appraisal.final_rating = calibrated_rating
        appraisal.rating_label = rating_label
        appraisal.status = AppraisalStatus.COMPLETED
        appraisal.completed_on = date.today()

        # Calculate final KRA scores
        for score in appraisal.kra_scores:
            score.final_rating = score.manager_rating or score.self_rating
            if score.final_rating and score.weightage:
                score.weighted_score = (
                    Decimal(str(score.final_rating)) * score.weightage / Decimal("100")
                ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        # Calculate overall score
        total_weighted = sum(
            ((s.weighted_score or Decimal("0")) for s in appraisal.kra_scores),
            Decimal("0"),
        )
        appraisal.final_score = total_weighted

        self.db.flush()
        return appraisal

    # =========================================================================
    # Scorecards
    # =========================================================================

    def list_scorecards(
        self,
        org_id: UUID,
        *,
        employee_id: UUID | None = None,
        department_id: UUID | None = None,
        cycle_id: UUID | None = None,
        is_finalized: bool | None = None,
        pagination: PaginationParams | None = None,
    ) -> PaginatedResult[Scorecard]:
        """List scorecards."""
        query = select(Scorecard).where(Scorecard.organization_id == org_id)

        if department_id:
            from app.models.people.hr.employee import Employee

            query = query.join(
                Employee, Scorecard.employee_id == Employee.employee_id
            ).where(
                Employee.department_id == department_id,
                Employee.organization_id == org_id,
            )

        if employee_id:
            query = query.where(Scorecard.employee_id == employee_id)

        if cycle_id:
            cycle = self.db.get(AppraisalCycle, cycle_id)
            if cycle and cycle.organization_id == org_id:
                query = query.where(
                    Scorecard.period_start >= cycle.review_period_start,
                    Scorecard.period_end <= cycle.review_period_end,
                )
            else:
                query = query.where(false())

        if is_finalized is not None:
            query = query.where(Scorecard.is_finalized == is_finalized)

        query = query.options(joinedload(Scorecard.items))
        query = query.order_by(Scorecard.period_start.desc())

        # Count total
        count_query = select(func.count()).select_from(query.subquery())
        total = self.db.scalar(count_query) or 0

        # Apply pagination
        if pagination:
            query = query.offset(pagination.offset).limit(pagination.limit)

        items = list(self.db.scalars(query).unique().all())

        return PaginatedResult(
            items=items,
            total=total,
            offset=pagination.offset if pagination else 0,
            limit=pagination.limit if pagination else len(items),
        )

    def get_scorecard(self, org_id: UUID, scorecard_id: UUID) -> Scorecard:
        """Get a scorecard by ID."""
        scorecard = self.db.scalar(
            select(Scorecard)
            .options(joinedload(Scorecard.items))
            .where(
                Scorecard.scorecard_id == scorecard_id,
                Scorecard.organization_id == org_id,
            )
        )
        if not scorecard:
            raise ScorecardNotFoundError(scorecard_id)
        return scorecard

    def create_scorecard(
        self,
        org_id: UUID,
        *,
        employee_id: UUID,
        period_start: date,
        period_end: date,
        period_label: str | None = None,
        items: list[dict] | None = None,
    ) -> Scorecard:
        """Create a new scorecard."""
        scorecard = Scorecard(
            organization_id=org_id,
            employee_id=employee_id,
            period_start=period_start,
            period_end=period_end,
            period_label=period_label,
            is_finalized=False,
        )

        self.db.add(scorecard)
        self.db.flush()

        # Add scorecard items
        if items:
            for idx, item_data in enumerate(items):
                item = ScorecardItem(
                    organization_id=org_id,
                    scorecard_id=scorecard.scorecard_id,
                    perspective=item_data["perspective"],
                    metric_name=item_data["metric_name"],
                    target_value=item_data.get("target_value"),
                    unit_of_measure=item_data.get("unit_of_measure"),
                    weightage=item_data.get("weightage", Decimal("0")),
                    sequence=idx,
                    description=item_data.get("description"),
                )
                self.db.add(item)

        self.db.flush()
        return scorecard

    def update_scorecard_item(
        self,
        org_id: UUID,
        scorecard_id: UUID,
        item_id: UUID,
        *,
        actual_value: Decimal,
    ) -> ScorecardItem:
        """Update a scorecard item's actual value."""
        scorecard = self.get_scorecard(org_id, scorecard_id)

        if scorecard.is_finalized:
            raise PerformanceServiceError("Cannot update finalized scorecard")

        item = self.db.get(ScorecardItem, item_id)
        if not item or item.scorecard_id != scorecard_id:
            raise PerformanceServiceError(f"Item {item_id} not found in scorecard")

        item.actual_value = actual_value

        # Calculate score (0-100 based on target achievement)
        if item.target_value and item.target_value > 0:
            item.score = (actual_value / item.target_value * 100).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            # Cap at 100
            if item.score > 100:
                item.score = Decimal("100")

        # Calculate weighted score
        if item.score and item.weightage:
            item.weighted_score = (
                item.score * item.weightage / Decimal("100")
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        self.db.flush()
        return item

    def finalize_scorecard(
        self,
        org_id: UUID,
        scorecard_id: UUID,
        *,
        summary: str | None = None,
    ) -> Scorecard:
        """Finalize a scorecard."""
        scorecard = self.get_scorecard(org_id, scorecard_id)

        if scorecard.is_finalized:
            raise PerformanceServiceError("Scorecard is already finalized")

        # Calculate perspective scores
        perspectives: dict[str, list[Decimal]] = {
            "FINANCIAL": [],
            "CUSTOMER": [],
            "PROCESS": [],
            "LEARNING": [],
        }

        for item in scorecard.items:
            if item.perspective in perspectives and item.weighted_score:
                perspectives[item.perspective].append(item.weighted_score)

        scorecard.financial_score = (
            sum(perspectives["FINANCIAL"], Decimal("0"))
            if perspectives["FINANCIAL"]
            else None
        )
        scorecard.customer_score = (
            sum(perspectives["CUSTOMER"], Decimal("0"))
            if perspectives["CUSTOMER"]
            else None
        )
        scorecard.process_score = (
            sum(perspectives["PROCESS"], Decimal("0"))
            if perspectives["PROCESS"]
            else None
        )
        scorecard.learning_score = (
            sum(perspectives["LEARNING"], Decimal("0"))
            if perspectives["LEARNING"]
            else None
        )

        # Calculate overall score
        all_weighted = sum(
            ((item.weighted_score or Decimal("0")) for item in scorecard.items),
            Decimal("0"),
        )
        scorecard.overall_score = all_weighted

        # Determine rating (1-5 based on score)
        if scorecard.overall_score:
            if scorecard.overall_score >= 90:
                scorecard.overall_rating = 5
                scorecard.rating_label = "Exceptional"
            elif scorecard.overall_score >= 80:
                scorecard.overall_rating = 4
                scorecard.rating_label = "Exceeds Expectations"
            elif scorecard.overall_score >= 70:
                scorecard.overall_rating = 3
                scorecard.rating_label = "Meets Expectations"
            elif scorecard.overall_score >= 60:
                scorecard.overall_rating = 2
                scorecard.rating_label = "Needs Improvement"
            else:
                scorecard.overall_rating = 1
                scorecard.rating_label = "Unsatisfactory"

        scorecard.summary = summary
        scorecard.is_finalized = True
        scorecard.finalized_on = date.today()

        self.db.flush()
        return scorecard

    # =========================================================================
    # Reporting
    # =========================================================================

    def get_performance_stats(self, org_id: UUID) -> dict:
        """Get performance statistics for dashboard."""
        # Active cycles
        active_cycles = (
            self.db.scalar(
                select(func.count(AppraisalCycle.cycle_id)).where(
                    AppraisalCycle.organization_id == org_id,
                    AppraisalCycle.status == AppraisalCycleStatus.ACTIVE,
                )
            )
            or 0
        )

        # Pending self assessment
        pending_self = (
            self.db.scalar(
                select(func.count(Appraisal.appraisal_id)).where(
                    Appraisal.organization_id == org_id,
                    Appraisal.status.in_(
                        [AppraisalStatus.DRAFT, AppraisalStatus.SELF_ASSESSMENT]
                    ),
                )
            )
            or 0
        )

        # Pending manager review
        pending_manager = (
            self.db.scalar(
                select(func.count(Appraisal.appraisal_id)).where(
                    Appraisal.organization_id == org_id,
                    Appraisal.status == AppraisalStatus.UNDER_REVIEW,
                )
            )
            or 0
        )

        # Pending calibration
        pending_calibration = (
            self.db.scalar(
                select(func.count(Appraisal.appraisal_id)).where(
                    Appraisal.organization_id == org_id,
                    Appraisal.status == AppraisalStatus.CALIBRATION,
                )
            )
            or 0
        )

        # Completed appraisals (this year)
        year_start = date(date.today().year, 1, 1)
        completed = (
            self.db.scalar(
                select(func.count(Appraisal.appraisal_id)).where(
                    Appraisal.organization_id == org_id,
                    Appraisal.status == AppraisalStatus.COMPLETED,
                    Appraisal.completed_on >= year_start,
                )
            )
            or 0
        )

        # Average rating
        avg_rating = self.db.scalar(
            select(func.avg(Appraisal.final_rating)).where(
                Appraisal.organization_id == org_id,
                Appraisal.status == AppraisalStatus.COMPLETED,
            )
        )

        return {
            "active_cycles": active_cycles,
            "pending_self_assessment": pending_self,
            "pending_manager_review": pending_manager,
            "pending_calibration": pending_calibration,
            "completed_appraisals": completed,
            "average_rating": Decimal(str(avg_rating)).quantize(Decimal("0.1"))
            if avg_rating
            else None,
        }

    def get_cycle_statistics(self, org_id: UUID, cycle_id: UUID) -> dict:
        """Get statistics for a specific appraisal cycle."""
        self.get_cycle(org_id, cycle_id)

        total = (
            self.db.scalar(
                select(func.count(Appraisal.appraisal_id)).where(
                    Appraisal.organization_id == org_id,
                    Appraisal.cycle_id == cycle_id,
                )
            )
            or 0
        )

        results = self.db.execute(
            select(Appraisal.status, func.count(Appraisal.appraisal_id))
            .where(
                Appraisal.organization_id == org_id,
                Appraisal.cycle_id == cycle_id,
            )
            .group_by(Appraisal.status)
        ).all()
        status_counts = {status.value: count for status, count in results}

        avg_final_rating = self.db.scalar(
            select(func.avg(Appraisal.final_rating)).where(
                Appraisal.organization_id == org_id,
                Appraisal.cycle_id == cycle_id,
                Appraisal.final_rating.isnot(None),
            )
        )

        return {
            "cycle_id": cycle_id,
            "total": total,
            "status_counts": status_counts,
            "average_final_rating": float(avg_final_rating)
            if avg_final_rating is not None
            else None,
        }

    # ─────────────────────────────────────────────────────────────────────────────
    # Performance Reports
    # ─────────────────────────────────────────────────────────────────────────────

    def get_ratings_distribution_report(
        self,
        org_id: UUID,
        *,
        cycle_id: UUID | None = None,
    ) -> dict:
        """Get performance ratings distribution report.

        Returns rating distribution across all completed appraisals,
        optionally filtered by cycle.
        """
        # Base filter for completed appraisals
        filters = [
            Appraisal.organization_id == org_id,
            Appraisal.status == AppraisalStatus.COMPLETED,
            Appraisal.final_rating.isnot(None),
        ]
        if cycle_id:
            filters.append(Appraisal.cycle_id == cycle_id)

        # Get rating distribution (1-5 scale)
        results = self.db.execute(
            select(Appraisal.final_rating, func.count(Appraisal.appraisal_id))
            .where(*filters)
            .group_by(Appraisal.final_rating)
            .order_by(Appraisal.final_rating.desc())
        ).all()

        total_appraisals = sum(count for _, count in results)

        # Build distribution
        rating_labels = {
            5: "Exceptional",
            4: "Exceeds Expectations",
            3: "Meets Expectations",
            2: "Needs Improvement",
            1: "Unsatisfactory",
        }

        distribution = []
        rating_counts = {rating: count for rating, count in results}

        for rating in range(5, 0, -1):
            count = rating_counts.get(rating, 0)
            pct = (
                round(count / total_appraisals * 100, 1) if total_appraisals > 0 else 0
            )
            distribution.append(
                {
                    "rating": rating,
                    "label": rating_labels[rating],
                    "count": count,
                    "percentage": pct,
                }
            )

        # Calculate average rating
        avg_rating = self.db.scalar(
            select(func.avg(Appraisal.final_rating)).where(*filters)
        )

        # Get cycles for filter dropdown
        cycles = self.db.scalars(
            select(AppraisalCycle)
            .where(AppraisalCycle.organization_id == org_id)
            .order_by(AppraisalCycle.created_at.desc())
        ).all()

        return {
            "distribution": distribution,
            "total_appraisals": total_appraisals,
            "average_rating": round(float(avg_rating), 1) if avg_rating else None,
            "cycles": cycles,
            "selected_cycle_id": cycle_id,
        }

    def get_performance_by_department_report(
        self,
        org_id: UUID,
        *,
        cycle_id: UUID | None = None,
    ) -> dict:
        """Get performance breakdown by department.

        Returns average ratings and appraisal counts per department.
        """
        from app.models.people.hr import Department, Employee

        # Base filters
        filters = [
            Appraisal.organization_id == org_id,
            Appraisal.status == AppraisalStatus.COMPLETED,
        ]
        if cycle_id:
            filters.append(Appraisal.cycle_id == cycle_id)

        # Query aggregated by department
        results = self.db.execute(
            select(
                Department.department_id,
                Department.department_name,
                func.count(Appraisal.appraisal_id).label("appraisal_count"),
                func.avg(Appraisal.final_rating).label("avg_rating"),
                func.avg(Appraisal.final_score).label("avg_score"),
            )
            .select_from(Appraisal)
            .join(Employee, Appraisal.employee_id == Employee.employee_id)
            .join(Department, Employee.department_id == Department.department_id)
            .where(*filters)
            .group_by(Department.department_id, Department.department_name)
            .order_by(func.avg(Appraisal.final_rating).desc())
        ).all()

        departments = []
        total_appraisals = sum(r.appraisal_count for r in results)

        for row in results:
            departments.append(
                {
                    "department_id": row.department_id,
                    "department_name": row.department_name,
                    "appraisal_count": row.appraisal_count,
                    "average_rating": round(float(row.avg_rating), 1)
                    if row.avg_rating
                    else None,
                    "average_score": round(float(row.avg_score), 1)
                    if row.avg_score
                    else None,
                    "percentage": round(row.appraisal_count / total_appraisals * 100, 1)
                    if total_appraisals > 0
                    else 0,
                }
            )

        # Overall stats
        overall_avg = self.db.scalar(
            select(func.avg(Appraisal.final_rating)).where(*filters)
        )

        return {
            "departments": departments,
            "total_departments": len(departments),
            "total_appraisals": total_appraisals,
            "overall_average_rating": round(float(overall_avg), 1)
            if overall_avg
            else None,
        }

    def get_kpi_achievement_report(
        self,
        org_id: UUID,
        *,
        start_date: date | None = None,
        end_date: date | None = None,
        department_id: UUID | None = None,
    ) -> dict:
        """Get KPI achievement rates report.

        Returns KPI achievement statistics by status and category.
        """
        from app.models.people.hr import Employee

        # Base filters
        filters = [KPI.organization_id == org_id]

        if department_id:
            subquery = select(Employee.employee_id).where(
                Employee.department_id == department_id,
                Employee.organization_id == org_id,
            )
            filters.append(KPI.employee_id.in_(subquery))
        if start_date:
            filters.append(KPI.period_start >= start_date)
        if end_date:
            filters.append(KPI.period_end <= end_date)

        # Status breakdown
        status_results = self.db.execute(
            select(KPI.status, func.count(KPI.kpi_id))
            .where(*filters)
            .group_by(KPI.status)
        ).all()

        total_kpis = sum(count for _, count in status_results)
        status_breakdown = []

        for status, count in status_results:
            status_breakdown.append(
                {
                    "status": status.value,
                    "count": count,
                    "percentage": round(count / total_kpis * 100, 1)
                    if total_kpis > 0
                    else 0,
                }
            )

        # Achievement statistics for achieved KPIs
        completed_kpis = self.db.scalars(
            select(KPI).where(
                *filters,
                KPI.status == KPIStatus.ACHIEVED,
                KPI.target_value.isnot(None),
                KPI.actual_value.isnot(None),
            )
        ).all()

        achieved = 0
        exceeded = 0
        partial = 0

        for kpi in completed_kpis:
            if kpi.actual_value is None or kpi.target_value is None:
                continue
            if kpi.actual_value >= kpi.target_value:
                if kpi.actual_value > kpi.target_value:
                    exceeded += 1
                else:
                    achieved += 1
            else:
                partial += 1

        total_completed = len(completed_kpis)
        achievement_stats = {
            "total_completed": total_completed,
            "achieved": achieved,
            "exceeded": exceeded,
            "partial": partial,
            "achievement_rate": round((achieved + exceeded) / total_completed * 100, 1)
            if total_completed > 0
            else 0,
        }

        # Top performing KPIs (by achievement percentage)
        class TopKPIEntry(TypedDict):
            kpi_id: UUID
            kpi_title: str
            employee_id: UUID
            achievement_percentage: float

        top_kpis: list[TopKPIEntry] = []
        for kpi in completed_kpis:
            if kpi.target_value is None or kpi.actual_value is None:
                continue
            if kpi.target_value > 0:
                achievement_pct = float(kpi.actual_value / kpi.target_value * 100)
                if achievement_pct >= 100:
                    top_kpis.append(
                        {
                            "kpi_id": kpi.kpi_id,
                            "kpi_title": kpi.kpi_name,
                            "employee_id": kpi.employee_id,
                            "achievement_percentage": round(achievement_pct, 1),
                        }
                    )

        top_kpis.sort(key=lambda x: x["achievement_percentage"], reverse=True)

        return {
            "total_kpis": total_kpis,
            "status_breakdown": status_breakdown,
            "achievement_stats": achievement_stats,
            "top_kpis": top_kpis[:10],
        }

    def get_performance_trends_report(
        self,
        org_id: UUID,
        *,
        department_id: UUID | None = None,
    ) -> dict:
        """Get performance trends across cycles.

        Returns historical performance data by appraisal cycle.
        """
        from app.models.people.hr import Employee

        employee_subquery = None
        if department_id:
            employee_subquery = select(Employee.employee_id).where(
                Employee.department_id == department_id,
                Employee.organization_id == org_id,
            )

        # Get all cycles with their statistics
        cycles = self.db.scalars(
            select(AppraisalCycle)
            .where(AppraisalCycle.organization_id == org_id)
            .order_by(AppraisalCycle.review_period_start.desc())
        ).all()

        cycle_data = []
        all_ratings = []

        for cycle in cycles:
            # Get stats for this cycle
            base_filters = [
                Appraisal.organization_id == org_id,
                Appraisal.cycle_id == cycle.cycle_id,
            ]
            if employee_subquery is not None:
                base_filters.append(Appraisal.employee_id.in_(employee_subquery))

            total = (
                self.db.scalar(
                    select(func.count(Appraisal.appraisal_id)).where(*base_filters)
                )
                or 0
            )

            completed = (
                self.db.scalar(
                    select(func.count(Appraisal.appraisal_id)).where(
                        *base_filters,
                        Appraisal.status == AppraisalStatus.COMPLETED,
                    )
                )
                or 0
            )

            avg_rating = self.db.scalar(
                select(func.avg(Appraisal.final_rating)).where(
                    *base_filters,
                    Appraisal.final_rating.isnot(None),
                )
            )

            completion_rate = round(completed / total * 100, 1) if total > 0 else 0

            cycle_data.append(
                {
                    "cycle_id": cycle.cycle_id,
                    "cycle_name": cycle.cycle_name,
                    "period_start": cycle.review_period_start,
                    "period_end": cycle.review_period_end,
                    "status": cycle.status.value,
                    "total_appraisals": total,
                    "completed_appraisals": completed,
                    "completion_rate": completion_rate,
                    "average_rating": round(float(avg_rating), 1)
                    if avg_rating
                    else None,
                }
            )

            if avg_rating:
                all_ratings.append(float(avg_rating))

        # Overall trend
        overall_avg = (
            round(sum(all_ratings) / len(all_ratings), 1) if all_ratings else None
        )

        return {
            "cycles": cycle_data,
            "total_cycles": len(cycles),
            "overall_average_rating": overall_avg,
        }

    # ─────────────────────────────────────────────────────────────────────────────
    # 360° Feedback
    # ─────────────────────────────────────────────────────────────────────────────

    def list_feedback(
        self,
        org_id: UUID,
        *,
        appraisal_id: UUID | None = None,
        feedback_from_id: UUID | None = None,
        feedback_type: str | None = None,
        submitted: bool | None = None,
        pagination: PaginationParams | None = None,
    ) -> PaginatedResult[AppraisalFeedback]:
        """List feedback entries."""
        query = select(AppraisalFeedback).where(
            AppraisalFeedback.organization_id == org_id
        )

        if appraisal_id:
            query = query.where(AppraisalFeedback.appraisal_id == appraisal_id)

        if feedback_from_id:
            query = query.where(AppraisalFeedback.feedback_from_id == feedback_from_id)

        if feedback_type:
            query = query.where(AppraisalFeedback.feedback_type == feedback_type)

        if submitted is not None:
            if submitted:
                query = query.where(AppraisalFeedback.submitted_on.isnot(None))
            else:
                query = query.where(AppraisalFeedback.submitted_on.is_(None))

        query = query.options(
            joinedload(AppraisalFeedback.feedback_from),
            joinedload(AppraisalFeedback.appraisal).joinedload(Appraisal.employee),
        )
        query = query.order_by(AppraisalFeedback.created_at.desc())

        count_query = select(func.count()).select_from(query.subquery())
        total = self.db.scalar(count_query) or 0

        if pagination:
            query = query.offset(pagination.offset).limit(pagination.limit)

        items = list(self.db.scalars(query).unique().all())

        return PaginatedResult(
            items=items,
            total=total,
            offset=pagination.offset if pagination else 0,
            limit=pagination.limit if pagination else len(items),
        )

    def get_feedback(self, org_id: UUID, feedback_id: UUID) -> AppraisalFeedback:
        """Get feedback by ID."""
        feedback = self.db.scalar(
            select(AppraisalFeedback)
            .options(
                joinedload(AppraisalFeedback.feedback_from),
                joinedload(AppraisalFeedback.appraisal).joinedload(Appraisal.employee),
            )
            .where(
                AppraisalFeedback.feedback_id == feedback_id,
                AppraisalFeedback.organization_id == org_id,
            )
        )
        if not feedback:
            raise PerformanceServiceError(f"Feedback {feedback_id} not found")
        return feedback

    def request_feedback(
        self,
        org_id: UUID,
        *,
        appraisal_id: UUID,
        feedback_from_id: UUID,
        feedback_type: str,
        is_anonymous: bool = False,
    ) -> AppraisalFeedback:
        """Request feedback from an employee."""
        # Verify appraisal exists
        self.get_appraisal(org_id, appraisal_id)

        feedback = AppraisalFeedback(
            organization_id=org_id,
            appraisal_id=appraisal_id,
            feedback_from_id=feedback_from_id,
            feedback_type=feedback_type,
            is_anonymous=is_anonymous,
        )
        self.db.add(feedback)
        self.db.flush()
        return feedback

    def submit_feedback(
        self,
        org_id: UUID,
        feedback_id: UUID,
        *,
        overall_rating: int | None = None,
        strengths: str | None = None,
        areas_for_improvement: str | None = None,
        general_comments: str | None = None,
    ) -> AppraisalFeedback:
        """Submit feedback."""
        feedback = self.get_feedback(org_id, feedback_id)

        feedback.overall_rating = overall_rating
        feedback.strengths = strengths
        feedback.areas_for_improvement = areas_for_improvement
        feedback.general_comments = general_comments
        feedback.submitted_on = date.today()

        self.db.flush()
        return feedback

    def delete_feedback(self, org_id: UUID, feedback_id: UUID) -> None:
        """Delete a feedback request."""
        feedback = self.get_feedback(org_id, feedback_id)
        if feedback.submitted_on:
            raise PerformanceServiceError("Cannot delete submitted feedback")
        self.db.delete(feedback)
        self.db.flush()

    def get_pending_feedback_for_employee(
        self, org_id: UUID, employee_id: UUID
    ) -> list[AppraisalFeedback]:
        """Get pending feedback requests for an employee."""
        result = (
            self.db.scalars(
                select(AppraisalFeedback)
                .options(
                    joinedload(AppraisalFeedback.appraisal).joinedload(
                        Appraisal.employee
                    ),
                )
                .where(
                    AppraisalFeedback.organization_id == org_id,
                    AppraisalFeedback.feedback_from_id == employee_id,
                    AppraisalFeedback.submitted_on.is_(None),
                )
                .order_by(AppraisalFeedback.created_at.desc())
            )
            .unique()
            .all()
        )
        return list(result)
