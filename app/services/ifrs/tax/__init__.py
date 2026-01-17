"""
Tax Services.

This module provides services for tax management including VAT/GST,
withholding taxes, income tax, deferred taxes (IAS 12), and tax reporting.
"""

from app.services.ifrs.tax.tax_master import (
    TaxCodeService,
    TaxCodeInput,
    TaxCalculationResult,
    TaxJurisdictionService,
    TaxJurisdictionInput,
    tax_code_service,
    tax_jurisdiction_service,
)
from app.services.ifrs.tax.tax_transaction import (
    TaxTransactionService,
    TaxTransactionInput,
    TaxReturnSummary,
    TaxByCodeSummary,
    tax_transaction_service,
)
from app.services.ifrs.tax.deferred_tax import (
    DeferredTaxService,
    DeferredTaxBasisInput,
    DeferredTaxCalculationResult,
    DeferredTaxMovementResult,
    DeferredTaxSummary,
    deferred_tax_service,
)
from app.services.ifrs.tax.tax_reconciliation import (
    TaxReconciliationService,
    TaxReconciliationInput,
    ReconciliationLine,
    tax_reconciliation_service,
)
from app.services.ifrs.tax.tax_posting_adapter import (
    TAXPostingAdapter,
    TAXPostingResult,
    tax_posting_adapter,
)
from app.services.ifrs.tax.tax_period import (
    TaxPeriodService,
    tax_period_service,
    TaxPeriodInput,
)
from app.services.ifrs.tax.tax_return import (
    TaxReturnService,
    tax_return_service,
    TaxReturnInput,
)
from app.services.ifrs.tax.tax_calculation import (
    TaxCalculationService,
    LineTaxInput,
    LineTaxResult,
    LineCalculationResult,
    InvoiceLineTaxInput,
    InvoiceTaxResult,
    tax_calculation_service,
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
