"""
Accounts Receivable (AR) Services.

This module provides services for customer management, AR invoices,
payments, GL posting, and aging analysis.
"""

from app.services.finance.ar.ar_aging import (
    ARAgingService,
    CustomerAgingSummary,
    OrganizationARAgingSummary,
    ar_aging_service,
)
from app.services.finance.ar.ar_posting_adapter import (
    ARPostingAdapter,
    ARPostingResult,
    ar_posting_adapter,
)
from app.services.finance.ar.contract import (
    ContractInput,
    ContractService,
    PerformanceObligationInput,
    ProgressUpdateInput,
    contract_service,
)
from app.services.finance.ar.customer import (
    CustomerInput,
    CustomerService,
    customer_service,
)
from app.services.finance.ar.customer_payment import (
    CustomerPaymentInput,
    CustomerPaymentService,
    PaymentAllocationInput,
    customer_payment_service,
)
from app.services.finance.ar.invoice import (
    ARInvoiceInput,
    ARInvoiceLineInput,
    ARInvoiceService,
    ar_invoice_service,
)

__all__ = [
    # Customer
    "CustomerService",
    "CustomerInput",
    "customer_service",
    # Invoice
    "ARInvoiceService",
    "ARInvoiceInput",
    "ARInvoiceLineInput",
    "ar_invoice_service",
    # Payment
    "CustomerPaymentService",
    "CustomerPaymentInput",
    "PaymentAllocationInput",
    "customer_payment_service",
    # Posting
    "ARPostingAdapter",
    "ARPostingResult",
    "ar_posting_adapter",
    # Aging
    "ARAgingService",
    "CustomerAgingSummary",
    "OrganizationARAgingSummary",
    "ar_aging_service",
    # IFRS 15 Contract
    "ContractService",
    "contract_service",
    "ContractInput",
    "PerformanceObligationInput",
    "ProgressUpdateInput",
]
