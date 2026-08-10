"""
GL (General Ledger) Services - Accounting Spine.

Keep package import-light to avoid importing the entire GL stack during
unrelated imports (especially during test collection).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from app.services.finance.gl.account_balance import (  # noqa: F401
        AccountBalanceService,
        account_balance_service,
    )
    from app.services.finance.gl.balance_invalidation import (  # noqa: F401
        BalanceInvalidationService,
    )
    from app.services.finance.gl.balance_refresh import BalanceRefreshService  # noqa: F401
    from app.services.finance.gl.category import (  # noqa: F401
        CategoryNode,
        CategoryService,
        category_service,
    )
    from app.services.finance.gl.chart_of_accounts import (  # noqa: F401
        AccountInput,
        ChartOfAccountsService,
        chart_of_accounts_service,
    )
    from app.services.finance.gl.fiscal_period import (  # noqa: F401
        FiscalPeriodInput,
        FiscalPeriodService,
        fiscal_period_service,
    )
    from app.services.finance.gl.fiscal_year import (  # noqa: F401
        FiscalYearInput,
        FiscalYearService,
        fiscal_year_service,
    )
    from app.services.finance.gl.gl_posting_adapter import (  # noqa: F401
        GLPostingAdapter,
        GLPostingResult,
        gl_posting_adapter,
    )
    from app.services.finance.gl.journal import (  # noqa: F401
        JournalInput,
        JournalLineInput,
        JournalService,
        journal_service,
    )
    from app.services.finance.gl.ledger_posting import (  # noqa: F401
        LedgerPostingService,
        ledger_posting_service,
    )
    from app.services.finance.gl.period_guard import (  # noqa: F401
        PeriodGuardService,
        period_guard_service,
    )
    from app.services.finance.gl.reversal import ReversalService, reversal_service  # noqa: F401


__all__ = [
    # Period Guard
    "PeriodGuardService",
    "period_guard_service",
    # Ledger Posting
    "LedgerPostingService",
    "ledger_posting_service",
    # Journal
    "JournalService",
    "JournalInput",
    "JournalLineInput",
    "journal_service",
    # Reversal
    "ReversalService",
    "reversal_service",
    # Account Balance
    "AccountBalanceService",
    "account_balance_service",
    "BalanceInvalidationService",
    "BalanceRefreshService",
    "balance_service",
    # Chart of Accounts
    "ChartOfAccountsService",
    "AccountInput",
    "chart_of_accounts_service",
    # Category hierarchy
    "CategoryService",
    "CategoryNode",
    "category_service",
    # Fiscal Period
    "FiscalPeriodService",
    "FiscalPeriodInput",
    "fiscal_period_service",
    # Fiscal Year
    "FiscalYearService",
    "FiscalYearInput",
    "fiscal_year_service",
    # GL Posting Adapter
    "GLPostingAdapter",
    "GLPostingResult",
    "gl_posting_adapter",
]


_NAME_TO_MODULE = {
    "PeriodGuardService": "period_guard",
    "period_guard_service": "period_guard",
    "LedgerPostingService": "ledger_posting",
    "ledger_posting_service": "ledger_posting",
    "JournalService": "journal",
    "JournalInput": "journal",
    "JournalLineInput": "journal",
    "journal_service": "journal",
    "ReversalService": "reversal",
    "reversal_service": "reversal",
    "AccountBalanceService": "account_balance",
    "account_balance_service": "account_balance",
    "BalanceInvalidationService": "balance_invalidation",
    "BalanceRefreshService": "balance_refresh",
    "ChartOfAccountsService": "chart_of_accounts",
    "AccountInput": "chart_of_accounts",
    "chart_of_accounts_service": "chart_of_accounts",
    "CategoryService": "category",
    "CategoryNode": "category",
    "category_service": "category",
    "FiscalPeriodService": "fiscal_period",
    "FiscalPeriodInput": "fiscal_period",
    "fiscal_period_service": "fiscal_period",
    "FiscalYearService": "fiscal_year",
    "FiscalYearInput": "fiscal_year",
    "fiscal_year_service": "fiscal_year",
    "GLPostingAdapter": "gl_posting_adapter",
    "GLPostingResult": "gl_posting_adapter",
    "gl_posting_adapter": "gl_posting_adapter",
}


def __getattr__(name: str) -> Any:  # pragma: no cover
    if name == "balance_service":
        from app.services.finance.gl.account_balance import account_balance_service

        return account_balance_service
    module_name = _NAME_TO_MODULE.get(name)
    if not module_name:
        raise AttributeError(name)
    module = __import__(f"{__name__}.{module_name}", fromlist=[name])
    return getattr(module, name)


from app.services.setting_domain_declaration import ModuleSettingDomains  # noqa: E402

# Setting domain(s) this module owns — general-ledger posting policy.
# Validated by `app.services.setting_domains` at startup and at every write;
# see that module for why ownership lives here rather than in a central list.
SETTING_DOMAINS = ModuleSettingDomains(setting_domains=("gl",))
