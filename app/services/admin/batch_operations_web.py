"""Batch operations admin UI — the screen that makes batch runs visible.

`BatchOperation` shipped in January 2026 with a model, a table and a
migration, and was never read or written by anything. This is the read side:
who ran what, when, against which organization, what it touched, and whether
it worked.

Every query is scoped to `auth.organization_id`. A run belonging to another
tenant is not filtered out of the view — it is never selected, so a missing
scope produces an error rather than a cross-tenant listing.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from urllib.parse import urlencode

from app.models.batch_operation import BatchOperationStatus
from app.services.batch_operation import batch_operation_service
from app.templates import templates
from app.web.deps import WebAuthContext, brand_context, org_brand_context

logger = logging.getLogger(__name__)

# Status → the badge colour the admin macros understand.
_STATUS_TONE = {
    BatchOperationStatus.RUNNING: "blue",
    BatchOperationStatus.COMPLETED: "emerald",
    BatchOperationStatus.FAILED: "rose",
    BatchOperationStatus.ROLLED_BACK: "amber",
}


class BatchOperationsWebService:
    """Read-only admin views over batch-run records."""

    def _base_context(
        self,
        request: Request,
        auth: WebAuthContext | None,
        title: str,
        db: Session | None = None,
    ) -> dict[str, Any]:
        org_branding = None
        if db and auth and auth.organization_id:
            org_branding = org_brand_context(db, auth.organization_id)
        return {
            "request": request,
            "auth": auth,
            "title": title,
            "page_title": title,
            "brand": org_branding or brand_context(),
            "org_branding": org_branding,
            "user": auth.user if auth else {"name": "Admin", "initials": "AD"},
            "csrf_token": getattr(request.state, "csrf_token", ""),
            "active_page": "batch-operations",
            "module": "admin",
            "sub_module": "batch-operations",
            "status_tone": _STATUS_TONE,
        }

    def _require_admin(
        self, request: Request, auth: WebAuthContext | None
    ) -> RedirectResponse | None:
        if not auth or not auth.is_authenticated:
            next_path = request.url.path
            if request.url.query:
                next_path = f"{next_path}?{request.url.query}"
            return RedirectResponse(
                url=f"/admin/login?{urlencode({'next': next_path})}",
                status_code=302,
            )
        if not auth.is_admin:
            raise HTTPException(status_code=403, detail="Admin access required")
        return None

    def list_response(
        self, request: Request, db: Session, auth: WebAuthContext | None
    ) -> HTMLResponse | RedirectResponse:
        guard = self._require_admin(request, auth)
        if guard is not None:
            return guard

        runs = batch_operation_service.recent(
            db, organization_id=auth.organization_id, limit=100
        )
        context = self._base_context(request, auth, "Batch Operations", db)
        context["runs"] = runs
        context["counts"] = {
            "total": len(runs),
            "running": sum(1 for r in runs if r.status is BatchOperationStatus.RUNNING),
            "failed": sum(1 for r in runs if r.status is BatchOperationStatus.FAILED),
        }
        return templates.TemplateResponse("admin/batch_operations/list.html", context)

    def detail_response(
        self,
        request: Request,
        db: Session,
        auth: WebAuthContext | None,
        operation_id: uuid.UUID,
    ) -> HTMLResponse | RedirectResponse:
        guard = self._require_admin(request, auth)
        if guard is not None:
            return guard

        run = batch_operation_service.get(
            db, organization_id=auth.organization_id, operation_id=operation_id
        )
        if run is None:
            # Scoped lookup: another tenant's run is indistinguishable from a
            # nonexistent one, which is the intended answer.
            raise HTTPException(status_code=404, detail="Batch operation not found")

        context = self._base_context(request, auth, run.operation_name, db)
        context["run"] = run
        return templates.TemplateResponse("admin/batch_operations/detail.html", context)


batch_operations_web_service = BatchOperationsWebService()
