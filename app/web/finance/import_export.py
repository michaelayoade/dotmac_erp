"""
Import/Export Web Routes.

HTML template routes for data import functionality.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from app.services.finance.import_export.web import import_web_service
from app.templates import templates
from app.web.deps import WebAuthContext, base_context, get_db, require_finance_access


def _build_target_fields(
    columns: dict[str, list[str]],
) -> list[dict[str, str | bool]]:
    """Build target_fields list from column requirements for the wizard."""
    fields: list[dict[str, str | bool]] = []
    for col in columns.get("required", []):
        fields.append({"source_field": col, "target_field": col, "required": True})
    for col in columns.get("optional", []):
        fields.append({"source_field": col, "target_field": col, "required": False})
    return fields


router = APIRouter(prefix="/import", tags=["import-web"])


@router.get("", response_class=HTMLResponse)
def import_dashboard(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Data import dashboard page."""
    context = base_context(request, auth, "Data Import", "import")

    # Define supported entity types with their info
    entity_types = [
        {
            "id": "accounts",
            "name": "Chart of Accounts",
            "description": "Import account categories and GL accounts",
            "icon": "book-open",
            "order": 1,
            "prereqs": [],
        },
        {
            "id": "customers",
            "name": "Customers",
            "description": "Import customer contacts and addresses",
            "icon": "users",
            "order": 2,
            "prereqs": ["accounts"],
        },
        {
            "id": "suppliers",
            "name": "Suppliers/Vendors",
            "description": "Import vendor contacts and payment terms",
            "icon": "truck",
            "order": 3,
            "prereqs": ["accounts"],
        },
        {
            "id": "items",
            "name": "Inventory Items",
            "description": "Import products, services, and inventory",
            "icon": "cube",
            "order": 4,
            "prereqs": ["accounts"],
        },
        {
            "id": "assets",
            "name": "Fixed Assets",
            "description": "Import fixed assets and depreciation schedules",
            "icon": "building-office",
            "order": 5,
            "prereqs": ["accounts"],
        },
        {
            "id": "bank_accounts",
            "name": "Bank Accounts",
            "description": "Import bank account details",
            "icon": "building-library",
            "order": 6,
            "prereqs": ["accounts"],
        },
        {
            "id": "invoices",
            "name": "Customer Invoices",
            "description": "Import sales invoices and credit notes",
            "icon": "document-text",
            "order": 7,
            "prereqs": ["customers"],
        },
        {
            "id": "expenses",
            "name": "Expenses",
            "description": "Import expense entries",
            "icon": "receipt-percent",
            "order": 8,
            "prereqs": ["accounts"],
        },
        {
            "id": "customer_payments",
            "name": "Customer Payments",
            "description": "Import payment receipts from customers",
            "icon": "banknotes",
            "order": 9,
            "prereqs": ["customers"],
        },
        {
            "id": "supplier_payments",
            "name": "Supplier Payments",
            "description": "Import payments to vendors",
            "icon": "credit-card",
            "order": 10,
            "prereqs": ["suppliers"],
        },
    ]

    context["entity_types"] = entity_types

    return templates.TemplateResponse(
        request, "finance/import_export/dashboard.html", context
    )


@router.get("/{entity_type}", response_class=HTMLResponse)
def import_form(
    request: Request,
    entity_type: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Import form for a specific entity type."""
    entity_names = {
        "accounts": "Chart of Accounts",
        "customers": "Customers",
        "suppliers": "Suppliers/Vendors",
        "items": "Inventory Items",
        "assets": "Fixed Assets",
        "bank_accounts": "Bank Accounts",
        "invoices": "Customer Invoices",
        "expenses": "Expenses",
        "customer_payments": "Customer Payments",
        "supplier_payments": "Supplier Payments",
    }

    entity_columns = {
        "accounts": {
            "required": ["Account Name", "Account Type"],
            "optional": [
                "Account Code",
                "Description",
                "Currency",
                "Status",
                "Parent Account",
            ],
        },
        "customers": {
            "required": ["Display Name OR Company Name"],
            "optional": [
                "Phone",
                "Email",
                "Currency Code",
                "Credit Limit",
                "Payment Terms",
                "Billing Address",
            ],
        },
        "suppliers": {
            "required": ["Display Name OR Contact Name"],
            "optional": [
                "Phone",
                "Email",
                "Currency Code",
                "Payment Terms",
                "Billing Address",
            ],
        },
        "items": {
            "required": ["Item Name OR Name"],
            "optional": [
                "Item Code",
                "SKU",
                "Description",
                "Unit Price",
                "Selling Price",
                "Category",
            ],
        },
        "assets": {
            "required": ["Asset Name"],
            "optional": [
                "Asset Number",
                "Acquisition Date",
                "Acquisition Cost",
                "Category",
                "Useful Life",
            ],
        },
        "bank_accounts": {
            "required": ["Bank Name", "Account Number"],
            "optional": [
                "Account Type",
                "Currency",
                "IBAN",
                "Branch Name",
                "Opening Balance",
            ],
        },
        "invoices": {
            "required": ["Customer Name", "Total Amount"],
            "optional": [
                "Invoice Number",
                "Invoice Date",
                "Due Date",
                "Tax Amount",
                "Status",
            ],
        },
        "expenses": {
            "required": ["Amount"],
            "optional": ["Date", "Description", "Category", "Payment Method", "Payee"],
        },
        "customer_payments": {
            "required": ["Customer Name", "Amount"],
            "optional": ["Payment Date", "Reference", "Payment Method"],
        },
        "supplier_payments": {
            "required": ["Vendor Name", "Amount"],
            "optional": ["Payment Date", "Reference", "Payment Method"],
        },
    }

    from app.services.finance.import_export.base import build_alias_map

    columns = entity_columns.get(entity_type, {"required": [], "optional": []})
    context = base_context(
        request, auth, f"Import {entity_names.get(entity_type, entity_type)}", "import"
    )
    context["entity_type"] = entity_type
    context["entity_name"] = entity_names.get(entity_type, entity_type)
    context["columns"] = columns
    # Wizard context
    context["preview_url"] = f"/import/{entity_type}/preview"
    context["import_url"] = f"/import/{entity_type}"
    context["cancel_url"] = "/import"
    context["alias_map"] = build_alias_map()
    context["target_fields"] = _build_target_fields(columns)
    context["accent_color"] = "teal"

    return templates.TemplateResponse(
        request, "finance/import_export/import_form.html", context
    )


@router.post("/{entity_type}/preview", response_class=JSONResponse)
async def preview_import(
    request: Request,
    entity_type: str,
    file: UploadFile = File(...),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Preview import with validation and column mapping (web route)."""
    try:
        result = await import_web_service.preview_import(
            db=db,
            organization_id=auth.organization_id,
            user_id=auth.person_id,
            entity_type=entity_type,
            file=file,
        )
        return JSONResponse(content=result)
    except ValueError as e:
        return JSONResponse(content={"detail": str(e)}, status_code=400)
    except Exception as e:
        return JSONResponse(
            content={"detail": f"Preview failed: {str(e)}"}, status_code=500
        )


@router.post("/{entity_type}", response_class=JSONResponse)
async def execute_import(
    request: Request,
    entity_type: str,
    file: UploadFile = File(...),
    skip_duplicates: str | None = Form(default=None),
    dry_run: str | None = Form(default=None),
    column_mapping: str | None = Form(default=None),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Execute import operation (web route)."""
    import json

    try:
        skip_dups = skip_duplicates is not None and skip_duplicates.lower() in (
            "true",
            "1",
            "on",
            "",
        )
        is_dry_run = dry_run is not None and dry_run.lower() in ("true", "1", "on", "")
        mapping = json.loads(column_mapping) if column_mapping else None

        result = await import_web_service.execute_import(
            db=db,
            organization_id=auth.organization_id,
            user_id=auth.person_id,
            entity_type=entity_type,
            file=file,
            skip_duplicates=skip_dups,
            dry_run=is_dry_run,
            column_mapping=mapping,
        )
        return JSONResponse(content=result)
    except ValueError as e:
        return JSONResponse(content={"detail": str(e)}, status_code=400)
    except Exception as e:
        return JSONResponse(
            content={"detail": f"Import failed: {str(e)}"}, status_code=500
        )
