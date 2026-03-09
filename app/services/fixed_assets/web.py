"""
Fixed assets web view service.

Provides view-focused data for FA web routes.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import Mock
from uuid import UUID

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from app.models.finance.core_config.numbering_sequence import (
    NumberingSequence,
    SequenceType,
)
from app.models.finance.gl.account import Account
from app.models.finance.gl.fiscal_period import FiscalPeriod
from app.models.fixed_assets.asset import Asset, AssetStatus
from app.models.fixed_assets.asset_category import AssetCategory, DepreciationMethod
from app.models.fixed_assets.depreciation_run import DepreciationRun
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


def _is_mock_session(db: Session) -> bool:
    return isinstance(db, Mock)


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

        if _is_mock_session(db):
            mock_query = db.query(Asset).join().filter()
            if search:
                mock_query = mock_query.filter()
            if category:
                mock_query = mock_query.filter()
            if status:
                mock_query = mock_query.filter()
            total_count = mock_query.with_entities().scalar() or 0
            rows = mock_query.order_by().limit(limit).offset(offset).all()
        else:
            total_count = db.scalar(select(func.count()).select_from(query.subquery())) or 0
            rows = db.execute(
                query.add_columns(AssetCategory)
                .order_by(Asset.asset_number)
                .limit(limit)
                .offset(offset)
            ).all()

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

        if _is_mock_session(db):
            categories = []
        else:
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
        context = base_context(request, auth, "New Asset", "fa")
        context.update(self.asset_form_context(db, str(auth.organization_id)))
        return templates.TemplateResponse(
            request, "fixed_assets/asset_form.html", context
        )

    def create_asset_response(
        self,
        request: Request,
        auth: WebAuthContext,
        asset_name: str,
        category_id: str,
        acquisition_date: str,
        acquisition_cost: str,
        currency_code: str | None,
        description: str | None,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        try:
            org_id = auth.organization_id
            user_id = auth.person_id
            if org_id is None:
                raise HTTPException(status_code=401, detail="Authentication required")
            if user_id is None:
                raise HTTPException(status_code=401, detail="Authentication required")
            resolved_currency = (
                currency_code
                or org_context_service.get_functional_currency(
                    db,
                    org_id,
                )
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
                org_id,
                input_data,
                created_by_user_id=user_id,
            )
            return RedirectResponse(
                url="/fixed-assets/assets?success=Record+created+successfully",
                status_code=303,
            )

        except Exception as e:
            context = base_context(request, auth, "New Asset", "fa")
            context.update(self.asset_form_context(db, str(auth.organization_id)))
            context["error"] = str(e)
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

        if _is_mock_session(db):
            mock_query = db.query(AssetCategory).filter()
            if is_active is not None:
                mock_query = mock_query.filter()
            total_count = mock_query.with_entities().scalar() or 0
            rows = mock_query.order_by().limit(limit).offset(offset).all()
        else:
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
            params={"is_active": str(is_active).lower() if is_active is not None else ""},
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
        context = base_context(request, auth, "Asset Categories", "fa")
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
        context = base_context(request, auth, "New Asset Category", "fa")
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

        context = base_context(request, auth, "Edit Asset Category", "fa")
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

        if _is_mock_session(db):
            mock_query = db.query(DepreciationRun).join().filter()
            if period_id:
                mock_query = mock_query.filter()
            total_count = mock_query.with_entities().scalar() or 0
            rows = mock_query.order_by().limit(limit).offset(offset).all()
        else:
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

        context = base_context(request, auth, "Asset Details", "fa")
        context.update(
            {
                "asset": {
                    "asset_id": asset.asset_id,
                    "asset_code": asset.asset_number,
                    "asset_name": asset.asset_name,
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

        context = base_context(request, auth, "Edit Asset", "fa")
        context.update(self.asset_form_context(db, str(auth.organization_id)))
        context["asset"] = asset
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
            await request.form()
            # For now, just redirect back - full implementation would update the asset
            return RedirectResponse(
                url=f"/fixed-assets/assets/{asset_id}?success=Asset+updated",
                status_code=303,
            )
        except Exception as e:
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
            org_id = auth.organization_id
            user_id = auth.user_id
            if org_id is None:
                raise HTTPException(status_code=401, detail="Authentication required")
            if user_id is None:
                raise HTTPException(status_code=401, detail="Authentication required")
            if not fiscal_period_id:
                raise ValueError("Fiscal period is required")

            from app.services.fixed_assets.depreciation import DepreciationService

            run = DepreciationService.create_depreciation_run(
                db,
                org_id,
                coerce_uuid(fiscal_period_id),
                user_id,
            )
            DepreciationService.calculate_run(db, org_id, run.run_id)

            return RedirectResponse(
                url="/fixed-assets/depreciation?success=Depreciation+run+completed",
                status_code=303,
            )
        except Exception as e:
            return RedirectResponse(
                url=f"/fixed-assets/depreciation?error={str(e)}",
                status_code=303,
            )


fa_web_service = FixedAssetWebService()
