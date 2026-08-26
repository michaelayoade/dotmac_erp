"""Startup-only loading of the ERP OIDC relying-party registration."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.config import settings
from app.services.oidc_runtime import OIDCProviderConfig, clear, install
from app.services.secrets import resolve_secret


def install_oidc_provider(db: Session | None = None) -> list[str]:
    enabled = settings.erp_oidc_enabled
    configured = (
        settings.erp_oidc_issuer,
        settings.erp_oidc_client_id,
        settings.erp_oidc_client_secret,
        settings.erp_oidc_redirect_uri,
    )
    if not enabled:
        clear()
        return []
    if not all(configured):
        # Initial enablement still fails startup through the returned error.
        # An explicit refresh keeps an already-working held registration.
        return [
            "ERP OIDC is enabled but its issuer/client/secret/redirect is incomplete"
        ]
    material = resolve_secret(settings.erp_oidc_client_secret, db)
    if not material:
        return ["ERP OIDC client secret could not be installed"]
    install(
        OIDCProviderConfig(
            provider_binding=settings.erp_oidc_provider_binding,
            issuer=settings.erp_oidc_issuer,
            client_id=settings.erp_oidc_client_id,
            client_secret=material,
            redirect_uri=settings.erp_oidc_redirect_uri,
            discovery_url=settings.erp_oidc_discovery_url,
            timeout_seconds=settings.erp_oidc_timeout_seconds,
            ceremony_ttl_seconds=settings.erp_oidc_ceremony_ttl_seconds,
            clock_skew_seconds=settings.erp_oidc_clock_skew_seconds,
        )
    )
    return []


__all__ = ["install_oidc_provider"]
