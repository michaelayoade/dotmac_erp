"""Organization discovery for the Splynx FIFO allocator.

The FIFO decision always lived in a service. What lived in the script was the
choice of tenant, and it chose with `LIMIT 1` over an unordered scan — so a
second organization with Splynx payments would have been skipped silently,
and which one ran depended on the query plan.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import MagicMock

from tests._helpers.source_introspection import mentions_in_code

from app.services.finance.ar.allocation_backlog import (
    organizations_with_splynx_payments,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO_ROOT / "scripts" / "allocate_splynx_fifo.py"


def _db(rows):
    db = MagicMock()
    db.execute.return_value.all.return_value = rows
    return db


def test_every_organization_is_returned_not_just_one():
    """The defect, stated as a test: two organizations must yield two."""
    a, b = uuid.uuid4(), uuid.uuid4()
    result = organizations_with_splynx_payments(_db([(a,), (b,)]))
    assert result == [a, b]


def test_no_organizations_is_an_empty_list_not_an_error():
    assert organizations_with_splynx_payments(_db([])) == []


def test_the_query_is_ordered_so_runs_are_reproducible():
    """`LIMIT 1` over an unordered scan meant the chosen tenant could differ
    between runs. Ordering makes a partial run diagnosable."""
    db = _db([])
    organizations_with_splynx_payments(db)
    sql = str(db.execute.call_args[0][0]).upper()
    assert "ORDER BY" in sql
    assert "LIMIT" not in sql


def test_results_are_uuids_not_raw_rows():
    raw = uuid.uuid4()
    assert organizations_with_splynx_payments(_db([(str(raw),)])) == [raw]


# --------------------------------------------------------------------------
# Regression guards on the script
# --------------------------------------------------------------------------


def test_the_script_no_longer_infers_the_organization_from_data():
    """No `LIMIT 1` org discovery left in executable code. The script's own
    docstring quotes the old query to explain what changed, so this asserts on
    executable strings only."""
    assert mentions_in_code(SCRIPT, "LIMIT 1") == []
    assert mentions_in_code(SCRIPT, "SELECT DISTINCT") == []


def test_the_script_requires_an_explicit_organization():
    source = SCRIPT.read_text(encoding="utf-8")
    assert "add_mutually_exclusive_group(required=True)" in source
    assert '"--org-id"' in source


def test_the_script_uses_a_scoped_session_and_records_the_run():
    source = SCRIPT.read_text(encoding="utf-8")
    assert "session_for_org" in source
    assert "batch_operation(" in source
    assert "SessionLocal" not in source
