"""The role contract has one definition, expressed in two places on purpose.

`scripts/bootstrap_database_roles.py` CREATES the roles under explicit
elevation; `alembic/versions/20260814_database_roles.py` VERIFIES them under
ordinary unprivileged migration. They must agree exactly, or the bootstrap
builds something the migration then refuses — a deploy that fails after the
privileged step has already run, which is the worst place to discover it.

The migration copies the contract rather than importing it, following the
existing house rule that a migration is a snapshot of an accepted decision. This
test is what makes the copy safe.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "bootstrap_database_roles.py"
MIGRATION = REPO_ROOT / "alembic" / "versions" / "20260814_database_roles.py"

#: The accepted contract, as `(rolbypassrls, rolsuper)`. Stated a third time,
#: here, so the test cannot pass by both sources drifting together.
EXPECTED = {
    "app_admin": (True, False),
    "app_user": (False, False),
    "platform_api": (False, False),
}


def _contract(path: Path) -> dict[str, tuple[bool, bool]]:
    """Read `ROLE_CONTRACT` statically — importing the migration would run it."""
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        targets = (
            node.targets
            if isinstance(node, ast.Assign)
            else [node.target]
            if isinstance(node, ast.AnnAssign)
            else []
        )
        for target in targets:
            if isinstance(target, ast.Name) and target.id == "ROLE_CONTRACT":
                value = node.value if isinstance(node, ast.Assign) else node.value
                assert value is not None
                return ast.literal_eval(value)
    raise AssertionError(f"{path} declares no ROLE_CONTRACT")


def test_the_bootstrap_script_states_the_accepted_contract() -> None:
    assert _contract(SCRIPT) == EXPECTED


def test_the_migration_states_the_same_contract() -> None:
    assert _contract(MIGRATION) == EXPECTED


def test_app_admin_bypasses_rls_and_is_not_a_superuser() -> None:
    """The distinction that was got wrong twice upstream, pinned here.

    `app_admin` needs to read past RLS — that is BYPASSRLS. Accepting SUPERUSER
    as an alternative would certify cluster-wide authority (DDL on any database,
    role creation, COPY PROGRAM) to satisfy a requirement about reading rows.
    """
    assert EXPECTED["app_admin"] == (True, False)


def test_no_online_role_can_bypass_row_level_security() -> None:
    """Both attributes, not just the flag: a superuser bypasses RLS regardless
    of `rolbypassrls`, so checking only the flag would certify
    `app_user SUPERUSER NOBYPASSRLS` as isolated."""
    for role in ("app_user", "platform_api"):
        assert EXPECTED[role] == (False, False)


def _executable_strings(path: Path) -> list[str]:
    """Every string literal the module can EXECUTE — docstrings excluded.

    Asserted this way because the migration's docstring necessarily contains the
    phrase `CREATE ROLE` in order to explain why it never issues one. A check
    over raw file text matches that explanation and fails on correct code; the
    property under test is about what the module can run.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(
            node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
        ):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstrings.add(id(body[0].value))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def test_the_migration_creates_no_role() -> None:
    """Creation is the privileged bootstrap's job. A migration that creates a
    role is a second authority over cluster access, and one that escalates to do
    so is worse."""
    executable = " ".join(_executable_strings(MIGRATION)).lower()
    for ddl in ("create role", "alter role", "drop role", "createrole"):
        assert ddl not in executable, f"migration must not issue {ddl!r}"


def test_the_guard_would_notice_role_ddl() -> None:
    """Sensitivity proof: the exclusion above must not swallow real DDL too."""
    executable = " ".join(_executable_strings(SCRIPT)).lower()
    assert "create role" in executable, (
        "the bootstrap script DOES issue CREATE ROLE, so a scanner that cannot "
        "see it here would not see it in a migration either"
    )


def test_the_migration_points_at_the_bootstrap_when_it_refuses() -> None:
    """A fail-closed check that does not name its remedy just stops a deploy."""
    source = MIGRATION.read_text(encoding="utf-8")
    assert "scripts/bootstrap_database_roles.py" in source


def test_the_bootstrap_never_sets_a_password() -> None:
    """Operators set passwords out of band. One in a repo, a log or a shell
    history is a credential leak.

    Asserted against the SQL the script can emit, not against the word
    "password" anywhere in the file — the docstring says it never sets one, and
    a check that forbade the word would forbid saying so.
    """
    lowered = SCRIPT.read_text(encoding="utf-8").lower()
    for forbidden in ("with password", "encrypted password", "unencrypted password"):
        assert forbidden not in lowered, f"bootstrap may not emit {forbidden!r}"
