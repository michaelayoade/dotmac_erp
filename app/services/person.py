import logging

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.person import Person, PersonStatus
from app.schemas.person import PersonCreate, PersonUpdate
from app.services.common import coerce_uuid
from app.services.response import ListResponseMixin

logger = logging.getLogger(__name__)


def _apply_ordering(query, order_by, order_dir, allowed_columns):
    if order_by not in allowed_columns:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid order_by. Allowed: {', '.join(sorted(allowed_columns))}",
        )
    column = allowed_columns[order_by]
    if order_dir == "desc":
        return query.order_by(column.desc())
    return query.order_by(column.asc())


def _apply_pagination(query, limit, offset):
    return query.limit(limit).offset(offset)


def _validate_enum(value, enum_cls, label):
    if value is None:
        return None
    try:
        return enum_cls(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid {label}") from exc


class People(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: PersonCreate):
        person = Person(**payload.model_dump())
        db.add(person)
        db.commit()
        db.refresh(person)
        return person

    @staticmethod
    def get(db: Session, person_id: str):
        person = db.get(Person, coerce_uuid(person_id))
        if not person:
            raise HTTPException(status_code=404, detail="Person not found")
        return person

    @staticmethod
    def list(
        db: Session,
        email: str | None,
        status: str | None,
        is_active: bool | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = db.query(Person)
        if email:
            query = query.filter(Person.email.ilike(f"%{email}%"))
        if status:
            query = query.filter(
                Person.status == _validate_enum(status, PersonStatus, "status")
            )
        if is_active is not None:
            query = query.filter(Person.is_active == is_active)
        query = _apply_ordering(
            query,
            order_by,
            order_dir,
            {
                "created_at": Person.created_at,
                "last_name": Person.last_name,
                "email": Person.email,
            },
        )
        return _apply_pagination(query, limit, offset).all()

    @staticmethod
    def update(db: Session, person_id: str, payload: PersonUpdate):
        person = db.get(Person, coerce_uuid(person_id))
        if not person:
            raise HTTPException(status_code=404, detail="Person not found")
        for key, value in payload.model_dump(exclude_unset=True).items():
            setattr(person, key, value)
        db.commit()
        db.refresh(person)
        return person

    @staticmethod
    def delete(db: Session, person_id: str):
        person = db.get(Person, coerce_uuid(person_id))
        if not person:
            raise HTTPException(status_code=404, detail="Person not found")
        db.delete(person)
        db.commit()


people = People()
