"""Splynx is gone from reconciliation, and its payments still reconcile.

Those two claims only make sense together. The Splynx and general customer-
payment providers used to PARTITION `ar.customer_payment` by `splynx_id` —
one took `IS NOT NULL`, the other `IS NULL`. Deleting the Splynx side alone
would have left every Splynx-era payment unmatchable by auto-reconciliation.
The integration is retired; the money is not.

Replaces `test_splynx_retirement_stage1.py`, which asserted the reversible
intermediate state (flags present, code present but disabled). That state no
longer exists.
"""

from __future__ import annotations

from pathlib import Path

from app.services.finance.banking.auto_reconciliation_parts.base import (
    AutoMatchDefaults,
)
from app.services.finance.banking.reconciliation_policy import (
    build_policy_from_config,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BANKING = REPO_ROOT / "app" / "services" / "finance" / "banking"


def _banking_sources() -> list[tuple[str, str]]:
    return [
        (p.relative_to(REPO_ROOT).as_posix(), p.read_text(encoding="utf-8"))
        for p in sorted(BANKING.rglob("*.py"))
    ]


# Retired symbols. Deliberately NOT "the word splynx anywhere" — two things
# legitimately survive and a word-ban would force deleting them:
#   * `splynx_id` correlation-id derivation, which is how journals posted for
#     Splynx-era payments are still found; and
#   * comments explaining what was retired and why.
# A guard that punishes the explanation is worse than no guard.
_RETIRED_SYMBOLS = (
    "SplynxCustomerPaymentProvider",
    "CustomerPaymentReferenceStrategy",
    "UniqueDateAmountStrategy",
    "_splynx_ref_lookup",
    "_load_splynx_payments",
    "_match_splynx_payments",
    "pass_splynx_by_ref_enabled",
    "pass_splynx_date_amount_enabled",
    "receivable_payment_synced",
    "exact_synced_receivable_reference",
)


def test_no_retired_splynx_symbol_remains_in_reconciliation():
    offenders = [
        f"{name}:{n}  {symbol}"
        for name, src in _banking_sources()
        for n, line in enumerate(src.splitlines(), start=1)
        # Comments may NAME a retired symbol to explain what went and why.
        # A guard that punishes its own explanation is worse than no guard —
        # it pressures the next person to delete the reasoning.
        if not line.lstrip().startswith("#")
        for symbol in _RETIRED_SYMBOLS
        if symbol in line
    ]
    assert offenders == [], "these symbols were retired with Splynx:\n  " + "\n  ".join(
        offenders
    )


def test_the_correlation_id_for_splynx_era_payments_is_preserved():
    """Journals posted for Splynx-era payments were correlated as
    `splynx-pmt-{id}`. That derivation stays, because it is how those existing
    rows are still found — `splynx_id` survives as a historical identifier,
    not as an integration."""
    from types import SimpleNamespace

    from app.services.finance.banking.reconciliation_engine_parts.helpers import (
        ReconciliationEngineHelpers,
    )

    derive = ReconciliationEngineHelpers._get_correlation_id
    legacy = SimpleNamespace(splynx_id="4471", payment_id="uuid-x")
    modern = SimpleNamespace(splynx_id=None, payment_id="uuid-y")

    assert derive(legacy, "CUSTOMER_PAYMENT") == "splynx-pmt-4471"
    assert derive(modern, "CUSTOMER_PAYMENT") == "uuid-y"


def test_the_pass_flags_are_gone_not_merely_false():
    """A flag defaulting to False is still a flag somebody can turn on."""
    config = AutoMatchDefaults()
    assert not hasattr(config, "pass_splynx_by_ref_enabled")
    assert not hasattr(config, "pass_splynx_date_amount_enabled")


def test_the_splynx_provider_and_strategies_are_unimportable():
    from app.services.finance.banking import programmatic_parts

    for name in (
        "SplynxCustomerPaymentProvider",
        "CustomerPaymentReferenceStrategy",
        "UniqueDateAmountStrategy",
    ):
        assert not hasattr(programmatic_parts, name), f"{name} survives"


def test_the_policy_no_longer_enables_the_retired_provider():
    policy = build_policy_from_config(AutoMatchDefaults())
    assert not policy.allows_provider("receivable_payment_synced")
    assert not policy.allows_strategy("exact_synced_receivable_reference")


def test_the_surviving_passes_are_untouched():
    """Retirement must not quietly disable anything else."""
    config = AutoMatchDefaults()
    assert config.pass_payment_intents_enabled is True
    assert config.pass_ap_payments_enabled is True
    assert config.pass_ar_payments_enabled is True


def test_splynx_era_payments_are_still_reachable():
    """The load-bearing claim. The general AR loader must NOT exclude rows
    carrying a splynx_id — it used to, when a Splynx-specific pass handled
    them. Without this, retiring the passes strands real payments."""
    payments = (BANKING / "auto_reconciliation_parts" / "payments.py").read_text(
        encoding="utf-8"
    )
    assert "def _load_ar_payments(" in payments
    assert "CustomerPayment.splynx_id" not in payments
