from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.services.auth_dependencies import require_permission
from app.schemas.common import ListResponse
from app.schemas.settings import DomainSettingRead, DomainSettingUpdate
from app.schemas.finance.branding import (
    BrandingCreate,
    BrandingUpdate,
    BrandingResponse,
    BrandingPreview,
    ColorPaletteResponse,
    FontListResponse,
    FontOption,
)
from app.services import settings_api as settings_service
from app.services.finance.branding import (
    BrandingService,
    generate_color_palette,
    CSSGenerator,
    FONT_PRESETS,
)

router = APIRouter(prefix="/settings", tags=["settings"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/auth", response_model=ListResponse[DomainSettingRead], tags=["settings-auth"])
def list_auth_settings(
    is_active: bool | None = None,
    order_by: str = Query(default="created_at"),
    order_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    auth: dict = Depends(require_permission("settings:manage")),
    db: Session = Depends(get_db),
):
    return settings_service.list_auth_settings_response(
        db, is_active, order_by, order_dir, limit, offset
    )


@router.put(
    "/auth/{key}",
    response_model=DomainSettingRead,
    status_code=status.HTTP_200_OK,
    tags=["settings-auth"],
)
def upsert_auth_setting(
    key: str,
    payload: DomainSettingUpdate,
    auth: dict = Depends(require_permission("settings:manage")),
    db: Session = Depends(get_db),
):
    return settings_service.upsert_auth_setting(db, key, payload)


@router.get(
    "/auth/{key}",
    response_model=DomainSettingRead,
    tags=["settings-auth"],
)
def get_auth_setting(
    key: str,
    auth: dict = Depends(require_permission("settings:manage")),
    db: Session = Depends(get_db),
):
    return settings_service.get_auth_setting(db, key)


@router.get(
    "/audit",
    response_model=ListResponse[DomainSettingRead],
    tags=["settings-audit"],
)
def list_audit_settings(
    is_active: bool | None = None,
    order_by: str = Query(default="created_at"),
    order_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    auth: dict = Depends(require_permission("settings:manage")),
    db: Session = Depends(get_db),
):
    return settings_service.list_audit_settings_response(
        db, is_active, order_by, order_dir, limit, offset
    )


@router.put(
    "/audit/{key}",
    response_model=DomainSettingRead,
    status_code=status.HTTP_200_OK,
    tags=["settings-audit"],
)
def upsert_audit_setting(
    key: str,
    payload: DomainSettingUpdate,
    auth: dict = Depends(require_permission("settings:manage")),
    db: Session = Depends(get_db),
):
    return settings_service.upsert_audit_setting(db, key, payload)


@router.get(
    "/audit/{key}",
    response_model=DomainSettingRead,
    tags=["settings-audit"],
)
def get_audit_setting(
    key: str,
    auth: dict = Depends(require_permission("settings:manage")),
    db: Session = Depends(get_db),
):
    return settings_service.get_audit_setting(db, key)


@router.get(
    "/scheduler",
    response_model=ListResponse[DomainSettingRead],
    tags=["settings-scheduler"],
)
def list_scheduler_settings(
    is_active: bool | None = None,
    order_by: str = Query(default="created_at"),
    order_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    auth: dict = Depends(require_permission("settings:manage")),
    db: Session = Depends(get_db),
):
    return settings_service.list_scheduler_settings_response(
        db, is_active, order_by, order_dir, limit, offset
    )


@router.put(
    "/scheduler/{key}",
    response_model=DomainSettingRead,
    status_code=status.HTTP_200_OK,
    tags=["settings-scheduler"],
)
def upsert_scheduler_setting(
    key: str,
    payload: DomainSettingUpdate,
    auth: dict = Depends(require_permission("settings:manage")),
    db: Session = Depends(get_db),
):
    return settings_service.upsert_scheduler_setting(db, key, payload)


@router.get(
    "/scheduler/{key}",
    response_model=DomainSettingRead,
    tags=["settings-scheduler"],
)
def get_scheduler_setting(
    key: str,
    auth: dict = Depends(require_permission("settings:manage")),
    db: Session = Depends(get_db),
):
    return settings_service.get_scheduler_setting(db, key)


@router.get(
    "/email",
    response_model=ListResponse[DomainSettingRead],
    tags=["settings-email"],
)
def list_email_settings(
    is_active: bool | None = None,
    order_by: str = Query(default="created_at"),
    order_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    auth: dict = Depends(require_permission("settings:manage")),
    db: Session = Depends(get_db),
):
    return settings_service.list_email_settings_response(
        db, is_active, order_by, order_dir, limit, offset
    )


@router.put(
    "/email/{key}",
    response_model=DomainSettingRead,
    status_code=status.HTTP_200_OK,
    tags=["settings-email"],
)
def upsert_email_setting(
    key: str,
    payload: DomainSettingUpdate,
    auth: dict = Depends(require_permission("settings:manage")),
    db: Session = Depends(get_db),
):
    return settings_service.upsert_email_setting(db, key, payload)


@router.get(
    "/email/{key}",
    response_model=DomainSettingRead,
    tags=["settings-email"],
)
def get_email_setting(
    key: str,
    auth: dict = Depends(require_permission("settings:manage")),
    db: Session = Depends(get_db),
):
    return settings_service.get_email_setting(db, key)


@router.get(
    "/features",
    response_model=ListResponse[DomainSettingRead],
    tags=["settings-features"],
)
def list_features_settings(
    is_active: bool | None = None,
    order_by: str = Query(default="created_at"),
    order_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    auth: dict = Depends(require_permission("settings:manage")),
    db: Session = Depends(get_db),
):
    return settings_service.list_features_settings_response(
        db, is_active, order_by, order_dir, limit, offset
    )


@router.put(
    "/features/{key}",
    response_model=DomainSettingRead,
    status_code=status.HTTP_200_OK,
    tags=["settings-features"],
)
def upsert_features_setting(
    key: str,
    payload: DomainSettingUpdate,
    auth: dict = Depends(require_permission("settings:manage")),
    db: Session = Depends(get_db),
):
    return settings_service.upsert_features_setting(db, key, payload)


@router.get(
    "/features/{key}",
    response_model=DomainSettingRead,
    tags=["settings-features"],
)
def get_features_setting(
    key: str,
    auth: dict = Depends(require_permission("settings:manage")),
    db: Session = Depends(get_db),
):
    return settings_service.get_features_setting(db, key)


@router.get(
    "/automation",
    response_model=ListResponse[DomainSettingRead],
    tags=["settings-automation"],
)
def list_automation_settings(
    is_active: bool | None = None,
    order_by: str = Query(default="created_at"),
    order_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    auth: dict = Depends(require_permission("settings:manage")),
    db: Session = Depends(get_db),
):
    return settings_service.list_automation_settings_response(
        db, is_active, order_by, order_dir, limit, offset
    )


@router.put(
    "/automation/{key}",
    response_model=DomainSettingRead,
    status_code=status.HTTP_200_OK,
    tags=["settings-automation"],
)
def upsert_automation_setting(
    key: str,
    payload: DomainSettingUpdate,
    auth: dict = Depends(require_permission("settings:manage")),
    db: Session = Depends(get_db),
):
    return settings_service.upsert_automation_setting(db, key, payload)


@router.get(
    "/automation/{key}",
    response_model=DomainSettingRead,
    tags=["settings-automation"],
)
def get_automation_setting(
    key: str,
    auth: dict = Depends(require_permission("settings:manage")),
    db: Session = Depends(get_db),
):
    return settings_service.get_automation_setting(db, key)


@router.get(
    "/reporting",
    response_model=ListResponse[DomainSettingRead],
    tags=["settings-reporting"],
)
def list_reporting_settings(
    is_active: bool | None = None,
    order_by: str = Query(default="created_at"),
    order_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    auth: dict = Depends(require_permission("settings:manage")),
    db: Session = Depends(get_db),
):
    return settings_service.list_reporting_settings_response(
        db, is_active, order_by, order_dir, limit, offset
    )


@router.put(
    "/reporting/{key}",
    response_model=DomainSettingRead,
    status_code=status.HTTP_200_OK,
    tags=["settings-reporting"],
)
def upsert_reporting_setting(
    key: str,
    payload: DomainSettingUpdate,
    auth: dict = Depends(require_permission("settings:manage")),
    db: Session = Depends(get_db),
):
    return settings_service.upsert_reporting_setting(db, key, payload)


@router.get(
    "/reporting/{key}",
    response_model=DomainSettingRead,
    tags=["settings-reporting"],
)
def get_reporting_setting(
    key: str,
    auth: dict = Depends(require_permission("settings:manage")),
    db: Session = Depends(get_db),
):
    return settings_service.get_reporting_setting(db, key)


# ─────────────────────────────────────────────────────────────────────────────
# Branding Endpoints
# ─────────────────────────────────────────────────────────────────────────────


@router.get(
    "/branding/org/{org_id}",
    response_model=BrandingResponse,
    tags=["settings-branding"],
    summary="Get branding for organization",
)
def get_branding(
    org_id: UUID,
    auth: dict = Depends(require_permission("settings:manage")),
    db: Session = Depends(get_db),
):
    """Get branding configuration for an organization."""
    service = BrandingService(db)
    branding = service.get_by_org_id(org_id)
    if not branding:
        raise HTTPException(status_code=404, detail="Branding not found")
    return branding


@router.get(
    "/branding/org/{org_id}/or-create",
    response_model=BrandingResponse,
    tags=["settings-branding"],
    summary="Get or create branding for organization",
)
def get_or_create_branding(
    org_id: UUID,
    auth: dict = Depends(require_permission("settings:manage")),
    db: Session = Depends(get_db),
):
    """Get existing branding or create with defaults."""
    service = BrandingService(db)
    user_id = auth.get("user_id") if auth else None
    branding = service.get_or_create(org_id, user_id)
    db.commit()
    return branding


@router.post(
    "/branding",
    response_model=BrandingResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["settings-branding"],
    summary="Create branding configuration",
)
def create_branding(
    payload: BrandingCreate,
    auth: dict = Depends(require_permission("settings:manage")),
    db: Session = Depends(get_db),
):
    """Create new branding configuration for an organization."""
    service = BrandingService(db)
    user_id = auth.get("user_id") if auth else None
    try:
        branding = service.create(payload, user_id)
        db.commit()
        return branding
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put(
    "/branding/{branding_id}",
    response_model=BrandingResponse,
    tags=["settings-branding"],
    summary="Update branding configuration",
)
def update_branding(
    branding_id: UUID,
    payload: BrandingUpdate,
    auth: dict = Depends(require_permission("settings:manage")),
    db: Session = Depends(get_db),
):
    """Update branding configuration."""
    service = BrandingService(db)
    branding = service.update(branding_id, payload)
    if not branding:
        raise HTTPException(status_code=404, detail="Branding not found")
    db.commit()
    return branding


@router.delete(
    "/branding/{branding_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["settings-branding"],
    summary="Delete branding configuration",
)
def delete_branding(
    branding_id: UUID,
    auth: dict = Depends(require_permission("settings:manage")),
    db: Session = Depends(get_db),
):
    """Delete branding configuration."""
    service = BrandingService(db)
    if not service.delete(branding_id):
        raise HTTPException(status_code=404, detail="Branding not found")
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/branding/org/{org_id}/css",
    tags=["settings-branding"],
    summary="Get generated CSS for organization branding",
)
def get_branding_css(
    org_id: UUID,
    db: Session = Depends(get_db),
):
    """
    Get generated CSS for organization branding.

    This endpoint returns CSS that can be included in the page to apply
    the organization's branding. No authentication required for CSS serving.
    """
    service = BrandingService(db)
    css = service.generate_css(org_id)
    return Response(content=css, media_type="text/css")


@router.get(
    "/branding/org/{org_id}/fonts-url",
    tags=["settings-branding"],
    summary="Get Google Fonts URL for custom fonts",
)
def get_branding_fonts_url(
    org_id: UUID,
    db: Session = Depends(get_db),
):
    """Get Google Fonts import URL for organization's custom fonts."""
    service = BrandingService(db)
    url = service.get_fonts_url(org_id)
    return {"url": url}


@router.post(
    "/branding/preview-css",
    tags=["settings-branding"],
    summary="Generate preview CSS from branding options",
)
def preview_branding_css(
    payload: BrandingPreview,
    auth: dict = Depends(require_permission("settings:manage")),
):
    """
    Generate CSS from branding options without saving.

    Used for live preview in the branding settings UI.
    """
    # Create a temporary branding object for CSS generation
    from app.models.finance.core_org import OrganizationBranding

    temp_branding = OrganizationBranding(
        primary_color=payload.primary_color,
        accent_color=payload.accent_color,
        font_family_display=payload.font_family_display,
        font_family_body=payload.font_family_body,
        border_radius=payload.border_radius,
        button_style=payload.button_style,
        sidebar_style=payload.sidebar_style,
    )

    css = CSSGenerator(temp_branding).generate()
    fonts_url = CSSGenerator(temp_branding).get_google_fonts_url()

    return {"css": css, "fonts_url": fonts_url}


@router.get(
    "/branding/colors/palette/{hex_color}",
    response_model=ColorPaletteResponse,
    tags=["settings-branding"],
    summary="Generate color palette from base color",
)
def get_color_palette(
    hex_color: str,
    auth: dict = Depends(require_permission("settings:manage")),
):
    """
    Generate a full color palette from a base color.

    Returns shades from 50 (lightest) to 950 (darkest).
    """
    # Ensure proper format
    if not hex_color.startswith("#"):
        hex_color = f"#{hex_color}"

    try:
        return generate_color_palette(hex_color)
    except (ValueError, IndexError):
        raise HTTPException(status_code=400, detail="Invalid hex color format")


@router.get(
    "/branding/fonts",
    response_model=FontListResponse,
    tags=["settings-branding"],
    summary="List available font options",
)
def list_fonts(
    category: Optional[str] = Query(
        None, description="Filter by category: sans-serif, serif, monospace"
    ),
    auth: dict = Depends(require_permission("settings:manage")),
):
    """List available font options for branding."""
    fonts = FONT_PRESETS
    if category:
        fonts = [f for f in fonts if f["category"] == category]

    return FontListResponse(
        fonts=[FontOption(**f) for f in fonts]
    )
