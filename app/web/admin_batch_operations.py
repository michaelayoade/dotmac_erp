"""Batch operations admin web routes.

Thin wrappers around BatchOperationsWebService — the read side of the
batch-run record. No logic here; see `app/services/admin/batch_operations_web.py`.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.services.admin.batch_operations_web import batch_operations_web_service
from app.web.deps import WebAuthContext, get_db, optional_web_auth

router = APIRouter(
    prefix="/admin/batch-operations", tags=["admin-batch-operations-web"]
)


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def list_operations(
    request: Request,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    return batch_operations_web_service.list_response(request, db, auth)


@router.get("/{operation_id}", response_class=HTMLResponse)
def operation_detail(
    operation_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    return batch_operations_web_service.detail_response(request, db, auth, operation_id)
