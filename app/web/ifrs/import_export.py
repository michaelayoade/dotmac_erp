"""
Import/Export Web Routes.

HTML template routes for data import functionality.
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.web.deps import get_db, require_web_auth, WebAuthContext, base_context

templates = Jinja2Templates(directory="templates")

router = APIRouter(prefix="/import", tags=["import-web"])


@router.get("", response_class=HTMLResponse)
def import_dashboard(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
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

    return templates.TemplateResponse(request, "ifrs/import_export/dashboard.html", context)


@router.get("/{entity_type}", response_class=HTMLResponse)
def import_form(
    request: Request,
    entity_type: str,
    auth: WebAuthContext = Depends(require_web_auth),
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
            "optional": ["Account Code", "Description", "Currency", "Status", "Parent Account"],
        },
        "customers": {
            "required": ["Display Name OR Company Name"],
            "optional": ["Phone", "Email", "Currency Code", "Credit Limit", "Payment Terms", "Billing Address"],
        },
        "suppliers": {
            "required": ["Display Name OR Contact Name"],
            "optional": ["Phone", "Email", "Currency Code", "Payment Terms", "Billing Address"],
        },
        "items": {
            "required": ["Item Name OR Name"],
            "optional": ["Item Code", "SKU", "Description", "Unit Price", "Selling Price", "Category"],
        },
        "assets": {
            "required": ["Asset Name"],
            "optional": ["Asset Number", "Acquisition Date", "Acquisition Cost", "Category", "Useful Life"],
        },
        "bank_accounts": {
            "required": ["Bank Name", "Account Number"],
            "optional": ["Account Type", "Currency", "IBAN", "Branch Name", "Opening Balance"],
        },
        "invoices": {
            "required": ["Customer Name", "Total Amount"],
            "optional": ["Invoice Number", "Invoice Date", "Due Date", "Tax Amount", "Status"],
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

    context = base_context(request, auth, f"Import {entity_names.get(entity_type, entity_type)}", "import")
    context["entity_type"] = entity_type
    context["entity_name"] = entity_names.get(entity_type, entity_type)
    context["columns"] = entity_columns.get(entity_type, {"required": [], "optional": []})

    return templates.TemplateResponse(request, "ifrs/import_export/import_form.html", context)
