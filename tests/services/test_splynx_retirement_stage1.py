"""Stage 1 of the Splynx retirement: passes off, data still reachable.

Splynx is retired, but its payments are not: `ar.customer_payment` rows
carrying a `splynx_id` are real historical payments that still need to
reconcile against bank statements.

The two providers used to PARTITION customer payments —
`SplynxCustomerPaymentProvider` took `splynx_id IS NOT NULL`,
`CustomerReceiptProvider` took `splynx_id IS NULL`. Disabling the Splynx side
without widening the other would have made every Splynx-era payment
permanently unmatchable by auto-reconciliation, which is a worse outcome than
leaving the integration wired.

So stage 1 is two changes that only make sense together:

1. the Splynx passes default off, which drops their provider and strategies
   out of the resolved policy; and
2. the general AR loader stops excluding `splynx_id IS NOT NULL` rows.

Nothing is deleted — the flags restore the old behaviour if verification
turns up a regression.
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
PAYMENTS = (
    REPO_ROOT / "app/services/finance/banking/auto_reconciliation_parts/payments.py"
)


# --------------------------------------------------------------------------
# The passes are off
# --------------------------------------------------------------------------


def test_both_splynx_passes_default_to_off():
    config = AutoMatchDefaults()
    assert config.pass_splynx_by_ref_enabled is False
    assert config.pass_splynx_date_amount_enabled is False


def test_the_splynx_provider_is_not_in_the_resolved_policy():
    """This is what actually stops the strategies: they check
    `policy.allows_provider(...)` and return early."""
    policy = build_policy_from_config(AutoMatchDefaults())
    assert not policy.allows_provider("receivable_payment_synced")


def test_the_splynx_strategies_are_not_enabled():
    policy = build_policy_from_config(AutoMatchDefaults())
    assert not policy.allows_strategy("exact_synced_receivable_reference")


def test_turning_a_flag_back_on_restores_the_pass():
    """The reversible half. Nothing is deleted, so a regression found during
    verification is a config change, not a revert."""
    config = AutoMatchDefaults()
    config.pass_splynx_by_ref_enabled = True
    policy = build_policy_from_config(config)
    assert policy.allows_provider("receivable_payment_synced")
    assert policy.allows_strategy("exact_synced_receivable_reference")


# --------------------------------------------------------------------------
# ...and the data is still reachable
# --------------------------------------------------------------------------


def test_the_general_ar_loader_no_longer_excludes_splynx_rows():
    """The load-bearing half. Without this, disabling the passes above would
    strand every Splynx-era payment."""
    source = PAYMENTS.read_text(encoding="utf-8")
    assert "def _load_ar_payments(" in source
    assert "def _load_non_splynx_ar_payments(" not in source
    assert "CustomerPayment.splynx_id.is_(None)" not in source


def test_the_splynx_loader_still_exists_and_still_filters():
    """Stage 1 deletes nothing. The Splynx loader remains, unreachable via
    policy but intact, so restoring a flag genuinely restores behaviour."""
    source = PAYMENTS.read_text(encoding="utf-8")
    assert "def _load_splynx_payments(" in source
    assert "CustomerPayment.splynx_id.isnot(None)" in source


def test_the_other_passes_are_untouched():
    """Retirement must not quietly disable anything else."""
    config = AutoMatchDefaults()
    assert config.pass_payment_intents_enabled is True
    assert config.pass_ap_payments_enabled is True
    assert config.pass_ar_payments_enabled is True
