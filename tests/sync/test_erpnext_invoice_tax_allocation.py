from decimal import Decimal

from app.services.erpnext.sync.purchase_invoice import PurchaseInvoiceSyncService
from app.services.erpnext.sync.sales_invoice import SalesInvoiceSyncService


def _assert_allocation_behavior(calc_fn):
    # Uses explicit first-line tax and allocates invoice remainder by amount.
    taxes = calc_fn(
        [
            {"line_amount": Decimal("100"), "tax_amount": Decimal("5")},
            {"line_amount": Decimal("100")},
        ],
        Decimal("30"),
    )
    assert taxes == [Decimal("17.50"), Decimal("12.50")]

    # No explicit item tax: allocates proportionally by line amount.
    taxes = calc_fn(
        [
            {"line_amount": Decimal("30")},
            {"line_amount": Decimal("70")},
        ],
        Decimal("10"),
    )
    assert taxes == [Decimal("3.00"), Decimal("7.00")]

    # Zero line amounts: assigns remaining tax to last line deterministically.
    taxes = calc_fn(
        [
            {"line_amount": Decimal("0")},
            {"line_amount": Decimal("0")},
        ],
        Decimal("5"),
    )
    assert taxes == [Decimal("0.00"), Decimal("5.00")]


def test_sales_invoice_line_tax_allocation():
    _assert_allocation_behavior(SalesInvoiceSyncService._calculate_line_taxes)


def test_purchase_invoice_line_tax_allocation():
    _assert_allocation_behavior(PurchaseInvoiceSyncService._calculate_line_taxes)
