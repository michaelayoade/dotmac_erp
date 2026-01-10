"""
Consolidation Services.

This module provides services for group consolidation including legal entity
management, ownership tracking, intercompany balances, and consolidation runs
per IFRS 10.
"""

from app.services.ifrs.cons.legal_entity import (
    LegalEntityService,
    LegalEntityInput,
    GroupStructure,
    legal_entity_service,
)
from app.services.ifrs.cons.ownership import (
    OwnershipService,
    OwnershipInput,
    EffectiveOwnershipResult,
    NCISummary,
    ownership_service,
)
from app.services.ifrs.cons.intercompany import (
    IntercompanyService,
    IntercompanyBalanceInput,
    MatchingResult,
    IntercompanySummary,
    intercompany_service,
)
from app.services.ifrs.cons.consolidation import (
    ConsolidationService,
    ConsolidationRunInput,
    EliminationInput,
    ConsolidationSummary,
    consolidation_service,
)
from app.services.ifrs.cons.cons_posting_adapter import (
    CONSPostingAdapter,
    CONSPostingResult,
    cons_posting_adapter,
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
