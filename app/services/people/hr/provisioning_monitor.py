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


def provisioning_status(employee: Employee) -> str:
    """Derive the aggregate from every stage shown in the UI."""
    state = employee.workforce_provisioning_state or {}
    statuses = {
        stage: (state.get(stage) or {}).get("status", "not_started") for stage in STAGES
    }
    if "failed" in statuses.values():
        return "failed"
    if any(
        status in {"running", "pending", "retry_scheduled"}
        for status in statuses.values()
    ):
        return "in_progress"
    if all(status in FINAL_STATUSES for status in statuses.values()):
        return "completed"
    return "pending"


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
    employee.workforce_provisioning_status = provisioning_status(employee)


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
    if limit <= 0 or status not in {
        None,
        "failed",
        "in_progress",
        "completed",
        "pending",
    }:
        return []
    stmt = (
        select(Employee, Person)
        .join(Person, Person.id == Employee.person_id)
        .where(
            Employee.organization_id == organization_id,
            Employee.mailcow_provisioning_requested_at.is_not(None),
        )
        .order_by(Employee.mailcow_provisioning_requested_at.desc())
    )
    if search:
        term = f"%{search.strip()}%"
        stmt = stmt.where(
            or_(
                Employee.employee_code.ilike(term),
                Person.name_expr().ilike(term),
                Person.email.ilike(term),
            )
        )
    if not status:
        stmt = stmt.limit(limit)
    rows: list[dict[str, Any]] = []
    result = db.execute(stmt.execution_options(yield_per=100))
    try:
        for employee, person in result:
            if status and provisioning_status(employee) != status:
                continue
            rows.append(
                {"employee": employee, "person": person, "stages": stage_rows(employee)}
            )
            if len(rows) >= limit:
                break
    finally:
        result.close()
    return rows
