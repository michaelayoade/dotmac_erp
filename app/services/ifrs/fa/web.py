"""
Fixed assets web view service.

Provides view-focused data for FA web routes.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models.ifrs.fa.asset import Asset, AssetStatus
from app.models.ifrs.fa.asset_category import AssetCategory
from app.models.ifrs.fa.depreciation_run import DepreciationRun
from app.models.ifrs.gl.fiscal_period import FiscalPeriod
from app.config import settings
from app.services.common import coerce_uuid


def _format_date(value: Optional[date]) -> str:
    return value.strftime("%Y-%m-%d") if value else ""


def _format_currency(
    amount: Optional[Decimal],
    currency: str = settings.default_presentation_currency_code,
) -> str:
    if amount is None:
        return ""
    value = Decimal(str(amount))
    return f"{currency} {value:,.2f}"


def _parse_status(value: Optional[str]) -> Optional[AssetStatus]:
    if not value:
        return None
    try:
        return AssetStatus(value)
    except ValueError:
        try:
            return AssetStatus(value.upper())
        except ValueError:
            return None


def _try_uuid(value: Optional[str]) -> Optional[UUID]:
    if not value:
        return None
    try:
        return UUID(str(value))
    except ValueError:
        return None


class FixedAssetWebService:
    """View service for fixed assets web routes."""

    @staticmethod
    def list_assets_context(
        db: Session,
        organization_id: str,
        search: Optional[str],
        category: Optional[str],
        status: Optional[str],
        page: int,
        limit: int = 50,
    ) -> dict:
        org_id = coerce_uuid(organization_id)
        offset = (page - 1) * limit

        status_value = _parse_status(status)
        category_id = _try_uuid(category)

        query = (
            db.query(Asset, AssetCategory)
            .join(AssetCategory, Asset.category_id == AssetCategory.category_id)
            .filter(Asset.organization_id == org_id)
        )

        if status_value:
            query = query.filter(Asset.status == status_value)
        if category_id:
            query = query.filter(Asset.category_id == category_id)
        elif category:
            query = query.filter(AssetCategory.category_code == category)
        if search:
            search_pattern = f"%{search}%"
            query = query.filter(
                or_(
                    Asset.asset_number.ilike(search_pattern),
                    Asset.asset_name.ilike(search_pattern),
                    Asset.serial_number.ilike(search_pattern),
                    Asset.barcode.ilike(search_pattern),
                )
            )

        total_count = query.with_entities(func.count(Asset.asset_id)).scalar() or 0
        rows = (
            query.order_by(Asset.asset_number)
            .limit(limit)
            .offset(offset)
            .all()
        )

        assets_view = []
        for asset, category_row in rows:
            assets_view.append(
                {
                    "asset_id": asset.asset_id,
                    "asset_number": asset.asset_number,
                    "asset_name": asset.asset_name,
                    "category_name": category_row.category_name,
                    "category_code": category_row.category_code,
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

        categories = (
            db.query(AssetCategory)
            .filter(
                AssetCategory.organization_id == org_id,
                AssetCategory.is_active.is_(True),
            )
            .order_by(AssetCategory.category_code)
            .all()
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
        }

    @staticmethod
    def depreciation_context(
        db: Session,
        organization_id: str,
        asset_id: Optional[str],
        period: Optional[str],
        page: int = 1,
        limit: int = 50,
    ) -> dict:
        org_id = coerce_uuid(organization_id)
        offset = (page - 1) * limit

        period_id = _try_uuid(period)

        query = (
            db.query(DepreciationRun, FiscalPeriod)
            .join(
                FiscalPeriod,
                DepreciationRun.fiscal_period_id == FiscalPeriod.fiscal_period_id,
            )
            .filter(DepreciationRun.organization_id == org_id)
        )

        if period_id:
            query = query.filter(DepreciationRun.fiscal_period_id == period_id)

        total_count = query.with_entities(func.count(DepreciationRun.run_id)).scalar() or 0
        rows = (
            query.order_by(DepreciationRun.created_at.desc())
            .limit(limit)
            .offset(offset)
            .all()
        )

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
                    "created_at": _format_date(run.created_at.date() if run.created_at else None),
                }
            )

        total_pages = max(1, (total_count + limit - 1) // limit)

        return {
            "depreciation_runs": runs_view,
            "asset_id": asset_id,
            "period": period,
            "page": page,
            "limit": limit,
            "offset": offset,
            "total_count": total_count,
            "total_pages": total_pages,
        }


fa_web_service = FixedAssetWebService()
