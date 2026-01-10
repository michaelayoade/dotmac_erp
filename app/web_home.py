from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.services import person as person_service

templates = Jinja2Templates(directory="templates")

router = APIRouter()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _brand_mark(name: str) -> str:
    parts = [part for part in name.split() if part]
    if not parts:
        return "ST"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[1][0]).upper()


@router.get("/", tags=["web"], response_class=HTMLResponse)
def home(request: Request, db: Session = Depends(get_db)):
    people = person_service.people.list(
        db=db,
        email=None,
        status=None,
        is_active=None,
        order_by="created_at",
        order_dir="desc",
        limit=25,
        offset=0,
    )
    brand_name = settings.brand_name
    brand = {
        "name": brand_name,
        "tagline": settings.brand_tagline,
        "logo_url": settings.brand_logo_url,
        "mark": _brand_mark(brand_name),
    }
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "title": brand_name,
            "people": people,
            "brand": brand,
        },
    )
