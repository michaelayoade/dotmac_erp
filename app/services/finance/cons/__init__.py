"""
Consolidation Services.

This module provides services for group consolidation including legal entity
management, ownership tracking, intercompany balances, and consolidation runs
per IFRS 10.
"""

from app.services.finance.cons.cons_posting_adapter import (
    CONSPostingAdapter,
    CONSPostingResult,
    cons_posting_adapter,
)
from app.services.finance.cons.consolidation import (
    ConsolidationRunInput,
    ConsolidationService,
    ConsolidationSummary,
    EliminationInput,
    consolidation_service,
)
from app.services.finance.cons.intercompany import (
    IntercompanyBalanceInput,
    IntercompanyService,
    IntercompanySummary,
    MatchingResult,
    intercompany_service,
)
from app.services.finance.cons.legal_entity import (
    GroupStructure,
    LegalEntityInput,
    LegalEntityService,
    legal_entity_service,
)
from app.services.finance.cons.ownership import (
    EffectiveOwnershipResult,
    NCISummary,
    OwnershipInput,
    OwnershipService,
    ownership_service,
)

__all__ = [
    # Legal Entity
    "LegalEntityService",
    "LegalEntityInput",
    "GroupStructure",
    "legal_entity_service",
    # Ownership
    "OwnershipService",
    "OwnershipInput",
    "EffectiveOwnershipResult",
    "NCISummary",
    "ownership_service",
    # Intercompany
    "IntercompanyService",
    "IntercompanyBalanceInput",
    "MatchingResult",
    "IntercompanySummary",
    "intercompany_service",
    # Consolidation
    "ConsolidationService",
    "ConsolidationRunInput",
    "EliminationInput",
    "ConsolidationSummary",
    "consolidation_service",
    # Posting
    "CONSPostingAdapter",
    "CONSPostingResult",
    "cons_posting_adapter",
]
