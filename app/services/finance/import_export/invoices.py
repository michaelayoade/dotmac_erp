"""
Invoices Importer.

Imports customer invoices from CSV data into the AR system.
"""

import logging
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.finance.ar.customer import Customer
from app.models.finance.ar.invoice import Invoice, InvoiceStatus, InvoiceType
from app.models.finance.ar.invoice_line import InvoiceLine

from .base import BaseImporter, FieldMapping, ImportConfig

logger = logging.getLogger(__name__)


class InvoiceImporter(BaseImporter[Invoice]):
    """
    Importer for customer invoices from CSV data.

    Expected CSV columns (flexible - maps common naming conventions):
    - Invoice Number / Invoice No / Number: Invoice number (required)
    - Invoice Date / Date: Invoice date (required)
    - Due Date: Payment due date
    - Customer Name / Customer / Client: Customer name (required)
    - Invoice Type / Type: STANDARD, CREDIT_NOTE, DEBIT_NOTE, PROFORMA
    - Currency Code / Currency: Currency (defaults to configured organization currency)
    - Exchange Rate: Exchange rate to functional currency
    - Subtotal / Net Amount: Subtotal before tax
    - Tax Amount / VAT / Tax: Tax amount
    - Total Amount / Total / Gross Amount: Total amount
    - Amount Paid / Paid: Amount already paid
    - Status: DRAFT, POSTED, PAID, etc.
    - Notes / Description: Invoice notes

    Line items (for single-line import):
    - Item Name / Description / Product: Line item description
    - Quantity / Qty: Quantity
    - Unit Price / Price / Rate: Unit price
    - Line Amount / Amount: Line total
    - Discount / Discount Amount: Discount
    """

    entity_name = "Invoice"
    model_class = Invoice

    def __init__(
        self,
        db: Session,
        config: ImportConfig,
        ar_control_account_id: UUID,
        default_revenue_account_id: UUID,
    ):
        super().__init__(db, config)
        self.ar_control_account_id = ar_control_account_id
        self.default_revenue_account_id = default_revenue_account_id
        self._customer_cache: dict[str, UUID] = {}
        self._invoice_number_counter = 0

    def get_field_mappings(self) -> list[FieldMapping]:
        """Define flexible field mappings supporting various CSV formats."""
        return [
            # Invoice header
            FieldMapping("Invoice Number", "invoice_number", required=False),
            FieldMapping("Invoice No", "invoice_no_alt", required=False),
            FieldMapping("Number", "number_alt", required=False),
            FieldMapping(
                "Invoice Date",
                "invoice_date",
                required=False,
                transformer=self.parse_date,
            ),
            FieldMapping(
                "Date", "date_alt", required=False, transformer=self.parse_date
            ),
            FieldMapping(
                "Due Date", "due_date", required=False, transformer=self.parse_date
            ),
            # Customer
            FieldMapping("Customer Name", "customer_name", required=False),
            FieldMapping("Customer", "customer_alt", required=False),
            FieldMapping("Client", "client_alt", required=False),
            # Type
            FieldMapping("Invoice Type", "invoice_type_str", required=False),
            FieldMapping("Type", "type_alt", required=False),
            # Currency
            FieldMapping(
                "Currency Code",
                "currency_code",
                required=False,
                default=settings.default_functional_currency_code,
            ),
            FieldMapping("Currency", "currency_alt", required=False),
            FieldMapping(
                "Exchange Rate",
                "exchange_rate",
                required=False,
                transformer=self.parse_decimal,
                default=Decimal("1"),
            ),
            # Amounts
            FieldMapping(
                "Subtotal", "subtotal", required=False, transformer=self.parse_decimal
            ),
            FieldMapping(
                "Net Amount",
                "net_amount_alt",
                required=False,
                transformer=self.parse_decimal,
            ),
            FieldMapping(
                "Tax Amount",
                "tax_amount",
                required=False,
                transformer=self.parse_decimal,
                default=Decimal("0"),
            ),
            FieldMapping(
                "VAT", "vat_alt", required=False, transformer=self.parse_decimal
            ),
            FieldMapping(
                "Tax", "tax_alt", required=False, transformer=self.parse_decimal
            ),
            FieldMapping(
                "Total Amount",
                "total_amount",
                required=False,
                transformer=self.parse_decimal,
            ),
            FieldMapping(
                "Total", "total_alt", required=False, transformer=self.parse_decimal
            ),
            FieldMapping(
                "Gross Amount",
                "gross_alt",
                required=False,
                transformer=self.parse_decimal,
            ),
            FieldMapping(
                "Amount Paid",
                "amount_paid",
                required=False,
                transformer=self.parse_decimal,
                default=Decimal("0"),
            ),
            FieldMapping(
                "Paid", "paid_alt", required=False, transformer=self.parse_decimal
            ),
            # Status
            FieldMapping("Invoice Status", "status_str", required=False),
            FieldMapping("Status", "status_alt", required=False),
            # Notes
            FieldMapping("Notes", "notes", required=False),
            FieldMapping("Description", "description_alt", required=False),
            # Line item (for single-line invoices)
            FieldMapping("Item Name", "line_item_name", required=False),
            FieldMapping("Item Desc", "line_item_desc", required=False),
            FieldMapping("Product", "line_product_alt", required=False),
            FieldMapping(
                "Quantity",
                "line_quantity",
                required=False,
                transformer=self.parse_decimal,
                default=Decimal("1"),
            ),
            FieldMapping(
                "Qty", "line_qty_alt", required=False, transformer=self.parse_decimal
            ),
            FieldMapping(
                "Unit Price",
                "line_unit_price",
                required=False,
                transformer=self.parse_decimal,
            ),
            FieldMapping(
                "Price",
                "line_price_alt",
                required=False,
                transformer=self.parse_decimal,
            ),
            FieldMapping(
                "Rate", "line_rate_alt", required=False, transformer=self.parse_decimal
            ),
            FieldMapping(
                "Line Amount",
                "line_amount",
                required=False,
                transformer=self.parse_decimal,
            ),
            FieldMapping(
                "Amount",
                "line_amount_alt",
                required=False,
                transformer=self.parse_decimal,
            ),
            FieldMapping(
                "Item Total",
                "line_item_total",
                required=False,
                transformer=self.parse_decimal,
            ),
            FieldMapping(
                "Item Price",
                "line_item_price",
                required=False,
                transformer=self.parse_decimal,
            ),
            FieldMapping(
                "Discount",
                "line_discount",
                required=False,
                transformer=self.parse_decimal,
                default=Decimal("0"),
            ),
            FieldMapping(
                "Discount Amount",
                "line_discount_amount_alt",
                required=False,
                transformer=self.parse_decimal,
            ),
            FieldMapping(
                "Item Tax Amount",
                "line_tax_amount",
                required=False,
                transformer=self.parse_decimal,
            ),
            FieldMapping(
                "Tax Amount",
                "line_tax_amount_alt",
                required=False,
                transformer=self.parse_decimal,
            ),
            FieldMapping(
                "Balance", "balance_alt", required=False, transformer=self.parse_decimal
            ),
        ]

    def get_unique_key(self, row: dict[str, Any]) -> str:
        """Unique key is invoice number."""
        return (
            row.get("Invoice Number")
            or row.get("Invoice No")
            or row.get("Number")
            or row.get("Invoice ID")
            or ""
        ).strip()

    def _import_rows(self, rows: list[dict[str, Any]]) -> None:
        """Import rows grouped by invoice number to preserve multi-line invoices."""
        from collections import defaultdict

        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            key = self.get_unique_key(row)
            if not key:
                self.result.add_error(0, "Missing invoice number", "Invoice Number")
                self.result.skipped_count += 1
                continue
            groups[key].append(row)

        batch = []

        for idx, (invoice_key, group_rows) in enumerate(groups.items(), start=1):
            if self.config.stop_on_error and self.result.error_count > 0:
                break

            header = group_rows[0]
            if not self.validate_with_rules(header, idx):
                self.result.skipped_count += 1
                continue

            if self.config.skip_duplicates:
                existing = self.check_duplicate(header)
                if existing:
                    self.result.duplicate_count += 1
                    self.result.skipped_count += 1
                    self.result.add_warning(
                        idx, f"Duplicate invoice skipped (key: {invoice_key})"
                    )
                    continue

            try:
                header_data = self.transform_row(header, idx)
                line_data = [self.transform_row(row, idx) for row in group_rows]
                invoice = self._build_invoice_from_rows(header_data, line_data)
                if not self.config.dry_run:
                    batch.append(invoice)
                    if len(batch) >= self.config.batch_size:
                        self._commit_batch(batch)
                        batch = []
                self.result.imported_count += 1
            except Exception as exc:
                self.result.add_error(idx, str(exc), None)
                self.result.skipped_count += 1

        if batch and not self.config.dry_run:
            self._commit_batch(batch)

    def _build_invoice_from_rows(
        self,
        header: dict[str, Any],
        line_rows: list[dict[str, Any]],
    ) -> Invoice:
        """Create a single invoice with multiple lines from grouped rows."""
        invoice_number = (
            header.get("invoice_number")
            or header.get("invoice_no_alt")
            or header.get("number_alt")
            or ""
        ).strip()
        if not invoice_number:
            self._invoice_number_counter += 1
            invoice_number = f"INV{self._invoice_number_counter:06d}"

        invoice_date = (
            header.get("invoice_date") or header.get("date_alt") or date.today()
        )
        due_date = header.get("due_date") or invoice_date

        customer_name = (
            header.get("customer_name")
            or header.get("customer_alt")
            or header.get("client_alt")
            or "Unknown Customer"
        ).strip()
        customer_id = self._get_customer_id(customer_name)

        type_str = (
            header.get("invoice_type_str") or header.get("type_alt") or "STANDARD"
        )
        invoice_type = self._parse_invoice_type(type_str)

        currency_code = (
            header.get("currency_code")
            or header.get("currency_alt")
            or settings.default_functional_currency_code
        )[:3]
        exchange_rate = header.get("exchange_rate") or Decimal("1")

        lines: list[InvoiceLine] = []
        subtotal = Decimal("0")
        tax_amount = Decimal("0")

        for line_number, line_row in enumerate(line_rows, start=1):
            line = self._create_invoice_line(
                line_row,
                line_number=line_number,
            )
            lines.append(line)
            subtotal += line.line_amount or Decimal("0")
            tax_amount += line.tax_amount or Decimal("0")

        total_amount = subtotal + tax_amount

        header_total = (
            header.get("total_amount")
            or header.get("total_alt")
            or header.get("gross_alt")
        )
        header_subtotal = header.get("subtotal") or header.get("net_amount_alt")
        header_tax = (
            header.get("tax_amount") or header.get("vat_alt") or header.get("tax_alt")
        )

        if header_subtotal is not None and subtotal == 0:
            subtotal = header_subtotal
        if header_tax is not None and tax_amount == 0:
            tax_amount = header_tax
        if header_total is not None and total_amount == 0:
            total_amount = header_total

        balance = header.get("balance_alt")
        amount_paid = (
            header.get("amount_paid") or header.get("paid_alt") or Decimal("0")
        )
        if balance is not None and total_amount is not None:
            amount_paid = total_amount - balance
            if amount_paid < 0:
                amount_paid = Decimal("0")

        functional_currency_amount = total_amount * exchange_rate

        status_str = header.get("status_str") or header.get("status_alt") or "DRAFT"
        status = self._parse_status(status_str, total_amount, amount_paid)

        invoice = Invoice(
            invoice_id=uuid4(),
            organization_id=self.config.organization_id,
            customer_id=customer_id,
            invoice_number=invoice_number[:30],
            invoice_type=invoice_type,
            invoice_date=invoice_date,
            due_date=due_date,
            currency_code=currency_code,
            exchange_rate=exchange_rate,
            subtotal=subtotal,
            tax_amount=tax_amount,
            total_amount=total_amount,
            amount_paid=amount_paid,
            functional_currency_amount=functional_currency_amount,
            status=status,
            ar_control_account_id=self.ar_control_account_id,
            notes=header.get("notes") or header.get("description_alt"),
            posting_status="NOT_POSTED",
            ecl_provision_amount=Decimal("0"),
            is_intercompany=False,
            created_by_user_id=self.config.user_id,
        )
        invoice.lines = lines
        return invoice

    def check_duplicate(self, row: dict[str, Any]) -> Invoice | None:
        """Check if invoice already exists."""
        invoice_number = self.get_unique_key(row)
        if not invoice_number:
            return None

        existing = self.db.execute(
            select(Invoice).where(
                Invoice.organization_id == self.config.organization_id,
                Invoice.invoice_number == invoice_number,
            )
        ).scalar_one_or_none()

        return existing

    def validate_row(self, row: dict[str, Any], row_num: int) -> bool:
        """Validate row data."""
        is_valid = super().validate_row(row, row_num)

        # Customer is required
        customer_name = (
            row.get("Customer Name") or row.get("Customer") or row.get("Client") or ""
        ).strip()
        if not customer_name:
            self.result.add_error(row_num, "Customer name is required", "Customer Name")
            is_valid = False

        # Amount is required
        total = (
            (
                row.get("Total Amount")
                or row.get("Total")
                or row.get("Gross Amount")
                or row.get("Subtotal")
                or row.get("Net Amount")
                or ""
            ).strip()
            if isinstance(
                row.get("Total Amount")
                or row.get("Total")
                or row.get("Gross Amount")
                or row.get("Subtotal")
                or row.get("Net Amount"),
                str,
            )
            else row.get("Total Amount")
            or row.get("Total")
            or row.get("Gross Amount")
            or row.get("Subtotal")
            or row.get("Net Amount")
        )
        if not total:
            self.result.add_error(row_num, "Total amount is required", "Total Amount")
            is_valid = False

        return is_valid

    def create_entity(self, row: dict[str, Any]) -> Invoice:
        """Create a new invoice from transformed row data."""
        # Get invoice number
        invoice_number = (
            row.get("invoice_number")
            or row.get("invoice_no_alt")
            or row.get("number_alt")
            or ""
        ).strip()
        if not invoice_number:
            self._invoice_number_counter += 1
            invoice_number = f"INV{self._invoice_number_counter:06d}"

        # Get dates
        invoice_date = row.get("invoice_date") or row.get("date_alt") or date.today()
        due_date = row.get("due_date") or invoice_date

        # Get customer
        customer_name = (
            row.get("customer_name")
            or row.get("customer_alt")
            or row.get("client_alt")
            or "Unknown Customer"
        ).strip()
        customer_id = self._get_customer_id(customer_name)

        # Parse invoice type
        type_str = row.get("invoice_type_str") or row.get("type_alt") or "STANDARD"
        invoice_type = self._parse_invoice_type(type_str)

        # Get currency
        currency_code = (
            row.get("currency_code")
            or row.get("currency_alt")
            or settings.default_functional_currency_code
        )[:3]
        exchange_rate = row.get("exchange_rate") or Decimal("1")

        # Get amounts
        subtotal = row.get("subtotal") or row.get("net_amount_alt") or Decimal("0")
        tax_amount = (
            row.get("tax_amount")
            or row.get("vat_alt")
            or row.get("tax_alt")
            or Decimal("0")
        )
        total_amount = (
            row.get("total_amount") or row.get("total_alt") or row.get("gross_alt")
        )

        # Calculate if needed
        if total_amount is None:
            if subtotal:
                total_amount = subtotal + (tax_amount or Decimal("0"))
            else:
                total_amount = Decimal("0")

        if subtotal is None or subtotal == Decimal("0"):
            subtotal = total_amount - (tax_amount or Decimal("0"))

        amount_paid = row.get("amount_paid") or row.get("paid_alt") or Decimal("0")
        functional_currency_amount = total_amount * exchange_rate

        # Parse status
        status_str = row.get("status_str") or row.get("status_alt") or "DRAFT"
        status = self._parse_status(status_str, total_amount, amount_paid)

        # Create invoice
        invoice = Invoice(
            invoice_id=uuid4(),
            organization_id=self.config.organization_id,
            customer_id=customer_id,
            invoice_number=invoice_number[:30],
            invoice_type=invoice_type,
            invoice_date=invoice_date,
            due_date=due_date,
            currency_code=currency_code,
            exchange_rate=exchange_rate,
            subtotal=subtotal,
            tax_amount=tax_amount,
            total_amount=total_amount,
            amount_paid=amount_paid,
            functional_currency_amount=functional_currency_amount,
            status=status,
            ar_control_account_id=self.ar_control_account_id,
            notes=row.get("notes") or row.get("description_alt"),
            posting_status="NOT_POSTED",
            ecl_provision_amount=Decimal("0"),
            is_intercompany=False,
            created_by_user_id=self.config.user_id,
        )

        # Create invoice line
        line = self._create_invoice_line(row, line_number=1, fallback_amount=subtotal)
        invoice.lines = [line]

        return invoice

    def _create_invoice_line(
        self,
        row: dict[str, Any],
        line_number: int,
        fallback_amount: Decimal | None = None,
    ) -> InvoiceLine:
        """Create invoice line from row data."""
        description = (
            row.get("line_item_name")
            or row.get("line_item_desc")
            or row.get("line_product_alt")
            or row.get("description_alt")
            or row.get("notes")
            or "Invoice Item"
        )

        quantity = row.get("line_quantity") or row.get("line_qty_alt") or Decimal("1")
        if quantity == 0:
            quantity = Decimal("1")

        unit_price = (
            row.get("line_unit_price")
            or row.get("line_price_alt")
            or row.get("line_rate_alt")
            or row.get("line_item_price")
        )

        line_amount = (
            row.get("line_item_total")
            or row.get("line_amount")
            or row.get("line_amount_alt")
        )

        if line_amount is None:
            if unit_price is None and fallback_amount is not None:
                unit_price = fallback_amount
            if unit_price is None:
                unit_price = Decimal("0")
            line_amount = quantity * unit_price if quantity else unit_price

        if unit_price is None:
            unit_price = line_amount / quantity if quantity else line_amount

        discount = (
            row.get("line_discount")
            or row.get("line_discount_amount_alt")
            or Decimal("0")
        )
        tax_amount = (
            row.get("line_tax_amount") or row.get("line_tax_amount_alt") or Decimal("0")
        )

        return InvoiceLine(
            line_id=uuid4(),
            line_number=line_number,
            description=description[:500] if description else "Item",
            quantity=quantity,
            unit_price=unit_price,
            discount_amount=discount,
            line_amount=line_amount,
            tax_amount=tax_amount,
            revenue_account_id=self.default_revenue_account_id,
        )

    def _get_customer_id(self, customer_name: str) -> UUID:
        """Get customer ID by name."""
        if customer_name in self._customer_cache:
            return self._customer_cache[customer_name]

        customer = self.db.execute(
            select(Customer).where(
                Customer.organization_id == self.config.organization_id,
                Customer.legal_name == customer_name,
            )
        ).scalar_one_or_none()

        if customer:
            self._customer_cache[customer_name] = customer.customer_id
            return customer.customer_id

        # Try trading name
        customer = self.db.execute(
            select(Customer).where(
                Customer.organization_id == self.config.organization_id,
                Customer.trading_name == customer_name,
            )
        ).scalar_one_or_none()

        if customer:
            self._customer_cache[customer_name] = customer.customer_id
            return customer.customer_id

        raise ValueError(
            f"Customer '{customer_name}' not found. Please import customers first."
        )

    def _parse_invoice_type(self, type_str: str) -> InvoiceType:
        """Parse invoice type string."""
        type_map = {
            "STANDARD": InvoiceType.STANDARD,
            "INVOICE": InvoiceType.STANDARD,
            "CREDIT_NOTE": InvoiceType.CREDIT_NOTE,
            "CREDIT NOTE": InvoiceType.CREDIT_NOTE,
            "CREDIT": InvoiceType.CREDIT_NOTE,
            "DEBIT_NOTE": InvoiceType.DEBIT_NOTE,
            "DEBIT NOTE": InvoiceType.DEBIT_NOTE,
            "DEBIT": InvoiceType.DEBIT_NOTE,
            "PROFORMA": InvoiceType.PROFORMA,
            "PRO FORMA": InvoiceType.PROFORMA,
            "QUOTE": InvoiceType.PROFORMA,
        }
        return type_map.get(type_str.upper().replace("-", "_"), InvoiceType.STANDARD)

    def _parse_status(
        self, status_str: str, total: Decimal, paid: Decimal
    ) -> InvoiceStatus:
        """Parse invoice status string, considering amounts."""
        status_map = {
            "DRAFT": InvoiceStatus.DRAFT,
            "SUBMITTED": InvoiceStatus.SUBMITTED,
            "APPROVED": InvoiceStatus.APPROVED,
            "POSTED": InvoiceStatus.POSTED,
            "PARTIALLY_PAID": InvoiceStatus.PARTIALLY_PAID,
            "PAID": InvoiceStatus.PAID,
            "CLOSED": InvoiceStatus.PAID,
            "OVERDUE": InvoiceStatus.OVERDUE,
            "VOID": InvoiceStatus.VOID,
            "CANCELLED": InvoiceStatus.VOID,
            "DISPUTED": InvoiceStatus.DISPUTED,
        }

        status = status_map.get(
            status_str.upper().replace(" ", "_"), InvoiceStatus.DRAFT
        )

        # Auto-determine based on payment
        if paid and total and paid > 0:
            if paid >= total:
                status = InvoiceStatus.PAID
            elif status not in (InvoiceStatus.VOID, InvoiceStatus.DISPUTED):
                status = InvoiceStatus.PARTIALLY_PAID

        return status
