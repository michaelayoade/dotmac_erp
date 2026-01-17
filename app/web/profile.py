"""
Web routes for user profile pages.

Provides profile view and edit functionality for authenticated users.
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.services.profile_web import profile_web_service
from app.web.deps import get_db, require_web_auth, WebAuthContext


router = APIRouter(tags=["web-profile"])


@router.get("/account/two-factor", response_class=HTMLResponse)
def two_factor_page(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """
    Display the two-factor authentication setup page.
    """
    return profile_web_service.two_factor_response(request, auth, db)


@router.get("/account/sessions", response_class=HTMLResponse)
def sessions_page(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """
    Display the sessions management page.
    """
    return profile_web_service.sessions_response(request, auth, db)


@router.get("/change-password", response_class=HTMLResponse)
def change_password_page(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """
    Display the change password page.
    """
    return profile_web_service.change_password_response(request, auth, db)


@router.get("/profile", response_class=HTMLResponse)
def profile_page(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """
    Display the user profile page.
    """
    return profile_web_service.profile_response(request, auth, db)
