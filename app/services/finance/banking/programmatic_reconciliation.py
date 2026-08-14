"""Programmatic reconciliation facade.

The providers, strategies, helpers, and engine live in ``programmatic_parts``.
This module keeps the historical import path stable.
"""

from __future__ import annotations

from app.services.finance.banking.programmatic_parts import (
    BankFeeStrategy,
    CustomerReceiptProvider,
    CustomerReceiptReferenceStrategy,
    ExpenseReimbursementStrategy,
    InterbankCounterpartStrategy,
    LegacyCustomRuleStrategy,
    PaymentIntentProvider,
    PaymentIntentReferenceStrategy,
    PayrollEntryStrategy,
    ProgrammaticReconciliationEngine,
    SupplierPaymentProvider,
    SupplierPaymentReferenceStrategy,
    build_extra_gl_account_ids,
)

__all__ = [
    "BankFeeStrategy",
    "CustomerReceiptProvider",
    "CustomerReceiptReferenceStrategy",
    "ExpenseReimbursementStrategy",
    "InterbankCounterpartStrategy",
    "LegacyCustomRuleStrategy",
    "PaymentIntentProvider",
    "PaymentIntentReferenceStrategy",
    "PayrollEntryStrategy",
    "ProgrammaticReconciliationEngine",
    "SupplierPaymentProvider",
    "SupplierPaymentReferenceStrategy",
    "build_extra_gl_account_ids",
]
