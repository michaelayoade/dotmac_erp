"""
Opening Balance Web Routes.

Provides web UI routes for opening balance import.
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app.templates import templates
from app.web.deps import WebAuthContext, base_context, require_finance_admin

router = APIRouter(prefix="/opening-balance", tags=["Opening Balance"])


@router.get("", response_class=HTMLResponse)
async def opening_balance_page(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_admin),
):
    """
    Opening balance import page.

    Provides UI for:
    - Uploading opening balance CSV
    - Previewing data with account matching
    - Importing and creating journal entry
    """
    context = base_context(request, auth, "Opening Balance Import", "import")
    context.update(
        {
            "title": "Opening Balance Import",
            "breadcrumbs": [
                {"label": "Import/Export", "url": "/import"},
                {"label": "Opening Balance", "url": None},
            ],
        }
    )
    return templates.TemplateResponse(
        "finance/import_export/opening_balance.html",
        context,
    )
