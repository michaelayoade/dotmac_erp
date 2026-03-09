import logging
from typing import Any

from fastapi import HTTPException
from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.models.scheduler import ScheduledTask, ScheduleType
from app.schemas.scheduler import ScheduledTaskCreate, ScheduledTaskUpdate
from app.services.common import coerce_uuid
from app.services.response import ListResponseMixin

logger = logging.getLogger(__name__)


def _apply_ordering(
    query: Select[Any],
    order_by: str,
    order_dir: str,
    allowed_columns: dict[str, Any],
) -> Select[Any]:
    if order_by not in allowed_columns:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid order_by. Allowed: {', '.join(sorted(allowed_columns))}",
        )
    column = allowed_columns[order_by]
    if order_dir == "desc":
        return query.order_by(column.desc())
    return query.order_by(column.asc())


def _apply_pagination(query: Select[Any], limit: int, offset: int) -> Select[Any]:
    return query.limit(limit).offset(offset)


def _validate_schedule_type(value: str | None) -> ScheduleType | None:
    if value is None:
        return None
    if isinstance(value, ScheduleType):
        return value
    try:
        return ScheduleType(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid schedule_type") from exc


class ScheduledTasks(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: ScheduledTaskCreate) -> ScheduledTask:
        if payload.interval_seconds < 1:
            raise HTTPException(status_code=400, detail="interval_seconds must be >= 1")
        task = ScheduledTask(**payload.model_dump())
        db.add(task)
        db.commit()
        db.refresh(task)
        return task

    @staticmethod
    def get(db: Session, task_id: str) -> ScheduledTask:
        task = db.get(ScheduledTask, coerce_uuid(task_id))
        if not task:
            raise HTTPException(status_code=404, detail="Scheduled task not found")
        return task

    @staticmethod
    def list(
        db: Session,
        enabled: bool | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ) -> list[ScheduledTask]:
        allowed_columns = {"created_at", "name"}
        if order_by not in allowed_columns:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid order_by. Allowed: {', '.join(sorted(allowed_columns))}",
            )

        query = select(ScheduledTask)
        if enabled is not None:
            query = query.where(ScheduledTask.enabled == enabled)

        sort_column = (
            ScheduledTask.created_at if order_by == "created_at" else ScheduledTask.name
        )
        if order_dir == "desc":
            query = query.order_by(sort_column.desc())
        else:
            query = query.order_by(sort_column.asc())

        return list(db.scalars(query.limit(limit).offset(offset)).all())

    @staticmethod
    def update(
        db: Session, task_id: str, payload: ScheduledTaskUpdate
    ) -> ScheduledTask:
        task = db.get(ScheduledTask, coerce_uuid(task_id))
        if not task:
            raise HTTPException(status_code=404, detail="Scheduled task not found")
        data = payload.model_dump(exclude_unset=True)
        if "schedule_type" in data:
            data["schedule_type"] = _validate_schedule_type(data["schedule_type"])
        if "interval_seconds" in data and data["interval_seconds"] is not None:
            if data["interval_seconds"] < 1:
                raise HTTPException(
                    status_code=400, detail="interval_seconds must be >= 1"
                )
        for key, value in data.items():
            setattr(task, key, value)
        db.commit()
        db.refresh(task)
        return task

    @staticmethod
    def delete(db: Session, task_id: str) -> None:
        task = db.get(ScheduledTask, coerce_uuid(task_id))
        if not task:
            raise HTTPException(status_code=404, detail="Scheduled task not found")
        db.delete(task)
        db.commit()


scheduled_tasks = ScheduledTasks()


def refresh_schedule() -> dict:
    return {"detail": "Celery beat refreshes schedules automatically."}


def enqueue_task(task_name: str, args: list | None, kwargs: dict | None) -> dict:
    from app.celery_app import celery_app

    async_result = celery_app.send_task(task_name, args=args or [], kwargs=kwargs or {})
    return {"queued": True, "task_id": str(async_result.id)}
