"""
Tax Services.

This module provides services for tax management including VAT/GST,
withholding taxes, income tax, deferred taxes (IAS 12), and tax reporting.
"""

from app.services.finance.tax.deferred_tax import (
    DeferredTaxBasisInput,
    DeferredTaxCalculationResult,
    DeferredTaxMovementResult,
    DeferredTaxService,
    DeferredTaxSummary,
    deferred_tax_service,
)
from app.services.finance.tax.tax_calculation import (
    InvoiceLineTaxInput,
    InvoiceTaxResult,
    LineCalculationResult,
    LineTaxInput,
    LineTaxResult,
    TaxCalculationService,
    tax_calculation_service,
)
from app.services.finance.tax.tax_master import (
    TaxCalculationResult,
    TaxCodeInput,
    TaxCodeService,
    TaxJurisdictionInput,
    TaxJurisdictionService,
    tax_code_service,
    tax_jurisdiction_service,
)
from app.services.finance.tax.tax_period import (
    TaxPeriodInput,
    TaxPeriodService,
    tax_period_service,
)
from app.services.finance.tax.tax_posting_adapter import (
    TAXPostingAdapter,
    TAXPostingResult,
    tax_posting_adapter,
)
from app.services.finance.tax.tax_reconciliation import (
    ReconciliationLine,
    TaxReconciliationInput,
    TaxReconciliationService,
    tax_reconciliation_service,
)
from app.services.finance.tax.tax_return import (
    TaxReturnInput,
    TaxReturnService,
    tax_return_service,
)
from app.services.finance.tax.tax_transaction import (
    TaxByCodeSummary,
    TaxReturnSummary,
    TaxTransactionInput,
    TaxTransactionService,
    tax_transaction_service,
)

__all__ = [
    # Tax Code
    "TaxCodeService",
    "TaxCodeInput",
    "TaxCalculationResult",
    "tax_code_service",
    # Tax Jurisdiction
    "TaxJurisdictionService",
    "TaxJurisdictionInput",
    "tax_jurisdiction_service",
    # Tax Transaction
    "TaxTransactionService",
    "TaxTransactionInput",
    "TaxReturnSummary",
    "TaxByCodeSummary",
    "tax_transaction_service",
    # Deferred Tax
    "DeferredTaxService",
    "DeferredTaxBasisInput",
    "DeferredTaxCalculationResult",
    "DeferredTaxMovementResult",
    "DeferredTaxSummary",
    "deferred_tax_service",
    # Tax Reconciliation
    "TaxReconciliationService",
    "TaxReconciliationInput",
    "ReconciliationLine",
    "tax_reconciliation_service",
    # Posting
    "TAXPostingAdapter",
    "TAXPostingResult",
    "tax_posting_adapter",
    # Tax Period
    "TaxPeriodService",
    "tax_period_service",
    "TaxPeriodInput",
    # Tax Return
    "TaxReturnService",
    "tax_return_service",
    "TaxReturnInput",
    # Tax Calculation
    "TaxCalculationService",
    "LineTaxInput",
    "LineTaxResult",
    "LineCalculationResult",
    "InvoiceLineTaxInput",
    "InvoiceTaxResult",
    "tax_calculation_service",
]
