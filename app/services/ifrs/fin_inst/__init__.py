"""
Financial Instruments (IFRS 9) Services.

This module provides services for financial instrument management including
classification, interest accrual, fair value measurement, ECL staging,
hedge accounting, and GL posting.
"""

from app.services.ifrs.fin_inst.instrument import (
    FinancialInstrumentService,
    InstrumentInput,
    ECLStagingResult,
    financial_instrument_service,
)
from app.services.ifrs.fin_inst.interest_accrual import (
    InterestAccrualService,
    AccrualCalculationResult,
    DayCountResult,
    interest_accrual_service,
)
from app.services.ifrs.fin_inst.valuation import (
    InstrumentValuationService,
    ValuationInput,
    ValuationResult,
    instrument_valuation_service,
)
from app.services.ifrs.fin_inst.hedge_accounting import (
    HedgeAccountingService,
    HedgeDesignationInput,
    EffectivenessTestInput,
    EffectivenessTestResult,
    hedge_accounting_service,
)
from app.services.ifrs.fin_inst.fin_inst_posting_adapter import (
    FININSTPostingAdapter,
    FININSTPostingResult,
    fin_inst_posting_adapter,
)

__all__ = [
    # Instrument
    "FinancialInstrumentService",
    "InstrumentInput",
    "ECLStagingResult",
    "financial_instrument_service",
    # Interest Accrual
    "InterestAccrualService",
    "AccrualCalculationResult",
    "DayCountResult",
    "interest_accrual_service",
    # Valuation
    "InstrumentValuationService",
    "ValuationInput",
    "ValuationResult",
    "instrument_valuation_service",
    # Hedge Accounting
    "HedgeAccountingService",
    "HedgeDesignationInput",
    "EffectivenessTestInput",
    "EffectivenessTestResult",
    "hedge_accounting_service",
    # Posting
    "FININSTPostingAdapter",
    "FININSTPostingResult",
    "fin_inst_posting_adapter",
]
