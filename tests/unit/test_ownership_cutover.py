"""The ownership cutover decides what moves before it moves anything.

Ownership transfer is not undone by re-running something — it rewrites who
controls production objects. So the decisions that gate it (which owners are
approved, whether the estate is clean afterwards) are pure functions with tests,
and the SQL that renders the statements is pinned by shape.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.migration_database_roles import (
    MIGRATION_EXECUTOR,
    MIGRATION_OWNERSHIP_SQL,
    OWNERSHIP_PLAN_SQL,
    migration_ownership_violations,
    unexpected_owners,
)

SCRIPT = (
    Path(__file__).resolve().parents[2] / "scripts" / "cutover_database_ownership.py"
)


# ── Which owners may move ───────────────────────────────────────────────────


def test_an_unapproved_owner_stops_the_cutover() -> None:
    """The case this exists for: a role nobody expected owns objects, and a
    blanket sweep would hand them to the migration executor silently."""
    problems = unexpected_owners(
        {"postgres": 840, "some_integration_user": 3}, frozenset({"postgres"})
    )
    assert len(problems) == 1
    assert "some_integration_user" in problems[0]
    assert "3 object(s)" in problems[0]


def test_approving_every_owner_present_clears_it() -> None:
    assert unexpected_owners({"postgres": 840}, frozenset({"postgres"})) == ()


def test_approving_nothing_refuses_everything() -> None:
    """A missing `--approve-owner` must not read as "approve all"."""
    assert unexpected_owners({"postgres": 1}, frozenset()) != ()


def test_an_empty_plan_has_nothing_to_approve() -> None:
    assert unexpected_owners({}, frozenset()) == ()


# ── The post-condition ──────────────────────────────────────────────────────


def test_residual_non_owned_objects_are_a_refusal() -> None:
    """After a transfer the migration inventory must be empty. Anything left
    means migrations still fail, so the cutover must not report success."""
    assert migration_ownership_violations({"relation": 2}) != ()


def test_a_clean_estate_reports_no_violation() -> None:
    assert migration_ownership_violations({"relation": 0, "routine": 0}) == ()


# ── The plan SQL ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "kind", ["'database'", "'schema'", "'relation'", "'type'", "'routine'"]
)
def test_the_plan_covers_every_kind_the_preflight_counts(kind: str) -> None:
    """The plan and the preflight must describe the same estate, or the cutover
    would 'succeed' and leave the deploy still refusing."""
    assert kind in OWNERSHIP_PLAN_SQL
    assert kind in MIGRATION_OWNERSHIP_SQL


@pytest.mark.parametrize(
    "exclusion",
    [
        "deptype = 'e'",  # extension-owned objects
        "'^(pg_|information_schema)'",  # system schemas
        "pg_database_owner",  # schemas Postgres owns structurally
    ],
)
def test_the_plan_applies_the_preflights_exclusions(exclusion: str) -> None:
    """Transferring an extension's objects, or a system schema, would break the
    database rather than prepare it."""
    assert exclusion in OWNERSHIP_PLAN_SQL
    assert exclusion in MIGRATION_OWNERSHIP_SQL


def test_statements_are_rendered_by_postgres_not_by_python() -> None:
    """`::regclass` / `::regprocedure` / `%I` quote identifiers correctly —
    including names with capitals, spaces or reserved words that hand-rolled
    quoting gets wrong. Python must never concatenate an identifier here."""
    assert "::regclass::text" in OWNERSHIP_PLAN_SQL
    assert "::regprocedure::text" in OWNERSHIP_PLAN_SQL
    assert "OWNER TO %%I" in OWNERSHIP_PLAN_SQL


def test_postgres_format_specifiers_are_escaped_for_psycopg() -> None:
    """psycopg parses the query for its OWN `%` placeholders and rejects `%I`
    before the statement ever reaches PostgreSQL, so `format()`'s specifiers
    must be doubled while the real `%(target)s` bind stays single.

    An earlier version asserted `"OWNER TO %I" in ...` and passed, because the
    string genuinely contained it — the query was still unusable. That is the
    class of defect a driver-level test catches and a text assertion cannot,
    which is why the integration rehearsal exists.
    """
    assert "%I'" not in OWNERSHIP_PLAN_SQL.replace("%%I'", "")
    assert "%(target)s" in OWNERSHIP_PLAN_SQL
    assert "%%(target)s" not in OWNERSHIP_PLAN_SQL


def test_every_bind_carries_an_explicit_type() -> None:
    """`format()` takes variadic `"any"`, so an untyped bind makes PostgreSQL
    raise `AmbiguousParameter: could not determine data type of parameter $1`.

    Found by the integration rehearsal, not by reading the SQL — and added here
    afterwards, because this is decidable statically and a driver round-trip is
    an expensive way to learn it a third time.
    """
    import re

    untyped = re.findall(r"%\(target\)s(?!::)", OWNERSHIP_PLAN_SQL)
    assert not untyped, (
        f"{len(untyped)} bind(s) lack an explicit cast; PostgreSQL cannot infer "
        "a type for a parameter passed to format()'s variadic argument"
    )


def test_no_stray_placeholder_survives() -> None:
    """Exhaustive rather than illustrative: after removing the two legal forms —
    doubled `%%` for PostgreSQL and `%(target)s::text` for psycopg — no `%` may
    remain. Either kind of leftover is a query the driver or the server rejects.
    """
    import re

    residue = re.sub(r"%\(target\)s::text", "", OWNERSHIP_PLAN_SQL.replace("%%", ""))
    assert "%" not in residue, (
        "unescaped '%' remains in the plan SQL: "
        f"{residue[max(0, residue.index('%') - 40) : residue.index('%') + 40]!r}"
    )


def test_every_relkind_maps_to_a_real_alter_form() -> None:
    """`ALTER TABLE` is invalid for a sequence, view, matview or foreign table;
    each needs its own keyword or the cutover fails mid-transaction."""
    for form in ("SEQUENCE", "VIEW", "MATERIALIZED VIEW", "FOREIGN TABLE", "TABLE"):
        assert f"'{form}'" in OWNERSHIP_PLAN_SQL
    for form in ("DOMAIN", "PROCEDURE", "FUNCTION"):
        assert f"'{form}'" in OWNERSHIP_PLAN_SQL


def test_the_plan_targets_the_migration_executor() -> None:
    assert MIGRATION_EXECUTOR == "app_admin"


# ── The script's safety posture ─────────────────────────────────────────────


def _script_source() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_execution_is_opt_in() -> None:
    """Default must be a dry run: the destructive direction needs a flag, and
    the safe direction needs none."""
    tree = ast.parse(_script_source())
    flags = {
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "attr", "") == "add_argument"
        and node.args
        and isinstance(node.args[0], ast.Constant)
    }
    assert "--execute" in flags, "execution must be opt-in, not the default"
    assert "--approve-owner" in flags, "the operator must name whose objects move"


def test_the_cutover_runs_in_one_transaction() -> None:
    """A failure part-way must leave ownership as it was, not half-transferred."""
    assert "autocommit=False" in _script_source()


def test_the_cutover_never_grants() -> None:
    """Ownership and privilege are different. This script must not widen anyone's
    access while repairing who may ALTER."""
    lowered = _script_source().lower()
    for forbidden in ("grant ", "revoke "):
        assert forbidden not in lowered.replace("never grants", "")


def test_it_is_not_wired_into_the_deploy() -> None:
    """The deploy preflight REPORTS this problem; it must never fix it, because
    fixing it needs privileges the deploy path must not hold."""
    deploy = (SCRIPT.parent / "deploy.sh").read_text(encoding="utf-8")
    assert "cutover_database_ownership" not in deploy
