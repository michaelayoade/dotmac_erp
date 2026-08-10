"""The GL posting backlog, extracted from a script into a service.

The two business rules this pins were previously discoverable only by reading
a 195-line script that ran when somebody typed its name: what counts as
balanced, and which fiscal periods accept a posting.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

from tests._helpers.source_introspection import (
    calls_named,
    mentions_in_code,
    module_level_assignments,
)

from app.services.finance.gl.posting_backlog import (
    IMBALANCE_TOLERANCE,
    POSTABLE_PERIOD_STATUSES,
    ApprovedJournal,
    JournalPostingResult,
    post_approved_journals,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO_ROOT / "scripts" / "post_approved_journals.py"
ORG = uuid.uuid4()
ACTOR = uuid.uuid4()


def _journal(imbalance="0", period="OPEN"):
    return ApprovedJournal(
        journal_entry_id=uuid.uuid4(),
        journal_number="JE-001",
        imbalance=Decimal(imbalance),
        period_status=period,
        source_module="GL",
    )


def _db(rows):
    db = MagicMock()
    db.execute.return_value.all.return_value = rows
    return db


# --------------------------------------------------------------------------
# The balance rule
# --------------------------------------------------------------------------


def test_a_journal_within_tolerance_is_balanced():
    assert _journal("0.009").is_balanced
    assert _journal("0").is_balanced


def test_a_journal_at_the_tolerance_is_not_balanced():
    """Strictly less than. A whole kobo out is a real imbalance, not dust."""
    assert not _journal(str(IMBALANCE_TOLERANCE)).is_balanced


def test_an_unbalanced_journal_is_never_postable():
    assert not _journal("5.00").is_postable


# --------------------------------------------------------------------------
# The period rule
# --------------------------------------------------------------------------


def test_open_and_reopened_periods_accept_postings():
    assert _journal(period="OPEN").is_postable
    assert _journal(period="REOPENED").is_postable


def test_a_journal_with_no_period_is_postable():
    assert _journal(period=None).is_postable


def test_a_closed_period_is_skipped_not_forced():
    """Closing a period is a decision the period service owns. A backlog job
    must not quietly reverse it."""
    assert not _journal(period="CLOSED").is_postable
    assert not _journal(period="SOFT_CLOSED").is_postable


def test_the_postable_period_set_is_exactly_these_three():
    assert set(POSTABLE_PERIOD_STATUSES) == {"OPEN", "REOPENED", None}


# --------------------------------------------------------------------------
# Behaviour
# --------------------------------------------------------------------------


def test_dry_run_posts_nothing_but_still_counts():
    rows = [
        (uuid.uuid4(), "JE-1", Decimal("0"), "OPEN", "GL"),
        (uuid.uuid4(), "JE-2", Decimal("9"), "OPEN", "GL"),
        (uuid.uuid4(), "JE-3", Decimal("0"), "CLOSED", "GL"),
    ]
    with patch("app.services.finance.gl.journal.JournalService") as svc:
        result = post_approved_journals(
            _db(rows), organization_id=ORG, posted_by_user_id=ACTOR, dry_run=True
        )
    svc.post_journal.assert_not_called()
    assert (result.found, result.unbalanced, result.closed_period) == (3, 1, 1)
    assert result.postable == 1


def test_only_postable_journals_are_posted():
    rows = [
        (uuid.uuid4(), "JE-1", Decimal("0"), "OPEN", "GL"),
        (uuid.uuid4(), "JE-2", Decimal("9"), "OPEN", "GL"),
        (uuid.uuid4(), "JE-3", Decimal("0"), "CLOSED", "GL"),
    ]
    with patch("app.services.finance.gl.journal.JournalService") as svc:
        result = post_approved_journals(
            _db(rows), organization_id=ORG, posted_by_user_id=ACTOR, dry_run=False
        )
    assert svc.post_journal.call_count == 1
    assert result.posted == 1


def test_a_failing_journal_does_not_abort_the_batch():
    rows = [
        (uuid.uuid4(), "JE-BAD", Decimal("0"), "OPEN", "GL"),
        (uuid.uuid4(), "JE-OK", Decimal("0"), "OPEN", "GL"),
    ]
    with patch("app.services.finance.gl.journal.JournalService") as svc:
        svc.post_journal.side_effect = [RuntimeError("boom"), None]
        result = post_approved_journals(
            _db(rows), organization_id=ORG, posted_by_user_id=ACTOR, dry_run=False
        )
    assert result.posted == 1
    assert len(result.errors) == 1
    assert "JE-BAD" in result.errors[0]


def test_the_service_never_commits():
    db = _db([])
    post_approved_journals(
        db, organization_id=ORG, posted_by_user_id=ACTOR, dry_run=False
    )
    db.commit.assert_not_called()


def test_the_query_is_scoped_to_the_organization():
    """The RLS GUC is a second line of defence, never the only one."""
    db = _db([])
    post_approved_journals(db, organization_id=ORG, posted_by_user_id=ACTOR)
    _, params = db.execute.call_args[0]
    assert params["org_id"] == str(ORG)


def test_result_defaults_are_zero():
    r = JournalPostingResult()
    assert (r.found, r.posted, r.unbalanced, r.closed_period) == (0, 0, 0, 0)
    assert r.errors == []


# --------------------------------------------------------------------------
# Regression guards on what the extraction removed
# --------------------------------------------------------------------------


def test_the_script_no_longer_hardcodes_an_organization():
    assert "ORG_ID" not in module_level_assignments(SCRIPT)


def test_the_script_requires_an_org_id_and_records_a_batch_operation():
    source = SCRIPT.read_text(encoding="utf-8")
    assert '"--org-id"' in source and "required=True" in source
    assert "batch_operation(" in source
    assert "session_for_org" in source


def test_the_script_issues_no_raw_sql():
    """Selection moved into the service; the CLI only parses and delegates."""
    assert calls_named(SCRIPT, "text") == []
    assert mentions_in_code(SCRIPT, "SELECT ") == []
