"""
DotMac CRM Sync Web Service.

Provides data and operations for the CRM sync management UI.
"""

import logging
import secrets
import uuid
from datetime import UTC, datetime
from urllib.parse import quote_plus, urlencode

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.models.auth import ApiKey
from app.models.person import Person
from app.models.sync import IntegrationConfig, IntegrationType
from app.models.sync.dotmac_crm_sync import CRMEntityType, CRMSyncMapping, CRMSyncStatus
from app.services.auth import hash_api_key
from app.templates import templates
from app.web.deps import WebAuthContext, brand_context, org_brand_context

logger = logging.getLogger(__name__)

# CRM sync entity types for display
CRM_ENTITY_TYPES = {
    CRMEntityType.PROJECT: "Projects",
    CRMEntityType.TICKET: "Tickets",
    CRMEntityType.WORK_ORDER: "Work Orders",
}


class CRMSyncWebService:
    """Service for CRM sync management web UI."""

    def _base_context(
        self,
        request: Request,
        auth: WebAuthContext | None,
        title: str,
        active_tab: str = "dashboard",
        db: Session | None = None,
    ) -> dict:
        """Build base context for templates."""
        org_branding = None
        if db and auth and auth.organization_id:
            org_branding = org_brand_context(db, auth.organization_id)
        return {
            "request": request,
            "auth": auth,
            "title": title,
            "page_title": title,
            "brand": org_branding or brand_context(),
            "org_branding": org_branding,
            "user": auth.user if auth else {"name": "Admin", "initials": "AD"},
            "csrf_token": getattr(request.state, "csrf_token", ""),
            "active_tab": active_tab,
            "active_page": "sync",
            "module": "admin",
            "sub_module": "crm-sync",
            "crm_entity_types": CRM_ENTITY_TYPES,
        }

    def _require_admin(
        self,
        request: Request,
        auth: WebAuthContext | None,
    ) -> HTMLResponse | RedirectResponse | None:
        """Check if user is admin, return error response if not."""
        if not auth or not auth.is_authenticated:
            next_path = request.url.path
            if request.url.query:
                next_path = f"{next_path}?{request.url.query}"
            return RedirectResponse(
                url=f"/admin/login?{urlencode({'next': next_path})}",
                status_code=302,
            )
        if not auth.is_admin:
            raise HTTPException(status_code=403, detail="Admin access required")
        return None

    def _get_crm_config(
        self, db: Session, org_id: uuid.UUID
    ) -> IntegrationConfig | None:
        """Get CRM integration config for organization."""
        stmt = select(IntegrationConfig).where(
            IntegrationConfig.organization_id == org_id,
            IntegrationConfig.integration_type == IntegrationType.DOTMAC_CRM,
        )
        return db.scalar(stmt)

    def _get_service_api_key(self, db: Session, org_id: uuid.UUID) -> ApiKey | None:
        """Get the CRM service API key for organization."""
        # Look for API key with label matching CRM service pattern
        stmt = (
            select(ApiKey)
            .join(Person, ApiKey.person_id == Person.id)
            .where(
                Person.organization_id == org_id,
                ApiKey.label.ilike("dotmac-crm-service%"),
                ApiKey.is_active.is_(True),
                ApiKey.revoked_at.is_(None),
            )
            .order_by(desc(ApiKey.created_at))
        )
        return db.scalar(stmt)

    def _get_sync_stats(self, db: Session, org_id: uuid.UUID) -> dict:
        """Get sync statistics by entity type."""
        stats = {}
        for entity_type in CRMEntityType:
            stmt = select(func.count(CRMSyncMapping.mapping_id)).where(
                CRMSyncMapping.organization_id == org_id,
                CRMSyncMapping.crm_entity_type == entity_type,
            )
            total = db.scalar(stmt) or 0

            # Count by status
            active_stmt = select(func.count(CRMSyncMapping.mapping_id)).where(
                CRMSyncMapping.organization_id == org_id,
                CRMSyncMapping.crm_entity_type == entity_type,
                CRMSyncMapping.crm_status == CRMSyncStatus.ACTIVE,
            )
            active = db.scalar(active_stmt) or 0

            stats[entity_type.value] = {
                "total": total,
                "active": active,
                "completed": total - active,
                "label": CRM_ENTITY_TYPES.get(entity_type, entity_type.value),
            }
        return stats

    def _get_recent_syncs(
        self, db: Session, org_id: uuid.UUID, limit: int = 10
    ) -> list:
        """Get recently synced entities."""
        stmt = (
            select(CRMSyncMapping)
            .where(CRMSyncMapping.organization_id == org_id)
            .order_by(desc(CRMSyncMapping.synced_at))
            .limit(limit)
        )
        return list(db.scalars(stmt).all())

    # ============ Dashboard ============

    def dashboard_response(
        self,
        request: Request,
        db: Session,
        auth: WebAuthContext | None,
    ) -> HTMLResponse | RedirectResponse:
        """Render CRM sync dashboard page."""
        error_response = self._require_admin(request, auth)
        if error_response:
            return error_response

        context = self._base_context(request, auth, "DotMac CRM Sync", "dashboard", db)
        org_id = auth.organization_id if auth else None

        if org_id:
            # Get config and API key
            config = self._get_crm_config(db, org_id)
            api_key = self._get_service_api_key(db, org_id)

            context["config"] = config
            context["api_key"] = api_key
            context["integration_configured"] = bool(
                config and config.is_active and api_key
            )

            # Get stats
            context["sync_stats"] = self._get_sync_stats(db, org_id)
            context["total_synced"] = sum(
                s["total"] for s in context["sync_stats"].values()
            )

            # Get recent activity
            context["recent_syncs"] = self._get_recent_syncs(db, org_id)

            # Last sync time
            if context["recent_syncs"]:
                context["last_sync_at"] = context["recent_syncs"][0].synced_at
            else:
                context["last_sync_at"] = None
        else:
            context["integration_configured"] = False
            context["sync_stats"] = {}
            context["total_synced"] = 0
            context["recent_syncs"] = []

        return templates.TemplateResponse(
            request, "admin/sync/crm/dashboard.html", context
        )

    # ============ Configuration ============

    def config_response(
        self,
        request: Request,
        db: Session,
        auth: WebAuthContext | None,
    ) -> HTMLResponse | RedirectResponse:
        """Render CRM config page."""
        error_response = self._require_admin(request, auth)
        if error_response:
            return error_response

        context = self._base_context(
            request, auth, "CRM Integration Settings", "config", db
        )
        org_id = auth.organization_id if auth else None

        if org_id:
            config = self._get_crm_config(db, org_id)
            api_key = self._get_service_api_key(db, org_id)

            context["config"] = config
            context["api_key"] = api_key
            context["has_api_key"] = bool(api_key)

            # Mask API key for display
            if api_key:
                context["api_key_masked"] = f"****{api_key.key_hash[-8:]}"
                context["api_key_created_at"] = api_key.created_at
                context["api_key_last_used"] = api_key.last_used_at

        return templates.TemplateResponse(
            request, "admin/sync/crm/config.html", context
        )

    def config_save_response(
        self,
        request: Request,
        db: Session,
        auth: WebAuthContext | None,
        is_active: bool,
        sync_projects: bool,
        sync_tickets: bool,
        sync_work_orders: bool,
    ) -> Response:
        """Save CRM config."""
        error_response = self._require_admin(request, auth)
        if error_response:
            return error_response

        org_id = auth.organization_id if auth else None
        if not org_id:
            return RedirectResponse(
                url="/admin/sync/crm/config?error=" + quote_plus("No organization"),
                status_code=302,
            )

        # Get or create config
        config = self._get_crm_config(db, org_id)
        if not config:
            config = IntegrationConfig(
                organization_id=org_id,
                integration_type=IntegrationType.DOTMAC_CRM,
                base_url="https://crm.dotmac.io",  # Default CRM URL
                is_active=is_active,
                created_by_user_id=auth.person_id if auth else None,
            )
            db.add(config)
        else:
            config.is_active = is_active
            config.updated_at = datetime.now(UTC)

        # Store sync settings in company field as JSON-like string
        sync_settings = []
        if sync_projects:
            sync_settings.append("projects")
        if sync_tickets:
            sync_settings.append("tickets")
        if sync_work_orders:
            sync_settings.append("work_orders")
        config.company = ",".join(sync_settings) if sync_settings else "all"

        db.commit()

        logger.info("CRM sync config saved for org %s: active=%s", org_id, is_active)
        return RedirectResponse(
            url="/admin/sync/crm/config?success=" + quote_plus("Configuration saved"),
            status_code=302,
        )

    def generate_api_key_response(
        self,
        request: Request,
        db: Session,
        auth: WebAuthContext | None,
    ) -> Response:
        """Generate a new API key for CRM sync."""
        error_response = self._require_admin(request, auth)
        if error_response:
            return error_response

        org_id = auth.organization_id if auth else None
        person_id = auth.person_id if auth else None

        if not org_id or not person_id:
            return RedirectResponse(
                url="/admin/sync/crm/config?error="
                + quote_plus("Authentication required"),
                status_code=302,
            )

        # Revoke any existing CRM service API keys for this org
        existing_keys = db.scalars(
            select(ApiKey)
            .join(Person, ApiKey.person_id == Person.id)
            .where(
                Person.organization_id == org_id,
                ApiKey.label.ilike("dotmac-crm-service%"),
                ApiKey.is_active.is_(True),
                ApiKey.revoked_at.is_(None),
            )
        ).all()

        for key in existing_keys:
            key.is_active = False
            key.revoked_at = datetime.now(UTC)
            logger.info("Revoked old CRM API key: %s", key.id)

        # Generate new API key
        raw_key = secrets.token_urlsafe(32)
        key_hash = hash_api_key(raw_key)

        new_key = ApiKey(
            person_id=person_id,
            label=f"dotmac-crm-service-{datetime.now(UTC).strftime('%Y%m%d')}",
            key_hash=key_hash,
            is_active=True,
        )
        db.add(new_key)
        db.commit()

        logger.info("Generated new CRM API key for org %s: %s", org_id, new_key.id)

        # Render config page directly with the raw key in context (not in URL).
        # The key is shown once and never stored in browser history or server logs.
        context = self._base_context(
            request, auth, "CRM Integration Settings", "config", db
        )
        context["config"] = self._get_crm_config(db, org_id)
        context["api_key"] = new_key
        context["has_api_key"] = True
        context["api_key_masked"] = f"****{key_hash[-8:]}"
        context["api_key_created_at"] = new_key.created_at
        context["api_key_last_used"] = new_key.last_used_at
        context["new_api_key"] = raw_key  # One-time display in template
        context["success"] = "API key generated. Copy it now — it won't be shown again."

        return templates.TemplateResponse(
            request, "admin/sync/crm/config.html", context
        )

    def revoke_api_key_response(
        self,
        request: Request,
        db: Session,
        auth: WebAuthContext | None,
    ) -> Response:
        """Revoke the CRM service API key."""
        error_response = self._require_admin(request, auth)
        if error_response:
            return error_response

        org_id = auth.organization_id if auth else None
        if not org_id:
            return RedirectResponse(
                url="/admin/sync/crm/config?error=" + quote_plus("No organization"),
                status_code=302,
            )

        api_key = self._get_service_api_key(db, org_id)
        if api_key:
            api_key.is_active = False
            api_key.revoked_at = datetime.now(UTC)
            db.commit()
            logger.info("Revoked CRM API key: %s", api_key.id)

        return RedirectResponse(
            url="/admin/sync/crm/config?success=" + quote_plus("API key revoked"),
            status_code=302,
        )

    # ============ Entities ============

    def entities_response(
        self,
        request: Request,
        db: Session,
        auth: WebAuthContext | None,
        entity_type: str | None = None,
        status: str | None = None,
        search: str | None = None,
        page: int = 1,
    ) -> HTMLResponse | RedirectResponse:
        """Render synced entities list."""
        error_response = self._require_admin(request, auth)
        if error_response:
            return error_response

        context = self._base_context(
            request, auth, "Synced CRM Entities", "entities", db
        )
        org_id = auth.organization_id if auth else None

        per_page = 25
        offset = (page - 1) * per_page

        if org_id:
            # Build query
            stmt = select(CRMSyncMapping).where(
                CRMSyncMapping.organization_id == org_id
            )

            # Filters
            if entity_type and entity_type in [e.value for e in CRMEntityType]:
                stmt = stmt.where(
                    CRMSyncMapping.crm_entity_type == CRMEntityType(entity_type)
                )

            if status and status in [s.value for s in CRMSyncStatus]:
                stmt = stmt.where(CRMSyncMapping.crm_status == CRMSyncStatus(status))

            if search:
                search_filter = f"%{search}%"
                stmt = stmt.where(
                    (CRMSyncMapping.display_name.ilike(search_filter))
                    | (CRMSyncMapping.display_code.ilike(search_filter))
                    | (CRMSyncMapping.crm_id.ilike(search_filter))
                )

            # Count
            count_stmt = select(func.count()).select_from(stmt.subquery())
            total_count = db.scalar(count_stmt) or 0

            # Paginate
            stmt = (
                stmt.order_by(desc(CRMSyncMapping.synced_at))
                .offset(offset)
                .limit(per_page)
            )
            entities = list(db.scalars(stmt).all())

            context["entities"] = entities
            context["total_count"] = total_count
            context["page"] = page
            context["per_page"] = per_page
            context["total_pages"] = (total_count + per_page - 1) // per_page
            context["filter_entity_type"] = entity_type
            context["filter_status"] = status
            context["filter_search"] = search
            context["entity_type_choices"] = [
                (e.value, CRM_ENTITY_TYPES[e]) for e in CRMEntityType
            ]
            context["status_choices"] = [
                (s.value, s.value.title()) for s in CRMSyncStatus
            ]
        else:
            context["entities"] = []
            context["total_count"] = 0

        return templates.TemplateResponse(
            request, "admin/sync/crm/entities.html", context
        )

    # ============ Inventory Push ============

    def inventory_push_response(
        self,
        request: Request,
        db: Session,
        auth: WebAuthContext | None,
    ) -> HTMLResponse | RedirectResponse:
        """Render inventory push management page."""
        from app.config import settings

        error_response = self._require_admin(request, auth)
        if error_response:
            return error_response

        context = self._base_context(
            request, auth, "Inventory Push to CRM", "inventory", db
        )
        org_id = auth.organization_id if auth else None

        # Check if push is configured
        context["is_configured"] = bool(
            settings.crm_inventory_webhook_url and settings.crm_api_token
        )
        context["webhook_url"] = settings.crm_inventory_webhook_url or "Not configured"
        context["has_api_token"] = bool(settings.crm_api_token)

        # Get inventory stats
        if org_id:
            from app.models.inventory.item import Item
            from app.services.inventory.balance import InventoryBalanceService

            # Total items
            total_items = (
                db.scalar(
                    select(func.count(Item.item_id)).where(
                        Item.organization_id == org_id,
                        Item.is_active.is_(True),
                        Item.track_inventory.is_(True),
                    )
                )
                or 0
            )

            # Low stock items
            low_stock_items = InventoryBalanceService.get_low_stock_items(db, org_id)

            context["total_items"] = total_items
            context["low_stock_count"] = len(low_stock_items)
        else:
            context["total_items"] = 0
            context["low_stock_count"] = 0

        return templates.TemplateResponse(
            request, "admin/sync/crm/inventory.html", context
        )

    def trigger_inventory_push_response(
        self,
        request: Request,
        db: Session,
        auth: WebAuthContext | None,
        push_type: str = "full",
        include_zero_stock: bool = False,
    ) -> RedirectResponse:
        """Trigger inventory push to CRM."""
        error_response = self._require_admin(request, auth)
        if error_response:
            return error_response  # type: ignore

        org_id = auth.organization_id if auth else None

        if not org_id:
            return RedirectResponse(
                url="/admin/sync/crm/inventory?error=" + quote_plus("No organization"),
                status_code=302,
            )

        # Trigger the appropriate Celery task
        from app.tasks.crm import (
            push_inventory_to_crm,
            push_low_stock_alerts_to_crm,
        )

        try:
            if push_type == "low_stock":
                push_low_stock_alerts_to_crm.delay(str(org_id))
                message = "Low stock alert push started"
            else:
                push_inventory_to_crm.delay(str(org_id), include_zero_stock)
                message = "Full inventory push started"

            logger.info("Triggered inventory push: type=%s, org=%s", push_type, org_id)

            return RedirectResponse(
                url="/admin/sync/crm/inventory?success=" + quote_plus(message),
                status_code=302,
            )

        except Exception as e:
            logger.exception("Failed to trigger inventory push: %s", str(e))
            return RedirectResponse(
                url="/admin/sync/crm/inventory?error=" + quote_plus(str(e)),
                status_code=302,
            )

    def inventory_health_check_response(
        self,
        request: Request,
        db: Session,
        auth: WebAuthContext | None,
    ) -> RedirectResponse:
        """Test CRM inventory webhook connectivity."""
        error_response = self._require_admin(request, auth)
        if error_response:
            return error_response  # type: ignore

        from app.services.sync.inventory_push_service import InventoryPushService

        with InventoryPushService(db) as service:
            result = service.health_check()

        if result.get("healthy"):
            return RedirectResponse(
                url="/admin/sync/crm/inventory?success="
                + quote_plus("CRM webhook is healthy"),
                status_code=302,
            )
        else:
            return RedirectResponse(
                url="/admin/sync/crm/inventory?error="
                + quote_plus(result.get("message", "Unknown error")),
                status_code=302,
            )


# Singleton instance
crm_sync_web_service = CRMSyncWebService()
