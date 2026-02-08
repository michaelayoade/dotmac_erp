from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.domain_settings import SettingDomain
from app.schemas.common import ListResponse
from app.schemas.finance.branding import (
    BrandingCreate,
    BrandingPreview,
    BrandingResponse,
    BrandingUpdate,
    ColorPaletteResponse,
    FontListResponse,
    FontOption,
)
from app.schemas.settings import (
    DomainSettingRead,
    DomainSettingUpdate,
    RestoreSettingRequest,
    SettingHistoryListResponse,
    SettingHistoryRead,
    SettingsExportRequest,
    SettingsExportResponse,
    SettingsImportRequest,
    SettingsImportResponse,
    SettingsImportResultItem,
)
from app.services import settings_api as settings_service
from app.services.auth_dependencies import require_permission
from app.services.domain_settings import (
    get_history_entry,
    list_setting_history,
    restore_from_history,
)
from app.services.finance.branding import (
    FONT_PRESETS,
    BrandingService,
    CSSGenerator,
    generate_color_palette,
)

router = APIRouter(prefix="/settings", tags=["settings"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get(
    "/auth", response_model=ListResponse[DomainSettingRead], tags=["settings-auth"]
)
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
# Payments Settings Endpoints
# ─────────────────────────────────────────────────────────────────────────────


@router.get(
    "/payments",
    response_model=ListResponse[DomainSettingRead],
    tags=["settings-payments"],
)
def list_payments_settings(
    is_active: bool | None = None,
    order_by: str = Query(default="created_at"),
    order_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    auth: dict = Depends(require_permission("settings:manage")),
    db: Session = Depends(get_db),
):
    return settings_service.list_payments_settings_response(
        db, is_active, order_by, order_dir, limit, offset
    )


@router.put(
    "/payments/{key}",
    response_model=DomainSettingRead,
    status_code=status.HTTP_200_OK,
    tags=["settings-payments"],
)
def upsert_payments_setting(
    key: str,
    payload: DomainSettingUpdate,
    auth: dict = Depends(require_permission("settings:manage")),
    db: Session = Depends(get_db),
):
    return settings_service.upsert_payments_setting(db, key, payload)


@router.get(
    "/payments/{key}",
    response_model=DomainSettingRead,
    tags=["settings-payments"],
)
def get_payments_setting(
    key: str,
    auth: dict = Depends(require_permission("settings:manage")),
    db: Session = Depends(get_db),
):
    return settings_service.get_payments_setting(db, key)


# ─────────────────────────────────────────────────────────────────────────────
# Settings Export/Import Endpoints
# ─────────────────────────────────────────────────────────────────────────────


@router.post(
    "/export",
    response_model=SettingsExportResponse,
    tags=["settings-export-import"],
    summary="Export settings to JSON",
)
def export_settings(
    payload: SettingsExportRequest,
    auth: dict = Depends(require_permission("settings:manage")),
    db: Session = Depends(get_db),
):
    """
    Export settings to a portable JSON format.

    Use this to backup settings or migrate between environments.

    **Security Note**: By default, secret values are masked. Set
    `include_secrets=True` only when needed and handle the export securely.
    """
    return settings_service.export_settings(
        db,
        domains=payload.domains,
        include_secrets=payload.include_secrets,
    )


@router.post(
    "/import",
    response_model=SettingsImportResponse,
    tags=["settings-export-import"],
    summary="Import settings from JSON",
)
def import_settings(
    payload: SettingsImportRequest,
    auth: dict = Depends(require_permission("settings:manage")),
    db: Session = Depends(get_db),
):
    """
    Import settings from an exported JSON.

    Use `dry_run=True` to validate the import without making changes.

    **Security Note**: By default, secret values are skipped during import.
    Set `skip_secrets=False` only when importing from a trusted source.
    """
    result = settings_service.import_settings(
        db,
        data=payload.data,
        domains=payload.domains,
        skip_secrets=payload.skip_secrets,
        dry_run=payload.dry_run,
    )

    # Convert to response model
    return SettingsImportResponse(
        imported=[SettingsImportResultItem(**item) for item in result["imported"]],
        skipped=[SettingsImportResultItem(**item) for item in result["skipped"]],
        errors=[SettingsImportResultItem(**item) for item in result["errors"]],
    )


# ─────────────────────────────────────────────────────────────────────────────
# Settings History Endpoints
# ─────────────────────────────────────────────────────────────────────────────


@router.get(
    "/history",
    response_model=SettingHistoryListResponse,
    tags=["settings-history"],
    summary="List settings change history",
)
def list_history(
    domain: SettingDomain | None = Query(default=None, description="Filter by domain"),
    key: str | None = Query(
        default=None, description="Filter by key (requires domain)"
    ),
    setting_id: UUID | None = Query(default=None, description="Filter by setting ID"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    auth: dict = Depends(require_permission("settings:manage")),
    db: Session = Depends(get_db),
):
    """
    List history of settings changes for audit and rollback.

    Use filters to narrow down:
    - `domain`: Show history for all settings in a domain
    - `domain` + `key`: Show history for a specific setting
    - `setting_id`: Show history for a specific setting by ID
    """
    items, total = list_setting_history(
        db,
        domain=domain,
        key=key,
        setting_id=str(setting_id) if setting_id else None,
        limit=limit,
        offset=offset,
    )

    return SettingHistoryListResponse(
        items=[SettingHistoryRead.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/history/{history_id}",
    response_model=SettingHistoryRead,
    tags=["settings-history"],
    summary="Get a specific history entry",
)
def get_history(
    history_id: UUID,
    auth: dict = Depends(require_permission("settings:manage")),
    db: Session = Depends(get_db),
):
    """Get details of a specific history entry."""
    entry = get_history_entry(db, str(history_id))
    if not entry:
        raise HTTPException(status_code=404, detail="History entry not found")
    return SettingHistoryRead.model_validate(entry)


@router.post(
    "/history/restore",
    response_model=DomainSettingRead,
    tags=["settings-history"],
    summary="Restore a setting from history",
)
def restore_setting(
    payload: RestoreSettingRequest,
    auth: dict = Depends(require_permission("settings:manage")),
    db: Session = Depends(get_db),
):
    """
    Restore a setting to a previous state from a history entry.

    This will:
    - UPDATE: Revert the setting to its old value before the change
    - DELETE: Recreate the setting with its value before deletion

    Note: Cannot restore from CREATE actions (use delete instead).
    """
    user_id = auth.get("user_id") if auth else None
    return restore_from_history(
        db,
        history_id=str(payload.history_id),
        changed_by_id=str(user_id) if user_id else None,
        change_reason=payload.change_reason,
    )


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
    category: str | None = Query(
        None, description="Filter by category: sans-serif, serif, monospace"
    ),
    auth: dict = Depends(require_permission("settings:manage")),
):
    """List available font options for branding."""
    fonts = FONT_PRESETS
    if category:
        fonts = [f for f in fonts if f["category"] == category]

    return FontListResponse(fonts=[FontOption.model_validate(f) for f in fonts])
