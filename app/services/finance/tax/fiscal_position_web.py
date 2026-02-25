"""
Fiscal Position web view service.

Provides view-focused data for fiscal position web routes.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from app.models.finance.ap.supplier import SupplierType
from app.models.finance.ar.customer import CustomerType
from app.models.finance.gl.account import Account
from app.models.finance.tax.fiscal_position import FiscalPosition
from app.models.finance.tax.tax_code import TaxCode
from app.services.common import coerce_uuid
from app.services.common_filters import build_active_filters
from app.services.finance.tax.fiscal_position_service import FiscalPositionService
from app.templates import templates
from app.web.deps import WebAuthContext, base_context

logger = logging.getLogger(__name__)


def _safe_text(value: object) -> str:
    """Normalize form values to text."""
    if value is None:
        return ""
    if isinstance(value, UploadFile):
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _fp_list_view(fp: FiscalPosition) -> dict[str, object]:
    """Format fiscal position for list display."""
    return {
        "fiscal_position_id": fp.fiscal_position_id,
        "name": fp.name,
        "description": fp.description or "",
        "auto_apply": fp.auto_apply,
        "customer_type": (
            fp.customer_type.replace("_", " ").title() if fp.customer_type else ""
        ),
        "supplier_type": (
            fp.supplier_type.replace("_", " ").title() if fp.supplier_type else ""
        ),
        "country_code": fp.country_code or "",
        "priority": fp.priority,
        "is_active": fp.is_active,
        "tax_map_count": len(fp.tax_maps),
        "account_map_count": len(fp.account_maps),
    }


def _get_tax_codes(db: Session, org_id: UUID) -> list[TaxCode]:
    """Get active tax codes for dropdowns."""
    return list(
        db.scalars(
            select(TaxCode)
            .where(
                TaxCode.organization_id == org_id,
                TaxCode.is_active.is_(True),
            )
            .order_by(TaxCode.tax_code)
        )
    )


def _get_accounts(db: Session, org_id: UUID) -> list[Account]:
    """Get active GL accounts for dropdowns."""
    return list(
        db.scalars(
            select(Account)
            .where(
                Account.organization_id == org_id,
                Account.is_active.is_(True),
            )
            .order_by(Account.account_code)
        )
    )


class FiscalPositionWebService:
    """Web view service for fiscal position pages."""

    def list_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        search: str | None = None,
        is_active: str | None = None,
    ) -> HTMLResponse:
        """Fiscal positions list page."""
        org_id = coerce_uuid(auth.organization_id)
        service = FiscalPositionService(db)

        active_filter: bool | None = None
        if is_active == "true":
            active_filter = True
        elif is_active == "false":
            active_filter = False

        positions = service.list_for_org(
            org_id,
            is_active=active_filter,
            search=search,
        )

        formatted = [_fp_list_view(fp) for fp in positions]

        context = base_context(request, auth, "Fiscal Positions", "tax")
        active_filters = build_active_filters(
            params={
                "is_active": is_active or "",
            },
            labels={"is_active": "Status"},
        )
        context.update(
            {
                "positions": formatted,
                "search": search or "",
                "is_active": is_active or "",
                "active_filters": active_filters,
                "total_count": len(formatted),
                "active_count": sum(1 for p in positions if p.is_active),
            }
        )
        return templates.TemplateResponse(
            request, "finance/tax/fiscal_positions/list.html", context
        )

    def detail_response(
        self,
        request: Request,
        auth: WebAuthContext,
        fiscal_position_id: str,
        db: Session,
    ) -> HTMLResponse:
        """Fiscal position detail page."""
        service = FiscalPositionService(db)
        fp = service.get_by_id(coerce_uuid(fiscal_position_id))

        org_id = coerce_uuid(auth.organization_id)

        if not fp or fp.organization_id != org_id:
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="Fiscal position not found")

        # Build tax map display data with code names
        tax_codes = {tc.tax_code_id: tc for tc in _get_tax_codes(db, org_id)}
        tax_maps_display = []
        for tm in fp.tax_maps:
            source = tax_codes.get(tm.tax_source_id)
            dest = tax_codes.get(tm.tax_dest_id) if tm.tax_dest_id else None
            tax_maps_display.append(
                {
                    "source_code": source.tax_code if source else "Unknown",
                    "source_name": source.tax_name if source else "",
                    "dest_code": dest.tax_code if dest else None,
                    "dest_name": dest.tax_name if dest else "(Exempt)",
                }
            )

        # Build account map display data
        accounts = {a.account_id: a for a in _get_accounts(db, org_id)}
        account_maps_display = []
        for am in fp.account_maps:
            src = accounts.get(am.account_source_id)
            dst = accounts.get(am.account_dest_id)
            account_maps_display.append(
                {
                    "source_code": src.account_code if src else "Unknown",
                    "source_name": src.account_name if src else "",
                    "dest_code": dst.account_code if dst else "Unknown",
                    "dest_name": dst.account_name if dst else "",
                }
            )

        context = base_context(request, auth, fp.name, "tax")
        context.update(
            {
                "fp": fp,
                "tax_maps": tax_maps_display,
                "account_maps": account_maps_display,
            }
        )
        return templates.TemplateResponse(
            request, "finance/tax/fiscal_positions/detail.html", context
        )

    def form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        fiscal_position_id: str | None = None,
        error: str | None = None,
        form_data: dict[str, object] | None = None,
    ) -> HTMLResponse:
        """Fiscal position create/edit form page."""
        org_id = coerce_uuid(auth.organization_id)
        fp: FiscalPosition | None = None
        if fiscal_position_id:
            service = FiscalPositionService(db)
            fp = service.get_by_id(coerce_uuid(fiscal_position_id))
            if not fp or fp.organization_id != org_id:
                from fastapi import HTTPException

                raise HTTPException(status_code=404, detail="Fiscal position not found")

        tax_codes = _get_tax_codes(db, org_id)
        accounts = _get_accounts(db, org_id)

        customer_types = [
            {"value": ct.value, "label": ct.value.replace("_", " ").title()}
            for ct in CustomerType
        ]
        supplier_types = [
            {"value": st.value, "label": st.value.replace("_", " ").title()}
            for st in SupplierType
        ]

        title = f"Edit {fp.name}" if fp else "New Fiscal Position"
        context = base_context(request, auth, title, "tax")
        context.update(
            {
                "fp": fp,
                "tax_codes": tax_codes,
                "accounts": accounts,
                "customer_types": customer_types,
                "supplier_types": supplier_types,
                "error": error,
                "form_data": form_data,
            }
        )
        return templates.TemplateResponse(
            request, "finance/tax/fiscal_positions/form.html", context
        )

    def create_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        form_data: dict[str, object],
    ) -> HTMLResponse | RedirectResponse:
        """Handle fiscal position creation form submit."""
        org_id = coerce_uuid(auth.organization_id)
        try:
            data = self._parse_form(form_data)
            service = FiscalPositionService(db)
            fp = service.create(org_id, data)
            db.flush()
            return RedirectResponse(
                f"/finance/tax/fiscal-positions/{fp.fiscal_position_id}",
                status_code=303,
            )
        except (ValueError, KeyError) as e:
            logger.warning("Fiscal position creation failed: %s", e)
            return self.form_response(
                request, auth, db, error=str(e), form_data=form_data
            )

    def update_response(
        self,
        request: Request,
        auth: WebAuthContext,
        fiscal_position_id: str,
        db: Session,
        form_data: dict[str, object],
    ) -> HTMLResponse | RedirectResponse:
        """Handle fiscal position update form submit."""
        try:
            data = self._parse_form(form_data)
            service = FiscalPositionService(db)
            fp = service.get_by_id(coerce_uuid(fiscal_position_id))
            if not fp or fp.organization_id != coerce_uuid(auth.organization_id):
                raise ValueError("Fiscal position not found")

            service.update(fp.fiscal_position_id, data)
            db.flush()
            return RedirectResponse(
                f"/finance/tax/fiscal-positions/{fp.fiscal_position_id}",
                status_code=303,
            )
        except (ValueError, KeyError) as e:
            logger.warning("Fiscal position update failed: %s", e)
            return self.form_response(
                request,
                auth,
                db,
                fiscal_position_id=fiscal_position_id,
                error=str(e),
                form_data=form_data,
            )

    def delete_response(
        self,
        request: Request,
        auth: WebAuthContext,
        fiscal_position_id: str,
        db: Session,
    ) -> RedirectResponse:
        """Handle fiscal position deletion."""
        org_id = coerce_uuid(auth.organization_id)
        service = FiscalPositionService(db)
        fp = service.get_by_id(coerce_uuid(fiscal_position_id))
        if not fp or fp.organization_id != org_id:
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="Fiscal position not found")

        service.delete(fp.fiscal_position_id)
        db.flush()
        return RedirectResponse("/finance/tax/fiscal-positions", status_code=303)

    @staticmethod
    def _parse_form(form: object) -> dict[str, Any]:
        """Parse form data into a dict for the service layer."""
        get: Any = getattr(form, "get", lambda k, d=None: d)
        getlist: Any = getattr(form, "getlist", lambda k: [])

        data: dict[str, Any] = {
            "name": _safe_text(get("name")),
            "description": _safe_text(get("description")),
            "auto_apply": get("auto_apply") == "true",
            "customer_type": _safe_text(get("customer_type")) or None,
            "supplier_type": _safe_text(get("supplier_type")) or None,
            "country_code": _safe_text(get("country_code")) or None,
            "state_code": _safe_text(get("state_code")) or None,
            "priority": int(_safe_text(get("priority")) or "10"),
            "is_active": get("is_active") != "false",
        }

        if not data["name"]:
            raise ValueError("Name is required")

        # Parse tax mappings (parallel lists from form)
        tax_source_ids = getlist("tax_source_id")
        tax_dest_ids = getlist("tax_dest_id")
        tax_maps: list[dict[str, str | None]] = []
        for i, src_id in enumerate(tax_source_ids):
            src = _safe_text(src_id)
            if not src:
                continue
            dest = _safe_text(tax_dest_ids[i]) if i < len(tax_dest_ids) else ""
            tax_maps.append(
                {
                    "tax_source_id": src,
                    "tax_dest_id": dest or None,
                }
            )
        data["tax_maps"] = tax_maps

        # Parse account mappings
        acct_source_ids = getlist("account_source_id")
        acct_dest_ids = getlist("account_dest_id")
        account_maps: list[dict[str, str | None]] = []
        for i, src_id in enumerate(acct_source_ids):
            src = _safe_text(src_id)
            if not src:
                continue
            dest = _safe_text(acct_dest_ids[i]) if i < len(acct_dest_ids) else ""
            if not dest:
                continue
            account_maps.append(
                {
                    "account_source_id": src,
                    "account_dest_id": dest,
                }
            )
        data["account_maps"] = account_maps

        return data


fiscal_position_web_service = FiscalPositionWebService()
