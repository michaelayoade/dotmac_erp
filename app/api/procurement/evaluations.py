"""
Bid Evaluation API Endpoints.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import require_organization_id, require_tenant_auth
from app.db import SessionLocal
from app.schemas.procurement.evaluation import (
    EvaluationCreate,
    EvaluationResponse,
    EvaluationScoreCreate,
    EvaluationScoreResponse,
)
from app.services.common import NotFoundError, ValidationError
from app.services.procurement.evaluation import BidEvaluationService

router = APIRouter(prefix="/evaluations", tags=["procurement-evaluations"])


def get_db():
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@router.get("", response_model=list[EvaluationResponse])
def list_evaluations(
    organization_id: UUID = Depends(require_organization_id),
    rfq_id: UUID | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """List bid evaluations."""
    service = BidEvaluationService(db)
    evals, _ = service.list_evaluations(
        organization_id,
        rfq_id=rfq_id,
        offset=offset,
        limit=limit,
    )
    return [EvaluationResponse.model_validate(e) for e in evals]


@router.get("/{evaluation_id}", response_model=EvaluationResponse)
def get_evaluation(
    evaluation_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Get a bid evaluation by ID."""
    service = BidEvaluationService(db)
    evaluation = service.get_by_id(organization_id, evaluation_id)
    if not evaluation:
        raise HTTPException(status_code=404, detail="Evaluation not found")
    return EvaluationResponse.model_validate(evaluation)


@router.post("", response_model=EvaluationResponse, status_code=status.HTTP_201_CREATED)
def create_evaluation(
    data: EvaluationCreate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Create a new bid evaluation."""
    service = BidEvaluationService(db)
    try:
        evaluation = service.create(organization_id, data)
        return EvaluationResponse.model_validate(evaluation)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{evaluation_id}/score", response_model=EvaluationScoreResponse)
def add_score(
    evaluation_id: UUID,
    data: EvaluationScoreCreate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Add a score to an evaluation."""
    service = BidEvaluationService(db)
    try:
        score = service.score_bid(organization_id, evaluation_id, data)
        return EvaluationScoreResponse.model_validate(score)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{evaluation_id}/approve", response_model=EvaluationResponse)
def approve_evaluation(
    evaluation_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_auth),
    db: Session = Depends(get_db),
):
    """Approve a bid evaluation."""
    service = BidEvaluationService(db)
    person_id = auth.get("person_id")
    if not person_id:
        raise HTTPException(status_code=400, detail="Missing person_id")
    user_id = UUID(person_id)
    try:
        evaluation = service.approve(organization_id, evaluation_id, user_id)
        return EvaluationResponse.model_validate(evaluation)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
