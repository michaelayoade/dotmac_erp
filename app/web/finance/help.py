"""
Finance help and support pages.
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.templates import templates
from app.web.deps import WebAuthContext, base_context, get_db, require_finance_access

router = APIRouter(prefix="/help", tags=["finance-help-web"])


@router.get("", response_class=HTMLResponse)
def help_page(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Help and support landing page."""
    context = base_context(request, auth, "Help & Support", "help", db=db)
    context["organization_id"] = auth.organization_id
    return templates.TemplateResponse(request, "finance/help.html", context)
