"""Guard the RLS properties that are defects at any coverage level.

ERP enables row-level security through point-in-time sweeps over
``information_schema``, so which tables are protected depends on what existed
when each sweep ran. Coverage is therefore **not derivable from source**, and
until this work there was no ``pg_catalog`` introspection anywhere in the
repository to derive it from anything else.

The coverage *number* is reported by ``scripts/architecture/rls_coverage_audit.py``,
which CI runs as its own step so the figure lands in the log whether or not
anything fails. This module deliberately asserts only what is wrong regardless
of what that number turns out to be.

That split is the point. Enforcing full coverage on day one would fail an
unknown number of times on its first run, and a gate like that gets disabled
rather than fixed. Once the baseline is recorded from a CI run, the audit's
``--enforce --baseline`` mode turns it into a ratchet that lets the gap shrink
and never grow — the shadow-then-enforce shape ADR-0008's migration sequence
gate already uses in Sub.
"""

from __future__ import annotations

import pytest

from scripts.architecture.rls_coverage_audit import (
    SCOPE_COLUMN,
    collect,
    resolve_database_url,
)

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def report():
    return collect(resolve_database_url(None))


def test_the_catalog_is_migrated_and_scoped(report):
    """A guard on the audit itself.

    If the database were empty, or the scope column renamed, every other
    assertion here would pass vacuously by finding nothing to check — the
    failure mode where a green suite means the test stopped looking.
    """
    assert report.tables, "no tables found — is the database migrated?"
    assert report.scoped, (
        f"no table carries {SCOPE_COLUMN!r}. Either the database is not migrated "
        f"or the scope column changed, in which case this audit is measuring "
        f"nothing and must be updated before it is trusted."
    )


def test_no_table_has_rls_enabled_with_no_policy(report):
    """RLS enabled with zero policies denies EVERY row to every non-owner.

    Unambiguous at any phase: never intended, not a partial migration, and it
    presents as data silently disappearing rather than as an error.
    """
    broken = [t.qualified for t in report.of("unpolicied")]
    assert not broken, (
        "RLS is enabled with no policy on: "
        + ", ".join(broken)
        + " — these deny all rows to non-owners."
    )


def test_policies_are_not_attached_to_unscoped_tables(report):
    """A policy on a table with no scope column cannot express tenant isolation.

    Either the table lost its column or the policy is keyed on something else
    and its name is misleading. Both are worth knowing at the point of change.
    """
    orphans = [t.qualified for t in report.of("orphan-policy")]
    assert not orphans, (
        f"RLS is enabled on tables with no {SCOPE_COLUMN}: "
        + ", ".join(orphans)
        + " — the policy cannot be scoping by organization."
    )
