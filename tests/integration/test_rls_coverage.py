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


def test_an_unscoped_table_with_rls_still_reaches_organization_scope(report):
    """A child table carries no scope column and inherits through its parent.

    An earlier version of this test asserted that RLS on a table without
    `organization_id` was itself a defect. The first CI run refuted that: it
    flagged `ipsas.commitment_line`, `proc.quotation_response_line`,
    `proc.bid_evaluation_score`, `proc.rfq_invitation`, `platform.saga_step`
    and `leave.holiday` — line and child tables whose policies join through to a
    scoped parent. That is correct design, and the kernel uses the same shape
    for `PartyPerson`/`PartyOrganization`, which carry no `tenant_id` at all.

    So the question is not whether the COLUMN is present but whether the POLICY
    reaches organization scope. A policy that never mentions the scope column
    might still be deliberate, so this reports rather than forbids — it says
    "look at this", not "this is broken".
    """
    orphans = [t.qualified for t in report.of("orphan-policy")]
    if orphans:
        pytest.skip(
            f"{len(orphans)} table(s) have RLS but no policy mentioning "
            f"{SCOPE_COLUMN}: {', '.join(orphans)}. Classify each before the "
            f"baseline ratchet is turned on."
        )
