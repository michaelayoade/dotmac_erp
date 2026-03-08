from __future__ import annotations

from app.services.audit_dispatcher import fire_audit_event

from .common import DEFAULT_NEW_LOCAL_PASSWORD, AdminWebCommonMixin, templates
from .identity import AdminIdentityMixin
from .operations import AdminOperationsMixin
from .organization_settings import AdminOrganizationSettingsMixin


class AdminWebService(
    AdminWebCommonMixin,
    AdminOperationsMixin,
    AdminOrganizationSettingsMixin,
    AdminIdentityMixin,
):
    """Modular admin web facade with legacy fallback for unmigrated methods."""

    def __getattr__(self, name: str):
        from ._legacy import LegacyAdminWebService

        legacy_service = LegacyAdminWebService()
        return getattr(legacy_service, name)


admin_web_service = AdminWebService()

__all__ = [
    "AdminWebService",
    "admin_web_service",
    "DEFAULT_NEW_LOCAL_PASSWORD",
    "fire_audit_event",
    "templates",
]
