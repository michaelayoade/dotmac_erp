"""
Accounts Receivable (AR) Services.

This module provides services for customer management, AR invoices,
payments, GL posting, and aging analysis.
"""

from app.services.ifrs.ar.customer import (
    CustomerService,
    CustomerInput,
    customer_service,
)
from app.services.ifrs.ar.invoice import (
    ARInvoiceService,
    ARInvoiceInput,
    ARInvoiceLineInput,
    ar_invoice_service,
)
from app.services.ifrs.ar.customer_payment import (
    CustomerPaymentService,
    CustomerPaymentInput,
    PaymentAllocationInput,
    customer_payment_service,
)
from app.services.ifrs.ar.ar_posting_adapter import (
    ARPostingAdapter,
    ARPostingResult,
    ar_posting_adapter,
)
from app.services.ifrs.ar.ar_aging import (
    ARAgingService,
    CustomerAgingSummary,
    OrganizationARAgingSummary,
    ar_aging_service,
)
from app.services.ifrs.ar.contract import (
    ContractService,
    contract_service,
    ContractInput,
    PerformanceObligationInput,
    ProgressUpdateInput,
)
from app.services.ifrs.ar.ecl import (
    ECLService,
    ecl_service,
    ECLCalculationInput,
    GeneralApproachInput,
    ECLResult,
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
    # IFRS 9 ECL
    "ECLService",
    "ecl_service",
    "ECLCalculationInput",
    "GeneralApproachInput",
    "ECLResult",
]
