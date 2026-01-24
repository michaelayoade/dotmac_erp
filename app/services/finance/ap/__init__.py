"""
AP (Accounts Payable) Services.

Provides supplier management, invoice processing, payment handling,
and GL integration for accounts payable.
"""

from app.services.finance.ap.supplier import SupplierService, supplier_service, SupplierInput
from app.services.finance.ap.supplier_invoice import (
    SupplierInvoiceService,
    supplier_invoice_service,
    SupplierInvoiceInput,
    InvoiceLineInput,
)
from app.services.finance.ap.supplier_payment import (
    SupplierPaymentService,
    supplier_payment_service,
    SupplierPaymentInput,
    PaymentAllocationInput,
)
from app.services.finance.ap.ap_posting_adapter import APPostingAdapter, ap_posting_adapter, APPostingResult
from app.services.finance.ap.ap_aging import (
    APAgingService,
    ap_aging_service,
    SupplierAgingSummary,
    OrganizationAgingSummary,
)
from app.services.finance.ap.purchase_order import (
    PurchaseOrderService,
    purchase_order_service,
    PurchaseOrderInput,
    POLineInput,
)
from app.services.finance.ap.goods_receipt import (
    GoodsReceiptService,
    goods_receipt_service,
    GoodsReceiptInput,
    GRLineInput,
)
from app.services.finance.ap.payment_batch import (
    PaymentBatchService,
    payment_batch_service,
    PaymentBatchInput,
)


__all__ = [
    # Supplier
    "SupplierService",
    "supplier_service",
    "SupplierInput",
    # Supplier Invoice
    "SupplierInvoiceService",
    "supplier_invoice_service",
    "SupplierInvoiceInput",
    "InvoiceLineInput",
    # Supplier Payment
    "SupplierPaymentService",
    "supplier_payment_service",
    "SupplierPaymentInput",
    "PaymentAllocationInput",
    # Posting Adapter
    "APPostingAdapter",
    "ap_posting_adapter",
    "APPostingResult",
    # Aging
    "APAgingService",
    "ap_aging_service",
    "SupplierAgingSummary",
    "OrganizationAgingSummary",
    # Purchase Order
    "PurchaseOrderService",
    "purchase_order_service",
    "PurchaseOrderInput",
    "POLineInput",
    # Goods Receipt
    "GoodsReceiptService",
    "goods_receipt_service",
    "GoodsReceiptInput",
    "GRLineInput",
    # Payment Batch
    "PaymentBatchService",
    "payment_batch_service",
    "PaymentBatchInput",
]
