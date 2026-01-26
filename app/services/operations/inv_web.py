"""Operations inventory web service helpers."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Optional

from fastapi import Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from app.services.finance.inv.material_request_web import material_request_web_service
from app.templates import templates
from app.web.deps import WebAuthContext, base_context


def _safe_form_text(value: object) -> str:
    """Normalize form values to text for safe parsing."""
    if value is None:
        return ""
    if isinstance(value, UploadFile):
        return ""
    if isinstance(value, str):
        return value
    return str(value)


class OperationsInventoryWebService:
    """Service layer for operations inventory web routes."""

    def material_request_list_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        status: Optional[str] = None,
        request_type: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> HTMLResponse:
        """Material request list page."""
        context = base_context(request, auth, "Material Requests", "material_requests")
        context.update(
            material_request_web_service.list_context(
                db,
                str(auth.organization_id),
                status=status,
                request_type=request_type,
                start_date=start_date,
                end_date=end_date,
                project_id=project_id,
            )
        )
        return templates.TemplateResponse(request, "operations/inv/material_requests.html", context)

    def new_material_request_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        """New material request form."""
        context = base_context(request, auth, "New Material Request", "material_requests")
        context.update(
            material_request_web_service.form_context(
                db,
                str(auth.organization_id),
            )
        )
        return templates.TemplateResponse(request, "operations/inv/material_request_form.html", context)

    async def create_material_request_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Create new material request."""
        form = await request.form()

        request_type = _safe_form_text(form.get("request_type", "PURCHASE"))
        schedule_date = _safe_form_text(form.get("schedule_date")) or None
        default_warehouse_id = _safe_form_text(form.get("default_warehouse_id")) or None
        requested_by_id = _safe_form_text(form.get("requested_by_id")) or None
        remarks = _safe_form_text(form.get("remarks")) or None

        # Parse items from JSON
        items_json = _safe_form_text(form.get("items_json", "[]"))
        try:
            items = json.loads(items_json) if items_json else []
        except json.JSONDecodeError:
            items = []

        try:
            mr = material_request_web_service.create_from_form(
                db=db,
                organization_id=auth.organization_id,
                user_id=auth.user_id,
                request_type=request_type,
                schedule_date=schedule_date,
                default_warehouse_id=default_warehouse_id,
                requested_by_id=requested_by_id,
                remarks=remarks,
                items=items,
            )
            db.commit()
            return RedirectResponse(f"/operations/inv/material-requests/{mr.request_id}", status_code=303)
        except Exception as e:
            db.rollback()
            context = base_context(request, auth, "New Material Request", "material_requests")
            context.update(
                material_request_web_service.form_context(db, str(auth.organization_id))
            )
            context["error"] = str(e)
            return templates.TemplateResponse(request, "operations/inv/material_request_form.html", context)

    def material_request_report_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        group_by: str = "status",
    ) -> HTMLResponse:
        """Material request summary report page."""
        context = base_context(request, auth, "Material Request Report", "material_requests")
        context.update(
            material_request_web_service.report_context(
                db,
                str(auth.organization_id),
                start_date=start_date,
                end_date=end_date,
                group_by=group_by,
            )
        )
        return templates.TemplateResponse(request, "operations/inv/material_request_report.html", context)

    def material_request_detail_response(
        self,
        request: Request,
        request_id: str,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Material request detail page."""
        context = base_context(request, auth, "Material Request", "material_requests")
        context.update(
            material_request_web_service.detail_context(
                db,
                str(auth.organization_id),
                request_id,
            )
        )
        if not context.get("material_request"):
            return RedirectResponse("/operations/inv/material-requests", status_code=302)
        return templates.TemplateResponse(request, "operations/inv/material_request_detail.html", context)

    def edit_material_request_form_response(
        self,
        request: Request,
        request_id: str,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Edit material request form."""
        context = base_context(request, auth, "Edit Material Request", "material_requests")
        context.update(
            material_request_web_service.form_context(
                db,
                str(auth.organization_id),
                request_id=request_id,
            )
        )
        if not context.get("material_request"):
            return RedirectResponse("/operations/inv/material-requests", status_code=302)
        return templates.TemplateResponse(request, "operations/inv/material_request_form.html", context)

    async def update_material_request_response(
        self,
        request: Request,
        request_id: str,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Update material request."""
        form = await request.form()

        request_type = form.get("request_type", "PURCHASE")
        schedule_date = form.get("schedule_date") or None
        default_warehouse_id = form.get("default_warehouse_id") or None
        requested_by_id = form.get("requested_by_id") or None
        remarks = form.get("remarks") or None

        # Parse items from JSON
        items_json = form.get("items_json", "[]")
        try:
            items = json.loads(items_json) if items_json else []
        except json.JSONDecodeError:
            items = []

        try:
            mr = material_request_web_service.update_from_form(
                db=db,
                organization_id=auth.organization_id,
                user_id=auth.user_id,
                request_id=request_id,
                request_type=request_type,
                schedule_date=schedule_date,
                default_warehouse_id=default_warehouse_id,
                requested_by_id=requested_by_id,
                remarks=remarks,
                items=items,
            )
            db.commit()
            return RedirectResponse(f"/operations/inv/material-requests/{mr.request_id}", status_code=303)
        except Exception as e:
            db.rollback()
            context = base_context(request, auth, "Edit Material Request", "material_requests")
            context.update(
                material_request_web_service.form_context(db, str(auth.organization_id), request_id)
            )
            context["error"] = str(e)
            return templates.TemplateResponse(request, "operations/inv/material_request_form.html", context)

    def submit_material_request_response(
        self,
        request_id: str,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        """Submit material request."""
        try:
            material_request_web_service.submit_request(
                db=db,
                organization_id=auth.organization_id,
                user_id=auth.user_id,
                request_id=request_id,
            )
            db.commit()
        except Exception:
            db.rollback()
        return RedirectResponse(f"/operations/inv/material-requests/{request_id}", status_code=303)

    def cancel_material_request_response(
        self,
        request_id: str,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        """Cancel material request."""
        try:
            material_request_web_service.cancel_request(
                db=db,
                organization_id=auth.organization_id,
                user_id=auth.user_id,
                request_id=request_id,
            )
            db.commit()
        except Exception:
            db.rollback()
        return RedirectResponse(f"/operations/inv/material-requests/{request_id}", status_code=303)

    def transaction_detail_response(
        self,
        request: Request,
        transaction_id: str,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Inventory transaction detail page."""
        from uuid import UUID as UUID_Type
        from app.models.finance.inv.inventory_transaction import InventoryTransaction
        from app.models.finance.inv.item import Item
        from app.models.finance.inv.warehouse import Warehouse
        from app.models.finance.inv.inventory_lot import InventoryLot

        context = base_context(request, auth, "Transaction Detail", "inv")

        try:
            txn_uuid = UUID_Type(transaction_id)
        except ValueError:
            return RedirectResponse("/operations/inv/transactions", status_code=302)

        txn = (
            db.query(InventoryTransaction)
            .filter(
                InventoryTransaction.transaction_id == txn_uuid,
                InventoryTransaction.organization_id == auth.organization_id,
            )
            .first()
        )

        if not txn:
            return RedirectResponse("/operations/inv/transactions", status_code=302)

        # Load related objects
        item = db.get(Item, txn.item_id)
        warehouse = db.get(Warehouse, txn.warehouse_id)
        lot = db.get(InventoryLot, txn.lot_id) if txn.lot_id else None

        # Get related transactions (same reference or reversal)
        related = []
        if txn.reference:
            related_txns = (
                db.query(InventoryTransaction)
                .filter(
                    InventoryTransaction.organization_id == auth.organization_id,
                    InventoryTransaction.reference == txn.reference,
                    InventoryTransaction.transaction_id != txn_uuid,
                )
                .limit(10)
                .all()
            )
            for rt in related_txns:
                related.append(
                    {
                        "transaction_id": rt.transaction_id,
                        "transaction_date": rt.transaction_date,
                        "transaction_type": rt.transaction_type,
                        "quantity": rt.quantity,
                        "total_cost": rt.total_cost,
                        "reference": rt.reference,
                        "relationship_type": "Same Reference",
                    }
                )

        context["transaction"] = txn
        context["transaction"].item = item
        context["transaction"].warehouse = warehouse
        context["transaction"].lot = lot
        context["related_transactions"] = related

        return templates.TemplateResponse(request, "operations/inv/transaction_detail.html", context)

    def reverse_transaction_response(
        self,
        transaction_id: str,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        """Reverse an inventory transaction."""
        from uuid import UUID as UUID_Type
        from app.models.finance.inv.inventory_transaction import InventoryTransaction

        try:
            txn_uuid = UUID_Type(transaction_id)
            txn = (
                db.query(InventoryTransaction)
                .filter(
                    InventoryTransaction.transaction_id == txn_uuid,
                    InventoryTransaction.organization_id == auth.organization_id,
                )
                .first()
            )

            if txn and not txn.is_reversed:
                # Create reversal transaction
                reversal = InventoryTransaction(
                    organization_id=auth.organization_id,
                    transaction_type=txn.transaction_type,
                    transaction_date=datetime.now(),
                    fiscal_period_id=txn.fiscal_period_id,
                    item_id=txn.item_id,
                    warehouse_id=txn.warehouse_id,
                    quantity=-txn.quantity,
                    uom=txn.uom,
                    unit_cost=txn.unit_cost,
                    total_cost=-txn.total_cost,
                    currency_code=txn.currency_code,
                    reference=f"REV-{txn.reference or txn.transaction_id}",
                    source_document_type="REVERSAL",
                    source_document_id=txn.transaction_id,
                    created_by_user_id=auth.user_id,
                )
                db.add(reversal)

                # Mark original as reversed
                txn.is_reversed = True
                txn.reversed_at = datetime.now()
                txn.reversed_by_id = reversal.transaction_id

                db.commit()

        except Exception:
            db.rollback()

        return RedirectResponse(f"/operations/inv/transactions/{transaction_id}", status_code=303)

    def list_counts_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        status: Optional[str] = None,
        search: Optional[str] = None,
        page: int = 1,
    ) -> HTMLResponse:
        """Inventory counts list page."""
        from app.models.finance.inv.inventory_count import InventoryCount, CountStatus
        from sqlalchemy import func, or_

        context = base_context(request, auth, "Stock Counts", "counts")
        limit = 50
        offset = (page - 1) * limit

        query = db.query(InventoryCount).filter(
            InventoryCount.organization_id == auth.organization_id
        )

        if status:
            try:
                query = query.filter(InventoryCount.status == CountStatus(status.upper()))
            except ValueError:
                pass

        if search:
            search_pattern = f"%{search}%"
            query = query.filter(
                or_(
                    InventoryCount.count_number.ilike(search_pattern),
                    InventoryCount.count_description.ilike(search_pattern),
                )
            )

        total_count = query.with_entities(func.count(InventoryCount.count_id)).scalar() or 0
        counts = query.order_by(InventoryCount.count_date.desc()).limit(limit).offset(offset).all()

        # Get stats
        stats = {
            "total": db.query(InventoryCount).filter(InventoryCount.organization_id == auth.organization_id).count(),
            "draft": db.query(InventoryCount).filter(
                InventoryCount.organization_id == auth.organization_id,
                InventoryCount.status == CountStatus.DRAFT,
            ).count(),
            "in_progress": db.query(InventoryCount).filter(
                InventoryCount.organization_id == auth.organization_id,
                InventoryCount.status == CountStatus.IN_PROGRESS,
            ).count(),
            "completed": db.query(InventoryCount).filter(
                InventoryCount.organization_id == auth.organization_id,
                InventoryCount.status == CountStatus.COMPLETED,
            ).count(),
        }

        context["counts"] = counts
        context["stats"] = stats
        context["search"] = search or ""
        context["status"] = status or ""
        context["page"] = page
        context["total_pages"] = max(1, (total_count + limit - 1) // limit)
        context["total_count"] = total_count

        return templates.TemplateResponse(request, "operations/inv/counts.html", context)

    def new_count_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        """New inventory count form."""
        from app.models.finance.inv.warehouse import Warehouse
        from app.models.finance.inv.item_category import ItemCategory
        from app.models.finance.gl.fiscal_period import FiscalPeriod, PeriodStatus

        context = base_context(request, auth, "New Stock Count", "counts")

        warehouses = (
            db.query(Warehouse)
            .filter(
                Warehouse.organization_id == auth.organization_id,
                Warehouse.is_active == True,
            )
            .order_by(Warehouse.warehouse_code)
            .all()
        )

        categories = (
            db.query(ItemCategory)
            .filter(
                ItemCategory.organization_id == auth.organization_id,
                ItemCategory.is_active == True,
            )
            .order_by(ItemCategory.category_code)
            .all()
        )

        fiscal_periods = (
            db.query(FiscalPeriod)
            .filter(
                FiscalPeriod.organization_id == auth.organization_id,
                FiscalPeriod.status.in_([PeriodStatus.OPEN, PeriodStatus.REOPENED]),
            )
            .order_by(FiscalPeriod.start_date.desc())
            .all()
        )

        context["warehouses"] = warehouses
        context["categories"] = categories
        context["fiscal_periods"] = fiscal_periods
        context["today"] = date.today().strftime("%Y-%m-%d")
        context["count"] = None

        return templates.TemplateResponse(request, "operations/inv/count_form.html", context)

    async def create_count_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Create new inventory count."""
        from app.services.finance.inv.count import InventoryCountService, CountInput
        from uuid import UUID as UUID_Type

        form = await request.form()

        try:
            count_input = CountInput(
                count_number=form.get("count_number"),
                count_date=datetime.strptime(form.get("count_date"), "%Y-%m-%d").date(),
                fiscal_period_id=UUID_Type(form.get("fiscal_period_id")),
                count_description=form.get("count_description") or None,
                warehouse_id=UUID_Type(form.get("warehouse_id")) if form.get("warehouse_id") else None,
                category_id=UUID_Type(form.get("category_id")) if form.get("category_id") else None,
                is_full_count=form.get("is_full_count") is not None,
                is_cycle_count=form.get("is_cycle_count") is not None,
            )

            count = InventoryCountService.create_count(
                db, auth.organization_id, count_input, auth.user_id
            )
            db.commit()

            return RedirectResponse(f"/operations/inv/counts/{count.count_id}", status_code=303)

        except Exception as e:
            db.rollback()
            context = base_context(request, auth, "New Stock Count", "counts")
            context["error"] = str(e)
            return templates.TemplateResponse(request, "operations/inv/count_form.html", context)

    def count_detail_response(
        self,
        request: Request,
        count_id: str,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Inventory count detail page."""
        from app.models.finance.inv.inventory_count import InventoryCount
        from app.models.finance.inv.inventory_count_line import InventoryCountLine
        from uuid import UUID as UUID_Type

        context = base_context(request, auth, "Count Detail", "counts")

        try:
            count_uuid = UUID_Type(count_id)
        except ValueError:
            return RedirectResponse("/operations/inv/counts", status_code=302)

        count = (
            db.query(InventoryCount)
            .filter(
                InventoryCount.count_id == count_uuid,
                InventoryCount.organization_id == auth.organization_id,
            )
            .first()
        )

        if not count:
            return RedirectResponse("/operations/inv/counts", status_code=302)

        lines = db.query(InventoryCountLine).filter(
            InventoryCountLine.count_id == count_uuid
        ).all()

        # Calculate summary
        total_items = len(lines)
        counted = sum(1 for l in lines if l.counted_quantity is not None)
        with_variance = sum(1 for l in lines if l.variance_quantity and l.variance_quantity != 0)

        context["count"] = count
        context["lines"] = lines
        context["summary"] = {
            "total_items": total_items,
            "counted": counted,
            "with_variance": with_variance,
            "progress_percent": (counted / total_items * 100) if total_items > 0 else 0,
        }

        return templates.TemplateResponse(request, "operations/inv/count_detail.html", context)

    def start_count_response(
        self,
        count_id: str,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        """Start an inventory count."""
        from app.services.finance.inv.count import InventoryCountService
        from uuid import UUID as UUID_Type

        try:
            count_uuid = UUID_Type(count_id)
            InventoryCountService.start_count(db, auth.organization_id, count_uuid, auth.user_id)
            db.commit()
        except Exception:
            db.rollback()

        return RedirectResponse(f"/operations/inv/counts/{count_id}", status_code=303)

    def complete_count_response(
        self,
        count_id: str,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        """Complete an inventory count."""
        from app.services.finance.inv.count import InventoryCountService
        from uuid import UUID as UUID_Type

        try:
            count_uuid = UUID_Type(count_id)
            InventoryCountService.complete_count(db, auth.organization_id, count_uuid, auth.user_id)
            db.commit()
        except Exception:
            db.rollback()

        return RedirectResponse(f"/operations/inv/counts/{count_id}", status_code=303)

    def post_count_response(
        self,
        count_id: str,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        """Post inventory count adjustments."""
        from app.services.finance.inv.count import InventoryCountService
        from uuid import UUID as UUID_Type

        try:
            count_uuid = UUID_Type(count_id)
            InventoryCountService.post_adjustments(db, auth.organization_id, count_uuid, auth.user_id)
            db.commit()
        except Exception:
            db.rollback()

        return RedirectResponse(f"/operations/inv/counts/{count_id}", status_code=303)

    def list_boms_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        search: Optional[str] = None,
        status: Optional[str] = None,
        page: int = 1,
    ) -> HTMLResponse:
        """Bill of Materials list page."""
        from app.models.finance.inv.bom import BillOfMaterials
        from sqlalchemy import func, or_

        context = base_context(request, auth, "Bill of Materials", "boms")
        limit = 50
        offset = (page - 1) * limit

        query = db.query(BillOfMaterials).filter(
            BillOfMaterials.organization_id == auth.organization_id
        )

        if status == "active":
            query = query.filter(BillOfMaterials.is_active == True)
        elif status == "inactive":
            query = query.filter(BillOfMaterials.is_active == False)

        if search:
            search_pattern = f"%{search}%"
            query = query.filter(
                or_(
                    BillOfMaterials.bom_code.ilike(search_pattern),
                    BillOfMaterials.bom_name.ilike(search_pattern),
                )
            )

        total_count = query.with_entities(func.count(BillOfMaterials.bom_id)).scalar() or 0
        boms = query.order_by(BillOfMaterials.bom_code).limit(limit).offset(offset).all()

        # Get stats
        total_boms = db.query(BillOfMaterials).filter(
            BillOfMaterials.organization_id == auth.organization_id
        ).count()
        active_boms = db.query(BillOfMaterials).filter(
            BillOfMaterials.organization_id == auth.organization_id,
            BillOfMaterials.is_active == True,
        ).count()

        context["boms"] = boms
        context["total_boms"] = total_boms
        context["active_boms"] = active_boms
        context["search"] = search or ""
        context["status"] = status or ""
        context["page"] = page
        context["total_pages"] = max(1, (total_count + limit - 1) // limit)

        return templates.TemplateResponse(request, "operations/inv/boms.html", context)

    def new_bom_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        """New BOM form."""
        from app.models.finance.inv.item import Item
        from app.models.finance.inv.bom import BOMType

        context = base_context(request, auth, "New Bill of Materials", "boms")

        items = (
            db.query(Item)
            .filter(
                Item.organization_id == auth.organization_id,
                Item.is_active == True,
            )
            .order_by(Item.item_code)
            .all()
        )

        context["items"] = items
        context["bom_types"] = [
            {"value": t.value, "label": t.value.replace("_", " ").title()} for t in BOMType
        ]
        context["bom"] = None

        return templates.TemplateResponse(request, "operations/inv/bom_form.html", context)

    async def create_bom_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Create new BOM."""
        from app.services.finance.inv.bom import BOMService, BOMInput, BOMComponentInput
        from app.models.finance.inv.bom import BOMType
        from uuid import UUID as UUID_Type

        form = await request.form()

        try:
            bom_input = BOMInput(
                bom_code=form.get("bom_code"),
                bom_name=form.get("bom_name"),
                item_id=UUID_Type(form.get("item_id")),
                output_quantity=Decimal(form.get("output_quantity") or "1"),
                output_uom=form.get("output_uom") or "EACH",
                bom_type=BOMType(form.get("bom_type") or "ASSEMBLY"),
                description=form.get("description") or None,
                is_default=form.get("is_default") is not None,
            )

            bom = BOMService.create_bom(db, auth.organization_id, bom_input)

            # Parse and add components from JSON
            components_json = form.get("components_json", "[]")
            try:
                components = json.loads(components_json) if components_json else []
                for idx, comp in enumerate(components):
                    if comp.get("component_item_id"):
                        comp_input = BOMComponentInput(
                            component_item_id=UUID_Type(comp["component_item_id"]),
                            quantity=Decimal(str(comp.get("quantity", 1))),
                            uom=comp.get("uom") or "EACH",
                            scrap_percent=Decimal(str(comp.get("scrap_percent", 0))),
                            line_number=idx + 1,
                        )
                        BOMService.add_component(db, auth.organization_id, bom.bom_id, comp_input)
            except json.JSONDecodeError:
                pass

            db.commit()
            return RedirectResponse(f"/operations/inv/boms/{bom.bom_id}", status_code=303)

        except Exception as e:
            db.rollback()
            context = base_context(request, auth, "New Bill of Materials", "boms")
            context["error"] = str(e)
            return templates.TemplateResponse(request, "operations/inv/bom_form.html", context)

    def bom_detail_response(
        self,
        request: Request,
        bom_id: str,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """BOM detail page."""
        from app.models.finance.inv.bom import BillOfMaterials, BOMComponent
        from app.models.finance.inv.item import Item
        from uuid import UUID as UUID_Type

        context = base_context(request, auth, "BOM Detail", "boms")

        try:
            bom_uuid = UUID_Type(bom_id)
        except ValueError:
            return RedirectResponse("/operations/inv/boms", status_code=302)

        bom = (
            db.query(BillOfMaterials)
            .filter(
                BillOfMaterials.bom_id == bom_uuid,
                BillOfMaterials.organization_id == auth.organization_id,
            )
            .first()
        )

        if not bom:
            return RedirectResponse("/operations/inv/boms", status_code=302)

        # Load item
        item = db.get(Item, bom.item_id)

        # Load components with item details
        components = (
            db.query(BOMComponent)
            .filter(
                BOMComponent.bom_id == bom_uuid,
            )
            .order_by(BOMComponent.line_number)
            .all()
        )

        components_view = []
        total_component_cost = Decimal("0")
        for comp in components:
            comp_item = db.get(Item, comp.component_item_id)
            unit_cost = (
                comp_item.average_cost
                or comp_item.standard_cost
                or Decimal("0")
                if comp_item
                else Decimal("0")
            )
            extended_cost = unit_cost * comp.quantity
            total_component_cost += extended_cost
            components_view.append(
                {
                    "component": comp,
                    "item": comp_item,
                    "unit_cost": unit_cost,
                    "extended_cost": extended_cost,
                }
            )

        context["bom"] = bom
        context["item"] = item
        context["components"] = components_view
        context["total_component_cost"] = total_component_cost

        return templates.TemplateResponse(request, "operations/inv/bom_detail.html", context)

    def list_price_lists_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        search: Optional[str] = None,
        list_type: Optional[str] = None,
        page: int = 1,
    ) -> HTMLResponse:
        """Price lists page."""
        from app.models.finance.inv.price_list import PriceList, PriceListType
        from sqlalchemy import func, or_

        context = base_context(request, auth, "Price Lists", "price_lists")
        limit = 50
        offset = (page - 1) * limit

        query = db.query(PriceList).filter(
            PriceList.organization_id == auth.organization_id
        )

        if list_type:
            try:
                query = query.filter(PriceList.price_list_type == PriceListType(list_type.upper()))
            except ValueError:
                pass

        if search:
            search_pattern = f"%{search}%"
            query = query.filter(
                or_(
                    PriceList.price_list_code.ilike(search_pattern),
                    PriceList.price_list_name.ilike(search_pattern),
                )
            )

        total_count = query.with_entities(func.count(PriceList.price_list_id)).scalar() or 0
        price_lists = query.order_by(PriceList.price_list_code).limit(limit).offset(offset).all()

        # Stats
        sales_count = db.query(PriceList).filter(
            PriceList.organization_id == auth.organization_id,
            PriceList.price_list_type == PriceListType.SALES,
        ).count()
        purchase_count = db.query(PriceList).filter(
            PriceList.organization_id == auth.organization_id,
            PriceList.price_list_type == PriceListType.PURCHASE,
        ).count()

        context["price_lists"] = price_lists
        context["sales_count"] = sales_count
        context["purchase_count"] = purchase_count
        context["search"] = search or ""
        context["list_type"] = list_type or ""
        context["page"] = page
        context["total_pages"] = max(1, (total_count + limit - 1) // limit)

        return templates.TemplateResponse(request, "operations/inv/price_lists.html", context)

    def new_price_list_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        """New price list form."""
        from app.models.finance.inv.item import Item
        from app.models.finance.inv.price_list import PriceListType
        from app.models.finance.core_fx.currency import Currency

        context = base_context(request, auth, "New Price List", "price_lists")

        items = (
            db.query(Item)
            .filter(
                Item.organization_id == auth.organization_id,
                Item.is_active == True,
            )
            .order_by(Item.item_code)
            .all()
        )

        currencies = (
            db.query(Currency)
            .filter(
                Currency.is_active == True,
            )
            .order_by(Currency.currency_code)
            .all()
        )

        context["inventory_items"] = items
        context["currencies"] = currencies
        context["price_list_types"] = [
            {"value": t.value, "label": t.value.title()} for t in PriceListType
        ]
        context["price_list"] = None

        return templates.TemplateResponse(request, "operations/inv/price_list_form.html", context)

    async def create_price_list_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Create new price list."""
        from app.services.finance.inv.price_list import PriceListService, PriceListInput, PriceListItemInput
        from app.models.finance.inv.price_list import PriceListType
        from uuid import UUID as UUID_Type

        form = await request.form()

        try:
            effective_from = None
            effective_to = None
            if form.get("effective_from"):
                effective_from = datetime.strptime(form.get("effective_from"), "%Y-%m-%d").date()
            if form.get("effective_to"):
                effective_to = datetime.strptime(form.get("effective_to"), "%Y-%m-%d").date()

            pl_input = PriceListInput(
                price_list_code=form.get("price_list_code")
                or form.get("price_list_name")[:20].upper().replace(" ", "_"),
                price_list_name=form.get("price_list_name"),
                price_list_type=PriceListType(form.get("price_list_type") or "SALES"),
                currency_code=form.get("currency_code"),
                description=form.get("description") or None,
                effective_from=effective_from,
                effective_to=effective_to,
                markup_percent=Decimal(form.get("markup_percentage"))
                if form.get("markup_percentage")
                else None,
                is_default=form.get("is_default") is not None,
            )

            price_list = PriceListService.create_price_list(db, auth.organization_id, pl_input)

            # Parse and add items from JSON
            items_json = form.get("items_json", "[]")
            try:
                items = json.loads(items_json) if items_json else []
                for item in items:
                    if item.get("item_id") and item.get("price"):
                        item_input = PriceListItemInput(
                            item_id=UUID_Type(item["item_id"]),
                            unit_price=Decimal(str(item["price"])),
                            currency_code=form.get("currency_code"),
                            min_quantity=Decimal(str(item.get("min_quantity", 1))),
                            discount_percent=Decimal(str(item.get("discount_percent", 0)))
                            if item.get("discount_percent")
                            else None,
                        )
                        PriceListService.add_item(
                            db, auth.organization_id, price_list.price_list_id, item_input
                        )
            except json.JSONDecodeError:
                pass

            db.commit()
            return RedirectResponse(f"/operations/inv/price-lists", status_code=303)

        except Exception as e:
            db.rollback()
            context = base_context(request, auth, "New Price List", "price_lists")
            context["error"] = str(e)
            return templates.TemplateResponse(request, "operations/inv/price_list_form.html", context)

    def list_lots_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        search: Optional[str] = None,
        status: Optional[str] = None,
        warehouse: Optional[str] = None,
        page: int = 1,
    ) -> HTMLResponse:
        """Lots and serial numbers list page."""
        from app.models.finance.inv.inventory_lot import InventoryLot
        from app.models.finance.inv.item import Item
        from app.models.finance.inv.warehouse import Warehouse
        from sqlalchemy import func, or_

        context = base_context(request, auth, "Lots & Serial Numbers", "lots")
        limit = 50
        offset = (page - 1) * limit

        query = db.query(InventoryLot).filter(
            InventoryLot.organization_id == auth.organization_id
        )

        if status == "available":
            query = query.filter(
                InventoryLot.quantity_available > 0,
                InventoryLot.is_quarantined == False,
            )
        elif status == "quarantine":
            query = query.filter(InventoryLot.is_quarantined == True)
        elif status == "expired":
            query = query.filter(InventoryLot.expiry_date < datetime.now().date())
        elif status == "depleted":
            query = query.filter(InventoryLot.quantity_available <= 0)

        if warehouse:
            try:
                from uuid import UUID as UUID_Type

                wh_uuid = UUID_Type(warehouse)
                query = query.filter(InventoryLot.warehouse_id == wh_uuid)
            except ValueError:
                pass

        if search:
            search_pattern = f"%{search}%"
            query = query.filter(InventoryLot.lot_number.ilike(search_pattern))

        total_count = query.with_entities(func.count(InventoryLot.lot_id)).scalar() or 0
        lots = query.order_by(InventoryLot.received_date.desc()).limit(limit).offset(offset).all()

        # Load related data
        for lot in lots:
            lot.item = db.get(Item, lot.item_id)
            lot.warehouse = db.get(Warehouse, lot.warehouse_id) if lot.warehouse_id else None

        # Stats
        now = datetime.now().date()
        expiring_soon = datetime.now().date() + timedelta(days=30)

        total_lots = db.query(InventoryLot).filter(
            InventoryLot.organization_id == auth.organization_id
        ).count()
        available_count = db.query(InventoryLot).filter(
            InventoryLot.organization_id == auth.organization_id,
            InventoryLot.quantity_available > 0,
            InventoryLot.is_quarantined == False,
        ).count()
        expiring_count = db.query(InventoryLot).filter(
            InventoryLot.organization_id == auth.organization_id,
            InventoryLot.expiry_date != None,
            InventoryLot.expiry_date > now,
            InventoryLot.expiry_date <= expiring_soon,
        ).count()
        quarantine_count = db.query(InventoryLot).filter(
            InventoryLot.organization_id == auth.organization_id,
            InventoryLot.is_quarantined == True,
        ).count()

        # Expiring lots for alert
        expiring_lots = db.query(InventoryLot).filter(
            InventoryLot.organization_id == auth.organization_id,
            InventoryLot.expiry_date != None,
            InventoryLot.expiry_date > now,
            InventoryLot.expiry_date <= expiring_soon,
            InventoryLot.quantity_available > 0,
        ).all()

        # Get warehouses for filter
        warehouses = db.query(Warehouse).filter(
            Warehouse.organization_id == auth.organization_id,
            Warehouse.is_active == True,
        ).order_by(Warehouse.warehouse_code).all()

        context["lots"] = lots
        context["total_count"] = total_lots
        context["available_count"] = available_count
        context["expiring_count"] = expiring_count
        context["quarantine_count"] = quarantine_count
        context["expiring_lots"] = expiring_lots
        context["warehouses"] = warehouses
        context["search"] = search or ""
        context["status"] = status or ""
        context["warehouse"] = warehouse or ""
        context["page"] = page
        context["total_pages"] = max(1, (total_count + limit - 1) // limit)
        context["now"] = now

        return templates.TemplateResponse(request, "operations/inv/lots.html", context)

    def lot_detail_response(
        self,
        request: Request,
        lot_id: str,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Lot detail page."""
        from app.models.finance.inv.inventory_lot import InventoryLot
        from app.models.finance.inv.item import Item
        from app.models.finance.inv.warehouse import Warehouse
        from app.models.finance.inv.inventory_transaction import InventoryTransaction
        from uuid import UUID as UUID_Type

        context = base_context(request, auth, "Lot Detail", "lots")

        try:
            lot_uuid = UUID_Type(lot_id)
        except ValueError:
            return RedirectResponse("/operations/inv/lots", status_code=302)

        lot = (
            db.query(InventoryLot)
            .filter(
                InventoryLot.lot_id == lot_uuid,
                InventoryLot.organization_id == auth.organization_id,
            )
            .first()
        )

        if not lot:
            return RedirectResponse("/operations/inv/lots", status_code=302)

        lot.item = db.get(Item, lot.item_id)
        lot.warehouse = db.get(Warehouse, lot.warehouse_id) if lot.warehouse_id else None

        # Get transaction history for this lot
        transactions = (
            db.query(InventoryTransaction)
            .filter(
                InventoryTransaction.lot_id == lot_uuid
            )
            .order_by(InventoryTransaction.transaction_date.desc())
            .limit(50)
            .all()
        )

        context["lot"] = lot
        context["transactions"] = transactions
        context["now"] = date.today()

        return templates.TemplateResponse(request, "operations/inv/lot_detail.html", context)

    def toggle_lot_quarantine_response(
        self,
        lot_id: str,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        """Toggle lot quarantine status."""
        from app.models.finance.inv.inventory_lot import InventoryLot
        from uuid import UUID as UUID_Type

        try:
            lot_uuid = UUID_Type(lot_id)
            lot = (
                db.query(InventoryLot)
                .filter(
                    InventoryLot.lot_id == lot_uuid,
                    InventoryLot.organization_id == auth.organization_id,
                )
                .first()
            )

            if lot:
                lot.is_quarantined = not lot.is_quarantined
                db.commit()
        except Exception:
            db.rollback()

        return RedirectResponse(f"/operations/inv/lots/{lot_id}", status_code=303)

    def inventory_reports_hub_response(
        self,
        request: Request,
        auth: WebAuthContext,
    ) -> HTMLResponse:
        """Inventory reports hub page."""
        context = base_context(request, auth, "Inventory Reports", "inv_reports")
        return templates.TemplateResponse(request, "operations/inv/reports.html", context)

    def stock_on_hand_report_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        warehouse: Optional[str] = None,
        category: Optional[str] = None,
        show_zero: Optional[str] = None,
        format: Optional[str] = None,
        page: int = 1,
    ) -> HTMLResponse | Response:
        """Stock on hand report."""
        from app.models.finance.inv.item import Item
        from app.models.finance.inv.item_category import ItemCategory
        from app.models.finance.inv.warehouse import Warehouse
        from app.services.finance.inv.balance import inventory_balance_service
        from uuid import UUID as UUID_Type

        context = base_context(request, auth, "Stock on Hand", "inv_reports")
        limit = 100
        offset = (page - 1) * limit

        # Get filters
        wh_id = UUID_Type(warehouse) if warehouse else None
        cat_id = UUID_Type(category) if category else None
        include_zero = show_zero == "1"

        # Get all items with inventory tracking
        items_query = db.query(Item).filter(
            Item.organization_id == auth.organization_id,
            Item.is_active == True,
            Item.track_inventory == True,
        )

        if cat_id:
            items_query = items_query.filter(Item.category_id == cat_id)

        items = items_query.order_by(Item.item_code).all()

        # Build stock data
        stock_data = []
        total_quantity = Decimal("0")
        total_reserved = Decimal("0")
        total_available = Decimal("0")
        total_value = Decimal("0")
        below_reorder = 0

        for item in items:
            try:
                summary = inventory_balance_service.get_item_stock_summary(
                    db, auth.organization_id, item.item_id
                )

                on_hand = summary.total_on_hand or Decimal("0")
                reserved = summary.total_reserved or Decimal("0")
                available = summary.total_available or Decimal("0")
                unit_cost = item.average_cost or item.standard_cost or Decimal("0")
                item_value = on_hand * unit_cost

                if not include_zero and on_hand <= 0:
                    continue

                category_obj = db.get(ItemCategory, item.category_id)
                is_low = item.reorder_point and on_hand <= item.reorder_point

                if is_low:
                    below_reorder += 1

                stock_data.append(
                    {
                        "item_id": str(item.item_id),
                        "item_code": item.item_code,
                        "item_name": item.item_name,
                        "category_name": category_obj.category_name if category_obj else "-",
                        "warehouse_name": "All",
                        "on_hand": on_hand,
                        "reserved": reserved,
                        "available": available,
                        "unit_cost": unit_cost,
                        "total_value": item_value,
                        "is_low_stock": is_low,
                    }
                )

                total_quantity += on_hand
                total_reserved += reserved
                total_available += available
                total_value += item_value

            except Exception:
                continue

        # Handle CSV export
        if format == "csv":
            import csv
            import io

            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(
                [
                    "Item Code",
                    "Item Name",
                    "Category",
                    "On Hand",
                    "Reserved",
                    "Available",
                    "Unit Cost",
                    "Total Value",
                ]
            )
            for row in stock_data:
                writer.writerow(
                    [
                        row["item_code"],
                        row["item_name"],
                        row["category_name"],
                        row["on_hand"],
                        row["reserved"],
                        row["available"],
                        row["unit_cost"],
                        row["total_value"],
                    ]
                )
            csv_content = output.getvalue()
            return Response(
                content=csv_content,
                media_type="text/csv",
                headers={"Content-Disposition": "attachment; filename=stock_on_hand.csv"},
            )

        # Paginate
        total_items = len(stock_data)
        stock_data = stock_data[offset:offset + limit]
        total_pages = max(1, (total_items + limit - 1) // limit)

        # Get warehouses and categories for filters
        warehouses = (
            db.query(Warehouse)
            .filter(
                Warehouse.organization_id == auth.organization_id,
                Warehouse.is_active == True,
            )
            .order_by(Warehouse.warehouse_code)
            .all()
        )

        categories = (
            db.query(ItemCategory)
            .filter(
                ItemCategory.organization_id == auth.organization_id,
                ItemCategory.is_active == True,
            )
            .order_by(ItemCategory.category_code)
            .all()
        )

        context["stock_data"] = stock_data
        context["summary"] = {
            "total_items": total_items,
            "total_quantity": total_quantity,
            "total_reserved": total_reserved,
            "total_available": total_available,
            "total_value": total_value,
            "below_reorder": below_reorder,
        }
        context["warehouses"] = warehouses
        context["categories"] = categories
        context["warehouse"] = warehouse or ""
        context["category"] = category or ""
        context["show_zero"] = include_zero
        context["page"] = page
        context["total_pages"] = total_pages

        return templates.TemplateResponse(request, "operations/inv/report_stock_on_hand.html", context)


operations_inv_web_service = OperationsInventoryWebService()
