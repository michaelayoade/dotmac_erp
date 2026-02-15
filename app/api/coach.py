"""
Coach API Endpoints.

REST API for listing insights and submitting feedback.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import (
    get_current_employee_id_optional,
    require_organization_id,
    require_tenant_auth,
    require_tenant_permission,
)
from app.schemas.coach import (
    CoachInsightFeedbackUpdate,
    CoachInsightListResponse,
    CoachInsightSummary,
)
from app.services.coach.coach_service import CoachService
from app.services.common import coerce_uuid
from app.web.deps import get_db

router = APIRouter(
    prefix="/coach",
    tags=["coach"],
    dependencies=[
        Depends(require_tenant_auth),
        Depends(require_tenant_permission("coach:insights:read")),
    ],
)


@router.get("/insights", response_model=CoachInsightListResponse)
def list_insights(
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_auth),
    employee_id: UUID | None = Depends(get_current_employee_id_optional),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    include_expired: bool = Query(False),
    db: Session = Depends(get_db),
):
    svc = CoachService(db)
    if not svc.is_enabled():
        # Treat as "not found" to avoid leaking feature existence.
        return CoachInsightListResponse(items=[], total=0, page=page, per_page=per_page)

    person_id = coerce_uuid(auth["person_id"])
    roles = set(auth.get("roles") or [])
    scope = svc.build_scope_for_user(organization_id, person_id, employee_id, roles)
    items, total = svc.list_insights(
        organization_id,
        scope,
        page=page,
        per_page=per_page,
        include_expired=include_expired,
    )
    return CoachInsightListResponse(
        items=[CoachInsightSummary.model_validate(i) for i in items],
        total=total,
        page=page,
        per_page=per_page,
    )


@router.post(
    "/insights/{insight_id}/feedback",
    response_model=CoachInsightSummary,
    dependencies=[Depends(require_tenant_permission("coach:insights:feedback"))],
)
def submit_feedback(
    insight_id: str,
    payload: CoachInsightFeedbackUpdate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    svc = CoachService(db)
    updated = svc.update_feedback(
        organization_id=organization_id,
        insight_id=coerce_uuid(insight_id),
        feedback=payload.feedback,
    )
    db.commit()
    return CoachInsightSummary.model_validate(updated)
