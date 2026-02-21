"""
Automation Web Routes.

HTML template routes for Recurring Transactions, Workflow Rules,
Custom Fields, and Document Templates.
"""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.services.finance.automation.custom_fields import custom_fields_service
from app.services.finance.automation.recurring import recurring_service
from app.services.finance.automation.web import automation_web_service
from app.services.finance.automation.workflow import workflow_service
from app.templates import templates
from app.web.deps import WebAuthContext, base_context, get_db, require_finance_access

router = APIRouter(prefix="/automation", tags=["automation-web"])


# =============================================================================
# Automation Dashboard
# =============================================================================


@router.get("", response_class=HTMLResponse)
def automation_dashboard(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Automation dashboard page."""
    context = base_context(request, auth, "Automation", "automation")

    # Get counts for overview
    recurring_ctx = automation_web_service.list_recurring_context(
        db, str(auth.organization_id), page_size=5
    )
    workflows_ctx = automation_web_service.list_workflows_context(
        db, str(auth.organization_id)
    )
    fields_ctx = automation_web_service.list_custom_fields_context(
        db, str(auth.organization_id)
    )
    templates_ctx = automation_web_service.list_templates_context(
        db, str(auth.organization_id)
    )

    context.update(
        {
            "recurring_count": recurring_ctx["total"],
            "active_recurring": len(
                [t for t in recurring_ctx["templates"] if t["is_active"]]
            ),
            "workflow_count": workflows_ctx["total"],
            "active_workflows": len(
                [r for r in workflows_ctx["rules"] if r["is_active"]]
            ),
            "custom_fields_count": fields_ctx["total"],
            "templates_count": templates_ctx["total"],
            "recent_recurring": recurring_ctx["templates"][:5],
            "recent_workflows": workflows_ctx["rules"][:5],
        }
    )

    return templates.TemplateResponse(
        request, "finance/automation/dashboard.html", context
    )


# =============================================================================
# Recurring Transactions
# =============================================================================


@router.get("/recurring", response_class=HTMLResponse)
def list_recurring(
    request: Request,
    entity_type: str | None = None,
    status: str | None = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
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
        request, "finance/automation/recurring_list.html", context
    )


@router.get("/recurring/new", response_class=HTMLResponse)
def new_recurring_form(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """New recurring template form page."""
    context = base_context(request, auth, "New Recurring Template", "automation")
    context["form_data"] = {}
    context.update(
        automation_web_service.recurring_form_context(db, str(auth.organization_id))
    )
    return templates.TemplateResponse(
        request, "finance/automation/recurring_form.html", context
    )


@router.get("/recurring/{template_id}", response_class=HTMLResponse)
def view_recurring(
    request: Request,
    template_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
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
        request, "finance/automation/recurring_detail.html", context
    )


@router.get("/recurring/{template_id}/edit", response_class=HTMLResponse)
def edit_recurring_form(
    request: Request,
    template_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Edit recurring template form page."""
    context = base_context(request, auth, "Edit Recurring Template", "automation")
    context.update(
        automation_web_service.recurring_form_context(
            db, str(auth.organization_id), template_id
        )
    )
    return templates.TemplateResponse(
        request, "finance/automation/recurring_form.html", context
    )


@router.post("/recurring/new")
async def create_recurring(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Handle recurring template form submission."""
    content_type = request.headers.get("content-type", "")

    if "application/json" in content_type:
        data = await request.json()
    else:
        form_data = await request.form()
        data = dict(form_data)

    try:
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
            automation_web_service.recurring_form_context(db, str(auth.organization_id))
        )
        context["error"] = str(e)
        context["form_data"] = data
        return templates.TemplateResponse(
            request, "finance/automation/recurring_form.html", context
        )


@router.post("/recurring/{template_id}/edit")
async def update_recurring(
    request: Request,
    template_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
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
            request, "finance/automation/recurring_form.html", context
        )


@router.post("/recurring/{template_id}/pause")
def pause_recurring(
    request: Request,
    template_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
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
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
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
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
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
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
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
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
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
        request, "finance/automation/workflow_list.html", context
    )


@router.get("/workflows/new", response_class=HTMLResponse)
def new_workflow_form(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """New workflow rule form page."""
    context = base_context(request, auth, "New Workflow Rule", "automation")
    context["form_data"] = {}
    context.update(
        automation_web_service.workflow_form_context(db, str(auth.organization_id))
    )
    return templates.TemplateResponse(
        request, "finance/automation/workflow_form.html", context
    )


@router.get("/workflows/monitoring", response_class=HTMLResponse)
def workflow_monitoring(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Workflow execution monitoring dashboard."""
    context = base_context(request, auth, "Workflow Monitoring", "automation")
    context.update(
        automation_web_service.workflow_monitoring_context(
            db, str(auth.organization_id)
        )
    )
    return templates.TemplateResponse(
        request, "finance/automation/workflow_monitoring.html", context
    )


@router.get("/workflows/{rule_id}", response_class=HTMLResponse)
def view_workflow(
    request: Request,
    rule_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
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
        request, "finance/automation/workflow_detail.html", context
    )


@router.get("/workflows/{rule_id}/edit", response_class=HTMLResponse)
def edit_workflow_form(
    request: Request,
    rule_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Edit workflow rule form page."""
    context = base_context(request, auth, "Edit Workflow Rule", "automation")
    context.update(
        automation_web_service.workflow_form_context(
            db, str(auth.organization_id), rule_id
        )
    )
    return templates.TemplateResponse(
        request, "finance/automation/workflow_form.html", context
    )


@router.post("/workflows/new")
async def create_workflow(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
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
            request, "finance/automation/workflow_form.html", context
        )


@router.post("/workflows/{rule_id}/edit")
async def update_workflow(
    request: Request,
    rule_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
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
            request, "finance/automation/workflow_form.html", context
        )


@router.post("/workflows/{rule_id}/toggle")
def toggle_workflow(
    request: Request,
    rule_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Toggle workflow rule active status."""
    try:
        rule = workflow_service.get(db, UUID(rule_id))
        if not rule:
            return RedirectResponse(
                url="/automation/workflows?error=Rule+not+found",
                status_code=303,
            )

        workflow_service.update_rule(
            db=db,
            rule_id=UUID(rule_id),
            updates={"is_active": not rule.is_active},
            updated_by=auth.user_id,
        )

        status = "activated" if not rule.is_active else "deactivated"
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
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Workflow rule version history page."""
    context = base_context(request, auth, "Version History", "automation")
    context.update(
        automation_web_service.workflow_versions_context(
            db, str(auth.organization_id), rule_id
        )
    )
    return templates.TemplateResponse(
        request, "finance/automation/workflow_versions.html", context
    )


@router.post("/workflows/{rule_id}/test")
async def test_workflow(
    request: Request,
    rule_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Dry-run test a workflow rule against sample data."""
    content_type = request.headers.get("content-type", "")

    if "application/json" in content_type:
        data = await request.json()
    else:
        form_data = await request.form()
        data = dict(form_data)

    try:
        result = workflow_service.dry_run(db, UUID(rule_id), data)
        return JSONResponse(content=result)
    except Exception as e:
        return JSONResponse(
            status_code=400,
            content={"detail": str(e)},
        )


@router.post("/workflows/{rule_id}/delete")
def delete_workflow(
    request: Request,
    rule_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Delete a workflow rule."""
    try:
        workflow_service.delete(db, UUID(rule_id))
        return RedirectResponse(
            url="/automation/workflows?success=Rule+deleted",
            status_code=303,
        )
    except Exception as e:
        return RedirectResponse(
            url=f"/automation/workflows/{rule_id}?error={str(e)}",
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
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
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
        request, "finance/automation/fields_list.html", context
    )


@router.get("/fields/new", response_class=HTMLResponse)
def new_custom_field_form(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """New custom field form page."""
    context = base_context(request, auth, "New Custom Field", "automation")
    context["form_data"] = {}
    context.update(
        automation_web_service.custom_field_form_context(db, str(auth.organization_id))
    )
    return templates.TemplateResponse(
        request, "finance/automation/field_form.html", context
    )


@router.get("/fields/{field_id}", response_class=HTMLResponse)
def view_custom_field(
    request: Request,
    field_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
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
        request, "finance/automation/field_detail.html", context
    )


@router.get("/fields/{field_id}/edit", response_class=HTMLResponse)
def edit_custom_field_form(
    request: Request,
    field_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Edit custom field form page."""
    context = base_context(request, auth, "Edit Custom Field", "automation")
    context.update(
        automation_web_service.custom_field_form_context(
            db, str(auth.organization_id), field_id
        )
    )
    return templates.TemplateResponse(
        request, "finance/automation/field_form.html", context
    )


@router.post("/fields/new")
async def create_custom_field(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
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
            request, "finance/automation/field_form.html", context
        )


@router.post("/fields/{field_id}/edit")
async def update_custom_field(
    request: Request,
    field_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
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
            request, "finance/automation/field_form.html", context
        )


@router.post("/fields/{field_id}/delete")
def delete_custom_field(
    request: Request,
    field_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Delete (deactivate) a custom field."""
    try:
        custom_fields_service.delete(db, UUID(field_id))
        return RedirectResponse(
            url="/automation/fields?success=Field+deleted",
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
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
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
        request, "finance/automation/templates_list.html", context
    )


@router.get("/templates/new", response_class=HTMLResponse)
def new_template_form(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """New document template form page."""
    context = base_context(request, auth, "New Template", "automation")
    context["form_data"] = {}
    context.update(
        automation_web_service.template_form_context(db, str(auth.organization_id))
    )
    return templates.TemplateResponse(
        request, "finance/automation/template_form.html", context
    )


@router.get("/templates/{template_id}", response_class=HTMLResponse)
def view_template(
    request: Request,
    template_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
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
        request, "finance/automation/template_detail.html", context
    )


@router.get("/templates/{template_id}/edit", response_class=HTMLResponse)
def edit_template_form(
    request: Request,
    template_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Edit document template form page."""
    context = base_context(request, auth, "Edit Template", "automation")
    context.update(
        automation_web_service.template_form_context(
            db, str(auth.organization_id), template_id
        )
    )
    return templates.TemplateResponse(
        request, "finance/automation/template_form.html", context
    )


@router.post("/templates/new")
async def create_template(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
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
            request, "finance/automation/template_form.html", context
        )


@router.post("/templates/{template_id}/edit")
async def update_template(
    request: Request,
    template_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
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
            request, "finance/automation/template_form.html", context
        )


@router.post("/templates/{template_id}/delete")
def delete_template(
    request: Request,
    template_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Delete a document template."""
    try:
        automation_web_service.delete_template(db, UUID(template_id))
        return RedirectResponse(
            url="/automation/templates?success=Template+deleted",
            status_code=303,
        )
    except Exception as e:
        return RedirectResponse(
            url=f"/automation/templates/{template_id}?error={str(e)}",
            status_code=303,
        )
