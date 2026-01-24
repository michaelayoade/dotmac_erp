"""
Fixed assets web view service.

Provides view-focused data for FA web routes.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models.finance.core_config.numbering_sequence import NumberingSequence, SequenceType
from app.models.finance.fa.asset import Asset, AssetStatus
from app.models.finance.fa.asset_category import AssetCategory, DepreciationMethod
from app.models.finance.fa.depreciation_run import DepreciationRun
from app.models.finance.gl.account import Account
from app.models.finance.gl.fiscal_period import FiscalPeriod
from app.config import settings
from app.services.common import coerce_uuid
from app.services.finance.fa.asset import asset_service, asset_category_service, AssetInput, AssetCategoryInput
from app.services.finance.platform.currency_context import get_currency_context
from app.services.finance.platform.org_context import org_context_service
from app.templates import templates
from app.web.deps import base_context, WebAuthContext


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
    def _sequence_preview(sequence: Optional[NumberingSequence]) -> Optional[str]:
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
        return (
            db.query(Account)
            .filter(
                Account.organization_id == organization_id,
                Account.is_active.is_(True),
            )
            .order_by(Account.account_code)
            .all()
        )

    @staticmethod
    def asset_form_context(
        db: Session,
        organization_id: str,
    ) -> dict:
        """Build context for new asset form."""
        from app.models.finance.ap.supplier import Supplier

        org_id = coerce_uuid(organization_id)

        categories = (
            db.query(AssetCategory)
            .filter(
                AssetCategory.organization_id == org_id,
                AssetCategory.is_active.is_(True),
            )
            .order_by(AssetCategory.category_code)
            .all()
        )

        sequence = (
            db.query(NumberingSequence)
            .filter(
                NumberingSequence.organization_id == org_id,
                NumberingSequence.sequence_type == SequenceType.ASSET,
            )
            .first()
        )

        # Get suppliers list for FA → AP source tracking
        suppliers = (
            db.query(Supplier)
            .filter(
                Supplier.organization_id == org_id,
                Supplier.is_active.is_(True),
            )
            .order_by(Supplier.legal_name)
            .all()
        )
        suppliers_list = [
            {
                "supplier_id": str(s.supplier_id),
                "supplier_name": s.trading_name or s.legal_name,
                "supplier_code": s.supplier_code,
            }
            for s in suppliers
        ]

        context = {
            "categories": categories,
            "suppliers_list": suppliers_list,
            "today": _format_date(date.today()),
            "asset_number_preview": FixedAssetWebService._sequence_preview(sequence),
        }
        context.update(get_currency_context(db, organization_id))
        return context

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

    def asset_new_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        context = base_context(request, auth, "New Asset", "fa")
        context.update(self.asset_form_context(db, str(auth.organization_id)))
        return templates.TemplateResponse(request, "finance/fa/asset_form.html", context)

    def create_asset_response(
        self,
        request: Request,
        auth: WebAuthContext,
        asset_name: str,
        category_id: str,
        acquisition_date: str,
        acquisition_cost: str,
        currency_code: Optional[str],
        description: Optional[str],
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        try:
            resolved_currency = currency_code or org_context_service.get_functional_currency(
                db,
                auth.organization_id,
            )

            input_data = AssetInput(
                asset_name=asset_name,
                category_id=UUID(category_id),
                acquisition_date=datetime.strptime(acquisition_date, "%Y-%m-%d").date(),
                acquisition_cost=Decimal(acquisition_cost),
                currency_code=resolved_currency,
                description=description,
            )

            asset_service.create_asset(
                db,
                auth.organization_id,
                input_data,
                created_by_user_id=auth.person_id,
            )
            return RedirectResponse(url="/finance/fa/assets", status_code=303)

        except Exception as e:
            context = base_context(request, auth, "New Asset", "fa")
            context.update(self.asset_form_context(db, str(auth.organization_id)))
            context["error"] = str(e)
            return templates.TemplateResponse(request, "finance/fa/asset_form.html", context)

    @staticmethod
    def list_categories_context(
        db: Session,
        organization_id: str,
        is_active: Optional[bool],
        page: int,
        limit: int = 50,
    ) -> dict:
        org_id = coerce_uuid(organization_id)
        offset = (page - 1) * limit

        query = db.query(AssetCategory).filter(AssetCategory.organization_id == org_id)
        if is_active is not None:
            query = query.filter(AssetCategory.is_active.is_(True) if is_active else AssetCategory.is_active.is_(False))

        total_count = query.with_entities(func.count(AssetCategory.category_id)).scalar() or 0
        rows = (
            query.order_by(AssetCategory.category_code)
            .limit(limit)
            .offset(offset)
            .all()
        )

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

        return {
            "categories": categories_view,
            "is_active": is_active,
            "page": page,
            "limit": limit,
            "offset": offset,
            "total_count": total_count,
            "total_pages": total_pages,
        }

    @staticmethod
    def category_form_context(
        db: Session,
        organization_id: str,
        category_id: Optional[str] = None,
    ) -> dict:
        org_id = coerce_uuid(organization_id)
        categories = (
            db.query(AssetCategory)
            .filter(
                AssetCategory.organization_id == org_id,
            )
            .order_by(AssetCategory.category_code)
            .all()
        )
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
        is_active: Optional[bool],
        page: int,
        db: Session,
    ) -> HTMLResponse:
        context = base_context(request, auth, "Asset Categories", "fa")
        context.update(
            self.list_categories_context(
                db,
                str(auth.organization_id),
                is_active=is_active,
                page=page,
            )
        )
        return templates.TemplateResponse(request, "finance/fa/categories.html", context)

    def new_category_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        error: Optional[str] = None,
    ) -> HTMLResponse:
        context = base_context(request, auth, "New Asset Category", "fa")
        context.update(self.category_form_context(db, str(auth.organization_id)))
        context["error"] = error
        return templates.TemplateResponse(request, "finance/fa/category_form.html", context)

    async def create_category_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        form = await request.form()
        org_id = auth.organization_id

        try:
            category_input = AssetCategoryInput(
                category_code=form.get("category_code", "").strip(),
                category_name=form.get("category_name", "").strip(),
                asset_account_id=coerce_uuid(form.get("asset_account_id")),
                accumulated_depreciation_account_id=coerce_uuid(form.get("accumulated_depreciation_account_id")),
                depreciation_expense_account_id=coerce_uuid(form.get("depreciation_expense_account_id")),
                gain_loss_disposal_account_id=coerce_uuid(form.get("gain_loss_disposal_account_id")),
                useful_life_months=int(form.get("useful_life_months") or 0),
                depreciation_method=DepreciationMethod(form.get("depreciation_method") or DepreciationMethod.STRAIGHT_LINE.value),
                residual_value_percent=Decimal(form.get("residual_value_percent") or "0"),
                capitalization_threshold=Decimal(form.get("capitalization_threshold") or "0"),
                revaluation_model_allowed=bool(form.get("revaluation_model_allowed")),
                revaluation_surplus_account_id=coerce_uuid(form.get("revaluation_surplus_account_id")) if form.get("revaluation_surplus_account_id") else None,
                impairment_loss_account_id=coerce_uuid(form.get("impairment_loss_account_id")) if form.get("impairment_loss_account_id") else None,
                parent_category_id=coerce_uuid(form.get("parent_category_id")) if form.get("parent_category_id") else None,
                description=form.get("description") or None,
            )

            asset_category_service.create_category(db, org_id, category_input)
            return RedirectResponse(url="/finance/fa/categories", status_code=303)
        except Exception as e:
            return self.new_category_form_response(request, auth, db, error=str(e))

    def edit_category_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        category_id: str,
        db: Session,
        error: Optional[str] = None,
    ) -> HTMLResponse:
        category = asset_category_service.get(db, category_id)
        if not category or category.organization_id != auth.organization_id:
            return RedirectResponse(url="/finance/fa/categories", status_code=302)

        context = base_context(request, auth, "Edit Asset Category", "fa")
        context.update(self.category_form_context(db, str(auth.organization_id), category_id=category_id))
        context["error"] = error
        return templates.TemplateResponse(request, "finance/fa/category_form.html", context)

    async def update_category_response(
        self,
        request: Request,
        auth: WebAuthContext,
        category_id: str,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        form = await request.form()
        org_id = auth.organization_id

        try:
            category_input = AssetCategoryInput(
                category_code=form.get("category_code", "").strip(),
                category_name=form.get("category_name", "").strip(),
                asset_account_id=coerce_uuid(form.get("asset_account_id")),
                accumulated_depreciation_account_id=coerce_uuid(form.get("accumulated_depreciation_account_id")),
                depreciation_expense_account_id=coerce_uuid(form.get("depreciation_expense_account_id")),
                gain_loss_disposal_account_id=coerce_uuid(form.get("gain_loss_disposal_account_id")),
                useful_life_months=int(form.get("useful_life_months") or 0),
                depreciation_method=DepreciationMethod(form.get("depreciation_method") or DepreciationMethod.STRAIGHT_LINE.value),
                residual_value_percent=Decimal(form.get("residual_value_percent") or "0"),
                capitalization_threshold=Decimal(form.get("capitalization_threshold") or "0"),
                revaluation_model_allowed=bool(form.get("revaluation_model_allowed")),
                revaluation_surplus_account_id=coerce_uuid(form.get("revaluation_surplus_account_id")) if form.get("revaluation_surplus_account_id") else None,
                impairment_loss_account_id=coerce_uuid(form.get("impairment_loss_account_id")) if form.get("impairment_loss_account_id") else None,
                parent_category_id=coerce_uuid(form.get("parent_category_id")) if form.get("parent_category_id") else None,
                description=form.get("description") or None,
            )

            is_active = form.get("is_active") == "on"
            asset_category_service.update_category(db, org_id, category_id, category_input, is_active=is_active)
            return RedirectResponse(url="/finance/fa/categories", status_code=303)
        except Exception as e:
            return self.edit_category_form_response(request, auth, category_id, db, error=str(e))

    def toggle_category_response(
        self,
        auth: WebAuthContext,
        category_id: str,
        db: Session,
    ) -> RedirectResponse:
        try:
            asset_category_service.toggle_category(db, auth.organization_id, category_id)
        except Exception:
            pass

        return RedirectResponse(url="/finance/fa/categories", status_code=303)

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
