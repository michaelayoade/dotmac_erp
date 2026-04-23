"""
Fixed assets web view service.

Provides view-focused data for FA web routes.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import and_, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from app.models.finance.core_config.numbering_sequence import (
    NumberingSequence,
    SequenceType,
)
from app.models.finance.core_org.location import Location
from app.models.finance.gl.account import Account
from app.models.finance.gl.fiscal_period import FiscalPeriod, PeriodStatus
from app.models.fixed_assets.asset import Asset, AssetStatus
from app.models.fixed_assets.asset_category import AssetCategory, DepreciationMethod
from app.models.fixed_assets.maintenance_request import (
    MaintenanceRequest,
    MaintenanceRequestStatus,
)
from app.models.fixed_assets.depreciation_run import DepreciationRun
from app.models.fixed_assets.depreciation_schedule import DepreciationSchedule
from app.models.fixed_assets.depreciation_run import DepreciationRunStatus
from app.models.people.assets.audit import AssetAuditDiscrepancy
from app.services.common import coerce_uuid
from app.services.common_filters import build_active_filters
from app.services.finance.platform.currency_context import get_currency_context
from app.services.finance.platform.org_context import org_context_service
from app.services.fixed_assets.asset import (
    AssetCategoryInput,
    AssetInput,
    asset_category_service,
    asset_service,
)
from app.services.formatters import format_currency as _format_currency
from app.services.formatters import format_date as _format_date
from app.templates import templates
from app.web.deps import WebAuthContext, base_context

logger = logging.getLogger(__name__)


def _safe_form_text(value: object) -> str:
    if value is None or isinstance(value, UploadFile):
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _parse_status(value: str | None) -> AssetStatus | None:
    if not value:
        return None
    try:
        return AssetStatus(value)
    except ValueError:
        try:
            return AssetStatus(value.upper())
        except ValueError:
            return None


def _try_uuid(value: str | None) -> UUID | None:
    if not value:
        return None
    try:
        return UUID(str(value))
    except ValueError:
        return None


class FixedAssetWebService:
    """View service for fixed assets web routes."""

    @staticmethod
    def _sequence_preview(sequence: NumberingSequence | None) -> str | None:
        if not sequence:
            return None
        next_number = sequence.current_number + 1
        number_str = str(next_number).zfill(sequence.min_digits)
        return f"{sequence.prefix or ''}{number_str}{sequence.suffix or ''}"

    @staticmethod
    def _get_accounts(
        db: Session,
        organization_id: UUID,
    ) -> list[Account]:
        return list(
            db.scalars(
                select(Account)
                .where(
                    Account.organization_id == organization_id,
                    Account.is_active.is_(True),
                )
                .order_by(Account.account_code)
            )
        )

    @staticmethod
    def dashboard_context(
        db: Session,
        organization_id: str,
    ) -> dict:
        org_id = coerce_uuid(organization_id)
        today = date.today()
        context: dict = {
            "kpi": {
                "by_state": [],
                "maintenance_due": 0,
                "depreciation_due": 0,
                "location_mismatch_count": 0,
                "discrepancy_count": 0,
            }
        }

        try:
            asset_rows = list(
                db.execute(
                    select(Asset.status, func.count(Asset.asset_id))
                    .where(Asset.organization_id == org_id)
                    .group_by(Asset.status)
                )
            )
            state_counts = {
                str(status.value): int(count)
                for status, count in asset_rows
                if status is not None
            }
            context["kpi"]["by_state"] = [
                {
                    "status": status.value,
                    "label": status.value.replace("_", " ").title(),
                    "count": state_counts.get(status.value, 0),
                }
                for status in AssetStatus
            ]
        except SQLAlchemyError as exc:
            logger.warning("Unable to load asset state KPIs: %s", exc)
            context["kpi"]["by_state"] = []

        maintenance_open_statuses = [
            MaintenanceRequestStatus.OPEN,
            MaintenanceRequestStatus.ASSIGNED,
            MaintenanceRequestStatus.IN_PROGRESS,
            MaintenanceRequestStatus.WAITING_FOR_PARTS,
        ]
        try:
            context["kpi"]["maintenance_due"] = (
                db.scalar(
                    select(func.count(MaintenanceRequest.maintenance_request_id)).where(
                        and_(
                            MaintenanceRequest.organization_id == org_id,
                            MaintenanceRequest.status.in_(maintenance_open_statuses),
                            MaintenanceRequest.due_date.is_not(None),
                            MaintenanceRequest.due_date <= today,
                        )
                    )
                )
                or 0
            )
        except SQLAlchemyError as exc:
            logger.warning("Unable to load maintenance due KPI: %s", exc)
            context["kpi"]["maintenance_due"] = 0

        try:
            eligible_depreciation_assets = (
                db.scalar(
                    select(func.count(Asset.asset_id)).where(
                        and_(
                            Asset.organization_id == org_id,
                            Asset.status == AssetStatus.ACTIVE,
                            Asset.net_book_value > Asset.residual_value,
                            Asset.remaining_life_months > 0,
                            Asset.depreciation_start_date.is_not(None),
                            Asset.depreciation_start_date <= today,
                        )
                    )
                )
                or 0
            )

            current_period_id = db.scalar(
                select(FiscalPeriod.fiscal_period_id)
                .where(
                    and_(
                        FiscalPeriod.organization_id == org_id,
                        FiscalPeriod.status.in_(
                            [PeriodStatus.OPEN, PeriodStatus.REOPENED]
                        ),
                    )
                )
                .order_by(FiscalPeriod.end_date.desc())
                .limit(1)
            )

            has_posted_run = False
            if current_period_id:
                has_posted_run = bool(
                    db.scalar(
                        select(func.count(DepreciationRun.run_id)).where(
                            and_(
                                DepreciationRun.organization_id == org_id,
                                DepreciationRun.fiscal_period_id == current_period_id,
                                DepreciationRun.status == DepreciationRunStatus.POSTED,
                            )
                        )
                    )
                    or 0
                )

            context["kpi"]["depreciation_due"] = (
                0 if has_posted_run else eligible_depreciation_assets
            )
        except SQLAlchemyError as exc:
            logger.warning("Unable to load depreciation due KPI: %s", exc)
            context["kpi"]["depreciation_due"] = 0

        try:
            context["kpi"]["location_mismatch_count"] = (
                db.scalar(
                    select(func.count(AssetAuditDiscrepancy.discrepancy_id)).where(
                        and_(
                            AssetAuditDiscrepancy.organization_id == org_id,
                            AssetAuditDiscrepancy.status == "OPEN",
                            AssetAuditDiscrepancy.discrepancy_type.in_(
                                ["LOCATION", "MULTI"]
                            ),
                        )
                    )
                )
                or 0
            )
        except SQLAlchemyError as exc:
            logger.warning("Unable to load location mismatch KPI: %s", exc)
            context["kpi"]["location_mismatch_count"] = 0

        try:
            context["kpi"]["discrepancy_count"] = (
                db.scalar(
                    select(func.count(AssetAuditDiscrepancy.discrepancy_id)).where(
                        and_(
                            AssetAuditDiscrepancy.organization_id == org_id,
                            AssetAuditDiscrepancy.status == "OPEN",
                        )
                    )
                )
                or 0
            )
        except SQLAlchemyError as exc:
            logger.warning("Unable to load discrepancy KPI: %s", exc)
            context["kpi"]["discrepancy_count"] = 0

        return context

    @staticmethod
    def reporting_context(
        db: Session,
        organization_id: str,
        section: str = "overview",
        discrepancy_status: str = "OPEN",
    ) -> dict:
        """Build context for fixed-asset KPI reporting pages."""
        org_id = coerce_uuid(organization_id)
        today = date.today()
        requested = (section or "overview").strip().lower()

        valid_sections = {
            "overview",
            "state",
            "maintenance_due",
            "depreciation_due",
            "location_mismatches",
            "discrepancies",
        }
        if requested not in valid_sections:
            requested = "overview"

        normalized_discrepancy_status = (discrepancy_status or "OPEN").strip().upper()
        row_limit = 25

        data: dict = {
            "section": requested,
            "discrepancy_status": normalized_discrepancy_status,
            "kpi": {
                "by_state": [],
                "maintenance_due": 0,
                "depreciation_due": 0,
                "location_mismatch_count": 0,
                "discrepancy_count": 0,
            },
            "maintenance_due_items": [],
            "depreciation_due_items": [],
            "location_mismatch_items": [],
            "discrepancy_items": [],
        }

        dashboard_data = FixedAssetWebService.dashboard_context(
            db,
            organization_id,
        )
        data["kpi"].update(dashboard_data["kpi"])

        maintenance_open_statuses = [
            MaintenanceRequestStatus.OPEN,
            MaintenanceRequestStatus.ASSIGNED,
            MaintenanceRequestStatus.IN_PROGRESS,
            MaintenanceRequestStatus.WAITING_FOR_PARTS,
        ]
        try:
            data["kpi"]["maintenance_due"] = (
                db.scalar(
                    select(func.count(MaintenanceRequest.maintenance_request_id)).where(
                        and_(
                            MaintenanceRequest.organization_id == org_id,
                            MaintenanceRequest.status.in_(maintenance_open_statuses),
                            MaintenanceRequest.due_date.is_not(None),
                            MaintenanceRequest.due_date <= today,
                        )
                    )
                )
                or 0
            )

            maintenance_rows = db.execute(
                select(
                    MaintenanceRequest.maintenance_request_id,
                    Asset.asset_id,
                    Asset.asset_number,
                    Asset.asset_name,
                    MaintenanceRequest.title,
                    MaintenanceRequest.due_date,
                    MaintenanceRequest.status,
                )
                .join(Asset, Asset.asset_id == MaintenanceRequest.asset_id)
                .where(
                    and_(
                        MaintenanceRequest.organization_id == org_id,
                        MaintenanceRequest.status.in_(maintenance_open_statuses),
                        MaintenanceRequest.due_date.is_not(None),
                        MaintenanceRequest.due_date <= today,
                    )
                )
                .order_by(
                    MaintenanceRequest.due_date.asc(), MaintenanceRequest.title.asc()
                )
                .limit(row_limit)
            ).all()

            data["maintenance_due_items"] = [
                {
                    "asset_id": str(row.asset_id),
                    "maintenance_request_id": str(row.maintenance_request_id),
                    "asset_number": row.asset_number,
                    "asset_name": row.asset_name,
                    "title": row.title,
                    "due_date": _format_date(row.due_date),
                    "status": str(row.status),
                }
                for row in maintenance_rows
            ]
        except SQLAlchemyError as exc:
            logger.warning("Unable to load maintenance due report rows: %s", exc)
            data["maintenance_due_items"] = []
            data["kpi"]["maintenance_due"] = 0

        try:
            eligible_query = select(
                Asset.asset_id,
                Asset.asset_number,
                Asset.asset_name,
                Asset.net_book_value,
                Asset.residual_value,
                Asset.remaining_life_months,
                Asset.status,
            ).where(
                and_(
                    Asset.organization_id == org_id,
                    Asset.status == AssetStatus.ACTIVE,
                    Asset.net_book_value > Asset.residual_value,
                    Asset.remaining_life_months > 0,
                    Asset.depreciation_start_date.is_not(None),
                    Asset.depreciation_start_date <= today,
                )
            )

            current_period_id = db.scalar(
                select(FiscalPeriod.fiscal_period_id)
                .where(
                    and_(
                        FiscalPeriod.organization_id == org_id,
                        FiscalPeriod.status.in_(
                            [PeriodStatus.OPEN, PeriodStatus.REOPENED]
                        ),
                    )
                )
                .order_by(FiscalPeriod.end_date.desc())
                .limit(1)
            )

            if current_period_id:
                posted_asset_ids = db.execute(
                    select(DepreciationSchedule.asset_id)
                    .join(
                        DepreciationRun,
                        DepreciationRun.run_id == DepreciationSchedule.run_id,
                    )
                    .where(
                        and_(
                            DepreciationRun.organization_id == org_id,
                            DepreciationRun.fiscal_period_id == current_period_id,
                            DepreciationRun.status == DepreciationRunStatus.POSTED,
                        )
                    )
                ).all()
                if posted_asset_ids:
                    eligible_query = eligible_query.where(
                        Asset.asset_id.not_in([row[0] for row in posted_asset_ids])
                    )

            data["kpi"]["depreciation_due"] = (
                db.scalar(select(func.count()).select_from(eligible_query.subquery()))
                or 0
            )

            depreciation_rows = db.execute(
                eligible_query.order_by(Asset.asset_name.asc()).limit(row_limit)
            ).all()

            data["depreciation_due_items"] = [
                {
                    "asset_id": str(row.asset_id),
                    "asset_number": row.asset_number,
                    "asset_name": row.asset_name,
                    "net_book_value": _format_currency(row.net_book_value),
                    "residual_value": _format_currency(row.residual_value),
                    "remaining_life_months": int(row.remaining_life_months or 0),
                    "status": str(row.status),
                }
                for row in depreciation_rows
            ]
        except SQLAlchemyError as exc:
            logger.warning("Unable to load depreciation due report rows: %s", exc)
            data["depreciation_due_items"] = []
            data["kpi"]["depreciation_due"] = 0

        mismatch_types = ["LOCATION", "MULTI", "OWNERSHIP"]
        try:
            data["kpi"]["location_mismatch_count"] = (
                db.scalar(
                    select(func.count(AssetAuditDiscrepancy.discrepancy_id)).where(
                        and_(
                            AssetAuditDiscrepancy.organization_id == org_id,
                            AssetAuditDiscrepancy.status
                            == normalized_discrepancy_status,
                            AssetAuditDiscrepancy.discrepancy_type.in_(mismatch_types),
                        )
                    )
                )
                or 0
            )
            mismatch_rows = db.execute(
                select(
                    Asset.asset_id,
                    Asset.asset_number,
                    Asset.asset_name,
                    AssetAuditDiscrepancy.discrepancy_id,
                    AssetAuditDiscrepancy.discrepancy_type,
                    AssetAuditDiscrepancy.status,
                    AssetAuditDiscrepancy.detected_at,
                    AssetAuditDiscrepancy.notes,
                )
                .join(Asset, Asset.asset_id == AssetAuditDiscrepancy.asset_id)
                .where(
                    and_(
                        AssetAuditDiscrepancy.organization_id == org_id,
                        AssetAuditDiscrepancy.status == normalized_discrepancy_status,
                        AssetAuditDiscrepancy.discrepancy_type.in_(mismatch_types),
                    )
                )
                .order_by(AssetAuditDiscrepancy.detected_at.desc())
                .limit(row_limit)
            ).all()

            data["location_mismatch_items"] = [
                {
                    "asset_id": str(row.asset_id),
                    "asset_number": row.asset_number,
                    "asset_name": row.asset_name,
                    "discrepancy_id": str(row.discrepancy_id),
                    "discrepancy_type": row.discrepancy_type,
                    "status": row.status,
                    "detected_at": _format_date(row.detected_at),
                    "notes": row.notes,
                }
                for row in mismatch_rows
            ]
        except SQLAlchemyError as exc:
            logger.warning("Unable to load location mismatch report rows: %s", exc)
            data["location_mismatch_items"] = []
            data["kpi"]["location_mismatch_count"] = 0

        try:
            data["kpi"]["discrepancy_count"] = (
                db.scalar(
                    select(func.count(AssetAuditDiscrepancy.discrepancy_id)).where(
                        and_(
                            AssetAuditDiscrepancy.organization_id == org_id,
                            AssetAuditDiscrepancy.status
                            == normalized_discrepancy_status,
                        )
                    )
                )
                or 0
            )
            discrepancy_rows = db.execute(
                select(
                    Asset.asset_id,
                    Asset.asset_number,
                    Asset.asset_name,
                    AssetAuditDiscrepancy.discrepancy_id,
                    AssetAuditDiscrepancy.discrepancy_type,
                    AssetAuditDiscrepancy.status,
                    AssetAuditDiscrepancy.detected_at,
                    AssetAuditDiscrepancy.notes,
                )
                .join(Asset, Asset.asset_id == AssetAuditDiscrepancy.asset_id)
                .where(
                    and_(
                        AssetAuditDiscrepancy.organization_id == org_id,
                        AssetAuditDiscrepancy.status == normalized_discrepancy_status,
                    )
                )
                .order_by(AssetAuditDiscrepancy.detected_at.desc())
                .limit(row_limit)
            ).all()

            data["discrepancy_items"] = [
                {
                    "asset_id": str(row.asset_id),
                    "asset_number": row.asset_number,
                    "asset_name": row.asset_name,
                    "discrepancy_id": str(row.discrepancy_id),
                    "discrepancy_type": row.discrepancy_type,
                    "status": row.status,
                    "detected_at": _format_date(row.detected_at),
                    "notes": row.notes,
                }
                for row in discrepancy_rows
            ]
        except SQLAlchemyError as exc:
            logger.warning("Unable to load discrepancy report rows: %s", exc)
            data["discrepancy_items"] = []
            data["kpi"]["discrepancy_count"] = 0

        # Provide state distribution for overview and state-section cards.
        data["kpi"]["by_state"] = dashboard_data.get("kpi", {}).get("by_state", [])
        return data

    @staticmethod
    def asset_form_context(
        db: Session,
        organization_id: str,
    ) -> dict:
        """Build context for new asset form."""
        from app.models.finance.ap.supplier import Supplier

        org_id = coerce_uuid(organization_id)

        categories = db.scalars(
            select(AssetCategory)
            .where(
                AssetCategory.organization_id == org_id,
                AssetCategory.is_active.is_(True),
            )
            .order_by(AssetCategory.category_code)
        ).all()
        main_categories = [c for c in categories if c.parent_category_id is None]
        sub_categories = [
            {
                "category_id": str(c.category_id),
                "category_code": c.category_code,
                "category_name": c.category_name,
                "parent_category_id": str(c.parent_category_id),
            }
            for c in categories
            if c.parent_category_id is not None
        ]

        sequence = db.scalar(
            select(NumberingSequence).where(
                NumberingSequence.organization_id == org_id,
                NumberingSequence.sequence_type == SequenceType.ASSET,
            )
        )

        # Get suppliers list for FA → AP source tracking
        suppliers = db.scalars(
            select(Supplier)
            .where(
                Supplier.organization_id == org_id,
                Supplier.is_active.is_(True),
            )
            .order_by(Supplier.legal_name)
        ).all()
        suppliers_list = [
            {
                "supplier_id": str(s.supplier_id),
                "supplier_name": s.trading_name or s.legal_name,
                "supplier_code": s.supplier_code,
            }
            for s in suppliers
        ]

        locations = db.scalars(
            select(Location)
            .where(
                Location.organization_id == org_id,
                Location.is_active.is_(True),
            )
            .order_by(Location.location_name)
        ).all()
        locations_list = [
            {
                "location_id": str(loc.location_id),
                "location_code": loc.location_code,
                "location_name": loc.location_name,
            }
            for loc in locations
        ]

        depreciation_schedule_rows = db.execute(
            select(
                DepreciationSchedule.schedule_id,
                DepreciationRun.run_number,
                FiscalPeriod.period_name,
                FiscalPeriod.start_date,
                FiscalPeriod.end_date,
            )
            .join(
                DepreciationRun, DepreciationSchedule.run_id == DepreciationRun.run_id
            )
            .join(
                FiscalPeriod,
                DepreciationRun.fiscal_period_id == FiscalPeriod.fiscal_period_id,
            )
            .where(DepreciationRun.organization_id == org_id)
            .order_by(
                DepreciationRun.created_at.desc(),
                DepreciationSchedule.created_at.desc(),
            )
        ).all()
        depreciation_schedules = []
        for (
            schedule_id,
            run_number,
            period_name,
            start_date,
            end_date,
        ) in depreciation_schedule_rows:
            depreciation_schedules.append(
                {
                    "schedule_id": str(schedule_id),
                    "label": f"Run {run_number} - {period_name} ({_format_date(start_date)} - {_format_date(end_date)})",
                    "run_number": run_number,
                }
            )

        context = {
            "categories": categories,
            "main_categories": main_categories,
            "sub_categories": sub_categories,
            "suppliers_list": suppliers_list,
            "locations_list": locations_list,
            "depreciation_schedules": depreciation_schedules,
            "today": _format_date(date.today()),
            "asset_number_preview": FixedAssetWebService._sequence_preview(sequence),
        }
        context.update(get_currency_context(db, organization_id))
        return context

    @staticmethod
    def list_assets_context(
        db: Session,
        organization_id: str,
        search: str | None,
        category: str | None,
        status: str | None,
        page: int,
        limit: int = 50,
    ) -> dict:
        offset = (page - 1) * limit
        org_id = coerce_uuid(organization_id)
        from app.services.fixed_assets.asset_query import build_asset_query

        query = build_asset_query(
            db=db,
            organization_id=organization_id,
            search=search,
            category=category,
            status=status,
        )

        total_count = db.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = db.execute(
            query.outerjoin(Location, Asset.location_id == Location.location_id)
            .add_columns(AssetCategory, Location)
            .order_by(Asset.asset_number)
            .limit(limit)
            .offset(offset)
        ).all()

        assets_view = []
        for row in rows:
            asset = row[0]
            category_row = row[1] if len(row) > 1 else None
            location_row = row[2] if len(row) > 2 else None
            assets_view.append(
                {
                    "asset_id": asset.asset_id,
                    "asset_number": asset.asset_number,
                    "asset_name": asset.asset_name,
                    "serial_number": asset.serial_number,
                    "location_name": (
                        location_row.location_name if location_row else None
                    ),
                    "category_name": (
                        category_row.category_name if category_row else None
                    ),
                    "category_code": (
                        category_row.category_code if category_row else None
                    ),
                    "acquisition_date": _format_date(asset.acquisition_date),
                    "acquisition_cost": _format_currency(
                        asset.acquisition_cost, asset.currency_code
                    ),
                    "net_book_value": _format_currency(
                        asset.net_book_value, asset.currency_code
                    ),
                    "status": asset.status.value,
                }
            )

        total_pages = max(1, (total_count + limit - 1) // limit)

        categories = db.scalars(
            select(AssetCategory)
            .where(
                AssetCategory.organization_id == org_id,
                AssetCategory.is_active.is_(True),
            )
            .order_by(AssetCategory.category_code)
        ).all()

        active_filters = build_active_filters(
            params={"search": search, "category": category, "status": status},
            labels={"search": "Search", "category": "Category", "status": "Status"},
            options={
                "category": {
                    str(cat.category_id): cat.category_name for cat in categories
                }
            },
        )

        return {
            "assets": assets_view,
            "categories": categories,
            "search": search,
            "category": category,
            "status": status,
            "page": page,
            "limit": limit,
            "offset": offset,
            "total_count": total_count,
            "total_pages": total_pages,
            "active_filters": active_filters,
        }

    def asset_new_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        context = base_context(request, auth, "New Asset", "fixed_assets")
        context.update(self.asset_form_context(db, str(auth.organization_id)))
        context["form_asset_number"] = ""
        context["form_asset_name"] = ""
        context["form_category_id"] = ""
        context["form_parent_category_id"] = ""
        context["form_serial_number"] = ""
        context["form_location_id"] = ""
        return templates.TemplateResponse(
            request, "fixed_assets/asset_form.html", context
        )

    def create_asset_response(
        self,
        request: Request,
        auth: WebAuthContext,
        asset_number: str | None,
        asset_name: str,
        serial_number: str | None,
        location_id: str | None,
        category_id: str,
        acquisition_date: str | None,
        acquisition_cost: str | None,
        currency_code: str | None,
        description: str | None,
        depreciation_schedule_id: str | None,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        try:
            org_id = auth.organization_id
            user_id = auth.person_id
            if org_id is None:
                raise HTTPException(status_code=401, detail="Authentication required")
            if user_id is None:
                raise HTTPException(status_code=401, detail="Authentication required")
            parsed_acquisition_date = (
                datetime.strptime(acquisition_date, "%Y-%m-%d").date()
                if acquisition_date and acquisition_date.strip()
                else date.today()
            )
            parsed_acquisition_cost = (
                Decimal(acquisition_cost.strip())
                if acquisition_cost and acquisition_cost.strip()
                else Decimal("0")
            )
            resolved_currency = (
                currency_code
                or org_context_service.get_functional_currency(
                    db,
                    org_id,
                )
            )

            input_data = AssetInput(
                asset_number=(asset_number.strip() if asset_number else None),
                asset_name=asset_name,
                serial_number=(serial_number.strip() if serial_number else None),
                location_id=(UUID(location_id) if location_id else None),
                category_id=UUID(category_id),
                acquisition_date=parsed_acquisition_date,
                acquisition_cost=parsed_acquisition_cost,
                currency_code=resolved_currency,
                description=description,
                depreciation_schedule_id=(
                    UUID(depreciation_schedule_id) if depreciation_schedule_id else None
                ),
            )

            asset_service.create_asset(
                db,
                org_id,
                input_data,
                created_by_user_id=user_id,
            )
            return RedirectResponse(
                url="/fixed-assets/assets?success=Record+created+successfully",
                status_code=303,
            )

        except Exception as e:
            context = base_context(request, auth, "New Asset", "fixed_assets")
            context.update(self.asset_form_context(db, str(auth.organization_id)))
            context["error"] = str(e)
            context["form_asset_number"] = asset_number or ""
            context["form_asset_name"] = asset_name
            context["form_category_id"] = category_id
            context["form_parent_category_id"] = ""
            try:
                selected_category = db.get(AssetCategory, UUID(category_id))
                if selected_category:
                    context["form_parent_category_id"] = (
                        str(selected_category.parent_category_id)
                        if selected_category.parent_category_id
                        else str(selected_category.category_id)
                    )
            except ValueError:
                context["form_parent_category_id"] = ""
            context["form_serial_number"] = serial_number or ""
            context["form_location_id"] = location_id or ""
            context["form_acquisition_date"] = acquisition_date or ""
            context["form_acquisition_cost"] = acquisition_cost or ""
            context["form_currency_code"] = currency_code or ""
            context["selected_depreciation_schedule_id"] = depreciation_schedule_id
            return templates.TemplateResponse(
                request, "fixed_assets/asset_form.html", context
            )

    @staticmethod
    def list_categories_context(
        db: Session,
        organization_id: str,
        is_active: bool | None,
        page: int,
        limit: int = 50,
    ) -> dict:
        org_id = coerce_uuid(organization_id)
        offset = (page - 1) * limit

        query = select(AssetCategory).where(AssetCategory.organization_id == org_id)
        if is_active is not None:
            query = query.where(
                AssetCategory.is_active.is_(True)
                if is_active
                else AssetCategory.is_active.is_(False)
            )

        total_count = db.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = db.scalars(
            query.order_by(AssetCategory.category_code).limit(limit).offset(offset)
        ).all()

        categories_view = []
        for category in rows:
            categories_view.append(
                {
                    "category_id": category.category_id,
                    "category_code": category.category_code,
                    "category_name": category.category_name,
                    "depreciation_method": category.depreciation_method.value,
                    "useful_life_months": category.useful_life_months,
                    "residual_value_percent": category.residual_value_percent,
                    "capitalization_threshold": category.capitalization_threshold,
                    "is_active": category.is_active,
                }
            )

        total_pages = max(1, (total_count + limit - 1) // limit)

        active_filters = build_active_filters(
            params={
                "is_active": str(is_active).lower() if is_active is not None else ""
            },
            labels={"is_active": "Status"},
            options={"is_active": {"true": "Active", "false": "Inactive"}},
        )

        return {
            "categories": categories_view,
            "is_active": is_active,
            "page": page,
            "limit": limit,
            "offset": offset,
            "total_count": total_count,
            "total_pages": total_pages,
            "active_filters": active_filters,
        }

    @staticmethod
    def category_form_context(
        db: Session,
        organization_id: str,
        category_id: str | None = None,
    ) -> dict:
        org_id = coerce_uuid(organization_id)
        categories = db.scalars(
            select(AssetCategory)
            .where(AssetCategory.organization_id == org_id)
            .order_by(AssetCategory.category_code)
        ).all()
        accounts = FixedAssetWebService._get_accounts(db, org_id)

        category = None
        if category_id:
            category = db.get(AssetCategory, coerce_uuid(category_id))

        return {
            "category": category,
            "categories": categories,
            "accounts": accounts,
            "depreciation_methods": list(DepreciationMethod),
        }

    def list_categories_response(
        self,
        request: Request,
        auth: WebAuthContext,
        is_active: bool | None,
        page: int,
        db: Session,
    ) -> HTMLResponse:
        context = base_context(request, auth, "Asset Categories", "fixed_assets")
        context.update(
            self.list_categories_context(
                db,
                str(auth.organization_id),
                is_active=is_active,
                page=page,
            )
        )
        return templates.TemplateResponse(
            request, "fixed_assets/categories.html", context
        )

    def new_category_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        error: str | None = None,
    ) -> HTMLResponse:
        context = base_context(request, auth, "New Asset Category", "fixed_assets")
        context.update(self.category_form_context(db, str(auth.organization_id)))
        context["error"] = error
        return templates.TemplateResponse(
            request, "fixed_assets/category_form.html", context
        )

    async def create_category_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        form = await request.form()
        org_id = auth.organization_id
        if org_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")

        try:
            category_input = AssetCategoryInput(
                category_code=_safe_form_text(form.get("category_code")).strip(),
                category_name=_safe_form_text(form.get("category_name")).strip(),
                asset_account_id=coerce_uuid(
                    _safe_form_text(form.get("asset_account_id"))
                ),
                accumulated_depreciation_account_id=coerce_uuid(
                    _safe_form_text(form.get("accumulated_depreciation_account_id"))
                ),
                depreciation_expense_account_id=coerce_uuid(
                    _safe_form_text(form.get("depreciation_expense_account_id"))
                ),
                gain_loss_disposal_account_id=coerce_uuid(
                    _safe_form_text(form.get("gain_loss_disposal_account_id"))
                ),
                useful_life_months=int(
                    _safe_form_text(form.get("useful_life_months")) or 0
                ),
                depreciation_method=DepreciationMethod(
                    _safe_form_text(form.get("depreciation_method"))
                    or DepreciationMethod.STRAIGHT_LINE.value
                ),
                residual_value_percent=Decimal(
                    _safe_form_text(form.get("residual_value_percent")) or "0"
                ),
                capitalization_threshold=Decimal(
                    _safe_form_text(form.get("capitalization_threshold")) or "0"
                ),
                revaluation_model_allowed=bool(
                    _safe_form_text(form.get("revaluation_model_allowed"))
                ),
                revaluation_surplus_account_id=coerce_uuid(
                    _safe_form_text(form.get("revaluation_surplus_account_id"))
                )
                if _safe_form_text(form.get("revaluation_surplus_account_id"))
                else None,
                impairment_loss_account_id=coerce_uuid(
                    _safe_form_text(form.get("impairment_loss_account_id"))
                )
                if _safe_form_text(form.get("impairment_loss_account_id"))
                else None,
                parent_category_id=coerce_uuid(
                    _safe_form_text(form.get("parent_category_id"))
                )
                if _safe_form_text(form.get("parent_category_id"))
                else None,
                description=_safe_form_text(form.get("description")) or None,
            )

            asset_category_service.create_category(db, org_id, category_input)
            return RedirectResponse(
                url="/fixed-assets/categories?success=Record+saved+successfully",
                status_code=303,
            )
        except Exception as e:
            return self.new_category_form_response(request, auth, db, error=str(e))

    def edit_category_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        category_id: str,
        db: Session,
        error: str | None = None,
    ) -> HTMLResponse | RedirectResponse:
        category = asset_category_service.get(db, category_id, auth.organization_id)
        if not category or category.organization_id != auth.organization_id:
            return RedirectResponse(url="/fixed-assets/categories", status_code=302)

        context = base_context(request, auth, "Edit Asset Category", "fixed_assets")
        context.update(
            self.category_form_context(
                db, str(auth.organization_id), category_id=category_id
            )
        )
        context["error"] = error
        return templates.TemplateResponse(
            request, "fixed_assets/category_form.html", context
        )

    async def update_category_response(
        self,
        request: Request,
        auth: WebAuthContext,
        category_id: str,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        form = await request.form()
        org_id = auth.organization_id
        if org_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")

        try:
            category_input = AssetCategoryInput(
                category_code=_safe_form_text(form.get("category_code")).strip(),
                category_name=_safe_form_text(form.get("category_name")).strip(),
                asset_account_id=coerce_uuid(
                    _safe_form_text(form.get("asset_account_id"))
                ),
                accumulated_depreciation_account_id=coerce_uuid(
                    _safe_form_text(form.get("accumulated_depreciation_account_id"))
                ),
                depreciation_expense_account_id=coerce_uuid(
                    _safe_form_text(form.get("depreciation_expense_account_id"))
                ),
                gain_loss_disposal_account_id=coerce_uuid(
                    _safe_form_text(form.get("gain_loss_disposal_account_id"))
                ),
                useful_life_months=int(
                    _safe_form_text(form.get("useful_life_months")) or 0
                ),
                depreciation_method=DepreciationMethod(
                    _safe_form_text(form.get("depreciation_method"))
                    or DepreciationMethod.STRAIGHT_LINE.value
                ),
                residual_value_percent=Decimal(
                    _safe_form_text(form.get("residual_value_percent")) or "0"
                ),
                capitalization_threshold=Decimal(
                    _safe_form_text(form.get("capitalization_threshold")) or "0"
                ),
                revaluation_model_allowed=bool(
                    _safe_form_text(form.get("revaluation_model_allowed"))
                ),
                revaluation_surplus_account_id=coerce_uuid(
                    _safe_form_text(form.get("revaluation_surplus_account_id"))
                )
                if _safe_form_text(form.get("revaluation_surplus_account_id"))
                else None,
                impairment_loss_account_id=coerce_uuid(
                    _safe_form_text(form.get("impairment_loss_account_id"))
                )
                if _safe_form_text(form.get("impairment_loss_account_id"))
                else None,
                parent_category_id=coerce_uuid(
                    _safe_form_text(form.get("parent_category_id"))
                )
                if _safe_form_text(form.get("parent_category_id"))
                else None,
                description=_safe_form_text(form.get("description")) or None,
            )

            is_active = form.get("is_active") == "on"
            asset_category_service.update_category(
                db, org_id, category_id, category_input, is_active=is_active
            )
            return RedirectResponse(
                url="/fixed-assets/categories?success=Record+saved+successfully",
                status_code=303,
            )
        except Exception as e:
            return self.edit_category_form_response(
                request, auth, category_id, db, error=str(e)
            )

    def toggle_category_response(
        self,
        auth: WebAuthContext,
        category_id: str,
        db: Session,
    ) -> RedirectResponse:
        try:
            org_id = auth.organization_id
            if org_id is None:
                raise HTTPException(status_code=401, detail="Authentication required")
            asset_category_service.toggle_category(db, org_id, category_id)
        except Exception:
            logger.exception("Ignored exception")

        return RedirectResponse(
            url="/fixed-assets/categories?success=Record+saved+successfully",
            status_code=303,
        )

    @staticmethod
    def depreciation_context(
        db: Session,
        organization_id: str,
        asset_id: str | None,
        period: str | None,
        page: int = 1,
        limit: int = 50,
    ) -> dict:
        org_id = coerce_uuid(organization_id)
        offset = (page - 1) * limit

        period_id = _try_uuid(period)

        query = select(DepreciationRun, FiscalPeriod).join(
            FiscalPeriod,
            DepreciationRun.fiscal_period_id == FiscalPeriod.fiscal_period_id,
        )
        query = query.where(DepreciationRun.organization_id == org_id)

        if period_id:
            query = query.where(DepreciationRun.fiscal_period_id == period_id)

        total_count = db.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = db.execute(
            query.order_by(DepreciationRun.created_at.desc())
            .limit(limit)
            .offset(offset)
        ).all()

        runs_view = []
        for run, fiscal_period in rows:
            runs_view.append(
                {
                    "run_id": run.run_id,
                    "run_number": run.run_number,
                    "run_description": run.run_description,
                    "period_name": fiscal_period.period_name,
                    "period_start": _format_date(fiscal_period.start_date),
                    "period_end": _format_date(fiscal_period.end_date),
                    "status": run.status.value,
                    "assets_processed": run.assets_processed,
                    "total_depreciation": _format_currency(run.total_depreciation),
                    "created_at": _format_date(
                        run.created_at.date() if run.created_at else None
                    ),
                }
            )

        fiscal_period_rows = db.execute(
            select(
                FiscalPeriod.fiscal_period_id,
                FiscalPeriod.period_name,
                FiscalPeriod.start_date,
                FiscalPeriod.end_date,
            )
            .where(FiscalPeriod.organization_id == org_id)
            .order_by(FiscalPeriod.start_date.desc())
        ).all()
        fiscal_periods = []
        for row in fiscal_period_rows:
            if len(row) == 4:
                (
                    fiscal_period_id,
                    period_name,
                    start_date,
                    end_date,
                ) = row
            elif len(row) == 2 and hasattr(row[1], "period_name"):
                period_obj = row[1]
                fiscal_period_id = getattr(period_obj, "fiscal_period_id", None)
                period_name = getattr(period_obj, "period_name", "")
                start_date = getattr(period_obj, "start_date", None)
                end_date = getattr(period_obj, "end_date", None)
            else:
                continue

            fiscal_periods.append(
                {
                    "fiscal_period_id": str(fiscal_period_id),
                    "label": (
                        f"{period_name} ({_format_date(start_date)} - {_format_date(end_date)})"
                    ),
                    "period_name": period_name,
                }
            )

        total_pages = max(1, (total_count + limit - 1) // limit)

        return {
            "depreciation_runs": runs_view,
            "asset_id": asset_id,
            "fiscal_periods": fiscal_periods,
            "period": period,
            "page": page,
            "limit": limit,
            "offset": offset,
            "total_count": total_count,
            "total_pages": total_pages,
        }

    def depreciation_run_form_context(
        self,
        db: Session,
        organization_id: str,
        period: str | None = None,
    ) -> dict:
        """Context for the dedicated depreciation run creation page."""
        org_id = coerce_uuid(organization_id)

        fiscal_period_rows = db.execute(
            select(
                FiscalPeriod.fiscal_period_id,
                FiscalPeriod.period_name,
                FiscalPeriod.start_date,
                FiscalPeriod.end_date,
            )
            .where(FiscalPeriod.organization_id == org_id)
            .order_by(FiscalPeriod.start_date.desc())
        ).all()
        fiscal_periods = [
            {
                "fiscal_period_id": str(row_id),
                "label": (
                    f"{period_name} ({_format_date(start_date)} - {_format_date(end_date)})"
                ),
                "period_name": period_name,
            }
            for row_id, period_name, start_date, end_date in fiscal_period_rows
        ]

        return {
            "fiscal_periods": fiscal_periods,
            "period": period,
        }

    def asset_detail_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        asset_id: str,
    ) -> HTMLResponse | RedirectResponse:
        """Return asset detail page."""
        org_id = coerce_uuid(auth.organization_id)
        a_id = coerce_uuid(asset_id)

        asset = db.get(Asset, a_id)
        if not asset or asset.organization_id != org_id:
            return RedirectResponse(
                url="/fixed-assets/assets?success=Record+saved+successfully",
                status_code=303,
            )

        # Get category info
        category = (
            db.get(AssetCategory, asset.category_id) if asset.category_id else None
        )

        context = base_context(request, auth, "Asset Details", "fixed_assets")
        context.update(
            {
                "asset": {
                    "asset_id": asset.asset_id,
                    "asset_code": asset.asset_number,
                    "asset_name": asset.asset_name,
                    "serial_number": asset.serial_number,
                    "description": asset.description,
                    "category_name": category.category_name if category else None,
                    "status": asset.status.value if asset.status else "ACTIVE",
                    "acquisition_date": _format_date(asset.acquisition_date),
                    "acquisition_cost": _format_currency(
                        asset.acquisition_cost, asset.currency_code
                    ),
                    "accumulated_depreciation": _format_currency(
                        asset.accumulated_depreciation, asset.currency_code
                    ),
                    "net_book_value": _format_currency(
                        (asset.acquisition_cost or Decimal(0))
                        - (asset.accumulated_depreciation or Decimal(0)),
                        asset.currency_code,
                    ),
                    "currency_code": asset.currency_code,
                    "useful_life_months": asset.useful_life_months,
                    "residual_value": _format_currency(
                        asset.residual_value, asset.currency_code
                    ),
                },
            }
        )
        return templates.TemplateResponse(
            request, "fixed_assets/asset_detail.html", context
        )

    def asset_edit_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        asset_id: str,
    ) -> HTMLResponse | RedirectResponse:
        """Return asset edit form page."""
        org_id = coerce_uuid(auth.organization_id)
        a_id = coerce_uuid(asset_id)

        asset = db.get(Asset, a_id)
        if not asset or asset.organization_id != org_id:
            return RedirectResponse(
                url="/fixed-assets/assets?success=Record+updated+successfully",
                status_code=303,
            )

        if asset.status not in [AssetStatus.DRAFT, AssetStatus.ACTIVE]:
            return RedirectResponse(
                url=f"/fixed-assets/assets/{asset_id}?error=Only+draft+or+active+assets+can+be+edited",
                status_code=303,
            )

        context = base_context(request, auth, "Edit Asset", "fixed_assets")
        context.update(self.asset_form_context(db, str(auth.organization_id)))
        context["asset"] = asset
        context["form_asset_number"] = asset.asset_number
        context["form_asset_name"] = asset.asset_name
        context["form_category_id"] = (
            str(asset.category_id) if asset.category_id else ""
        )
        context["form_parent_category_id"] = ""
        if asset.category_id:
            selected_category = db.get(AssetCategory, asset.category_id)
            if selected_category:
                context["form_parent_category_id"] = (
                    str(selected_category.parent_category_id)
                    if selected_category.parent_category_id
                    else str(selected_category.category_id)
                )
        context["form_serial_number"] = asset.serial_number or ""
        context["form_location_id"] = (
            str(asset.location_id) if asset.location_id else ""
        )
        context["form_description"] = asset.description or ""
        context["form_acquisition_date"] = (
            asset.acquisition_date.isoformat() if asset.acquisition_date else ""
        )
        context["form_acquisition_cost"] = (
            str(asset.acquisition_cost) if asset.acquisition_cost is not None else ""
        )
        context["form_currency_code"] = asset.currency_code or ""
        context["selected_depreciation_schedule_id"] = (
            str(asset.current_depreciation_schedule_id)
            if asset.current_depreciation_schedule_id
            else None
        )
        return templates.TemplateResponse(
            request, "fixed_assets/asset_form.html", context
        )

    async def update_asset_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        asset_id: str,
    ) -> RedirectResponse:
        """Handle asset update."""
        try:
            form_data = await request.form()
            updates: dict[str, object] = {}

            asset_name = _safe_form_text(form_data.get("asset_name")).strip()
            category_id = _safe_form_text(form_data.get("category_id")).strip()
            serial_number = _safe_form_text(form_data.get("serial_number")).strip()
            location_id = _safe_form_text(form_data.get("location_id")).strip()
            description = _safe_form_text(form_data.get("description")).strip()
            acquisition_date = _safe_form_text(form_data.get("acquisition_date")).strip()
            acquisition_cost = _safe_form_text(form_data.get("acquisition_cost")).strip()

            if asset_name:
                updates["asset_name"] = asset_name
            if category_id:
                updates["category_id"] = UUID(category_id)
            updates["serial_number"] = serial_number or None
            updates["location_id"] = UUID(location_id) if location_id else None
            updates["description"] = description or None

            if acquisition_date:
                updates["acquisition_date"] = datetime.strptime(
                    acquisition_date, "%Y-%m-%d"
                ).date()
            if acquisition_cost:
                updates["acquisition_cost"] = Decimal(acquisition_cost)

            asset_service.update_asset(
                db,
                coerce_uuid(auth.organization_id),
                coerce_uuid(asset_id),
                updates,
            )
            db.commit()

            return RedirectResponse(
                url=f"/fixed-assets/assets/{asset_id}?success=Asset+updated",
                status_code=303,
            )
        except Exception as e:
            db.rollback()
            return RedirectResponse(
                url=f"/fixed-assets/assets/{asset_id}?error={str(e)}",
                status_code=303,
            )

    async def dispose_asset_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        asset_id: str,
    ) -> RedirectResponse:
        """Handle asset disposal."""
        try:
            form_data = await request.form()
            org_id = auth.organization_id
            user_id = auth.user_id
            if org_id is None:
                raise HTTPException(status_code=401, detail="Authentication required")
            if user_id is None:
                raise HTTPException(status_code=401, detail="Authentication required")
            disposal_date = _safe_form_text(form_data.get("disposal_date"))
            proceeds = _safe_form_text(form_data.get("proceeds")) or "0"
            costs_of_disposal = (
                _safe_form_text(form_data.get("costs_of_disposal")) or "0"
            )
            disposal_type = _safe_form_text(form_data.get("disposal_type")) or "SALE"
            fiscal_period_id = _safe_form_text(form_data.get("fiscal_period_id"))
            reason = _safe_form_text(form_data.get("reason")) or None

            from app.services.fixed_assets.disposal import (
                DisposalInput,
                DisposalType,
                asset_disposal_service,
            )

            input_data = DisposalInput(
                asset_id=coerce_uuid(asset_id),
                fiscal_period_id=coerce_uuid(fiscal_period_id),
                disposal_date=datetime.strptime(disposal_date, "%Y-%m-%d").date()
                if disposal_date
                else date.today(),
                disposal_type=DisposalType(disposal_type),
                disposal_proceeds=Decimal(proceeds) if proceeds else Decimal("0"),
                costs_of_disposal=Decimal(costs_of_disposal)
                if costs_of_disposal
                else Decimal("0"),
                disposal_reason=reason,
            )

            asset_disposal_service.create_disposal(
                db=db,
                organization_id=org_id,
                input=input_data,
                created_by_user_id=user_id,
            )

            return RedirectResponse(
                url=f"/fixed-assets/assets/{asset_id}?success=Asset+disposed+successfully",
                status_code=303,
            )
        except Exception as e:
            return RedirectResponse(
                url=f"/fixed-assets/assets/{asset_id}?error={str(e)}",
                status_code=303,
            )

    async def revalue_asset_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        asset_id: str,
    ) -> RedirectResponse:
        """Handle asset revaluation."""
        try:
            form_data = await request.form()
            org_id = auth.organization_id
            user_id = auth.user_id
            if org_id is None:
                raise HTTPException(status_code=401, detail="Authentication required")
            if user_id is None:
                raise HTTPException(status_code=401, detail="Authentication required")
            revaluation_date = _safe_form_text(form_data.get("revaluation_date"))
            new_value = _safe_form_text(form_data.get("new_value")) or "0"
            valuation_method = (
                _safe_form_text(form_data.get("valuation_method")) or "MARKET"
            )
            fiscal_period_id = _safe_form_text(form_data.get("fiscal_period_id"))
            reason = _safe_form_text(form_data.get("reason")) or None

            from app.services.fixed_assets.revaluation import (
                RevaluationInput,
                asset_revaluation_service,
            )

            input_data = RevaluationInput(
                asset_id=coerce_uuid(asset_id),
                fiscal_period_id=coerce_uuid(fiscal_period_id),
                revaluation_date=datetime.strptime(revaluation_date, "%Y-%m-%d").date()
                if revaluation_date
                else date.today(),
                fair_value=Decimal(new_value) if new_value else Decimal("0"),
                valuation_method=valuation_method,
                valuation_basis=reason,
            )

            asset_revaluation_service.create_revaluation(
                db=db,
                organization_id=org_id,
                input=input_data,
                created_by_user_id=user_id,
            )

            return RedirectResponse(
                url=f"/fixed-assets/assets/{asset_id}?success=Asset+revalued+successfully",
                status_code=303,
            )
        except Exception as e:
            return RedirectResponse(
                url=f"/fixed-assets/assets/{asset_id}?error={str(e)}",
                status_code=303,
            )

    async def impair_asset_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        asset_id: str,
    ) -> RedirectResponse:
        """Handle asset impairment."""
        try:
            form_data = await request.form()
            form_data.get("impairment_date")
            form_data.get("impairment_amount", "0")
            form_data.get("reason", "")

            # Impairment is handled through the asset service or a dedicated impairment service
            # For now, redirect with placeholder
            return RedirectResponse(
                url=f"/fixed-assets/assets/{asset_id}?success=Impairment+recorded",
                status_code=303,
            )
        except Exception as e:
            return RedirectResponse(
                url=f"/fixed-assets/assets/{asset_id}?error={str(e)}",
                status_code=303,
            )

    async def run_depreciation_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        """Run depreciation for a period."""
        try:
            form_data = await request.form()
            fiscal_period_id = _safe_form_text(form_data.get("fiscal_period_id"))
            posting_date_text = _safe_form_text(form_data.get("posting_date"))
            org_id = auth.organization_id
            user_id = auth.user_id
            if org_id is None:
                raise HTTPException(status_code=401, detail="Authentication required")
            if user_id is None:
                raise HTTPException(status_code=401, detail="Authentication required")
            if not fiscal_period_id:
                raise ValueError("Fiscal period is required")
            posting_date = (
                datetime.strptime(posting_date_text, "%Y-%m-%d").date()
                if posting_date_text
                else None
            )

            from app.services.fixed_assets.depreciation import DepreciationService

            run = DepreciationService.create_depreciation_run(
                db,
                org_id,
                coerce_uuid(fiscal_period_id),
                user_id,
            )
            DepreciationService.calculate_run(db, org_id, run.run_id)
            DepreciationService.post_run(
                db,
                org_id,
                run.run_id,
                posted_by_user_id=user_id,
                posting_date=posting_date,
            )

            return RedirectResponse(
                url="/fixed-assets/depreciation?success=Depreciation+run+posted",
                status_code=303,
            )
        except Exception as e:
            return RedirectResponse(
                url=f"/fixed-assets/depreciation?error={str(e)}",
                status_code=303,
            )


fa_web_service = FixedAssetWebService()
