"""The fiscal-period close gate.

Closing a period is one-way — everything downstream treats a closed period as
settled — so these are among the most consequential rules in the ledger, and
every one of them lived inside a 334-line script.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

from tests._helpers.source_introspection import (
    mentions_in_code,
    module_level_assignments,
)

from app.services.finance.gl.period_close import (
    CLOSEABLE_STATUSES,
    UNPOSTED_STATUSES,
    PeriodCloseResult,
    PeriodReadiness,
    close_periods,
)
from app.services.finance.gl.posting_backlog import IMBALANCE_TOLERANCE

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO_ROOT / "scripts" / "close_fiscal_periods.py"
SERVICE = REPO_ROOT / "app" / "services" / "finance" / "gl" / "period_close.py"
ORG = uuid.uuid4()
ACTOR = uuid.uuid4()


def _period(unposted=0, imbalance="0", status="OPEN", name="2025-01"):
    return PeriodReadiness(
        fiscal_period_id=uuid.uuid4(),
        period_name=name,
        year_name="FY2025",
        status=status,
        end_date=dt.date(2025, 1, 31),
        unposted_journals=unposted,
        posted_journals=10,
        imbalance=Decimal(imbalance),
    )


# --------------------------------------------------------------------------
# The gate
# --------------------------------------------------------------------------


def test_a_clean_period_is_ready():
    assert _period().is_ready
    assert _period().blockers == []


def test_unposted_journals_block_the_close():
    period = _period(unposted=3)
    assert not period.is_ready
    assert "3 unposted journal(s)" in period.blockers[0]


def test_approved_counts_as_unposted():
    """The subtle one. An APPROVED journal looks finished, but closing over it
    strands it permanently — it can never be posted into a closed period."""
    assert "APPROVED" in UNPOSTED_STATUSES
    assert "DRAFT" in UNPOSTED_STATUSES
    assert "SUBMITTED" in UNPOSTED_STATUSES
    assert "POSTED" not in UNPOSTED_STATUSES


def test_an_imbalance_blocks_the_close():
    period = _period(imbalance="5.00")
    assert not period.is_ready
    assert "trial balance out by" in period.blockers[0]


def test_both_blockers_are_reported_not_just_the_first():
    """An operator fixing one and re-running should not discover the second."""
    period = _period(unposted=2, imbalance="5.00")
    assert len(period.blockers) == 2


def test_only_open_and_reopened_periods_are_considered():
    assert set(CLOSEABLE_STATUSES) == {"OPEN", "REOPENED"}


# --------------------------------------------------------------------------
# The one-kobo behaviour change, made deliberately
# --------------------------------------------------------------------------


def test_an_imbalance_below_the_tolerance_does_not_block():
    assert _period(imbalance="0.009").is_ready


def test_an_imbalance_at_exactly_the_tolerance_now_blocks():
    """DELIBERATE CHANGE. The script blocked on `imbalance > 0.01`, so exactly
    one kobo passed. `gl.posting_backlog` treats a journal as balanced only
    when `imbalance < IMBALANCE_TOLERANCE`, so exactly 0.01 is an imbalance
    there. Two GL rules disagreeing about the same kobo is the drift worth
    removing, and the stricter reading is right for a one-way act."""
    assert not _period(imbalance=str(IMBALANCE_TOLERANCE)).is_ready


def test_the_tolerance_is_shared_not_redeclared():
    """A fifth independent declaration of 0.01 is what this avoids."""
    assert "IMBALANCE_TOLERANCE = " not in SERVICE.read_text(encoding="utf-8")
    assert (
        "from app.services.finance.gl.posting_backlog import IMBALANCE_TOLERANCE"
        in (SERVICE.read_text(encoding="utf-8"))
    )


# --------------------------------------------------------------------------
# force
# --------------------------------------------------------------------------


def _svc_and_periods(periods):
    service = patch("app.services.finance.gl.fiscal_period.FiscalPeriodService")
    assess = patch(
        "app.services.finance.gl.period_close.assess_periods", return_value=periods
    )
    return service, assess


def test_blocked_periods_are_not_closed_without_force():
    service, assess = _svc_and_periods([_period(unposted=1), _period(name="2025-02")])
    with service as svc, assess:
        result = close_periods(
            MagicMock(),
            organization_id=ORG,
            closed_by_user_id=ACTOR,
            dry_run=False,
        )
    assert result.closed == 1
    assert result.blocked == 1
    assert svc.soft_close_period.call_count == 1


def test_force_closes_over_blockers_and_reports_how_many():
    """The bypass is preserved — there are real situations needing it — but it
    leaves a trace of what it overrode."""
    service, assess = _svc_and_periods([_period(unposted=1), _period(name="2025-02")])
    with service, assess:
        result = close_periods(
            MagicMock(),
            organization_id=ORG,
            closed_by_user_id=ACTOR,
            force=True,
            dry_run=False,
        )
    assert result.closed == 2
    assert result.forced == 1
    assert result.blocked == 1  # still reported as blocked


def test_blocked_detail_names_the_period_and_its_reasons():
    service, assess = _svc_and_periods([_period(unposted=2, name="2025-03")])
    with service, assess:
        result = close_periods(
            MagicMock(), organization_id=ORG, closed_by_user_id=ACTOR, dry_run=True
        )
    assert result.blocked_detail[0][0] == "2025-03"
    assert "unposted" in result.blocked_detail[0][1][0]


# --------------------------------------------------------------------------
# Hard close sequencing
# --------------------------------------------------------------------------


def test_a_hard_close_of_an_open_period_soft_closes_first():
    """`hard_close_period` requires the period to be soft-closed already, so an
    OPEN period is two transitions, not one."""
    service, assess = _svc_and_periods([_period(status="OPEN")])
    with service as svc, assess:
        close_periods(
            MagicMock(),
            organization_id=ORG,
            closed_by_user_id=ACTOR,
            hard=True,
            dry_run=False,
        )
    assert svc.soft_close_period.call_count == 1
    assert svc.hard_close_period.call_count == 1


def test_a_hard_close_of_an_already_soft_closed_period_does_not_soft_close_again():
    service, assess = _svc_and_periods([_period(status="REOPENED")])
    with service as svc, assess:
        close_periods(
            MagicMock(),
            organization_id=ORG,
            closed_by_user_id=ACTOR,
            hard=True,
            dry_run=False,
        )
    assert svc.soft_close_period.call_count == 0
    assert svc.hard_close_period.call_count == 1


def test_dry_run_closes_nothing_but_still_assesses():
    service, assess = _svc_and_periods([_period(), _period(unposted=1)])
    with service as svc, assess:
        result = close_periods(
            MagicMock(), organization_id=ORG, closed_by_user_id=ACTOR, dry_run=True
        )
    svc.soft_close_period.assert_not_called()
    assert (result.assessed, result.ready, result.blocked) == (2, 1, 1)
    assert result.closed == 0


def test_a_failing_close_does_not_abort_the_rest():
    service, assess = _svc_and_periods([_period(name="a"), _period(name="b")])
    with service as svc, assess:
        svc.soft_close_period.side_effect = [RuntimeError("boom"), None]
        result = close_periods(
            MagicMock(), organization_id=ORG, closed_by_user_id=ACTOR, dry_run=False
        )
    assert result.closed == 1
    assert len(result.errors) == 1


def test_the_service_never_commits():
    db = MagicMock()
    service, assess = _svc_and_periods([])
    with service, assess:
        close_periods(db, organization_id=ORG, closed_by_user_id=ACTOR, dry_run=False)
    db.commit.assert_not_called()


def test_result_defaults_are_zero():
    r = PeriodCloseResult()
    assert (r.assessed, r.ready, r.blocked, r.closed, r.forced) == (0, 0, 0, 0, 0)


# --------------------------------------------------------------------------
# Regression guards on the script
# --------------------------------------------------------------------------


def test_the_script_no_longer_hardcodes_an_organization():
    assert "ORG_ID" not in module_level_assignments(SCRIPT)


def test_the_script_owns_no_sql_and_uses_a_scoped_session():
    assert mentions_in_code(SCRIPT, "SELECT ") == []
    assert mentions_in_code(SCRIPT, "SessionLocal") == []
    source = SCRIPT.read_text(encoding="utf-8")
    assert "session_for_org" in source and "batch_operation(" in source


def test_a_forced_run_is_recorded_on_the_batch_operation():
    """So a forced close is discoverable afterwards, not just at the terminal."""
    assert "FORCED" in SCRIPT.read_text(encoding="utf-8")
