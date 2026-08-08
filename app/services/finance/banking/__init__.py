"""
Banking Services.

Keep package import-light to avoid loading reconciliation/statement pipelines
during unrelated imports (especially test collection).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from app.services.finance.banking.bank_account import (  # noqa: F401
        BankAccountInput,
        BankAccountService,
        bank_account_service,
    )
    from app.services.finance.banking.bank_reconciliation import (  # noqa: F401
        BankReconciliationService,
        MatchSuggestion,
        ReconciliationInput,
        ReconciliationMatchInput,
        bank_reconciliation_service,
    )
    from app.services.finance.banking.bank_statement import (  # noqa: F401
        BankStatementService,
        StatementImportResult,
        StatementLineInput,
        bank_statement_service,
    )
    from app.services.finance.banking.categorization import (  # noqa: F401
        BatchCategorizationResult,
        CategorizationResult,
        CategorizationSuggestion,
        TransactionCategorizationService,
        categorization_service,
    )
    from app.services.finance.banking.contra_matching import (  # noqa: F401
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


_NAME_TO_MODULE = {
    # bank_account
    "BankAccountInput": "bank_account",
    "BankAccountService": "bank_account",
    "bank_account_service": "bank_account",
    # bank_statement
    "BankStatementService": "bank_statement",
    "StatementImportResult": "bank_statement",
    "StatementLineInput": "bank_statement",
    "bank_statement_service": "bank_statement",
    # bank_reconciliation
    "BankReconciliationService": "bank_reconciliation",
    "MatchSuggestion": "bank_reconciliation",
    "ReconciliationInput": "bank_reconciliation",
    "ReconciliationMatchInput": "bank_reconciliation",
    "bank_reconciliation_service": "bank_reconciliation",
    # categorization
    "BatchCategorizationResult": "categorization",
    "CategorizationResult": "categorization",
    "CategorizationSuggestion": "categorization",
    "TransactionCategorizationService": "categorization",
    "categorization_service": "categorization",
    # contra_matching
    "ContraLineCandidate": "contra_matching",
    "ContraMatch": "contra_matching",
    "build_contra_idempotency_key": "contra_matching",
    "choose_best_contra_matches": "contra_matching",
    "score_contra_pair": "contra_matching",
}


def __getattr__(name: str) -> Any:  # pragma: no cover
    module_name = _NAME_TO_MODULE.get(name)
    if not module_name:
        raise AttributeError(name)
    module = __import__(f"{__name__}.{module_name}", fromlist=[name])
    return getattr(module, name)


from app.services.setting_domain_declaration import ModuleSettingDomains  # noqa: E402

# Setting domain(s) this module owns — bank integrations and reconciliation.
# Validated by `app.services.setting_domains` at startup and at every write;
# see that module for why ownership lives here rather than in a central list.
SETTING_DOMAINS = ModuleSettingDomains(setting_domains=("banking",))
