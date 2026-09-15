"""Durable, operator-safe state for the employee account provisioning relay."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.people.hr.employee import Employee
from app.models.person import Person

STAGES = ("mailcow", "activation_email", "nextcloud", "selfcare", "talk")
FINAL_STATUSES = frozenset({"completed", "skipped", "disabled"})


def _safe_error(exc: BaseException | str | None) -> str | None:
    if exc is None:
        return None
    message = " ".join(str(exc).split())
    return message[:300] or "Provisioning failed; inspect worker logs for details."


def record_stage(
    employee: Employee,
    stage: str,
    status: str,
    *,
    error: BaseException | str | None = None,
    next_retry_at: datetime | None = None,
) -> None:
    """Replace one stage snapshot so SQLAlchemy reliably detects the JSON change."""
    if stage not in STAGES:
        raise ValueError(f"Unknown provisioning stage: {stage}")
    now = datetime.now(timezone.utc)
    state: dict[str, Any] = dict(
        getattr(employee, "workforce_provisioning_state", None) or {}
    )
    previous = dict(state.get(stage) or {})
    if status in {"running", "retry_scheduled"}:
        previous["attempts"] = int(previous.get("attempts") or 0) + 1
    previous.update(
        {
            "status": status,
            "updated_at": now.isoformat(),
            "error": _safe_error(error),
            "next_retry_at": next_retry_at.isoformat() if next_retry_at else None,
        }
    )
    if status == "completed":
        previous["completed_at"] = now.isoformat()
    state[stage] = previous
    employee.workforce_provisioning_state = state
    statuses = {
        str(item.get("status")) for item in state.values() if isinstance(item, dict)
    }
    if "failed" in statuses:
        aggregate = "failed"
    elif statuses & {"running", "pending", "retry_scheduled"}:
        aggregate = "in_progress"
    elif state and statuses.issubset(FINAL_STATUSES):
        aggregate = "completed"
    else:
        aggregate = "pending"
    employee.workforce_provisioning_status = aggregate


def stage_rows(employee: Employee) -> list[dict[str, Any]]:
    state = getattr(employee, "workforce_provisioning_state", None) or {}
    labels = {
        "mailcow": "Mailcow mailbox",
        "activation_email": "Activation email",
        "nextcloud": "Nextcloud account",
        "selfcare": "Selfcare account",
        "talk": "Talk mapping",
    }
    rows: list[dict[str, Any]] = []
    for stage in STAGES:
        row = {
            "key": stage,
            "label": labels[stage],
            **dict(state.get(stage) or {"status": "not_started"}),
        }
        if row.get("updated_at"):
            try:
                row["updated_at"] = datetime.fromisoformat(row["updated_at"])
            except (TypeError, ValueError):
                row["updated_at"] = None
        rows.append(row)
    return rows


def list_provisioning_employees(
    db: Session,
    organization_id: UUID,
    *,
    search: str | None = None,
    status: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Return tenant-scoped employee relay snapshots for the HR monitor."""
    stmt = (
        select(Employee, Person)
        .join(Person, Person.id == Employee.person_id)
        .where(
            Employee.organization_id == organization_id,
            Employee.mailcow_provisioning_requested_at.is_not(None),
        )
        .order_by(Employee.mailcow_provisioning_requested_at.desc())
        .limit(limit)
    )
    if status:
        stmt = stmt.where(Employee.workforce_provisioning_status == status)
    if search:
        term = f"%{search.strip()}%"
        stmt = stmt.where(
            or_(
                Employee.employee_code.ilike(term),
                Person.name_expr().ilike(term),
                Person.email.ilike(term),
            )
        )
    return [
        {"employee": employee, "person": person, "stages": stage_rows(employee)}
        for employee, person in db.execute(stmt).all()
    ]
