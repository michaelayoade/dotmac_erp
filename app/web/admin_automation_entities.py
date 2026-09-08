"""Admin controls for tenant automation entity availability."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.services.finance.automation.capabilities import capabilities_payload
from app.services.finance.automation.entity_configuration import (
    entity_configuration_service,
)
from app.templates import templates
from app.web.deps import (
    WebAuthContext,
    base_context,
    get_db_for_org,
    require_automation_access,
)

router = APIRouter(prefix="/automation/entities", tags=["admin-automation-entities"])


@router.get("", response_class=HTMLResponse)
def automation_entities(
    request: Request,
    auth: WebAuthContext = Depends(require_automation_access),
    db: Session = Depends(get_db_for_org),
):
    """Configure which registered entity connectors are available to the tenant."""
    context = base_context(request, auth, "Automation Entities", "automation")
    context["entity_groups"] = entity_configuration_service.grouped_catalog(
        db,
        auth.organization_id,
    )
    return templates.TemplateResponse(
        request,
        "admin/automation/entity_configuration.html",
        context,
    )


@router.get("/capabilities")
def configured_automation_capabilities(
    auth: WebAuthContext = Depends(require_automation_access),
    db: Session = Depends(get_db_for_org),
):
    """Return the code-backed capabilities enabled for the current tenant."""
    return {
        "entities": capabilities_payload(
            db,
            auth.organization_id,
        )
    }


@router.post("/{entity_type}/enable")
def enable_automation_entity(
    entity_type: str,
    auth: WebAuthContext = Depends(require_automation_access),
    db: Session = Depends(get_db_for_org),
):
    """Enable a code-registered entity for this organization."""
    entity_configuration_service.set_enabled(
        db,
        auth.organization_id,
        entity_type,
        True,
        auth.user_id,
    )
    return RedirectResponse(
        url="/admin/automation/entities?success=Entity+enabled",
        status_code=303,
    )


@router.post("/{entity_type}/disable")
def disable_automation_entity(
    entity_type: str,
    auth: WebAuthContext = Depends(require_automation_access),
    db: Session = Depends(get_db_for_org),
):
    """Pause a registered entity while retaining its rules and field data."""
    entity_configuration_service.set_enabled(
        db,
        auth.organization_id,
        entity_type,
        False,
        auth.user_id,
    )
    return RedirectResponse(
        url="/admin/automation/entities?success=Entity+disabled",
        status_code=303,
    )
