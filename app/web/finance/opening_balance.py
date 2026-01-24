"""
Opening Balance Web Routes.

Provides web UI routes for opening balance import.
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.db import get_db_session


router = APIRouter(prefix="/opening-balance", tags=["Opening Balance"])
templates = Jinja2Templates(directory="templates")


@router.get("", response_class=HTMLResponse)
async def opening_balance_page(
    request: Request,
    db: Session = Depends(get_db_session),
):
    """
    Opening balance import page.

    Provides UI for:
    - Uploading opening balance CSV
    - Previewing data with account matching
    - Importing and creating journal entry
    """
    return templates.TemplateResponse(
        "finance/import_export/opening_balance.html",
        {
            "request": request,
            "title": "Opening Balance Import",
            "breadcrumbs": [
                {"label": "Import/Export", "url": "/import"},
                {"label": "Opening Balance", "url": None},
            ],
        },
    )
