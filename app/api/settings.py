from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.schemas.common import ListResponse
from app.schemas.settings import DomainSettingRead, DomainSettingUpdate
from app.services import settings_api as settings_service

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
    key: str, payload: DomainSettingUpdate, db: Session = Depends(get_db)
):
    return settings_service.upsert_auth_setting(db, key, payload)


@router.get(
    "/auth/{key}",
    response_model=DomainSettingRead,
    tags=["settings-auth"],
)
def get_auth_setting(key: str, db: Session = Depends(get_db)):
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
    key: str, payload: DomainSettingUpdate, db: Session = Depends(get_db)
):
    return settings_service.upsert_audit_setting(db, key, payload)


@router.get(
    "/audit/{key}",
    response_model=DomainSettingRead,
    tags=["settings-audit"],
)
def get_audit_setting(key: str, db: Session = Depends(get_db)):
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
    key: str, payload: DomainSettingUpdate, db: Session = Depends(get_db)
):
    return settings_service.upsert_scheduler_setting(db, key, payload)


@router.get(
    "/scheduler/{key}",
    response_model=DomainSettingRead,
    tags=["settings-scheduler"],
)
def get_scheduler_setting(key: str, db: Session = Depends(get_db)):
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
    key: str, payload: DomainSettingUpdate, db: Session = Depends(get_db)
):
    return settings_service.upsert_email_setting(db, key, payload)


@router.get(
    "/email/{key}",
    response_model=DomainSettingRead,
    tags=["settings-email"],
)
def get_email_setting(key: str, db: Session = Depends(get_db)):
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
    key: str, payload: DomainSettingUpdate, db: Session = Depends(get_db)
):
    return settings_service.upsert_features_setting(db, key, payload)


@router.get(
    "/features/{key}",
    response_model=DomainSettingRead,
    tags=["settings-features"],
)
def get_features_setting(key: str, db: Session = Depends(get_db)):
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
    key: str, payload: DomainSettingUpdate, db: Session = Depends(get_db)
):
    return settings_service.upsert_automation_setting(db, key, payload)


@router.get(
    "/automation/{key}",
    response_model=DomainSettingRead,
    tags=["settings-automation"],
)
def get_automation_setting(key: str, db: Session = Depends(get_db)):
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
    key: str, payload: DomainSettingUpdate, db: Session = Depends(get_db)
):
    return settings_service.upsert_reporting_setting(db, key, payload)


@router.get(
    "/reporting/{key}",
    response_model=DomainSettingRead,
    tags=["settings-reporting"],
)
def get_reporting_setting(key: str, db: Session = Depends(get_db)):
    return settings_service.get_reporting_setting(db, key)
