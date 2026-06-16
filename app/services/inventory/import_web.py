"""
Inventory import web service.

Builds Inventory-specific import pages while reusing the shared import engine.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from app.templates import templates
from app.web.deps import WebAuthContext, base_context


def _normalize_alias(value: str) -> str:
    return value.strip().lower().replace(" ", "_")


def _build_item_alias_map() -> dict[str, str]:
    """Build item-specific aliases for the browser-side import wizard."""
    aliases = {
        "item_name": [
            "Item Name",
            "ItemName",
            "Name",
            "Product Name",
            "Product",
            "Service Name",
            "item_name",
        ],
        "item_code": [
            "Item Code",
            "ItemCode",
            "Code",
            "SKU",
            "Sku",
            "Product Code",
            "Part Number",
            "Part No",
            "item_code",
            "sku",
        ],
        "description": ["Description", "Item Description", "description"],
        "category_name": [
            "Category",
            "Category Name",
            "Item Group",
            "Product Category",
            "category_name",
        ],
        "base_uom": ["Base UOM", "Unit", "UOM", "Base Unit", "base_uom"],
        "purchase_uom": ["Purchase UOM", "Purchase Unit", "purchase_uom"],
        "sales_uom": ["Sales UOM", "Sales Unit", "sales_uom"],
        "item_type_str": ["Item Type", "Type", "item_type", "item_type_str"],
        "costing_method_str": [
            "Costing Method",
            "Cost Method",
            "costing_method",
            "costing_method_str",
        ],
        "standard_cost": ["Standard Cost", "standard_cost"],
        "purchase_cost": [
            "Purchase Price",
            "Cost",
            "Unit Cost",
            "Cost Price",
            "Buy Price",
            "purchase_cost",
        ],
        "list_price": [
            "Selling Price",
            "Sales Price",
            "List Price",
            "Unit Price",
            "Price",
            "Rate",
            "list_price",
        ],
        "currency_code": ["Currency Code", "Currency", "currency_code"],
        "reorder_point": ["Reorder Point", "Reorder Level", "reorder_point"],
        "reorder_quantity": ["Reorder Quantity", "reorder_quantity"],
        "minimum_stock": ["Minimum Stock", "minimum_stock"],
        "maximum_stock": ["Maximum Stock", "maximum_stock"],
        "lead_time_days": ["Lead Time Days", "lead_time_days"],
        "track_inventory": ["Track Inventory", "track_inventory"],
        "track_lots": ["Track Lots", "track_lots"],
        "track_serial_numbers": [
            "Track Serial Numbers",
            "Track Serials",
            "track_serial_numbers",
        ],
        "is_purchaseable": ["Is Purchaseable", "Purchasable", "is_purchaseable"],
        "is_saleable": ["Is Saleable", "Sellable", "is_saleable"],
        "barcode": ["Barcode", "Bar Code", "barcode"],
        "manufacturer_part_number": [
            "Manufacturer Part Number",
            "Manufacturer Part No",
            "MPN",
            "manufacturer_part_number",
        ],
        "weight": ["Weight", "weight"],
        "weight_uom": ["Weight Unit", "Weight UOM", "weight_uom"],
    }
    alias_map: dict[str, str] = {}
    for target, values in aliases.items():
        alias_map.setdefault(_normalize_alias(target), target)
        for value in values:
            alias_map.setdefault(_normalize_alias(value), target)
    return alias_map


def _build_target_fields(
    columns: dict[str, list[str]],
) -> list[dict[str, str | bool]]:
    """Build target_fields list from column requirements for the wizard."""
    target_by_label = {
        "Item Name": "item_name",
        "Item Code": "item_code",
        "SKU": "sku",
        "Description": "description",
        "Category": "category_name",
        "Category Name": "category_name",
        "Base UOM": "base_uom",
        "Purchase UOM": "purchase_uom",
        "Sales UOM": "sales_uom",
        "Item Type": "item_type_str",
        "Costing Method": "costing_method_str",
        "Standard Cost": "standard_cost",
        "Purchase Price": "purchase_cost",
        "Selling Price": "list_price",
        "Currency Code": "currency_code",
        "Reorder Point": "reorder_point",
        "Reorder Quantity": "reorder_quantity",
        "Minimum Stock": "minimum_stock",
        "Maximum Stock": "maximum_stock",
        "Lead Time Days": "lead_time_days",
        "Track Inventory": "track_inventory",
        "Track Lots": "track_lots",
        "Track Serial Numbers": "track_serial_numbers",
        "Is Purchaseable": "is_purchaseable",
        "Is Saleable": "is_saleable",
        "Barcode": "barcode",
        "Manufacturer Part Number": "manufacturer_part_number",
        "Weight": "weight",
        "Weight Unit": "weight_uom",
    }
    fields: list[dict[str, str | bool]] = []
    for col in columns.get("required", []):
        fields.append(
            {
                "source_field": col,
                "target_field": target_by_label.get(col, col),
                "required": True,
            }
        )
    for col in columns.get("optional", []):
        fields.append(
            {
                "source_field": col,
                "target_field": target_by_label.get(col, col),
                "required": False,
            }
        )
    return fields


def _is_truthy_form_value(value: str | None) -> bool:
    return value is not None and value.lower() in ("true", "1", "on", "")


class InventoryImportWebService:
    """Web-facing Inventory import workflow."""

    SUPPORTED_ENTITY_TYPES = {"items": "Inventory Items"}

    ITEM_COLUMNS = {
        "required": ["Item Name"],
        "optional": [
            "Item Code",
            "SKU",
            "Description",
            "Category",
            "Category Name",
            "Base UOM",
            "Purchase UOM",
            "Sales UOM",
            "Item Type",
            "Costing Method",
            "Standard Cost",
            "Purchase Price",
            "Selling Price",
            "Currency Code",
            "Reorder Point",
            "Reorder Quantity",
            "Minimum Stock",
            "Maximum Stock",
            "Lead Time Days",
            "Track Inventory",
            "Track Lots",
            "Track Serial Numbers",
            "Is Purchaseable",
            "Is Saleable",
            "Barcode",
            "Manufacturer Part Number",
            "Weight",
            "Weight Unit",
        ],
    }

    def dashboard_response(
        self,
        request: Request,
        auth: WebAuthContext,
    ) -> HTMLResponse:
        """Render the Inventory import dashboard."""
        context = base_context(request, auth, "Import Inventory", "items")
        context["entity_types"] = [
            {
                "id": "items",
                "name": "Inventory Items",
                "description": "Import products, services, categories, and stock settings",
                "order": 1,
            }
        ]
        return templates.TemplateResponse(
            request,
            "inventory/import_export/dashboard.html",
            context,
        )

    def import_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        entity_type: str,
    ) -> HTMLResponse:
        """Render the Inventory item import wizard."""
        if entity_type not in self.SUPPORTED_ENTITY_TYPES:
            raise HTTPException(
                status_code=404,
                detail=f"Unsupported import entity: {entity_type}",
            )

        columns = self.ITEM_COLUMNS
        context = base_context(
            request,
            auth,
            f"Import {self.SUPPORTED_ENTITY_TYPES[entity_type]}",
            "items",
        )
        context["entity_type"] = entity_type
        context["entity_name"] = self.SUPPORTED_ENTITY_TYPES[entity_type]
        context["columns"] = columns
        context["preview_url"] = f"/inventory/import/{entity_type}/preview"
        context["import_url"] = f"/inventory/import/{entity_type}"
        context["cancel_url"] = "/inventory/import"
        context["alias_map"] = _build_item_alias_map()
        context["target_fields"] = _build_target_fields(columns)
        context["accent_color"] = "emerald"
        return templates.TemplateResponse(
            request,
            "inventory/import_export/import_form.html",
            context,
        )

    async def preview_response(
        self,
        *,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        entity_type: str,
        file: UploadFile,
    ) -> JSONResponse:
        """Preview an Inventory import file."""
        del request
        if entity_type not in self.SUPPORTED_ENTITY_TYPES:
            return JSONResponse(
                content={"detail": f"Unsupported import entity: {entity_type}"},
                status_code=404,
            )
        if not auth.organization_id or not auth.person_id:
            return JSONResponse(
                content={"detail": "Missing user or organization context."},
                status_code=401,
            )
        try:
            from app.services.finance.import_export.web import import_web_service

            result = await import_web_service.preview_import(
                db=db,
                organization_id=auth.organization_id,
                user_id=auth.person_id,
                entity_type=entity_type,
                file=file,
            )
            return JSONResponse(content=result)
        except ValueError as exc:
            return JSONResponse(content={"detail": str(exc)}, status_code=400)
        except Exception as exc:
            return JSONResponse(
                content={"detail": f"Preview failed: {str(exc)}"},
                status_code=500,
            )

    async def execute_response(
        self,
        *,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        entity_type: str,
        file: UploadFile,
        skip_duplicates: str | None,
        dry_run: str | None,
        column_mapping: str | None,
    ) -> JSONResponse:
        """Execute an Inventory import."""
        del request
        if entity_type not in self.SUPPORTED_ENTITY_TYPES:
            return JSONResponse(
                content={"detail": f"Unsupported import entity: {entity_type}"},
                status_code=404,
            )
        if not auth.organization_id or not auth.person_id:
            return JSONResponse(
                content={"detail": "Missing user or organization context."},
                status_code=401,
            )

        try:
            from app.services.finance.import_export.web import import_web_service

            mapping: dict[str, Any] | None = (
                json.loads(column_mapping) if column_mapping else None
            )
            result = await import_web_service.execute_import(
                db=db,
                organization_id=auth.organization_id,
                user_id=auth.person_id,
                entity_type=entity_type,
                file=file,
                skip_duplicates=_is_truthy_form_value(skip_duplicates),
                dry_run=_is_truthy_form_value(dry_run),
                column_mapping=mapping,
            )
            return JSONResponse(content=result)
        except ValueError as exc:
            return JSONResponse(content={"detail": str(exc)}, status_code=400)
        except json.JSONDecodeError as exc:
            return JSONResponse(
                content={"detail": f"Invalid column mapping: {str(exc)}"},
                status_code=400,
            )
        except Exception as exc:
            return JSONResponse(
                content={"detail": f"Import failed: {str(exc)}"},
                status_code=500,
            )


inventory_import_web_service = InventoryImportWebService()
