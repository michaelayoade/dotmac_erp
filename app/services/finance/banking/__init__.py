"""
Banking Services.

Provides services for bank account management, statement import,
bank reconciliation, and transaction categorization.
"""

from app.services.finance.banking.bank_account import (
    BankAccountInput,
    BankAccountService,
    bank_account_service,
)
from app.services.finance.banking.bank_reconciliation import (
    BankReconciliationService,
    MatchSuggestion,
    ReconciliationInput,
    ReconciliationMatchInput,
    bank_reconciliation_service,
)
from app.services.finance.banking.bank_statement import (
    BankStatementService,
    StatementImportResult,
    StatementLineInput,
    bank_statement_service,
)
from app.services.finance.banking.categorization import (
    BatchCategorizationResult,
    CategorizationResult,
    CategorizationSuggestion,
    TransactionCategorizationService,
    categorization_service,
)
from app.services.finance.banking.contra_matching import (
    ContraLineCandidate,
    ContraMatch,
    build_contra_idempotency_key,
    choose_best_contra_matches,
    score_contra_pair,
)

__all__ = [
    # Bank Account
    "BankAccountService",
    "BankAccountInput",
    "bank_account_service",
    # Bank Statement
    "BankStatementService",
    "StatementLineInput",
    "StatementImportResult",
    "bank_statement_service",
    # Bank Reconciliation
    "BankReconciliationService",
    "ReconciliationInput",
    "ReconciliationMatchInput",
    "MatchSuggestion",
    "bank_reconciliation_service",
    # Categorization
    "TransactionCategorizationService",
    "CategorizationSuggestion",
    "CategorizationResult",
    "BatchCategorizationResult",
    "categorization_service",
    # Contra matching
    "ContraLineCandidate",
    "ContraMatch",
    "build_contra_idempotency_key",
    "score_contra_pair",
    "choose_best_contra_matches",
]
