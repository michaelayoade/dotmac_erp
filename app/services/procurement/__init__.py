"""
Procurement Services.

Business logic for procurement management:
- PPA 2007 threshold enforcement
- Procurement planning
- Purchase requisitions
- RFQ/bid management
- Bid evaluation
- Contract management
- Vendor prequalification
"""

from app.services.procurement.thresholds import (
    PPA_THRESHOLDS,
    determine_procurement_method,
    validate_procurement_method,
)

__all__ = [
    "PPA_THRESHOLDS",
    "determine_procurement_method",
    "validate_procurement_method",
]

# Setting domain(s) this module owns — purchasing and supplier policy.
# Validated by `app.services.setting_domains` at startup and at every write;
# see that module for why ownership lives here rather than in a central list.
SETTING_DOMAINS: tuple[str, ...] = ("procurement",)
