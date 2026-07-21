"""Authenticated, read-only SLA policy pages."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.services.sla_policies_web import SLAPolicyReadService
from app.templates import templates
from app.web.deps import WebAuthContext, base_context, get_db, require_web_auth

router = APIRouter(prefix="/sla-policies", tags=["sla-policies-web"])


@router.get("", response_class=HTMLResponse)
def sla_policies_page(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Display published SLA policies to any authenticated user."""
    policies = SLAPolicyReadService(db).list_published_for_org(auth.organization_id)
    context = base_context(request, auth, "SLA Policies", "sla_policies", db=db)
    context["policies"] = policies
    return templates.TemplateResponse(request, "sla_policies/index.html", context)
