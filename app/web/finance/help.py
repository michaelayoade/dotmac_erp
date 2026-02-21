"""
Finance help and support pages.
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from app.web.deps import WebAuthContext, require_finance_access

router = APIRouter(prefix="/help", tags=["finance-help-web"])


@router.get("")
def help_page(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
):
    """Legacy finance help path; redirects to unified app help center."""
    return RedirectResponse(url="/help", status_code=302)
