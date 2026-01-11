"""
Payments Importer.

Imports customer and supplier payments from CSV data.
"""

from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ifrs.ar.customer_payment import CustomerPayment, PaymentMethod as ARPaymentMethod, PaymentStatus as ARPaymentStatus
from app.models.ifrs.ar.customer import Customer
from app.models.ifrs.ap.supplier_payment import SupplierPayment, APPaymentMethod, APPaymentStatus
from app.models.ifrs.ap.supplier import Supplier

from .base import BaseImporter, FieldMapping, ImportConfig


class CustomerPaymentImporter(BaseImporter[CustomerPayment]):
    """
    Importer for customer payments from CSV data.

    Expected CSV columns (flexible - maps common naming conventions):
    - Payment Number / Payment No / Reference: Payment reference
    - Payment Date / Date: Date of payment
    - Customer Name / Customer: Customer name
    - Amount / Payment Amount: Payment amount
    - Currency Code / Currency: Currency (default: NGN)
    - Exchange Rate: Exchange rate
    - Payment Method / Method: CASH, CHECK, BANK_TRANSFER, etc.
    - Bank Account / Bank: Bank account used
    - Reference / Check Number / Transaction Ref: Reference
    - Description / Notes: Description
    - Status: PENDING, CLEARED, etc.
    """

    entity_name = "Customer Payment"
    model_class = CustomerPayment

    def __init__(
        self,
        db: Session,
        config: ImportConfig,
        default_bank_account_id: Optional[UUID] = None,
    ):
        super().__init__(db, config)
        self.default_bank_account_id = default_bank_account_id
        self._customer_cache: Dict[str, UUID] = {}
        self._payment_counter = 0

    def get_field_mappings(self) -> List[FieldMapping]:
        """Define flexible field mappings."""
        return [
            # Reference
            FieldMapping("Payment Number", "payment_number", required=False),
            FieldMapping("Payment No", "payment_no_alt", required=False),
            FieldMapping("Reference", "reference", required=False),
            # Date
            FieldMapping("Payment Date", "payment_date", required=False,
                         transformer=self.parse_date),
            FieldMapping("Date", "date_alt", required=False,
                         transformer=self.parse_date),
            # Customer
            FieldMapping("Customer Name", "customer_name", required=False),
            FieldMapping("Customer", "customer_alt", required=False),
            # Amount
            FieldMapping("Amount", "amount", required=False,
                         transformer=self.parse_decimal),
            FieldMapping("Payment Amount", "payment_amount_alt", required=False,
                         transformer=self.parse_decimal),
            # Currency
            FieldMapping("Currency Code", "currency_code", required=False, default="NGN"),
            FieldMapping("Currency", "currency_alt", required=False),
            FieldMapping("Exchange Rate", "exchange_rate", required=False,
                         transformer=self.parse_decimal, default=Decimal("1")),
            # Payment details
            FieldMapping("Payment Method", "payment_method_str", required=False),
            FieldMapping("Method", "method_alt", required=False),
            FieldMapping("Bank Account", "bank_account_name", required=False),
            FieldMapping("Bank", "bank_alt", required=False),
            FieldMapping("Check Number", "check_number", required=False),
            FieldMapping("Transaction Ref", "transaction_ref", required=False),
            # Description
            FieldMapping("Description", "description", required=False),
            FieldMapping("Notes", "notes_alt", required=False),
            # Status
            FieldMapping("Status", "status_str", required=False, default="PENDING"),
        ]

    def get_unique_key(self, row: Dict[str, Any]) -> str:
        return (row.get("Payment Number") or row.get("Payment No") or
                row.get("Reference") or "").strip()

    def check_duplicate(self, row: Dict[str, Any]) -> Optional[CustomerPayment]:
        payment_number = self.get_unique_key(row)
        if not payment_number:
            return None

        existing = self.db.execute(
            select(CustomerPayment).where(
                CustomerPayment.organization_id == self.config.organization_id,
                CustomerPayment.payment_number == payment_number,
            )
        ).scalar_one_or_none()

        return existing

    def validate_row(self, row: Dict[str, Any], row_num: int) -> bool:
        is_valid = super().validate_row(row, row_num)

        customer_name = (row.get("Customer Name") or row.get("Customer") or "").strip()
        if not customer_name:
            self.result.add_error(row_num, "Customer name is required", "Customer Name")
            is_valid = False

        amount = row.get("Amount") or row.get("Payment Amount")
        if not amount:
            self.result.add_error(row_num, "Amount is required", "Amount")
            is_valid = False

        return is_valid

    def create_entity(self, row: Dict[str, Any]) -> CustomerPayment:
        # Get payment number
        payment_number = (row.get("payment_number") or row.get("payment_no_alt") or
                          row.get("reference") or "").strip()
        if not payment_number:
            self._payment_counter += 1
            payment_number = f"RCPT{self._payment_counter:06d}"

        # Get date
        payment_date = row.get("payment_date") or row.get("date_alt") or date.today()

        # Get customer
        customer_name = (row.get("customer_name") or row.get("customer_alt")).strip()
        customer_id = self._get_customer_id(customer_name)

        # Get amount and currency
        amount = row.get("amount") or row.get("payment_amount_alt") or Decimal("0")
        currency_code = (row.get("currency_code") or row.get("currency_alt") or "NGN")[:3]
        exchange_rate = row.get("exchange_rate") or Decimal("1")
        functional_currency_amount = amount * exchange_rate

        # Get payment method
        method_str = row.get("payment_method_str") or row.get("method_alt") or "BANK_TRANSFER"
        payment_method = self._parse_payment_method(method_str)

        # Get reference
        reference = (row.get("reference") or row.get("check_number") or
                     row.get("transaction_ref"))

        # Get status
        status_str = row.get("status_str", "PENDING")
        status = self._parse_status(status_str)

        payment = CustomerPayment(
            payment_id=uuid4(),
            organization_id=self.config.organization_id,
            customer_id=customer_id,
            payment_number=payment_number[:30],
            payment_date=payment_date,
            payment_method=payment_method,
            currency_code=currency_code,
            amount=amount,
            exchange_rate=exchange_rate,
            functional_currency_amount=functional_currency_amount,
            bank_account_id=self.default_bank_account_id,
            reference=reference[:100] if reference else None,
            description=row.get("description") or row.get("notes_alt"),
            status=status,
            created_by_user_id=self.config.user_id,
        )

        return payment

    def _get_customer_id(self, customer_name: str) -> UUID:
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

        raise ValueError(f"Customer '{customer_name}' not found.")

    def _parse_payment_method(self, method_str: str) -> ARPaymentMethod:
        method_map = {
            "CASH": ARPaymentMethod.CASH,
            "CHECK": ARPaymentMethod.CHECK,
            "CHEQUE": ARPaymentMethod.CHECK,
            "BANK_TRANSFER": ARPaymentMethod.BANK_TRANSFER,
            "BANK TRANSFER": ARPaymentMethod.BANK_TRANSFER,
            "TRANSFER": ARPaymentMethod.BANK_TRANSFER,
            "CARD": ARPaymentMethod.CARD,
            "CREDIT_CARD": ARPaymentMethod.CARD,
            "DIRECT_DEBIT": ARPaymentMethod.DIRECT_DEBIT,
            "MOBILE_MONEY": ARPaymentMethod.MOBILE_MONEY,
        }
        return method_map.get(method_str.upper().replace("-", "_"), ARPaymentMethod.BANK_TRANSFER)

    def _parse_status(self, status_str: str) -> ARPaymentStatus:
        status_map = {
            "PENDING": ARPaymentStatus.PENDING,
            "APPROVED": ARPaymentStatus.APPROVED,
            "CLEARED": ARPaymentStatus.CLEARED,
            "BOUNCED": ARPaymentStatus.BOUNCED,
            "REVERSED": ARPaymentStatus.REVERSED,
            "VOID": ARPaymentStatus.VOID,
        }
        return status_map.get(status_str.upper(), ARPaymentStatus.PENDING)


class SupplierPaymentImporter(BaseImporter[SupplierPayment]):
    """
    Importer for supplier/vendor payments from CSV data.
    """

    entity_name = "Supplier Payment"
    model_class = SupplierPayment

    def __init__(
        self,
        db: Session,
        config: ImportConfig,
        bank_account_id: UUID,
    ):
        super().__init__(db, config)
        self.bank_account_id = bank_account_id
        self._supplier_cache: Dict[str, UUID] = {}
        self._payment_counter = 0

    def get_field_mappings(self) -> List[FieldMapping]:
        return [
            FieldMapping("Payment Number", "payment_number", required=False),
            FieldMapping("Payment No", "payment_no_alt", required=False),
            FieldMapping("Reference", "reference", required=False),
            FieldMapping("Payment Date", "payment_date", required=False,
                         transformer=self.parse_date),
            FieldMapping("Date", "date_alt", required=False,
                         transformer=self.parse_date),
            FieldMapping("Vendor Name", "vendor_name", required=False),
            FieldMapping("Vendor", "vendor_alt", required=False),
            FieldMapping("Supplier Name", "supplier_name", required=False),
            FieldMapping("Supplier", "supplier_alt", required=False),
            FieldMapping("Amount", "amount", required=False,
                         transformer=self.parse_decimal),
            FieldMapping("Payment Amount", "payment_amount_alt", required=False,
                         transformer=self.parse_decimal),
            FieldMapping("Currency Code", "currency_code", required=False, default="NGN"),
            FieldMapping("Currency", "currency_alt", required=False),
            FieldMapping("Exchange Rate", "exchange_rate", required=False,
                         transformer=self.parse_decimal, default=Decimal("1")),
            FieldMapping("Payment Method", "payment_method_str", required=False),
            FieldMapping("Method", "method_alt", required=False),
            FieldMapping("Check Number", "check_number", required=False),
            FieldMapping("Transaction Ref", "transaction_ref", required=False),
            FieldMapping("Description", "description", required=False),
            FieldMapping("Notes", "notes_alt", required=False),
            FieldMapping("Status", "status_str", required=False, default="DRAFT"),
            FieldMapping("Withholding Tax", "withholding_tax", required=False,
                         transformer=self.parse_decimal, default=Decimal("0")),
        ]

    def get_unique_key(self, row: Dict[str, Any]) -> str:
        return (row.get("Payment Number") or row.get("Payment No") or
                row.get("Reference") or "").strip()

    def check_duplicate(self, row: Dict[str, Any]) -> Optional[SupplierPayment]:
        payment_number = self.get_unique_key(row)
        if not payment_number:
            return None

        existing = self.db.execute(
            select(SupplierPayment).where(
                SupplierPayment.organization_id == self.config.organization_id,
                SupplierPayment.payment_number == payment_number,
            )
        ).scalar_one_or_none()

        return existing

    def validate_row(self, row: Dict[str, Any], row_num: int) -> bool:
        is_valid = super().validate_row(row, row_num)

        supplier_name = (row.get("Vendor Name") or row.get("Vendor") or
                         row.get("Supplier Name") or row.get("Supplier") or "").strip()
        if not supplier_name:
            self.result.add_error(row_num, "Vendor/Supplier name is required", "Vendor Name")
            is_valid = False

        amount = row.get("Amount") or row.get("Payment Amount")
        if not amount:
            self.result.add_error(row_num, "Amount is required", "Amount")
            is_valid = False

        return is_valid

    def create_entity(self, row: Dict[str, Any]) -> SupplierPayment:
        # Get payment number
        payment_number = (row.get("payment_number") or row.get("payment_no_alt") or
                          row.get("reference") or "").strip()
        if not payment_number:
            self._payment_counter += 1
            payment_number = f"VPMT{self._payment_counter:06d}"

        # Get date
        payment_date = row.get("payment_date") or row.get("date_alt") or date.today()

        # Get supplier
        supplier_name = (row.get("vendor_name") or row.get("vendor_alt") or
                         row.get("supplier_name") or row.get("supplier_alt")).strip()
        supplier_id = self._get_supplier_id(supplier_name)

        # Get amount and currency
        amount = row.get("amount") or row.get("payment_amount_alt") or Decimal("0")
        currency_code = (row.get("currency_code") or row.get("currency_alt") or "NGN")[:3]
        exchange_rate = row.get("exchange_rate") or Decimal("1")
        functional_currency_amount = amount * exchange_rate

        # Get payment method
        method_str = row.get("payment_method_str") or row.get("method_alt") or "BANK_TRANSFER"
        payment_method = self._parse_payment_method(method_str)

        # Get reference
        reference = (row.get("reference") or row.get("check_number") or
                     row.get("transaction_ref"))

        # Get status
        status_str = row.get("status_str", "DRAFT")
        status = self._parse_status(status_str)

        payment = SupplierPayment(
            payment_id=uuid4(),
            organization_id=self.config.organization_id,
            supplier_id=supplier_id,
            payment_number=payment_number[:30],
            payment_date=payment_date,
            payment_method=payment_method,
            currency_code=currency_code,
            amount=amount,
            exchange_rate=exchange_rate,
            functional_currency_amount=functional_currency_amount,
            bank_account_id=self.bank_account_id,
            reference=reference[:100] if reference else None,
            status=status,
            withholding_tax_amount=row.get("withholding_tax", Decimal("0")),
            remittance_advice_sent=False,
            created_by_user_id=self.config.user_id,
        )

        return payment

    def _get_supplier_id(self, supplier_name: str) -> UUID:
        if supplier_name in self._supplier_cache:
            return self._supplier_cache[supplier_name]

        supplier = self.db.execute(
            select(Supplier).where(
                Supplier.organization_id == self.config.organization_id,
                Supplier.legal_name == supplier_name,
            )
        ).scalar_one_or_none()

        if supplier:
            self._supplier_cache[supplier_name] = supplier.supplier_id
            return supplier.supplier_id

        raise ValueError(f"Supplier '{supplier_name}' not found.")

    def _parse_payment_method(self, method_str: str) -> APPaymentMethod:
        method_map = {
            "CHECK": APPaymentMethod.CHECK,
            "CHEQUE": APPaymentMethod.CHECK,
            "BANK_TRANSFER": APPaymentMethod.BANK_TRANSFER,
            "BANK TRANSFER": APPaymentMethod.BANK_TRANSFER,
            "TRANSFER": APPaymentMethod.BANK_TRANSFER,
            "WIRE": APPaymentMethod.WIRE,
            "ACH": APPaymentMethod.ACH,
            "CARD": APPaymentMethod.CARD,
        }
        return method_map.get(method_str.upper().replace("-", "_"), APPaymentMethod.BANK_TRANSFER)

    def _parse_status(self, status_str: str) -> APPaymentStatus:
        status_map = {
            "DRAFT": APPaymentStatus.DRAFT,
            "PENDING": APPaymentStatus.PENDING,
            "APPROVED": APPaymentStatus.APPROVED,
            "SENT": APPaymentStatus.SENT,
            "CLEARED": APPaymentStatus.CLEARED,
            "VOID": APPaymentStatus.VOID,
            "REJECTED": APPaymentStatus.REJECTED,
        }
        return status_map.get(status_str.upper(), APPaymentStatus.DRAFT)
