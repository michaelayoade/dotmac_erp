"""AR API router compatibility module."""

from app.api.finance.ar_routes import router
from app.api.finance.ar_routes.contracts import (
    ContractCreate,
    ContractRead,
    PerformanceObligationCreate,
    ProgressUpdateCreate,
    RevenueEventRead,
)

__all__ = [
    "ContractCreate",
    "ContractRead",
    "PerformanceObligationCreate",
    "ProgressUpdateCreate",
    "RevenueEventRead",
    "router",
]
