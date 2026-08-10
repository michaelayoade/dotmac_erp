"""Stranded-journal posting, and the substring classification it replaced.

The load-bearing one here is replay detection. The old loop decided whether
anything had been written by doing `if "Already posted" in msg` on a
human-readable message. Reword that message and every replay silently counts
as a fresh posting — an over-count in a ledger repair job.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

from tests._helpers.source_introspection import (
    mentions_in_code,
    module_level_assignments,
)

from app.services.finance.gl.ledger_posting import PostingResult
from app.services.finance.gl.stranded_fee_posting import (
    StrandedPostingResult,
    find_stranded_journals,
    post_one,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO_ROOT / "scripts" / "post_stranded_bank_fees.py"
ORG = uuid.uuid4()


def _journal():
    journal = MagicMock()
    journal.journal_entry_id = uuid.uuid4()
    journal.journal_number = "JE-001"
    journal.organization_id = ORG
    journal.approved_by_user_id = uuid.uuid4()
    journal.created_by_user_id = uuid.uuid4()
    return journal


# --------------------------------------------------------------------------
# Replay detection — structured, not prose
# --------------------------------------------------------------------------


def test_posting_result_carries_a_structured_replay_flag():
    """The field that replaces substring matching, and its safe default."""
    assert PostingResult(success=True).idempotent_replay is False
    assert PostingResult(success=True, idempotent_replay=True).idempotent_replay


def test_a_replay_is_reported_from_the_flag_not_the_message():
    with patch(
        "app.services.finance.gl.ledger_posting.LedgerPostingService"
    ) as service:
        service.post_journal_entry.return_value = PostingResult(
            success=True,
            idempotent_replay=True,
            message="anything at all",  # deliberately not the old wording
        )
        ok, replay, _ = post_one(MagicMock(), _journal(), source_module="BANKING")
    assert (ok, replay) == (True, True)


def test_a_fresh_posting_is_not_mistaken_for_a_replay():
    with patch(
        "app.services.finance.gl.ledger_posting.LedgerPostingService"
    ) as service:
        service.post_journal_entry.return_value = PostingResult(
            success=True, message="Already posted (idempotent replay)"
        )
        # Message says replay, flag says no. The FLAG is the decision — this is
        # the exact inversion that proves prose is not being consulted.
        ok, replay, _ = post_one(MagicMock(), _journal(), source_module="BANKING")
    assert (ok, replay) == (True, False)


def test_the_service_does_not_read_the_message_to_classify():
    from app.services.finance.gl import stranded_fee_posting

    source = Path(stranded_fee_posting.__file__)
    assert mentions_in_code(source, "Already posted") == []


def test_an_exception_is_a_failure_not_a_replay():
    with patch(
        "app.services.finance.gl.ledger_posting.LedgerPostingService"
    ) as service:
        service.post_journal_entry.side_effect = RuntimeError("boom")
        ok, replay, msg = post_one(MagicMock(), _journal(), source_module="BANKING")
    assert (ok, replay) == (False, False)
    assert "RuntimeError" in msg


# --------------------------------------------------------------------------
# Scope and parameterisation
# --------------------------------------------------------------------------


def test_the_query_filters_by_organization():
    db = MagicMock()
    db.scalars.return_value.all.return_value = []
    find_stranded_journals(
        db,
        organization_id=ORG,
        year_code="FY2025",
        source_module="BANKING",
        source_document_type="BANK_FEE",
    )
    rendered = str(db.scalars.call_args[0][0])
    assert "organization_id" in rendered


def test_the_fiscal_year_is_a_parameter_not_a_constant():
    """`TARGET_YEAR_CODE = "FY2025"` at module level made a one-year repair
    look like a general tool."""
    from app.services.finance.gl import stranded_fee_posting

    names = module_level_assignments(Path(stranded_fee_posting.__file__))
    assert "TARGET_YEAR_CODE" not in names
    assert "SOURCE_MODULE" not in names
    assert "SOURCE_DOC_TYPE" not in names


def test_the_idempotency_key_is_stable_for_a_journal():
    """A repeat run must produce the same key, or the replay path never
    triggers and the repair double-posts."""
    journal = _journal()
    captured = {}

    with patch(
        "app.services.finance.gl.ledger_posting.LedgerPostingService"
    ) as service:
        service.post_journal_entry.side_effect = lambda db, req: (
            captured.setdefault("key", req.idempotency_key)
            and PostingResult(success=True)
        )
        post_one(MagicMock(), journal, source_module="BANKING")
        first = captured["key"]
        captured.clear()
        post_one(MagicMock(), journal, source_module="BANKING")

    assert first == f"backfill-stranded-{journal.journal_number}"


def test_result_defaults_are_zero():
    r = StrandedPostingResult()
    assert (r.found, r.posted, r.already_posted) == (0, 0, 0)
    assert r.failures == []


# --------------------------------------------------------------------------
# Regression guards on the script
# --------------------------------------------------------------------------


def test_the_script_requires_org_and_year():
    source = SCRIPT.read_text(encoding="utf-8")
    assert '"--org-id"' in source
    assert '"--year"' in source
    assert source.count("required=True") >= 2


def test_the_script_no_longer_hardcodes_a_year_or_opens_a_raw_session():
    names = module_level_assignments(SCRIPT)
    assert "TARGET_YEAR_CODE" not in names
    assert mentions_in_code(SCRIPT, "SessionLocal") == []
    assert "session_for_org" in SCRIPT.read_text(encoding="utf-8")


def test_the_script_no_longer_classifies_by_substring():
    assert mentions_in_code(SCRIPT, "Already posted") == []
