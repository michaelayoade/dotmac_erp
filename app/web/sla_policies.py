"""Authenticated, read-only SLA policy pages."""

import re
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.services.sla_policies_web import (
    SLAPolicyDocumentNotFoundError,
    SLAPolicyReadService,
)
from app.templates import templates
from app.web.deps import WebAuthContext, base_context, get_db, require_web_auth

router = APIRouter(prefix="/sla-policies", tags=["sla-policies-web"])

_UNSAFE_FILENAME_RE = re.compile(r'[\x00-\x1f\x7f"\\]')


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


@router.get(
    "/{article_id}/document",
    response_class=StreamingResponse,
    name="sla_policy_document_view",
)
def sla_policy_document_view(
    request: Request,
    article_id: UUID,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Render one published SLA policy document inline for its organization."""
    try:
        document = SLAPolicyReadService(db).get_published_document_for_org(
            auth.organization_id,
            article_id,
        )
    except SLAPolicyDocumentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    safe_name = _UNSAFE_FILENAME_RE.sub("_", document.file_name)
    ascii_name = safe_name.encode("ascii", "replace").decode("ascii")
    headers = {
        "Content-Disposition": (
            f"inline; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(safe_name)}"
        )
    }
    if document.content_length is not None:
        headers["Content-Length"] = str(document.content_length)

    # The security middleware recognizes this only together with this route's
    # exact name. No other response can opt into framing with the marker alone.
    request.state.allow_sla_document_frame = True
    return StreamingResponse(
        document.chunks,
        media_type=document.content_type,
        headers=headers,
    )
