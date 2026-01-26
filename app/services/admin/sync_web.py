"""
Sync Management Web Service.

Provides data and operations for the sync management UI.
"""
import uuid
from typing import Optional
from urllib.parse import quote_plus

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select, desc
from sqlalchemy.orm import Session

from app.models.sync import SyncHistory, SyncEntity, SyncStatus, IntegrationConfig, IntegrationType
from app.models.finance.core_org import Organization
from app.services.erpnext.sync.orchestrator import SUPPORTED_ENTITIES, SYNC_PHASES
from app.tasks.sync import (
    run_full_erpnext_sync,
    run_incremental_erpnext_sync,
    sync_single_entity_type,
)
from app.templates import templates
from app.web.deps import WebAuthContext, brand_context, org_brand_context


class SyncWebService:
    """Service for sync management web UI."""

    def _base_context(
        self,
        request: Request,
        auth: Optional[WebAuthContext],
        title: str,
        active_tab: str = "dashboard",
        db: Optional[Session] = None,
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
            "active_page": "sync",  # For sidebar highlighting
            "module": "admin",
            "sub_module": "sync",
            "supported_entities": SUPPORTED_ENTITIES,
            "sync_phases": SYNC_PHASES,
        }

    def _require_admin(self, request: Request, auth: Optional[WebAuthContext]) -> Optional[HTMLResponse]:
        """Check if user is admin, return error response if not."""
        if not auth or not auth.is_authenticated:
            return RedirectResponse(url="/login?next=/admin/sync", status_code=302)
        # Could add more granular permission check here
        return None

    def dashboard_response(
        self,
        request: Request,
        db: Session,
        auth: Optional[WebAuthContext],
    ) -> HTMLResponse:
        """Render sync dashboard page."""
        error_response = self._require_admin(request, auth)
        if error_response:
            return error_response

        context = self._base_context(request, auth, "Sync Management", "dashboard", db)

        # Get organization for current user
        org_id = auth.organization_id if auth else None

        # Get recent sync history
        history_query = (
            select(SyncHistory)
            .where(SyncHistory.source_system == "erpnext")
            .order_by(desc(SyncHistory.created_at))
            .limit(10)
        )
        if org_id:
            history_query = history_query.where(SyncHistory.organization_id == org_id)

        recent_history = db.execute(history_query).scalars().all()
        context["recent_history"] = recent_history

        # Get sync stats by entity type
        if org_id:
            entity_stats = (
                db.execute(
                    select(
                        SyncEntity.source_doctype,
                        SyncEntity.sync_status,
                        func.count().label("count"),
                    )
                    .where(SyncEntity.organization_id == org_id)
                    .group_by(SyncEntity.source_doctype, SyncEntity.sync_status)
                )
                .all()
            )

            # Aggregate by doctype
            stats_by_doctype = {}
            for row in entity_stats:
                doctype = row.source_doctype
                if doctype not in stats_by_doctype:
                    stats_by_doctype[doctype] = {"total": 0, "synced": 0, "failed": 0, "skipped": 0}
                stats_by_doctype[doctype]["total"] += row.count
                if row.sync_status == SyncStatus.SYNCED:
                    stats_by_doctype[doctype]["synced"] = row.count
                elif row.sync_status == SyncStatus.FAILED:
                    stats_by_doctype[doctype]["failed"] = row.count
                elif row.sync_status == SyncStatus.SKIPPED:
                    stats_by_doctype[doctype]["skipped"] = row.count

            context["entity_stats"] = stats_by_doctype
        else:
            context["entity_stats"] = {}

        # Get last successful sync time (filtered by org)
        last_sync_query = (
            select(SyncHistory)
            .where(
                SyncHistory.source_system == "erpnext",
                SyncHistory.status.in_(["COMPLETED", "COMPLETED_WITH_ERRORS"]),
            )
            .order_by(desc(SyncHistory.completed_at))
            .limit(1)
        )
        if org_id:
            last_sync_query = last_sync_query.where(SyncHistory.organization_id == org_id)
        last_sync = db.execute(last_sync_query).scalar_one_or_none()
        context["last_sync"] = last_sync

        # Check integration config status
        if org_id:
            config = (
                db.execute(
                    select(IntegrationConfig)
                    .where(
                        IntegrationConfig.organization_id == org_id,
                        IntegrationConfig.integration_type == IntegrationType.ERPNEXT,
                        IntegrationConfig.is_active.is_(True),
                    )
                )
                .scalar_one_or_none()
            )
            context["integration_configured"] = config is not None
        else:
            context["integration_configured"] = False

        # Summary stats
        if org_id:
            total_synced = db.execute(
                select(func.count())
                .select_from(SyncEntity)
                .where(
                    SyncEntity.organization_id == org_id,
                    SyncEntity.sync_status == SyncStatus.SYNCED,
                )
            ).scalar() or 0

            total_failed = db.execute(
                select(func.count())
                .select_from(SyncEntity)
                .where(
                    SyncEntity.organization_id == org_id,
                    SyncEntity.sync_status == SyncStatus.FAILED,
                )
            ).scalar() or 0

            context["total_synced"] = total_synced
            context["total_failed"] = total_failed
        else:
            context["total_synced"] = 0
            context["total_failed"] = 0

        return templates.TemplateResponse(request, "admin/sync/dashboard.html", context)

    def history_response(
        self,
        request: Request,
        db: Session,
        auth: Optional[WebAuthContext],
        page: int = 1,
        status: str = "",
    ) -> HTMLResponse:
        """Render sync history list page."""
        error_response = self._require_admin(request, auth)
        if error_response:
            return error_response

        context = self._base_context(request, auth, "Sync History", "history", db)

        org_id = auth.organization_id if auth else None
        per_page = 20
        offset = (page - 1) * per_page

        # Build query
        query = (
            select(SyncHistory)
            .where(SyncHistory.source_system == "erpnext")
            .order_by(desc(SyncHistory.created_at))
        )
        if org_id:
            query = query.where(SyncHistory.organization_id == org_id)
        if status:
            query = query.where(SyncHistory.status == status)

        # Get total count
        count_query = select(func.count()).select_from(SyncHistory).where(SyncHistory.source_system == "erpnext")
        if org_id:
            count_query = count_query.where(SyncHistory.organization_id == org_id)
        if status:
            count_query = count_query.where(SyncHistory.status == status)

        total = db.execute(count_query).scalar() or 0

        # Get page results
        history = db.execute(query.offset(offset).limit(per_page)).scalars().all()

        context["history"] = history
        context["page"] = page
        context["per_page"] = per_page
        context["total"] = total
        context["total_pages"] = (total + per_page - 1) // per_page
        context["filter_status"] = status

        return templates.TemplateResponse(request, "admin/sync/history.html", context)

    def history_detail_response(
        self,
        request: Request,
        db: Session,
        auth: Optional[WebAuthContext],
        history_id: str,
    ) -> HTMLResponse:
        """Render sync history detail page."""
        error_response = self._require_admin(request, auth)
        if error_response:
            return error_response

        context = self._base_context(request, auth, "Sync Detail", "history", db)

        org_id = auth.organization_id if auth else None

        try:
            history = db.get(SyncHistory, uuid.UUID(history_id))
        except ValueError:
            history = None

        # Security: Verify user has access to this history's organization
        if not history or (org_id and history.organization_id != org_id):
            context["error"] = "Sync history not found"
            return templates.TemplateResponse(request, "admin/sync/history_detail.html", context)

        context["history"] = history

        # Get entity sync details for this run (by time window)
        if history.started_at and history.completed_at:
            entities = (
                db.execute(
                    select(SyncEntity)
                    .where(
                        SyncEntity.organization_id == history.organization_id,
                        SyncEntity.synced_at >= history.started_at,
                        SyncEntity.synced_at <= history.completed_at,
                    )
                    .order_by(desc(SyncEntity.synced_at))
                    .limit(100)
                )
                .scalars()
                .all()
            )
            context["sync_entities"] = entities

        return templates.TemplateResponse(request, "admin/sync/history_detail.html", context)

    def trigger_sync_response(
        self,
        request: Request,
        db: Session,
        auth: Optional[WebAuthContext],
        sync_type: str,
        entity_types: Optional[list[str]] = None,
    ) -> RedirectResponse:
        """Trigger a sync operation."""
        error_response = self._require_admin(request, auth)
        if error_response:
            return error_response

        if not auth or not auth.organization_id or not auth.user_id:
            return RedirectResponse(
                url="/admin/sync?error=Authentication+required",
                status_code=302,
            )

        org_id = str(auth.organization_id)
        user_id = str(auth.user_id)

        try:
            task = None
            task_ids = []

            if sync_type == "full":
                task = run_full_erpnext_sync.delay(org_id, user_id, entity_types)
                task_ids.append(task.id)
            elif sync_type == "incremental":
                task = run_incremental_erpnext_sync.delay(org_id, user_id, entity_types)
                task_ids.append(task.id)
            elif sync_type == "entity" and entity_types:
                # Sync single entity type(s)
                for entity_type in entity_types:
                    task = sync_single_entity_type.delay(org_id, user_id, entity_type, True)
                    task_ids.append(task.id)
            else:
                # Default to incremental if sync_type is invalid
                task = run_incremental_erpnext_sync.delay(org_id, user_id, entity_types)
                task_ids.append(task.id)

            task_id_str = ", ".join(task_ids) if len(task_ids) > 1 else task_ids[0] if task_ids else "unknown"
            return RedirectResponse(
                url=f"/admin/sync?success=Sync+started+(Task+ID:+{quote_plus(task_id_str)})",
                status_code=302,
            )
        except Exception as e:
            return RedirectResponse(
                url=f"/admin/sync?error={quote_plus(str(e))}",
                status_code=302,
            )

    def config_response(
        self,
        request: Request,
        db: Session,
        auth: Optional[WebAuthContext],
    ) -> HTMLResponse:
        """Render integration configuration page."""
        error_response = self._require_admin(request, auth)
        if error_response:
            return error_response

        context = self._base_context(request, auth, "ERPNext Integration", "config", db)

        org_id = auth.organization_id if auth else None

        if org_id:
            config = (
                db.execute(
                    select(IntegrationConfig)
                    .where(
                        IntegrationConfig.organization_id == org_id,
                        IntegrationConfig.integration_type == IntegrationType.ERPNEXT,
                    )
                )
                .scalar_one_or_none()
            )
            context["config"] = config

            # Get organization list for dropdown
            orgs = db.execute(select(Organization)).scalars().all()
            context["organizations"] = orgs

        return templates.TemplateResponse(request, "admin/sync/config.html", context)

    def save_config_response(
        self,
        request: Request,
        db: Session,
        auth: Optional[WebAuthContext],
        base_url: str,
        api_key: str,
        api_secret: str,
        company: str,
        is_active: bool,
    ) -> RedirectResponse:
        """Save integration configuration."""
        error_response = self._require_admin(request, auth)
        if error_response:
            return error_response

        if not auth or not auth.organization_id:
            return RedirectResponse(
                url="/admin/sync/config?error=Authentication+required",
                status_code=302,
            )

        org_id = auth.organization_id
        user_id = auth.user_id if auth else None

        try:
            from app.services.integration_config import IntegrationConfigService

            service = IntegrationConfigService(db)

            # Check if config exists
            existing = service.get_config(org_id, IntegrationType.ERPNEXT, active_only=False)

            if existing:
                # Update existing config - only update credentials if provided
                service.update_credentials(
                    organization_id=org_id,
                    integration_type=IntegrationType.ERPNEXT,
                    base_url=base_url if base_url else None,
                    api_key=api_key if api_key else None,  # None = keep existing
                    api_secret=api_secret if api_secret else None,  # None = keep existing
                    company=company if company else None,
                )
                # Update is_active separately
                existing.is_active = is_active
            else:
                # Create new config - require credentials for new config
                if not api_key or not api_secret:
                    return RedirectResponse(
                        url="/admin/sync/config?error=API+key+and+secret+are+required+for+new+configuration",
                        status_code=302,
                    )
                service.create_config(
                    organization_id=org_id,
                    integration_type=IntegrationType.ERPNEXT,
                    base_url=base_url,
                    api_key=api_key,
                    api_secret=api_secret,
                    company=company,
                    user_id=user_id,
                )

            db.commit()

            return RedirectResponse(
                url="/admin/sync/config?success=Configuration+saved",
                status_code=302,
            )
        except Exception as e:
            db.rollback()
            return RedirectResponse(
                url=f"/admin/sync/config?error={quote_plus(str(e))}",
                status_code=302,
            )

    def entities_response(
        self,
        request: Request,
        db: Session,
        auth: Optional[WebAuthContext],
        doctype: str = "",
        status: str = "",
        page: int = 1,
    ) -> HTMLResponse:
        """Render entity sync status page."""
        error_response = self._require_admin(request, auth)
        if error_response:
            return error_response

        context = self._base_context(request, auth, "Sync Entities", "entities", db)

        org_id = auth.organization_id if auth else None
        per_page = 50
        offset = (page - 1) * per_page

        if org_id:
            # Build query
            query = (
                select(SyncEntity)
                .where(SyncEntity.organization_id == org_id)
                .order_by(desc(SyncEntity.synced_at))
            )
            if doctype:
                query = query.where(SyncEntity.source_doctype == doctype)
            if status:
                query = query.where(SyncEntity.sync_status == status)

            # Get total
            count_query = (
                select(func.count())
                .select_from(SyncEntity)
                .where(SyncEntity.organization_id == org_id)
            )
            if doctype:
                count_query = count_query.where(SyncEntity.source_doctype == doctype)
            if status:
                count_query = count_query.where(SyncEntity.sync_status == status)

            total = db.execute(count_query).scalar() or 0

            # Get page
            entities = db.execute(query.offset(offset).limit(per_page)).scalars().all()

            # Get unique doctypes for filter (filter out None values)
            doctypes = (
                db.execute(
                    select(SyncEntity.source_doctype)
                    .where(
                        SyncEntity.organization_id == org_id,
                        SyncEntity.source_doctype.isnot(None),
                    )
                    .distinct()
                )
                .scalars()
                .all()
            )

            context["entities"] = entities
            context["doctypes"] = sorted(doctypes)
            context["total"] = total
        else:
            context["entities"] = []
            context["doctypes"] = []
            context["total"] = 0

        context["page"] = page
        context["per_page"] = per_page
        context["total_pages"] = (context["total"] + per_page - 1) // per_page
        context["filter_doctype"] = doctype
        context["filter_status"] = status

        return templates.TemplateResponse(request, "admin/sync/entities.html", context)

    def test_connection_response(
        self,
        request: Request,
        db: Session,
        auth: Optional[WebAuthContext],
    ) -> RedirectResponse:
        """Test ERPNext connection and redirect with result."""
        error_response = self._require_admin(request, auth)
        if error_response:
            return error_response

        if not auth or not auth.organization_id:
            return RedirectResponse(
                url="/admin/sync/config?error=Authentication+required",
                status_code=302,
            )

        org_id = auth.organization_id

        try:
            from app.services.integration_config import IntegrationConfigService

            service = IntegrationConfigService(db)
            success, error_message = service.verify_connection(org_id, IntegrationType.ERPNEXT)

            if success:
                service.mark_verified(org_id, IntegrationType.ERPNEXT)
                db.commit()
                return RedirectResponse(
                    url="/admin/sync/config?success=Connection+successful",
                    status_code=302,
                )
            else:
                return RedirectResponse(
                    url=f"/admin/sync/config?error={quote_plus(error_message or 'Connection failed')}",
                    status_code=302,
                )
        except Exception as e:
            return RedirectResponse(
                url=f"/admin/sync/config?error={quote_plus(str(e))}",
                status_code=302,
            )


sync_web_service = SyncWebService()
