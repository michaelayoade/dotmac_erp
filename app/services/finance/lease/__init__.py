"""
Lease (IFRS 16) Services.

This module provides services for lease contract management,
liability and ROU asset calculations, and GL posting.
"""

from app.services.finance.lease.lease_contract import (
    LeaseContractService,
    LeaseContractInput,
    lease_contract_service,
)
from app.services.finance.lease.lease_calculation import (
    LeaseCalculationService,
    LiabilityCalculationResult,
    PaymentScheduleEntry,
    InterestAccrualResult,
    lease_calculation_service,
)
from app.services.finance.lease.lease_posting_adapter import (
    LeasePostingAdapter,
    LeasePostingResult,
    lease_posting_adapter,
)
from app.services.finance.lease.lease_modification import (
    LeaseModificationService,
    lease_modification_service,
    ModificationInput,
    ModificationResult,
)
from app.services.finance.lease.lease_variable_payment import (
    LeaseVariablePaymentService,
    lease_variable_payment_service,
    VariablePaymentInput,
    IndexAdjustmentInput,
    IndexAdjustmentResult,
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
