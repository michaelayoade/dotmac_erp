"""
Automation Web Routes.

HTML template routes for Recurring Transactions, Workflow Rules,
Custom Fields, and Document Templates.
"""

from datetime import date
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.models.finance.automation import CustomFieldEntityType
from app.services.finance.automation.custom_fields import custom_fields_service
from app.services.finance.automation.recurring import recurring_service
from app.services.finance.automation.web import automation_web_service
from app.services.finance.automation.workflow import workflow_service
from app.templates import templates
from app.web.deps import (
    get_db_for_org,
    WebAuthContext,
    base_context,
    require_automation_access,
)

router = APIRouter(prefix="/automation", tags=["automation-web"])
legacy_router = APIRouter(
    prefix="/finance/automation",
    tags=["automation-legacy-redirects"],
    include_in_schema=False,
)


def _legacy_automation_redirect(
    request: Request, legacy_path: str = ""
) -> RedirectResponse:
    """Redirect legacy Finance bookmarks to the Admin-owned Automation URL."""
    suffix = f"/{legacy_path}" if legacy_path else ""
    target = f"/automation{suffix}"
    if request.url.query:
        target = f"{target}?{request.url.query}"
    return RedirectResponse(url=target, status_code=308)


@legacy_router.get("")
def legacy_automation_root(request: Request) -> RedirectResponse:
    """Redirect the former Finance Automation root without rendering Finance UI."""
    return _legacy_automation_redirect(request)


@legacy_router.get("/{legacy_path:path}")
def legacy_automation_path(request: Request, legacy_path: str) -> RedirectResponse:
    """Redirect legacy Finance Automation GET links to their canonical paths."""
    return _legacy_automation_redirect(request, legacy_path)


# =============================================================================
# Automation Dashboard
# =============================================================================


@router.get("", response_class=HTMLResponse)
def automation_dashboard(
    request: Request,
    auth: WebAuthContext = Depends(require_automation_access),
    db: Session = Depends(get_db_for_org),
):
    """Automation landing page."""
    context = base_context(request, auth, "Automation", "automation")
    return templates.TemplateResponse(request, "admin/automation/index.html", context)


@router.get("/capabilities")
def automation_capability_catalog(
    auth: WebAuthContext = Depends(require_automation_access),
):
    """Expose only entity/event combinations backed by a model connector."""
    from app.services.finance.automation.capabilities import capabilities_payload

    return {"entities": capabilities_payload()}


# =============================================================================
# Recurring Transactions
# =============================================================================


@router.get("/recurring", response_class=HTMLResponse)
def list_recurring(
    request: Request,
    entity_type: str | None = None,
    status: str | None = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_automation_access),
    db: Session = Depends(get_db_for_org),
):
    """Recurring templates list page."""
    context = base_context(request, auth, "Recurring Transactions", "automation")
    context.update(
        automation_web_service.list_recurring_context(
            db,
            str(auth.organization_id),
            entity_type=entity_type,
            status=status,
            page=page,
        )
    )
    return templates.TemplateResponse(
        request, "admin/automation/recurring_list.html", context
    )


@router.get("/recurring/new", response_class=HTMLResponse)
def new_recurring_form(
    request: Request,
    source_type: str | None = Query(None),
    source_id: str | None = Query(None),
    auth: WebAuthContext = Depends(require_automation_access),
    db: Session = Depends(get_db_for_org),
):
    """New recurring template form page."""
    context = base_context(request, auth, "New Recurring Template", "automation")
    context["form_data"] = {}
    context.update(
        automation_web_service.recurring_form_context(
            db,
            str(auth.organization_id),
            source_type=source_type,
            source_id=source_id,
        )
    )
    return templates.TemplateResponse(
        request, "admin/automation/recurring_form.html", context
    )


@router.get("/recurring/{template_id}", response_class=HTMLResponse)
def view_recurring(
    request: Request,
    template_id: str,
    auth: WebAuthContext = Depends(require_automation_access),
    db: Session = Depends(get_db_for_org),
):
    """Recurring template detail page."""
    context = base_context(request, auth, "Recurring Template", "automation")
    context.update(
        automation_web_service.recurring_detail_context(
            db,
            str(auth.organization_id),
            template_id,
        )
    )
    return templates.TemplateResponse(
        request, "admin/automation/recurring_detail.html", context
    )


@router.get("/recurring/{template_id}/edit", response_class=HTMLResponse)
def edit_recurring_form(
    request: Request,
    template_id: str,
    auth: WebAuthContext = Depends(require_automation_access),
    db: Session = Depends(get_db_for_org),
):
    """Edit recurring template form page."""
    context = base_context(request, auth, "Edit Recurring Template", "automation")
    context.update(
        automation_web_service.recurring_form_context(
            db, str(auth.organization_id), template_id
        )
    )
    return templates.TemplateResponse(
        request, "admin/automation/recurring_form.html", context
    )


@router.post("/recurring/new")
async def create_recurring(
    request: Request,
    auth: WebAuthContext = Depends(require_automation_access),
    db: Session = Depends(get_db_for_org),
):
    """Handle recurring template form submission."""
    content_type = request.headers.get("content-type", "")

    if "application/json" in content_type:
        data = await request.json()
    else:
        form_data = await request.form()
        data = dict(form_data)

    try:
        source_type = data.pop("source_type", None)
        source_id = data.pop("source_id", None)

        if source_type in ("invoice", "bill") and source_id:
            # Create recurring template directly from the source entity
            from app.models.finance.automation import RecurringFrequency
            from app.services.formatters import parse_date as _parse_date

            common_kwargs: dict = dict(
                db=db,
                organization_id=auth.organization_id,
                template_name=data.get("template_name", ""),
                frequency=RecurringFrequency(data["frequency"]),
                start_date=_parse_date(data.get("start_date")) or date.today(),
                created_by=auth.user_id,
                end_date=_parse_date(data.get("end_date")),
                occurrences_limit=int(data["occurrences_limit"])
                if data.get("occurrences_limit")
                else None,
                auto_post=data.get("auto_post") == "on",
                auto_send=data.get("auto_send") == "on",
                days_before_due=int(data.get("days_before_due", 30)),
                notify_on_generation=data.get("notify_on_generation") != "off",
                notify_email=data.get("notify_email"),
                description=data.get("description"),
            )

            if source_type == "invoice":
                template = recurring_service.create_from_invoice(
                    invoice_id=UUID(source_id), **common_kwargs
                )
            else:
                template = recurring_service.create_from_bill(
                    bill_id=UUID(source_id), **common_kwargs
                )
        else:
            input_data = automation_web_service.build_recurring_input(data)

            template = recurring_service.create_template(
                db=db,
                organization_id=auth.organization_id,
                input_data=input_data,
                created_by=auth.user_id,
            )

        if "application/json" in content_type:
            return {"success": True, "template_id": str(template.template_id)}

        return RedirectResponse(
            url="/automation/recurring?success=Template+created+successfully",
            status_code=303,
        )

    except Exception as e:
        if "application/json" in content_type:
            return JSONResponse(
                status_code=400,
                content={"detail": str(e)},
            )

        context = base_context(request, auth, "New Recurring Template", "automation")
        context.update(
            automation_web_service.recurring_form_context(
                db,
                str(auth.organization_id),
                source_type=source_type,
                source_id=source_id,
            )
        )
        context["error"] = str(e)
        context["form_data"] = data
        return templates.TemplateResponse(
            request, "admin/automation/recurring_form.html", context
        )


@router.post("/recurring/{template_id}/edit")
async def update_recurring(
    request: Request,
    template_id: str,
    auth: WebAuthContext = Depends(require_automation_access),
    db: Session = Depends(get_db_for_org),
):
    """Handle recurring template update form submission."""
    content_type = request.headers.get("content-type", "")

    if "application/json" in content_type:
        data = await request.json()
    else:
        form_data = await request.form()
        data = dict(form_data)

    try:
        # Build updates dict from form data
        updates = {
            "template_name": data.get("template_name"),
            "description": data.get("description"),
            "frequency": data.get("frequency"),
            "auto_post": data.get("auto_post") == "on",
            "auto_send": data.get("auto_send") == "on",
            "notify_on_generation": data.get("notify_on_generation") != "off",
            "notify_email": data.get("notify_email"),
        }
        updates = {k: v for k, v in updates.items() if v is not None}

        template = recurring_service.update(
            db=db,
            template_id=UUID(template_id),
            updates=updates,
            updated_by=auth.user_id,
        )

        if "application/json" in content_type:
            return {"success": True, "template_id": str(template.template_id)}

        return RedirectResponse(
            url=f"/automation/recurring/{template_id}?success=Template+updated",
            status_code=303,
        )

    except Exception as e:
        if "application/json" in content_type:
            return JSONResponse(
                status_code=400,
                content={"detail": str(e)},
            )

        context = base_context(request, auth, "Edit Recurring Template", "automation")
        context.update(
            automation_web_service.recurring_form_context(
                db, str(auth.organization_id), template_id
            )
        )
        context["error"] = str(e)
        context["form_data"] = data
        return templates.TemplateResponse(
            request, "admin/automation/recurring_form.html", context
        )


@router.post("/recurring/{template_id}/pause")
def pause_recurring(
    request: Request,
    template_id: str,
    auth: WebAuthContext = Depends(require_automation_access),
    db: Session = Depends(get_db_for_org),
):
    """Pause a recurring template."""
    try:
        recurring_service.pause(db, UUID(template_id))
        return RedirectResponse(
            url=f"/automation/recurring/{template_id}?success=Template+paused",
            status_code=303,
        )
    except Exception as e:
        return RedirectResponse(
            url=f"/automation/recurring/{template_id}?error={str(e)}",
            status_code=303,
        )


@router.post("/recurring/{template_id}/resume")
def resume_recurring(
    request: Request,
    template_id: str,
    auth: WebAuthContext = Depends(require_automation_access),
    db: Session = Depends(get_db_for_org),
):
    """Resume a paused recurring template."""
    try:
        recurring_service.resume(db, UUID(template_id))
        return RedirectResponse(
            url=f"/automation/recurring/{template_id}?success=Template+resumed",
            status_code=303,
        )
    except Exception as e:
        return RedirectResponse(
            url=f"/automation/recurring/{template_id}?error={str(e)}",
            status_code=303,
        )


@router.post("/recurring/{template_id}/cancel")
def cancel_recurring(
    request: Request,
    template_id: str,
    auth: WebAuthContext = Depends(require_automation_access),
    db: Session = Depends(get_db_for_org),
):
    """Cancel a recurring template."""
    try:
        recurring_service.cancel(db, UUID(template_id))
        return RedirectResponse(
            url="/automation/recurring?success=Template+cancelled",
            status_code=303,
        )
    except Exception as e:
        return RedirectResponse(
            url=f"/automation/recurring/{template_id}?error={str(e)}",
            status_code=303,
        )


@router.post("/recurring/{template_id}/generate")
def generate_now(
    request: Request,
    template_id: str,
    auth: WebAuthContext = Depends(require_automation_access),
    db: Session = Depends(get_db_for_org),
):
    """Manually generate the next occurrence."""
    try:
        template = recurring_service.get(db, UUID(template_id))
        if not template:
            return RedirectResponse(
                url="/automation/recurring?error=Template+not+found",
                status_code=303,
            )

        log = recurring_service.generate_next(db, template)

        if log.status.value == "SUCCESS":
            return RedirectResponse(
                url=f"/automation/recurring/{template_id}?success=Generated+successfully",
                status_code=303,
            )
        else:
            return RedirectResponse(
                url=f"/automation/recurring/{template_id}?error=Generation+failed:+{log.error_message}",
                status_code=303,
            )
    except Exception as e:
        return RedirectResponse(
            url=f"/automation/recurring/{template_id}?error={str(e)}",
            status_code=303,
        )


# =============================================================================
# Workflow Rules
# =============================================================================


@router.get("/workflows", response_class=HTMLResponse)
def list_workflows(
    request: Request,
    entity_type: str | None = None,
    trigger_event: str | None = None,
    is_active: str | None = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_automation_access),
    db: Session = Depends(get_db_for_org),
):
    """Workflow rules list page."""
    context = base_context(request, auth, "Workflow Rules", "automation")

    active_filter = None
    if is_active == "true":
        active_filter = True
    elif is_active == "false":
        active_filter = False

    context.update(
        automation_web_service.list_workflows_context(
            db,
            str(auth.organization_id),
            entity_type=entity_type,
            trigger_event=trigger_event,
            is_active=active_filter,
        )
    )
    return templates.TemplateResponse(
        request, "admin/automation/workflow_list.html", context
    )


@router.get("/workflows/archived", response_class=HTMLResponse)
def list_archived_workflows(
    request: Request,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_automation_access),
    db: Session = Depends(get_db_for_org),
):
    """Archived workflow rules and their restore actions."""
    context = base_context(request, auth, "Archived Workflow Rules", "automation")
    context.update(
        automation_web_service.list_workflows_context(
            db,
            str(auth.organization_id),
            is_active=None,
            archived=True,
            page=page,
        )
    )
    return templates.TemplateResponse(
        request,
        "admin/automation/workflow_list.html",
        context,
    )


@router.get("/workflows/new", response_class=HTMLResponse)
def new_workflow_form(
    request: Request,
    auth: WebAuthContext = Depends(require_automation_access),
    db: Session = Depends(get_db_for_org),
):
    """New workflow rule form page."""
    context = base_context(request, auth, "New Workflow Rule", "automation")
    context["form_data"] = {}
    context.update(
        automation_web_service.workflow_form_context(db, str(auth.organization_id))
    )
    return templates.TemplateResponse(
        request, "admin/automation/workflow_form.html", context
    )


@router.get("/workflows/monitoring", response_class=HTMLResponse)
def workflow_monitoring(
    request: Request,
    auth: WebAuthContext = Depends(require_automation_access),
    db: Session = Depends(get_db_for_org),
):
    """Workflow execution monitoring dashboard."""
    context = base_context(request, auth, "Workflow Monitoring", "automation")
    context.update(
        automation_web_service.workflow_monitoring_context(
            db, str(auth.organization_id)
        )
    )
    return templates.TemplateResponse(
        request, "admin/automation/workflow_monitoring.html", context
    )


@router.post("/workflows/deliveries/{event_id}/retry")
async def retry_workflow_delivery(
    request: Request,
    event_id: UUID,
    auth: WebAuthContext = Depends(require_automation_access),
    db: Session = Depends(get_db_for_org),
):
    """Requeue an automation outbox delivery owned by the current tenant."""
    await request.form()
    from app.models.finance.platform.event_outbox import EventOutbox
    from app.services.finance.platform.outbox_publisher import OutboxPublisher

    event = db.get(EventOutbox, event_id)
    if (
        event is None
        or event.event_name != "automation.workflow.requested"
        or str((event.headers or {}).get("organization_id"))
        != str(auth.organization_id)
    ):
        return RedirectResponse(
            "/automation/workflows/monitoring?error=delivery_not_found",
            status_code=303,
        )
    try:
        OutboxPublisher.requeue_dead_event(db, event_id)
        db.commit()
    except ValueError:
        return RedirectResponse(
            "/automation/workflows/monitoring?error=delivery_not_retryable",
            status_code=303,
        )
    return RedirectResponse(
        "/automation/workflows/monitoring?message=delivery_requeued",
        status_code=303,
    )


@router.get("/workflows/{rule_id}", response_class=HTMLResponse)
def view_workflow(
    request: Request,
    rule_id: str,
    auth: WebAuthContext = Depends(require_automation_access),
    db: Session = Depends(get_db_for_org),
):
    """Workflow rule detail page."""
    context = base_context(request, auth, "Workflow Rule", "automation")
    context.update(
        automation_web_service.workflow_detail_context(
            db,
            str(auth.organization_id),
            rule_id,
        )
    )
    return templates.TemplateResponse(
        request, "admin/automation/workflow_detail.html", context
    )


@router.get("/workflows/{rule_id}/edit", response_class=HTMLResponse)
def edit_workflow_form(
    request: Request,
    rule_id: str,
    auth: WebAuthContext = Depends(require_automation_access),
    db: Session = Depends(get_db_for_org),
):
    """Edit workflow rule form page."""
    context = base_context(request, auth, "Edit Workflow Rule", "automation")
    context.update(
        automation_web_service.workflow_form_context(
            db, str(auth.organization_id), rule_id
        )
    )
    return templates.TemplateResponse(
        request, "admin/automation/workflow_form.html", context
    )


@router.post("/workflows/new")
async def create_workflow(
    request: Request,
    auth: WebAuthContext = Depends(require_automation_access),
    db: Session = Depends(get_db_for_org),
):
    """Handle workflow rule form submission."""
    content_type = request.headers.get("content-type", "")

    if "application/json" in content_type:
        data = await request.json()
    else:
        form_data = await request.form()
        data = dict(form_data)

    try:
        input_data = automation_web_service.build_workflow_input(data)

        rule = workflow_service.create_rule(
            db=db,
            organization_id=auth.organization_id,
            input_data=input_data,
            created_by=auth.user_id,
        )

        if "application/json" in content_type:
            return {"success": True, "rule_id": str(rule.rule_id)}

        return RedirectResponse(
            url="/automation/workflows?success=Rule+created+successfully",
            status_code=303,
        )

    except Exception as e:
        if "application/json" in content_type:
            return JSONResponse(
                status_code=400,
                content={"detail": str(e)},
            )

        context = base_context(request, auth, "New Workflow Rule", "automation")
        context.update(
            automation_web_service.workflow_form_context(db, str(auth.organization_id))
        )
        context["error"] = str(e)
        context["form_data"] = data
        return templates.TemplateResponse(
            request, "admin/automation/workflow_form.html", context
        )


@router.post("/workflows/{rule_id}/edit")
async def update_workflow(
    request: Request,
    rule_id: str,
    auth: WebAuthContext = Depends(require_automation_access),
    db: Session = Depends(get_db_for_org),
):
    """Handle workflow rule update form submission."""

    def _parse_json_field(value: Any, default: dict | None) -> dict | None:
        if value in (None, "", {}):
            return default
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            import json

            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                return default
            if parsed in (None, "", {}):
                return default
            if isinstance(parsed, dict):
                return parsed
        return default

    content_type = request.headers.get("content-type", "")

    if "application/json" in content_type:
        data = await request.json()
    else:
        form_data = await request.form()
        data = dict(form_data)

    try:
        cooldown = None
        if data.get("cooldown_seconds"):
            try:
                cooldown = int(data["cooldown_seconds"])
            except (ValueError, TypeError):
                pass

        trigger_conditions = _parse_json_field(
            data.get("trigger_conditions"), default=None
        )
        action_config = _parse_json_field(data.get("action_config"), default=None)
        schedule_config = _parse_json_field(data.get("schedule_config"), default=None)

        updates = {
            "rule_name": data.get("rule_name"),
            "description": data.get("description"),
            "trigger_conditions": trigger_conditions,
            "action_config": action_config,
            "priority": int(data["priority"]) if data.get("priority") else None,
            "stop_on_match": data.get("stop_on_match") == "on",
            "execute_async": data.get("execute_async") != "off",
            "is_active": data.get("is_active") != "off",
            "cooldown_seconds": cooldown,
            "schedule_config": schedule_config,
        }
        updates = {k: v for k, v in updates.items() if v is not None}

        rule = workflow_service.update_rule(
            db=db,
            rule_id=UUID(rule_id),
            organization_id=auth.organization_id,
            updates=updates,
            updated_by=auth.user_id,
        )

        if "application/json" in content_type:
            return {"success": True, "rule_id": str(rule.rule_id)}

        return RedirectResponse(
            url=f"/automation/workflows/{rule_id}?success=Rule+updated",
            status_code=303,
        )

    except Exception as e:
        if "application/json" in content_type:
            return JSONResponse(
                status_code=400,
                content={"detail": str(e)},
            )

        context = base_context(request, auth, "Edit Workflow Rule", "automation")
        context.update(
            automation_web_service.workflow_form_context(
                db, str(auth.organization_id), rule_id
            )
        )
        context["error"] = str(e)
        context["form_data"] = data
        return templates.TemplateResponse(
            request, "admin/automation/workflow_form.html", context
        )


@router.post("/workflows/{rule_id}/toggle")
def toggle_workflow(
    request: Request,
    rule_id: str,
    auth: WebAuthContext = Depends(require_automation_access),
    db: Session = Depends(get_db_for_org),
):
    """Toggle workflow rule active status."""
    try:
        rule = workflow_service.get(db, UUID(rule_id), auth.organization_id)
        if not rule:
            return RedirectResponse(
                url="/automation/workflows?error=Rule+not+found",
                status_code=303,
            )

        activate = not rule.is_active
        workflow_service.update_rule(
            db=db,
            rule_id=UUID(rule_id),
            organization_id=auth.organization_id,
            updates={"is_active": activate},
            updated_by=auth.user_id,
        )

        status = "activated" if activate else "deactivated"
        return RedirectResponse(
            url=f"/automation/workflows?success=Rule+{status}",
            status_code=303,
        )
    except Exception as e:
        return RedirectResponse(
            url=f"/automation/workflows?error={str(e)}",
            status_code=303,
        )


@router.get("/workflows/{rule_id}/versions", response_class=HTMLResponse)
def workflow_versions(
    request: Request,
    rule_id: str,
    auth: WebAuthContext = Depends(require_automation_access),
    db: Session = Depends(get_db_for_org),
):
    """Workflow rule version history page."""
    context = base_context(request, auth, "Version History", "automation")
    context.update(
        automation_web_service.workflow_versions_context(
            db, str(auth.organization_id), rule_id
        )
    )
    return templates.TemplateResponse(
        request, "admin/automation/workflow_versions.html", context
    )


@router.post("/workflows/{rule_id}/test")
async def test_workflow(
    request: Request,
    rule_id: str,
    auth: WebAuthContext = Depends(require_automation_access),
    db: Session = Depends(get_db_for_org),
):
    """Dry-run test a workflow rule against sample data."""
    content_type = request.headers.get("content-type", "")

    if "application/json" in content_type:
        data = await request.json()
    else:
        form_data = await request.form()
        data = dict(form_data)

    try:
        result = workflow_service.dry_run(
            db,
            UUID(rule_id),
            auth.organization_id,
            data,
        )
        return JSONResponse(content=result)
    except Exception as e:
        return JSONResponse(
            status_code=400,
            content={"detail": str(e)},
        )


@router.post("/workflows/{rule_id}/archive")
def archive_workflow(
    request: Request,
    rule_id: str,
    auth: WebAuthContext = Depends(require_automation_access),
    db: Session = Depends(get_db_for_org),
):
    """Archive a workflow rule while preserving versions and executions."""
    try:
        archived = workflow_service.archive(
            db,
            UUID(rule_id),
            auth.organization_id,
            auth.user_id,
        )
        if not archived:
            return RedirectResponse(
                url="/automation/workflows?error=Rule+not+found",
                status_code=303,
            )
        return RedirectResponse(
            url="/automation/workflows?success=Rule+archived",
            status_code=303,
        )
    except Exception as e:
        return RedirectResponse(
            url=f"/automation/workflows/{rule_id}?error={str(e)}",
            status_code=303,
        )


@router.post("/workflows/{rule_id}/restore")
def restore_workflow(
    request: Request,
    rule_id: str,
    auth: WebAuthContext = Depends(require_automation_access),
    db: Session = Depends(get_db_for_org),
):
    """Restore an archived workflow as inactive."""
    try:
        restored = workflow_service.restore(
            db,
            UUID(rule_id),
            auth.organization_id,
            auth.user_id,
        )
        if not restored:
            return RedirectResponse(
                url="/automation/workflows/archived?error=Rule+not+found",
                status_code=303,
            )
        return RedirectResponse(
            url=f"/automation/workflows/{rule_id}?success=Rule+restored+as+inactive",
            status_code=303,
        )
    except Exception as e:
        return RedirectResponse(
            url=f"/automation/workflows/archived?error={str(e)}",
            status_code=303,
        )


# =============================================================================
# Custom Fields
# =============================================================================


@router.get("/fields", response_class=HTMLResponse)
def list_custom_fields(
    request: Request,
    entity_type: str | None = None,
    is_active: str | None = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_automation_access),
    db: Session = Depends(get_db_for_org),
):
    """Custom fields list page."""
    context = base_context(request, auth, "Custom Fields", "automation")

    active_filter = None
    if is_active == "true":
        active_filter = True
    elif is_active == "false":
        active_filter = False

    context.update(
        automation_web_service.list_custom_fields_context(
            db,
            str(auth.organization_id),
            entity_type=entity_type,
            is_active=active_filter,
        )
    )
    return templates.TemplateResponse(
        request, "admin/automation/fields_list.html", context
    )


@router.get("/fields/new", response_class=HTMLResponse)
def new_custom_field_form(
    request: Request,
    auth: WebAuthContext = Depends(require_automation_access),
    db: Session = Depends(get_db_for_org),
):
    """New custom field form page."""
    context = base_context(request, auth, "New Custom Field", "automation")
    context["form_data"] = {}
    context.update(
        automation_web_service.custom_field_form_context(db, str(auth.organization_id))
    )
    return templates.TemplateResponse(
        request, "admin/automation/field_form.html", context
    )


@router.get("/fields/render/{entity_type}", response_class=HTMLResponse)
def render_custom_fields(
    request: Request,
    entity_type: CustomFieldEntityType,
    entity_id: UUID | None = None,
    auth: WebAuthContext = Depends(require_automation_access),
    db: Session = Depends(get_db_for_org),
):
    """Reusable HTMX fragment for any module create or edit form."""
    values = (
        custom_fields_service.get_values(
            db, auth.organization_id, entity_type, entity_id
        )
        if entity_id
        else custom_fields_service.merge_with_defaults(
            db, auth.organization_id, entity_type
        )
    )
    return templates.TemplateResponse(
        request,
        "admin/automation/_custom_fields.html",
        {
            "request": request,
            "sections": custom_fields_service.get_form_schema(
                db,
                auth.organization_id,
                entity_type,
                include_inactive_codes=set(values) if entity_id else None,
            ),
            "custom_field_values": values,
            "entity_type": entity_type.value,
        },
    )


@router.get("/fields/schema/{entity_type}")
def custom_field_schema(
    entity_type: CustomFieldEntityType,
    entity_id: UUID | None = None,
    auth: WebAuthContext = Depends(require_automation_access),
    db: Session = Depends(get_db_for_org),
):
    """Typed schema/value contract used by module forms and API clients."""
    values = (
        custom_fields_service.get_values(
            db, auth.organization_id, entity_type, entity_id
        )
        if entity_id
        else custom_fields_service.merge_with_defaults(
            db, auth.organization_id, entity_type
        )
    )
    return {
        "entity_type": entity_type.value,
        "sections": custom_fields_service.get_form_schema(
            db,
            auth.organization_id,
            entity_type,
            include_inactive_codes=set(values) if entity_id else None,
        ),
        "values": values,
    }


@router.post("/fields/values/{entity_type}/{entity_id}")
async def save_custom_field_values(
    request: Request,
    entity_type: CustomFieldEntityType,
    entity_id: UUID,
    auth: WebAuthContext = Depends(require_automation_access),
    db: Session = Depends(get_db_for_org),
):
    """Validate and save the reusable custom-field portion of a module form."""
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        payload = await request.json()
        values = payload.get("values", payload)
    else:
        form = await request.form()
        raw: dict[str, Any] = dict(form)
        for key in form:
            if key.startswith("custom_field__"):
                items = form.getlist(key)
                raw[key] = items if len(items) > 1 else items[0]
        values = custom_fields_service.parse_form_values(
            db, auth.organization_id, entity_type, raw
        )
    saved = custom_fields_service.save_values(
        db,
        auth.organization_id,
        entity_type,
        entity_id,
        values,
        auth.user_id,
    )
    db.commit()
    return {"success": True, "entity_id": str(entity_id), "values": saved}


@router.get("/fields/{field_id}", response_class=HTMLResponse)
def view_custom_field(
    request: Request,
    field_id: str,
    auth: WebAuthContext = Depends(require_automation_access),
    db: Session = Depends(get_db_for_org),
):
    """Custom field detail page."""
    context = base_context(request, auth, "Custom Field", "automation")
    context.update(
        automation_web_service.custom_field_detail_context(
            db,
            str(auth.organization_id),
            field_id,
        )
    )
    return templates.TemplateResponse(
        request, "admin/automation/field_detail.html", context
    )


@router.get("/fields/{field_id}/edit", response_class=HTMLResponse)
def edit_custom_field_form(
    request: Request,
    field_id: str,
    auth: WebAuthContext = Depends(require_automation_access),
    db: Session = Depends(get_db_for_org),
):
    """Edit custom field form page."""
    context = base_context(request, auth, "Edit Custom Field", "automation")
    context.update(
        automation_web_service.custom_field_form_context(
            db, str(auth.organization_id), field_id
        )
    )
    return templates.TemplateResponse(
        request, "admin/automation/field_form.html", context
    )


@router.post("/fields/new")
async def create_custom_field(
    request: Request,
    auth: WebAuthContext = Depends(require_automation_access),
    db: Session = Depends(get_db_for_org),
):
    """Handle custom field form submission."""
    content_type = request.headers.get("content-type", "")

    if "application/json" in content_type:
        data = await request.json()
    else:
        form_data = await request.form()
        data = dict(form_data)

    try:
        input_data = automation_web_service.build_custom_field_input(data)

        field = custom_fields_service.create_field(
            db=db,
            organization_id=auth.organization_id,
            input_data=input_data,
            created_by=auth.user_id,
        )

        if "application/json" in content_type:
            return {"success": True, "field_id": str(field.field_id)}

        return RedirectResponse(
            url="/automation/fields?success=Field+created+successfully",
            status_code=303,
        )

    except Exception as e:
        if "application/json" in content_type:
            return JSONResponse(
                status_code=400,
                content={"detail": str(e)},
            )

        context = base_context(request, auth, "New Custom Field", "automation")
        context.update(
            automation_web_service.custom_field_form_context(
                db, str(auth.organization_id)
            )
        )
        context["error"] = str(e)
        context["form_data"] = data
        return templates.TemplateResponse(
            request, "admin/automation/field_form.html", context
        )


@router.post("/fields/{field_id}/edit")
async def update_custom_field(
    request: Request,
    field_id: str,
    auth: WebAuthContext = Depends(require_automation_access),
    db: Session = Depends(get_db_for_org),
):
    """Handle custom field update form submission."""
    content_type = request.headers.get("content-type", "")

    if "application/json" in content_type:
        data = await request.json()
    else:
        form_data = await request.form()
        data = dict(form_data)

    try:
        updates = {
            "field_name": data.get("field_name"),
            "description": data.get("description"),
            "is_required": data.get("is_required") == "on",
            "default_value": data.get("default_value"),
            "placeholder": data.get("placeholder"),
            "help_text": data.get("help_text"),
            "display_order": int(data["display_order"])
            if data.get("display_order")
            else None,
            "section_name": data.get("section_name"),
            "show_in_list": data.get("show_in_list") == "on",
            "show_in_form": data.get("show_in_form") != "off",
            "show_in_detail": data.get("show_in_detail") != "off",
            "show_in_print": data.get("show_in_print") == "on",
        }
        updates = {k: v for k, v in updates.items() if v is not None}

        field = custom_fields_service.update_field(
            db=db,
            field_id=UUID(field_id),
            organization_id=auth.organization_id,
            updates=updates,
            updated_by=auth.user_id,
        )

        if "application/json" in content_type:
            return {"success": True, "field_id": str(field.field_id)}

        return RedirectResponse(
            url=f"/automation/fields/{field_id}?success=Field+updated",
            status_code=303,
        )

    except Exception as e:
        if "application/json" in content_type:
            return JSONResponse(
                status_code=400,
                content={"detail": str(e)},
            )

        context = base_context(request, auth, "Edit Custom Field", "automation")
        context.update(
            automation_web_service.custom_field_form_context(
                db, str(auth.organization_id), field_id
            )
        )
        context["error"] = str(e)
        context["form_data"] = data
        return templates.TemplateResponse(
            request, "admin/automation/field_form.html", context
        )


@router.post("/fields/{field_id}/deactivate")
def deactivate_custom_field(
    request: Request,
    field_id: str,
    auth: WebAuthContext = Depends(require_automation_access),
    db: Session = Depends(get_db_for_org),
):
    """Deactivate a custom field without deleting historical values."""
    try:
        deactivated = custom_fields_service.deactivate(
            db,
            UUID(field_id),
            auth.organization_id,
            auth.user_id,
        )
        if not deactivated:
            return RedirectResponse(
                url="/automation/fields?error=Field+not+found",
                status_code=303,
            )
        return RedirectResponse(
            url=f"/automation/fields/{field_id}?success=Field+deactivated",
            status_code=303,
        )
    except Exception as e:
        return RedirectResponse(
            url=f"/automation/fields/{field_id}?error={str(e)}",
            status_code=303,
        )


@router.post("/fields/{field_id}/reactivate")
def reactivate_custom_field(
    request: Request,
    field_id: str,
    auth: WebAuthContext = Depends(require_automation_access),
    db: Session = Depends(get_db_for_org),
):
    """Reactivate a custom field for future forms."""
    try:
        reactivated = custom_fields_service.reactivate(
            db,
            UUID(field_id),
            auth.organization_id,
            auth.user_id,
        )
        if not reactivated:
            return RedirectResponse(
                url="/automation/fields?error=Field+not+found",
                status_code=303,
            )
        return RedirectResponse(
            url=f"/automation/fields/{field_id}?success=Field+reactivated",
            status_code=303,
        )
    except Exception as e:
        return RedirectResponse(
            url=f"/automation/fields/{field_id}?error={str(e)}",
            status_code=303,
        )


# =============================================================================
# Document Templates
# =============================================================================


@router.get("/templates", response_class=HTMLResponse)
def list_templates(
    request: Request,
    template_type: str | None = None,
    is_active: str | None = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_automation_access),
    db: Session = Depends(get_db_for_org),
):
    """Document templates list page."""
    context = base_context(request, auth, "Document Templates", "automation")

    active_filter = None
    if is_active == "true":
        active_filter = True
    elif is_active == "false":
        active_filter = False

    context.update(
        automation_web_service.list_templates_context(
            db,
            str(auth.organization_id),
            template_type=template_type,
            is_active=active_filter,
        )
    )
    return templates.TemplateResponse(
        request, "admin/automation/templates_list.html", context
    )


@router.get("/templates/new", response_class=HTMLResponse)
def new_template_form(
    request: Request,
    auth: WebAuthContext = Depends(require_automation_access),
    db: Session = Depends(get_db_for_org),
):
    """New document template form page."""
    context = base_context(request, auth, "New Template", "automation")
    context["form_data"] = {}
    context.update(
        automation_web_service.template_form_context(db, str(auth.organization_id))
    )
    return templates.TemplateResponse(
        request, "admin/automation/template_form.html", context
    )


@router.get("/templates/{template_id}", response_class=HTMLResponse)
def view_template(
    request: Request,
    template_id: str,
    auth: WebAuthContext = Depends(require_automation_access),
    db: Session = Depends(get_db_for_org),
):
    """Document template detail page."""
    context = base_context(request, auth, "Template", "automation")
    context.update(
        automation_web_service.template_detail_context(
            db,
            str(auth.organization_id),
            template_id,
        )
    )
    return templates.TemplateResponse(
        request, "admin/automation/template_detail.html", context
    )


@router.get("/templates/{template_id}/edit", response_class=HTMLResponse)
def edit_template_form(
    request: Request,
    template_id: str,
    auth: WebAuthContext = Depends(require_automation_access),
    db: Session = Depends(get_db_for_org),
):
    """Edit document template form page."""
    context = base_context(request, auth, "Edit Template", "automation")
    context.update(
        automation_web_service.template_form_context(
            db, str(auth.organization_id), template_id
        )
    )
    return templates.TemplateResponse(
        request, "admin/automation/template_form.html", context
    )


@router.post("/templates/new")
async def create_template(
    request: Request,
    auth: WebAuthContext = Depends(require_automation_access),
    db: Session = Depends(get_db_for_org),
):
    """Handle document template form submission."""
    content_type = request.headers.get("content-type", "")

    if "application/json" in content_type:
        data = await request.json()
    else:
        form_data = await request.form()
        data = dict(form_data)

    try:
        template = automation_web_service.create_template(
            db=db,
            organization_id=auth.organization_id,
            user_id=auth.user_id,
            data=data,
        )

        if "application/json" in content_type:
            return {"success": True, "template_id": str(template.template_id)}

        return RedirectResponse(
            url="/automation/templates?success=Template+created+successfully",
            status_code=303,
        )

    except Exception as e:
        if "application/json" in content_type:
            return JSONResponse(
                status_code=400,
                content={"detail": str(e)},
            )

        context = base_context(request, auth, "New Template", "automation")
        context.update(
            automation_web_service.template_form_context(db, str(auth.organization_id))
        )
        context["error"] = str(e)
        context["form_data"] = data
        return templates.TemplateResponse(
            request, "admin/automation/template_form.html", context
        )


@router.post("/templates/{template_id}/edit")
async def update_template(
    request: Request,
    template_id: str,
    auth: WebAuthContext = Depends(require_automation_access),
    db: Session = Depends(get_db_for_org),
):
    """Handle document template update form submission."""
    content_type = request.headers.get("content-type", "")

    if "application/json" in content_type:
        data = await request.json()
    else:
        form_data = await request.form()
        data = dict(form_data)

    try:
        template = automation_web_service.update_template(
            db=db,
            template_id=UUID(template_id),
            user_id=auth.user_id,
            data=data,
            organization_id=auth.organization_id,
        )

        if "application/json" in content_type:
            return {"success": True, "template_id": str(template.template_id)}

        return RedirectResponse(
            url=f"/automation/templates/{template_id}?success=Template+updated",
            status_code=303,
        )

    except Exception as e:
        if "application/json" in content_type:
            return JSONResponse(
                status_code=400,
                content={"detail": str(e)},
            )

        context = base_context(request, auth, "Edit Template", "automation")
        context.update(
            automation_web_service.template_form_context(
                db, str(auth.organization_id), template_id
            )
        )
        context["error"] = str(e)
        context["form_data"] = data
        return templates.TemplateResponse(
            request, "admin/automation/template_form.html", context
        )


@router.post("/templates/{template_id}/delete")
def delete_template(
    request: Request,
    template_id: str,
    auth: WebAuthContext = Depends(require_automation_access),
    db: Session = Depends(get_db_for_org),
):
    """Delete a document template."""
    try:
        automation_web_service.delete_template(
            db, UUID(template_id), organization_id=auth.organization_id
        )
        return RedirectResponse(
            url="/automation/templates?success=Template+deleted",
            status_code=303,
        )
    except Exception as e:
        return RedirectResponse(
            url=f"/automation/templates/{template_id}?error={str(e)}",
            status_code=303,
        )
