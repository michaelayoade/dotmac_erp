"""Employee lifecycle service implementation."""
from __future__ import annotations

from datetime import date
from typing import Optional
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.models.people.hr.lifecycle import (
    BoardingStatus,
    EmployeeOnboarding,
    EmployeeOnboardingActivity,
    EmployeeSeparation,
    EmployeeSeparationActivity,
    EmployeePromotion,
    EmployeePromotionDetail,
    EmployeeTransfer,
    EmployeeTransferDetail,
)
from app.services.common import PaginatedResult, PaginationParams
from app.services.people.hr.errors import (
    LifecycleStatusError,
    OnboardingNotFoundError,
    PromotionNotFoundError,
    SeparationNotFoundError,
    TransferNotFoundError,
)

__all__ = ["LifecycleService"]


class LifecycleService:
    """Service for onboarding, separation, promotions, and transfers."""

    def __init__(self, db: Session) -> None:
        self.db = db

    # =========================================================================
    # Onboarding
    # =========================================================================

    def list_onboardings(
        self,
        org_id: UUID,
        *,
        employee_id: Optional[UUID] = None,
        status: Optional[BoardingStatus] = None,
        pagination: Optional[PaginationParams] = None,
    ) -> PaginatedResult[EmployeeOnboarding]:
        query = select(EmployeeOnboarding).where(EmployeeOnboarding.organization_id == org_id)

        if employee_id:
            query = query.where(EmployeeOnboarding.employee_id == employee_id)

        if status:
            query = query.where(EmployeeOnboarding.status == status)

        query = query.options(joinedload(EmployeeOnboarding.activities))
        query = query.order_by(EmployeeOnboarding.created_at.desc())

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

    def get_onboarding(self, org_id: UUID, onboarding_id: UUID) -> EmployeeOnboarding:
        onboarding = self.db.scalar(
            select(EmployeeOnboarding)
            .options(joinedload(EmployeeOnboarding.activities))
            .where(
                EmployeeOnboarding.organization_id == org_id,
                EmployeeOnboarding.onboarding_id == onboarding_id,
            )
        )
        if not onboarding:
            raise OnboardingNotFoundError(str(onboarding_id))
        return onboarding

    def create_onboarding(
        self,
        org_id: UUID,
        *,
        employee_id: UUID,
        job_applicant_id: Optional[UUID] = None,
        job_offer_id: Optional[UUID] = None,
        date_of_joining: Optional[date] = None,
        department_id: Optional[UUID] = None,
        designation_id: Optional[UUID] = None,
        template_name: Optional[str] = None,
        notes: Optional[str] = None,
        activities: Optional[list[dict]] = None,
    ) -> EmployeeOnboarding:
        onboarding = EmployeeOnboarding(
            organization_id=org_id,
            employee_id=employee_id,
            job_applicant_id=job_applicant_id,
            job_offer_id=job_offer_id,
            date_of_joining=date_of_joining,
            department_id=department_id,
            designation_id=designation_id,
            template_name=template_name,
            notes=notes,
            status=BoardingStatus.PENDING,
        )
        self.db.add(onboarding)
        self.db.flush()

        if activities:
            for idx, activity in enumerate(activities):
                self.db.add(
                    EmployeeOnboardingActivity(
                        onboarding_id=onboarding.onboarding_id,
                        activity_name=activity["activity_name"],
                        assignee_role=activity.get("assignee_role"),
                        status=activity.get("status"),
                        completed_on=activity.get("completed_on"),
                        sequence=activity.get("sequence", idx),
                    )
                )
        self.db.flush()
        return onboarding

    def update_onboarding(
        self,
        org_id: UUID,
        onboarding_id: UUID,
        **kwargs,
    ) -> EmployeeOnboarding:
        activities = kwargs.pop("activities", None)
        onboarding = self.get_onboarding(org_id, onboarding_id)

        for key, value in kwargs.items():
            if value is not None and hasattr(onboarding, key):
                setattr(onboarding, key, value)

        if activities is not None:
            self.db.query(EmployeeOnboardingActivity).filter(
                EmployeeOnboardingActivity.onboarding_id == onboarding_id
            ).delete()
            for idx, activity in enumerate(activities):
                self.db.add(
                    EmployeeOnboardingActivity(
                        onboarding_id=onboarding_id,
                        activity_name=activity["activity_name"],
                        assignee_role=activity.get("assignee_role"),
                        status=activity.get("status"),
                        completed_on=activity.get("completed_on"),
                        sequence=activity.get("sequence", idx),
                    )
                )

        self.db.flush()
        return onboarding

    def start_onboarding(self, org_id: UUID, onboarding_id: UUID) -> EmployeeOnboarding:
        onboarding = self.get_onboarding(org_id, onboarding_id)
        if onboarding.status != BoardingStatus.PENDING:
            raise LifecycleStatusError(onboarding.status.value, "start onboarding")
        onboarding.status = BoardingStatus.IN_PROGRESS
        self.db.flush()
        return onboarding

    def complete_onboarding(self, org_id: UUID, onboarding_id: UUID) -> EmployeeOnboarding:
        onboarding = self.get_onboarding(org_id, onboarding_id)
        if onboarding.status not in {BoardingStatus.PENDING, BoardingStatus.IN_PROGRESS}:
            raise LifecycleStatusError(onboarding.status.value, "complete onboarding")
        onboarding.status = BoardingStatus.COMPLETED
        self.db.flush()
        return onboarding

    def complete_onboarding_activity(
        self, org_id: UUID, onboarding_id: UUID, activity_id: UUID, completed: bool = True
    ) -> EmployeeOnboardingActivity:
        """Mark an onboarding activity as complete or incomplete."""
        onboarding = self.get_onboarding(org_id, onboarding_id)
        activity = self.db.scalar(
            select(EmployeeOnboardingActivity).where(
                EmployeeOnboardingActivity.activity_id == activity_id,
                EmployeeOnboardingActivity.onboarding_id == onboarding_id,
            )
        )
        if not activity:
            raise OnboardingNotFoundError(f"Activity {activity_id} not found")

        if completed:
            activity.status = "completed"
            activity.completed_on = date.today()
        else:
            activity.status = None
            activity.completed_on = None

        self.db.flush()
        return activity

    def get_onboarding_for_employee(self, org_id: UUID, employee_id: UUID) -> EmployeeOnboarding | None:
        """Get the active onboarding record for an employee (if any)."""
        return self.db.scalar(
            select(EmployeeOnboarding)
            .options(joinedload(EmployeeOnboarding.activities))
            .where(
                EmployeeOnboarding.organization_id == org_id,
                EmployeeOnboarding.employee_id == employee_id,
            )
            .order_by(EmployeeOnboarding.created_at.desc())
        )

    # =========================================================================
    # Separation
    # =========================================================================

    def list_separations(
        self,
        org_id: UUID,
        *,
        employee_id: Optional[UUID] = None,
        status: Optional[BoardingStatus] = None,
        pagination: Optional[PaginationParams] = None,
    ) -> PaginatedResult[EmployeeSeparation]:
        query = select(EmployeeSeparation).where(EmployeeSeparation.organization_id == org_id)

        if employee_id:
            query = query.where(EmployeeSeparation.employee_id == employee_id)

        if status:
            query = query.where(EmployeeSeparation.status == status)

        query = query.options(joinedload(EmployeeSeparation.activities))
        query = query.order_by(EmployeeSeparation.created_at.desc())

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

    def get_separation(self, org_id: UUID, separation_id: UUID) -> EmployeeSeparation:
        separation = self.db.scalar(
            select(EmployeeSeparation)
            .options(joinedload(EmployeeSeparation.activities))
            .where(
                EmployeeSeparation.organization_id == org_id,
                EmployeeSeparation.separation_id == separation_id,
            )
        )
        if not separation:
            raise SeparationNotFoundError(str(separation_id))
        return separation

    def create_separation(
        self,
        org_id: UUID,
        *,
        employee_id: UUID,
        separation_type: Optional[SeparationType] = None,
        resignation_letter_date: Optional[date] = None,
        separation_date: Optional[date] = None,
        department_id: Optional[UUID] = None,
        designation_id: Optional[UUID] = None,
        reason_for_leaving: Optional[str] = None,
        exit_interview: Optional[str] = None,
        template_name: Optional[str] = None,
        notes: Optional[str] = None,
        activities: Optional[list[dict]] = None,
    ) -> EmployeeSeparation:
        separation = EmployeeSeparation(
            organization_id=org_id,
            employee_id=employee_id,
            separation_type=separation_type,
            resignation_letter_date=resignation_letter_date,
            separation_date=separation_date,
            department_id=department_id,
            designation_id=designation_id,
            reason_for_leaving=reason_for_leaving,
            exit_interview=exit_interview,
            template_name=template_name,
            notes=notes,
            status=BoardingStatus.PENDING,
        )
        self.db.add(separation)
        self.db.flush()

        if activities:
            for idx, activity in enumerate(activities):
                self.db.add(
                    EmployeeSeparationActivity(
                        separation_id=separation.separation_id,
                        activity_name=activity["activity_name"],
                        assignee_role=activity.get("assignee_role"),
                        status=activity.get("status"),
                        completed_on=activity.get("completed_on"),
                        sequence=activity.get("sequence", idx),
                    )
                )
        self.db.flush()
        return separation

    def update_separation(
        self,
        org_id: UUID,
        separation_id: UUID,
        **kwargs,
    ) -> EmployeeSeparation:
        activities = kwargs.pop("activities", None)
        separation = self.get_separation(org_id, separation_id)

        for key, value in kwargs.items():
            if value is not None and hasattr(separation, key):
                setattr(separation, key, value)

        if activities is not None:
            self.db.query(EmployeeSeparationActivity).filter(
                EmployeeSeparationActivity.separation_id == separation_id
            ).delete()
            for idx, activity in enumerate(activities):
                self.db.add(
                    EmployeeSeparationActivity(
                        separation_id=separation_id,
                        activity_name=activity["activity_name"],
                        assignee_role=activity.get("assignee_role"),
                        status=activity.get("status"),
                        completed_on=activity.get("completed_on"),
                        sequence=activity.get("sequence", idx),
                    )
                )
        self.db.flush()
        return separation

    def start_separation(self, org_id: UUID, separation_id: UUID) -> EmployeeSeparation:
        separation = self.get_separation(org_id, separation_id)
        if separation.status != BoardingStatus.PENDING:
            raise LifecycleStatusError(separation.status.value, "start separation")
        separation.status = BoardingStatus.IN_PROGRESS
        self.db.flush()
        return separation

    def complete_separation(self, org_id: UUID, separation_id: UUID) -> EmployeeSeparation:
        separation = self.get_separation(org_id, separation_id)
        if separation.status not in {BoardingStatus.PENDING, BoardingStatus.IN_PROGRESS}:
            raise LifecycleStatusError(separation.status.value, "complete separation")
        separation.status = BoardingStatus.COMPLETED
        self.db.flush()
        return separation

    # =========================================================================
    # Promotions
    # =========================================================================

    def list_promotions(
        self,
        org_id: UUID,
        *,
        employee_id: Optional[UUID] = None,
        pagination: Optional[PaginationParams] = None,
    ) -> PaginatedResult[EmployeePromotion]:
        query = select(EmployeePromotion).where(EmployeePromotion.organization_id == org_id)

        if employee_id:
            query = query.where(EmployeePromotion.employee_id == employee_id)

        query = query.options(joinedload(EmployeePromotion.details))
        query = query.order_by(EmployeePromotion.promotion_date.desc())

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

    def get_promotion(self, org_id: UUID, promotion_id: UUID) -> EmployeePromotion:
        promotion = self.db.scalar(
            select(EmployeePromotion)
            .options(joinedload(EmployeePromotion.details))
            .where(
                EmployeePromotion.organization_id == org_id,
                EmployeePromotion.promotion_id == promotion_id,
            )
        )
        if not promotion:
            raise PromotionNotFoundError(str(promotion_id))
        return promotion

    def create_promotion(
        self,
        org_id: UUID,
        *,
        employee_id: UUID,
        promotion_date: date,
        notes: Optional[str] = None,
        details: Optional[list[dict]] = None,
    ) -> EmployeePromotion:
        promotion = EmployeePromotion(
            organization_id=org_id,
            employee_id=employee_id,
            promotion_date=promotion_date,
            notes=notes,
        )
        self.db.add(promotion)
        self.db.flush()

        if details:
            for idx, detail in enumerate(details):
                self.db.add(
                    EmployeePromotionDetail(
                        promotion_id=promotion.promotion_id,
                        property_name=detail["property_name"],
                        current_value=detail.get("current_value"),
                        new_value=detail.get("new_value"),
                        sequence=detail.get("sequence", idx),
                    )
                )
        self.db.flush()
        return promotion

    def update_promotion(
        self,
        org_id: UUID,
        promotion_id: UUID,
        **kwargs,
    ) -> EmployeePromotion:
        details = kwargs.pop("details", None)
        promotion = self.get_promotion(org_id, promotion_id)

        for key, value in kwargs.items():
            if value is not None and hasattr(promotion, key):
                setattr(promotion, key, value)

        if details is not None:
            self.db.query(EmployeePromotionDetail).filter(
                EmployeePromotionDetail.promotion_id == promotion_id
            ).delete()
            for idx, detail in enumerate(details):
                self.db.add(
                    EmployeePromotionDetail(
                        promotion_id=promotion_id,
                        property_name=detail["property_name"],
                        current_value=detail.get("current_value"),
                        new_value=detail.get("new_value"),
                        sequence=detail.get("sequence", idx),
                    )
                )
        self.db.flush()
        return promotion

    # =========================================================================
    # Transfers
    # =========================================================================

    def list_transfers(
        self,
        org_id: UUID,
        *,
        employee_id: Optional[UUID] = None,
        pagination: Optional[PaginationParams] = None,
    ) -> PaginatedResult[EmployeeTransfer]:
        query = select(EmployeeTransfer).where(EmployeeTransfer.organization_id == org_id)

        if employee_id:
            query = query.where(EmployeeTransfer.employee_id == employee_id)

        query = query.options(joinedload(EmployeeTransfer.details))
        query = query.order_by(EmployeeTransfer.transfer_date.desc())

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

    def get_transfer(self, org_id: UUID, transfer_id: UUID) -> EmployeeTransfer:
        transfer = self.db.scalar(
            select(EmployeeTransfer)
            .options(joinedload(EmployeeTransfer.details))
            .where(
                EmployeeTransfer.organization_id == org_id,
                EmployeeTransfer.transfer_id == transfer_id,
            )
        )
        if not transfer:
            raise TransferNotFoundError(str(transfer_id))
        return transfer

    def create_transfer(
        self,
        org_id: UUID,
        *,
        employee_id: UUID,
        transfer_date: date,
        notes: Optional[str] = None,
        details: Optional[list[dict]] = None,
    ) -> EmployeeTransfer:
        transfer = EmployeeTransfer(
            organization_id=org_id,
            employee_id=employee_id,
            transfer_date=transfer_date,
            notes=notes,
        )
        self.db.add(transfer)
        self.db.flush()

        if details:
            for idx, detail in enumerate(details):
                self.db.add(
                    EmployeeTransferDetail(
                        transfer_id=transfer.transfer_id,
                        property_name=detail["property_name"],
                        current_value=detail.get("current_value"),
                        new_value=detail.get("new_value"),
                        sequence=detail.get("sequence", idx),
                    )
                )
        self.db.flush()
        return transfer

    def update_transfer(
        self,
        org_id: UUID,
        transfer_id: UUID,
        **kwargs,
    ) -> EmployeeTransfer:
        details = kwargs.pop("details", None)
        transfer = self.get_transfer(org_id, transfer_id)

        for key, value in kwargs.items():
            if value is not None and hasattr(transfer, key):
                setattr(transfer, key, value)

        if details is not None:
            self.db.query(EmployeeTransferDetail).filter(
                EmployeeTransferDetail.transfer_id == transfer_id
            ).delete()
            for idx, detail in enumerate(details):
                self.db.add(
                    EmployeeTransferDetail(
                        transfer_id=transfer_id,
                        property_name=detail["property_name"],
                        current_value=detail.get("current_value"),
                        new_value=detail.get("new_value"),
                        sequence=detail.get("sequence", idx),
                    )
                )
        self.db.flush()
        return transfer
