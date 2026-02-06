"""
Lease (IFRS 16) Services.

This module provides services for lease contract management,
liability and ROU asset calculations, and GL posting.
"""

from app.services.finance.lease.lease_calculation import (
    InterestAccrualResult,
    LeaseCalculationService,
    LiabilityCalculationResult,
    PaymentScheduleEntry,
    lease_calculation_service,
)
from app.services.finance.lease.lease_contract import (
    LeaseContractInput,
    LeaseContractService,
    lease_contract_service,
)
from app.services.finance.lease.lease_modification import (
    LeaseModificationService,
    ModificationInput,
    ModificationResult,
    lease_modification_service,
)
from app.services.finance.lease.lease_posting_adapter import (
    LeasePostingAdapter,
    LeasePostingResult,
    lease_posting_adapter,
)
from app.services.finance.lease.lease_variable_payment import (
    IndexAdjustmentInput,
    IndexAdjustmentResult,
    LeaseVariablePaymentService,
    VariablePaymentInput,
    lease_variable_payment_service,
)

__all__ = [
    # Contract
    "LeaseContractService",
    "LeaseContractInput",
    "lease_contract_service",
    # Calculation
    "LeaseCalculationService",
    "LiabilityCalculationResult",
    "PaymentScheduleEntry",
    "InterestAccrualResult",
    "lease_calculation_service",
    # Posting
    "LeasePostingAdapter",
    "LeasePostingResult",
    "lease_posting_adapter",
    # Modification
    "LeaseModificationService",
    "lease_modification_service",
    "ModificationInput",
    "ModificationResult",
    # Variable Payments
    "LeaseVariablePaymentService",
    "lease_variable_payment_service",
    "VariablePaymentInput",
    "IndexAdjustmentInput",
    "IndexAdjustmentResult",
]
