from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.schemas.common import ListResponse
from app.schemas.person import PersonCreate, PersonRead, PersonUpdate
from app.services import person as person_service
from app.services.auth_dependencies import require_permission

router = APIRouter(prefix="/people", tags=["people"])


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


@router.post("", response_model=PersonRead, status_code=status.HTTP_201_CREATED)
def create_person(
    payload: PersonCreate,
    auth: dict = Depends(require_permission("people:write")),
    db: Session = Depends(get_db),
):
    return person_service.people.create(db, payload)


@router.get("/{person_id}", response_model=PersonRead)
def get_person(
    person_id: UUID,
    auth: dict = Depends(require_permission("people:read")),
    db: Session = Depends(get_db),
):
    return person_service.people.get(db, str(person_id))


@router.get("", response_model=ListResponse[PersonRead])
def list_people(
    email: str | None = None,
    person_status: str | None = None,
    is_active: bool | None = None,
    order_by: str = Query(default="created_at"),
    order_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    auth: dict = Depends(require_permission("people:read")),
    db: Session = Depends(get_db),
):
    return person_service.people.list_response(
        db, email, person_status, is_active, order_by, order_dir, limit, offset
    )


@router.patch("/{person_id}", response_model=PersonRead)
def update_person(
    person_id: UUID,
    payload: PersonUpdate,
    auth: dict = Depends(require_permission("people:write")),
    db: Session = Depends(get_db),
):
    return person_service.people.update(db, str(person_id), payload)


@router.delete("/{person_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_person(
    person_id: UUID,
    auth: dict = Depends(require_permission("people:write")),
    db: Session = Depends(get_db),
):
    person_service.people.delete(db, str(person_id))
