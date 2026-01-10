"""
GL (General Ledger) Services - Accounting Spine.

Provides core accounting functionality including period control,
ledger posting, journal management, and balance tracking.
"""

from app.services.ifrs.gl.period_guard import PeriodGuardService, period_guard_service
from app.services.ifrs.gl.ledger_posting import LedgerPostingService, ledger_posting_service
from app.services.ifrs.gl.journal import (
    JournalService,
    JournalInput,
    JournalLineInput,
    journal_service,
)
from app.services.ifrs.gl.reversal import ReversalService, reversal_service
from app.services.ifrs.gl.account_balance import AccountBalanceService, account_balance_service

# Alias for backward compatibility with API imports
balance_service = account_balance_service
from app.services.ifrs.gl.chart_of_accounts import (
    ChartOfAccountsService,
    AccountInput,
    chart_of_accounts_service,
)
from app.services.ifrs.gl.fiscal_period import (
    FiscalPeriodService,
    FiscalPeriodInput,
    fiscal_period_service,
)
from app.services.ifrs.gl.fiscal_year import (
    FiscalYearService,
    FiscalYearInput,
    fiscal_year_service,
)
from app.services.ifrs.gl.gl_posting_adapter import (
    GLPostingAdapter,
    GLPostingResult,
    gl_posting_adapter,
)


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
    "balance_service",  # Alias for API compatibility
    # Chart of Accounts
    "ChartOfAccountsService",
    "AccountInput",
    "chart_of_accounts_service",
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
