"""
Employee Lifecycle API Router.

Onboarding, separation, promotions, and transfers.
"""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import require_organization_id, require_tenant_auth
from app.db import SessionLocal
from app.models.people.hr.lifecycle import BoardingStatus
from app.schemas.people.lifecycle import (
    OnboardingCreate,
    OnboardingRead,
    OnboardingUpdate,
    OnboardingListResponse,
    SeparationCreate,
    SeparationRead,
    SeparationUpdate,
    SeparationListResponse,
    PromotionCreate,
    PromotionRead,
    PromotionUpdate,
    PromotionListResponse,
    TransferCreate,
    TransferRead,
    TransferUpdate,
    TransferListResponse,
)
from app.services.common import PaginationParams
from app.services.people.hr.lifecycle import LifecycleService

router = APIRouter(
    prefix="/lifecycle",
    tags=["lifecycle"],
    dependencies=[Depends(require_tenant_auth)],
)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def parse_enum(value: Optional[str], enum_type, field_name: str):
    if value is None:
        return None
    try:
        return enum_type(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail=f"Invalid {field_name}: {value}"
        ) from exc


# =============================================================================
# Onboarding
# =============================================================================


@router.get("/onboardings", response_model=OnboardingListResponse)
def list_onboardings(
    organization_id: UUID = Depends(require_organization_id),
    employee_id: Optional[UUID] = None,
    status: Optional[str] = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    svc = LifecycleService(db)
    status_enum = parse_enum(status, BoardingStatus, "status")
    result = svc.list_onboardings(
        org_id=organization_id,
        employee_id=employee_id,
        status=status_enum,
        pagination=PaginationParams(offset=offset, limit=limit),
    )
    return OnboardingListResponse(
        items=[OnboardingRead.model_validate(o) for o in result.items],
        total=result.total,
        offset=offset,
        limit=limit,
    )


@router.post(
    "/onboardings", response_model=OnboardingRead, status_code=status.HTTP_201_CREATED
)
def create_onboarding(
    payload: OnboardingCreate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    svc = LifecycleService(db)
    onboarding = svc.create_onboarding(
        org_id=organization_id,
        employee_id=payload.employee_id,
        job_applicant_id=payload.job_applicant_id,
        job_offer_id=payload.job_offer_id,
        date_of_joining=payload.date_of_joining,
        department_id=payload.department_id,
        designation_id=payload.designation_id,
        template_name=payload.template_name,
        notes=payload.notes,
        activities=[a.model_dump() for a in payload.activities],
    )
    db.commit()
    return OnboardingRead.model_validate(onboarding)


@router.get("/onboardings/{onboarding_id}", response_model=OnboardingRead)
def get_onboarding(
    onboarding_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    svc = LifecycleService(db)
    return OnboardingRead.model_validate(
        svc.get_onboarding(organization_id, onboarding_id)
    )


@router.patch("/onboardings/{onboarding_id}", response_model=OnboardingRead)
def update_onboarding(
    onboarding_id: UUID,
    payload: OnboardingUpdate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    svc = LifecycleService(db)
    update_data = payload.model_dump(exclude_unset=True)
    if "activities" in update_data and update_data["activities"] is not None:
        update_data["activities"] = [a.model_dump() for a in update_data["activities"]]
    onboarding = svc.update_onboarding(organization_id, onboarding_id, **update_data)
    db.commit()
    return OnboardingRead.model_validate(onboarding)


@router.post("/onboardings/{onboarding_id}/start", response_model=OnboardingRead)
def start_onboarding(
    onboarding_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    svc = LifecycleService(db)
    onboarding = svc.start_onboarding(organization_id, onboarding_id)
    db.commit()
    return OnboardingRead.model_validate(onboarding)


@router.post("/onboardings/{onboarding_id}/complete", response_model=OnboardingRead)
def complete_onboarding(
    onboarding_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    svc = LifecycleService(db)
    onboarding = svc.complete_onboarding(organization_id, onboarding_id)
    db.commit()
    return OnboardingRead.model_validate(onboarding)


# =============================================================================
# Separation
# =============================================================================


@router.get("/separations", response_model=SeparationListResponse)
def list_separations(
    organization_id: UUID = Depends(require_organization_id),
    employee_id: Optional[UUID] = None,
    status: Optional[str] = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    svc = LifecycleService(db)
    status_enum = parse_enum(status, BoardingStatus, "status")
    result = svc.list_separations(
        org_id=organization_id,
        employee_id=employee_id,
        status=status_enum,
        pagination=PaginationParams(offset=offset, limit=limit),
    )
    return SeparationListResponse(
        items=[SeparationRead.model_validate(s) for s in result.items],
        total=result.total,
        offset=offset,
        limit=limit,
    )


@router.post(
    "/separations", response_model=SeparationRead, status_code=status.HTTP_201_CREATED
)
def create_separation(
    payload: SeparationCreate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    svc = LifecycleService(db)
    separation = svc.create_separation(
        org_id=organization_id,
        employee_id=payload.employee_id,
        separation_type=payload.separation_type,
        resignation_letter_date=payload.resignation_letter_date,
        separation_date=payload.separation_date,
        department_id=payload.department_id,
        designation_id=payload.designation_id,
        reason_for_leaving=payload.reason_for_leaving,
        exit_interview=payload.exit_interview,
        template_name=payload.template_name,
        notes=payload.notes,
        activities=[a.model_dump() for a in payload.activities],
    )
    db.commit()
    return SeparationRead.model_validate(separation)


@router.get("/separations/{separation_id}", response_model=SeparationRead)
def get_separation(
    separation_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    svc = LifecycleService(db)
    return SeparationRead.model_validate(
        svc.get_separation(organization_id, separation_id)
    )


@router.patch("/separations/{separation_id}", response_model=SeparationRead)
def update_separation(
    separation_id: UUID,
    payload: SeparationUpdate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    svc = LifecycleService(db)
    update_data = payload.model_dump(exclude_unset=True)
    if "activities" in update_data and update_data["activities"] is not None:
        update_data["activities"] = [a.model_dump() for a in update_data["activities"]]
    separation = svc.update_separation(organization_id, separation_id, **update_data)
    db.commit()
    return SeparationRead.model_validate(separation)


@router.post("/separations/{separation_id}/start", response_model=SeparationRead)
def start_separation(
    separation_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    svc = LifecycleService(db)
    separation = svc.start_separation(organization_id, separation_id)
    db.commit()
    return SeparationRead.model_validate(separation)


@router.post("/separations/{separation_id}/complete", response_model=SeparationRead)
def complete_separation(
    separation_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    svc = LifecycleService(db)
    separation = svc.complete_separation(organization_id, separation_id)
    db.commit()
    return SeparationRead.model_validate(separation)


# =============================================================================
# Promotions
# =============================================================================


@router.get("/promotions", response_model=PromotionListResponse)
def list_promotions(
    organization_id: UUID = Depends(require_organization_id),
    employee_id: Optional[UUID] = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    svc = LifecycleService(db)
    result = svc.list_promotions(
        org_id=organization_id,
        employee_id=employee_id,
        pagination=PaginationParams(offset=offset, limit=limit),
    )
    return PromotionListResponse(
        items=[PromotionRead.model_validate(p) for p in result.items],
        total=result.total,
        offset=offset,
        limit=limit,
    )


@router.post(
    "/promotions", response_model=PromotionRead, status_code=status.HTTP_201_CREATED
)
def create_promotion(
    payload: PromotionCreate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    svc = LifecycleService(db)
    promotion = svc.create_promotion(
        org_id=organization_id,
        employee_id=payload.employee_id,
        promotion_date=payload.promotion_date,
        notes=payload.notes,
        details=[d.model_dump() for d in payload.details],
    )
    db.commit()
    return PromotionRead.model_validate(promotion)


@router.get("/promotions/{promotion_id}", response_model=PromotionRead)
def get_promotion(
    promotion_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    svc = LifecycleService(db)
    return PromotionRead.model_validate(
        svc.get_promotion(organization_id, promotion_id)
    )


@router.patch("/promotions/{promotion_id}", response_model=PromotionRead)
def update_promotion(
    promotion_id: UUID,
    payload: PromotionUpdate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    svc = LifecycleService(db)
    update_data = payload.model_dump(exclude_unset=True)
    if "details" in update_data and update_data["details"] is not None:
        update_data["details"] = [d.model_dump() for d in update_data["details"]]
    promotion = svc.update_promotion(organization_id, promotion_id, **update_data)
    db.commit()
    return PromotionRead.model_validate(promotion)


# =============================================================================
# Transfers
# =============================================================================


@router.get("/transfers", response_model=TransferListResponse)
def list_transfers(
    organization_id: UUID = Depends(require_organization_id),
    employee_id: Optional[UUID] = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    svc = LifecycleService(db)
    result = svc.list_transfers(
        org_id=organization_id,
        employee_id=employee_id,
        pagination=PaginationParams(offset=offset, limit=limit),
    )
    return TransferListResponse(
        items=[TransferRead.model_validate(t) for t in result.items],
        total=result.total,
        offset=offset,
        limit=limit,
    )


@router.post(
    "/transfers", response_model=TransferRead, status_code=status.HTTP_201_CREATED
)
def create_transfer(
    payload: TransferCreate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    svc = LifecycleService(db)
    transfer = svc.create_transfer(
        org_id=organization_id,
        employee_id=payload.employee_id,
        transfer_date=payload.transfer_date,
        notes=payload.notes,
        details=[d.model_dump() for d in payload.details],
    )
    db.commit()
    return TransferRead.model_validate(transfer)


@router.get("/transfers/{transfer_id}", response_model=TransferRead)
def get_transfer(
    transfer_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    svc = LifecycleService(db)
    return TransferRead.model_validate(svc.get_transfer(organization_id, transfer_id))


@router.patch("/transfers/{transfer_id}", response_model=TransferRead)
def update_transfer(
    transfer_id: UUID,
    payload: TransferUpdate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    svc = LifecycleService(db)
    update_data = payload.model_dump(exclude_unset=True)
    if "details" in update_data and update_data["details"] is not None:
        update_data["details"] = [d.model_dump() for d in update_data["details"]]
    transfer = svc.update_transfer(organization_id, transfer_id, **update_data)
    db.commit()
    return TransferRead.model_validate(transfer)
