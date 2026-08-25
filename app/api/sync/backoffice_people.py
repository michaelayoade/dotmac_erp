"""Read-only ERP People projection for the Backoffice replacement programme."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.service_principal import (
    get_db_with_service_org,
    require_explicit_service_scope,
)
from app.schemas.sync.backoffice_people import (
    PeopleProjectionEntity,
    PeopleProjectionPage,
)
from app.services.people.hr.replacement_projection import (
    BackofficePeopleProjectionService,
)

router = APIRouter(prefix="/sync/backoffice/people", tags=["backoffice-people"])


@router.get("/projection", response_model=PeopleProjectionPage)
def read_projection(
    entity: PeopleProjectionEntity,
    after: UUID | None = None,
    limit: int = Query(default=200, ge=1, le=500),
    auth: dict = Depends(require_explicit_service_scope("backoffice:people:read")),
    db: Session = Depends(get_db_with_service_org),
) -> PeopleProjectionPage:
    """Read one tenant-bound keyset page; this endpoint never mutates ERP."""
    organization_id = auth["organization_id"]
    if not isinstance(organization_id, UUID):
        organization_id = UUID(str(organization_id))
    return BackofficePeopleProjectionService(db).page(
        organization_id=organization_id,
        entity=entity,
        after=after,
        limit=limit,
    )


__all__ = ["router"]
