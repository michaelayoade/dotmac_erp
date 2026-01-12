from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app.templates import templates
from app.web.deps import optional_web_auth, WebAuthContext, brand_context, landing_content

router = APIRouter()


@router.get("/", tags=["web"], response_class=HTMLResponse)
def home(
    request: Request,
    auth: WebAuthContext = Depends(optional_web_auth),
):
    brand = brand_context()
    content = landing_content()
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "title": brand["name"],
            "brand": brand,
            "content": content,
            "user": auth.user,
        },
    )
