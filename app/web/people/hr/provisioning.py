"""HR workforce provisioning monitor and retry controls."""

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.models.people.hr.employee import Employee
from app.services.common import coerce_uuid
from app.services.people.hr.provisioning_monitor import (
    STAGES,
    list_provisioning_employees,
    record_stage,
)
from app.templates import templates
from app.web.deps import WebAuthContext, base_context, get_db_for_org, require_hr_access

router = APIRouter(tags=["workforce-provisioning"])


@router.get("/provisioning", response_class=HTMLResponse)
def provisioning_monitor(
    request: Request,
    search: str | None = None,
    status: str | None = Query(default=None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    org_id = coerce_uuid(auth.organization_id)
    rows = list_provisioning_employees(
        db, org_id, search=search, status=status, limit=200
    )
    context = base_context(request, auth, "Provisioning Monitor", "provisioning", db=db)
    context.update({"rows": rows, "search": search or "", "status": status or ""})
    return templates.TemplateResponse(
        request, "people/hr/provisioning_monitor.html", context
    )


@router.post("/employees/{employee_id}/provisioning/{stage}/retry")
def retry_provisioning_stage(
    employee_id: UUID,
    stage: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    org_id = coerce_uuid(auth.organization_id)
    employee = db.get(Employee, employee_id)
    if not employee or employee.organization_id != org_id or stage not in STAGES:
        return RedirectResponse(
            "/people/hr/provisioning?error=invalid", status_code=303
        )
    if stage == "activation_email":
        employee.mailcow_activation_token_hash = None
        employee.mailcow_activation_expires_at = None
        employee.mailcow_activation_sent_at = None
    record_stage(employee, stage, "pending")
    db.commit()
    if stage == "mailcow":
        from app.tasks.email import run_employee_mailcow_provisioning

        task = run_employee_mailcow_provisioning
    elif stage == "activation_email":
        from app.tasks.email import send_employee_mailbox_activation

        task = send_employee_mailbox_activation
    elif stage == "nextcloud":
        from app.tasks.email import run_employee_nextcloud_provisioning

        task = run_employee_nextcloud_provisioning
    else:
        from app.tasks.staff_sync import sync_employee_staff_account

        task = sync_employee_staff_account
    task.apply_async(args=[str(employee_id), str(org_id)])
    return RedirectResponse(
        f"/people/hr/employees/{employee_id}#account-provisioning", status_code=303
    )


@router.post("/employees/{employee_id}/mailbox-activation/resend")
def resend_mailbox_activation(
    employee_id: UUID,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    return retry_provisioning_stage(employee_id, "activation_email", auth, db)
