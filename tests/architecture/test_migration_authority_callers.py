"""One scanner, one evaluator, two callers — proved statically.

The deploy preflight (`scripts/bootstrap_database_roles.py`) and the thing that
actually migrates (`alembic/env.py`) both decide whether a connection holds the
authority to alter this database. A second, re-spelled copy of either the query
or the policy is how the two answers drift, and drift here is silent: both
callers keep printing green.

Static, by AST and by source read, because importing `alembic/env.py` executes
a migration environment.
"""

from __future__ import annotations

import ast
from pathlib import Path

from app.migration_authority import (
    AUTHORITY_SUBJECTS,
    ROLE_AUTHORITY_SQL,
    SERVER_FILE_OR_PROGRAM_ROLES,
    MigrationExecutorAuthorityPolicyV1,
    RuntimeRoleAuthorityPolicyV1,
)
from app.migration_database_roles import MIGRATION_EXECUTOR, ROLE_CONTRACT

REPO_ROOT = Path(__file__).resolve().parents[2]
AUTHORITY = REPO_ROOT / "app" / "migration_authority.py"
CONTRACT = REPO_ROOT / "app" / "migration_database_roles.py"
MIGRATION = REPO_ROOT / "alembic" / "versions" / "20260814_database_roles.py"
ALEMBIC_ENV = REPO_ROOT / "alembic" / "env.py"
BOOTSTRAP = REPO_ROOT / "scripts" / "bootstrap_database_roles.py"

CALLERS = (ALEMBIC_ENV, BOOTSTRAP)


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _imported_names(path: Path, module: str) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(_source(path))):
        if isinstance(node, ast.ImportFrom) and node.module == module:
            names.update(alias.name for alias in node.names)
    return names


def test_both_callers_execute_the_declared_scanner_and_never_a_copy() -> None:
    """Neither caller may contain SQL of its own for this question.

    `ROLE_AUTHORITY_SQL` is imported by name and passed through; a caller that
    inlined `pg_auth_members` would be running a query nobody else reviews.
    """
    for path in CALLERS:
        source = _source(path)
        assert "ROLE_AUTHORITY_SQL" in _imported_names(path, "app.migration_authority")
        assert "pg_auth_members" not in source, (
            f"{path.name} spells its own role-graph query; there is one scanner "
            "and it lives in app/migration_authority.py"
        )


def test_both_callers_pass_the_same_derived_subject_set() -> None:
    """The subjects are DERIVED, never listed.

    A caller that hand-listed four role names would stop covering the fifth the
    day a policy grows one, and would keep passing while it did.
    """
    for path in CALLERS:
        source = _source(path)
        assert 'ROLE_AUTHORITY_SQL, {"subjects": sorted(AUTHORITY_SUBJECTS)}' in source
        for role in sorted(AUTHORITY_SUBJECTS):
            assert f'"{role}"' not in source, (
                f"{path.name} names the subject {role!r} literally; the subject "
                "set must come from the policy declarations"
            )


def test_alembic_hands_the_query_to_the_driver_unchanged() -> None:
    """`exec_driver_sql`, never `text()`, and this is load-bearing.

    `ROLE_AUTHORITY_SQL` is psycopg pyformat (`%(subjects)s`). SQLAlchemy's
    `text()` applies its own `:name` paramstyle and would leave `%(subjects)s`
    a literal — the statement would fail, or worse, a future edit would
    "fix" it by re-spelling the query and the two callers would stop running
    the same bytes.
    """
    source = _source(ALEMBIC_ENV)
    assert "exec_driver_sql(\n        ROLE_AUTHORITY_SQL" in source
    assert "text(ROLE_AUTHORITY_SQL" not in source
    assert "%(subjects)s" in ROLE_AUTHORITY_SQL


def test_both_callers_evaluate_both_policies() -> None:
    """A caller that checked only runtime roles would leave `app_admin`
    unmonitored, which is the state this work exists to end."""
    for path in CALLERS:
        imported = _imported_names(path, "app.migration_authority")
        assert "RuntimeRoleAuthorityPolicyV1" in imported, path.name
        assert "MigrationExecutorAuthorityPolicyV1" in imported, path.name
        assert "role_authority_violations" in imported, path.name


def test_no_caller_declares_a_policy_of_its_own() -> None:
    """Policy lives in one module. A caller may bind an operator input to a
    policy; it may not decide what an authority class is permitted to be."""
    for path in CALLERS:
        source = _source(path)
        for forbidden in (
            "AuthorityPolicyV1(",
            "forbidden_target_attributes",
            "required_direct_attributes",
        ):
            assert forbidden not in source, (
                f"{path.name} constructs or edits an authority policy; policies "
                "are declared once in app/migration_authority.py"
            )


#: Role DDL, in the spelling it is actually written. A migration environment
#: that can issue any of these can grant itself the authority it was refused.
REPAIR_STATEMENTS = ("REVOKE ", "GRANT ", "ALTER ROLE ", "CREATE ROLE ")


def _repair_statements_in(source: str) -> list[str]:
    return [statement for statement in REPAIR_STATEMENTS if statement in source]


def test_alembic_never_repairs_the_role_graph() -> None:
    """A migration that can grant itself authority has no authority constraint.

    Repair is a separately authorised bootstrap action — normally a `REVOKE` or
    an `ALTER ROLE ... NOCREATEROLE` run by an operator who chose to elevate.
    Those statements belong in the bootstrap script by design; here they must
    not appear at all.
    """
    assert _repair_statements_in(_source(ALEMBIC_ENV)) == []


def test_the_no_repair_guard_still_bites() -> None:
    """SENSITIVITY. A guard that has only ever seen a clean file proves nothing
    about itself: it would pass identically if the check were `assert True`.

    Planted defect: the environment revokes the offending grant instead of
    refusing. Near-miss: prose that merely discusses revoking, which must not
    be reported, or the guard becomes something reviewers route around by
    rewording comments.
    """
    planted = _source(ALEMBIC_ENV).replace(
        "    if violations:",
        "    for edge in ():\n"
        '        connection.exec_driver_sql("REVOKE x FROM y")\n'
        "    if violations:",
        1,
    )
    assert _repair_statements_in(planted) == ["REVOKE "]

    near_miss = _source(ALEMBIC_ENV).replace(
        "    if violations:",
        "    # An operator revokes the grant; this file never does.\n    if violations:",
        1,
    )
    assert _repair_statements_in(near_miss) == []


def test_the_server_file_and_program_roles_are_named_in_both_policies() -> None:
    """PostgreSQL documents these as reaching server files or running programs.

    They hold none of SUPERUSER/CREATEROLE/BYPASSRLS, so nothing derives them —
    they have to be listed, and both authority classes have to list them.
    """
    assert set(SERVER_FILE_OR_PROGRAM_ROLES) == {
        "pg_read_server_files",
        "pg_write_server_files",
        "pg_execute_server_program",
    }
    assert RuntimeRoleAuthorityPolicyV1.forbidden_target_roles == (
        SERVER_FILE_OR_PROGRAM_ROLES
    )
    assert MigrationExecutorAuthorityPolicyV1.forbidden_target_roles == (
        SERVER_FILE_OR_PROGRAM_ROLES
    )


def _membership_branch(sql: str) -> str:
    """The scanner's final branch — everything after the membership marker."""
    marker = "'membership'::text"
    assert marker in sql, "the scanner no longer emits a membership branch"
    return sql.split(marker, 1)[1]


#: The last join of the membership branch. Everything after it is the branch's
#: OWN qualification — which must be nothing but the ordering.
MEMBERSHIP_JOIN = (
    "JOIN pg_roles AS target_role ON target_role.oid = reachable.target_oid"
)


def _filters_membership_targets(sql: str) -> bool:
    """Whether the membership branch restricts WHICH targets it reports.

    Deliberately NOT "does the branch contain the word WHERE". It legitimately
    contains two: the recursive walk's subject selection, and the correlated
    `EXISTS` that marks an edge direct. Only a qualification applied AFTER the
    join to the target role can drop an edge, and that is the one that caused
    the gap this scanner exists to close.
    """
    branch = _membership_branch(sql)
    assert MEMBERSHIP_JOIN in branch, "the membership branch no longer joins targets"
    return "WHERE" in branch.split(MEMBERSHIP_JOIN, 1)[1]


def test_the_scanner_filters_no_membership_target() -> None:
    """The gap that made naming those three roles insufficient.

    The superseded query ended `WHERE target_role.rolsuper OR
    target_role.rolcreaterole OR target_role.rolbypassrls`, which discarded
    every edge into a role holding none of them — the exact shape of
    `pg_execute_server_program`. Filtering is the POLICY's job, so the walk
    must hand over every reachable membership.
    """
    assert "rolsuper" in _membership_branch(ROLE_AUTHORITY_SQL), (
        "the branch must still report the target's attributes"
    )
    assert not _filters_membership_targets(ROLE_AUTHORITY_SQL), (
        "the membership branch filters targets again; a scanner that pre-filters "
        "by one policy's shape cannot be given a second policy"
    )


def test_the_unfiltered_scanner_guard_still_bites() -> None:
    """SENSITIVITY. Planted defect: the exact `WHERE` clause that caused the
    gap, reinstated. Near-miss: a `WHERE` in an EARLIER branch, which is where
    the subject set is legitimately selected and must not be reported.
    """
    planted = ROLE_AUTHORITY_SQL.replace(
        "ORDER BY 1, 2, 3",
        "WHERE target_role.rolsuper\n   OR target_role.rolcreaterole\n"
        "   OR target_role.rolbypassrls\nORDER BY 1, 2, 3",
        1,
    )
    assert _filters_membership_targets(planted)

    assert not _filters_membership_targets(ROLE_AUTHORITY_SQL), (
        "the two WHERE clauses the branch legitimately carries — the subject "
        "selection and the correlated EXISTS that marks an edge direct — must "
        "not be reported, or the guard is just a word search"
    )


def test_the_two_policies_are_not_one_policy_wearing_two_names() -> None:
    """The asymmetry, asserted rather than described.

    If these two ever agreed on the forbidden closure, the rejected shortcut —
    adding `app_admin` to the runtime subject set — would have been reinstated
    under a different spelling.
    """
    assert "bypassrls" in RuntimeRoleAuthorityPolicyV1.forbidden_target_attributes, (
        "a runtime role reaching BYPASSRLS is one SET ROLE from bypassing RLS"
    )
    assert (
        "bypassrls"
        not in MigrationExecutorAuthorityPolicyV1.forbidden_target_attributes
    ), "app_admin holds BYPASSRLS by contract; reaching it again gains nothing"
    assert MigrationExecutorAuthorityPolicyV1.required_direct_attributes["bypassrls"]
    assert not RuntimeRoleAuthorityPolicyV1.required_direct_attributes["bypassrls"]
    assert RuntimeRoleAuthorityPolicyV1.subjects.isdisjoint(
        MigrationExecutorAuthorityPolicyV1.subjects
    )


def test_the_executor_policy_agrees_with_the_frozen_role_contract() -> None:
    """Two owners, one cluster: they must not contradict each other.

    `ROLE_CONTRACT` is the applied `20260814` revision's snapshot and stays
    frozen. The new policy adds attributes it never asked about; it may not
    reverse one it did.
    """
    assert MigrationExecutorAuthorityPolicyV1.subjects == {MIGRATION_EXECUTOR}
    bypassrls, superuser = ROLE_CONTRACT[MIGRATION_EXECUTOR]
    required = MigrationExecutorAuthorityPolicyV1.required_direct_attributes
    assert required["bypassrls"] == bypassrls
    assert required["superuser"] == superuser
    for role in sorted(RuntimeRoleAuthorityPolicyV1.subjects & set(ROLE_CONTRACT)):
        contract_bypassrls, contract_superuser = ROLE_CONTRACT[role]
        runtime = RuntimeRoleAuthorityPolicyV1.required_direct_attributes
        assert runtime["bypassrls"] == contract_bypassrls
        assert runtime["superuser"] == contract_superuser


def test_the_frozen_contract_and_its_migration_snapshot_are_untouched() -> None:
    """This work adds a module; it does not rewrite migration semantics.

    `ROLE_CONTRACT` remains a two-attribute `(rolbypassrls, rolsuper)` mapping
    in both places. Widening it would change what an already-applied revision
    asserted in every existing database.
    """
    for path in (CONTRACT, MIGRATION):
        source = _source(path)
        assert "rolcreaterole" not in source, (
            f"{path.name} grew a third attribute; the frozen contract is "
            "(rolbypassrls, rolsuper) and the new attributes belong to "
            "app/migration_authority.py"
        )
    assert all(len(posture) == 2 for posture in ROLE_CONTRACT.values())


# ---------------------------------------------------------------------------
# The authentication tier, pinned to a cluster that really authenticates.
# ---------------------------------------------------------------------------
CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"
AUTHENTICATION_MARKER = "authentication"


def _integration_pytest_steps(workflow: dict) -> list[tuple[str, str, str]]:
    """`(job, run, auth method)` for every step that collects the lane.

    The authentication method comes from the job's OWN PostgreSQL service, so
    the pairing is read off the thing that actually runs rather than off a job
    name that anyone can keep while changing what it does.
    """
    found: list[tuple[str, str, str]] = []
    for job_name, job in workflow.get("jobs", {}).items():
        service = job.get("services", {}).get("postgres", {})
        method = str(service.get("env", {}).get("POSTGRES_HOST_AUTH_METHOD", ""))
        for step in job.get("steps", []):
            run = str(step.get("run", ""))
            if "pytest tests/integration/" in run:
                found.append((job_name, run, method))
    return found


def _selects_marker(run: str) -> bool:
    return f"-m {AUTHENTICATION_MARKER}" in run


def _deselects_marker(run: str) -> bool:
    return f'-m "not {AUTHENTICATION_MARKER}"' in run


def _unqualified_collections(steps: list[tuple[str, str, str]]) -> list[str]:
    return [
        job
        for job, run, _ in steps
        if not _selects_marker(run) and not _deselects_marker(run)
    ]


def _trust_selections(steps: list[tuple[str, str, str]]) -> list[str]:
    return [
        job for job, run, method in steps if _selects_marker(run) and method == "trust"
    ]


def test_the_authentication_proof_runs_on_a_cluster_that_authenticates() -> None:
    """`system_user` is NULL under `trust`, so a trust tier proves nothing.

    Three things are asserted together, because any one alone is satisfiable
    while the proof is dead:

    * some tier SELECTS the marker — or nothing runs the proof at all;
    * that tier is not `trust` — or it runs it against a cluster that
      authenticated nothing and reports green;
    * every other tier collecting the lane DESELECTS it — or the proof fails
      there for a reason that has nothing to do with the executor, and gets
      deleted rather than fixed.
    """
    import yaml

    steps = _integration_pytest_steps(yaml.safe_load(CI.read_text(encoding="utf-8")))
    assert steps, "nothing in CI collects tests/integration/"

    selecting = [job for job, run, _ in steps if _selects_marker(run)]
    assert selecting, (
        "no CI tier selects the authentication marker; the direct "
        "authentication proof is not being run anywhere"
    )
    assert _trust_selections(steps) == [], (
        "a tier runs the authentication proof against POSTGRES_HOST_AUTH_METHOD="
        "trust, where system_user is NULL — that green is computed under "
        "conditions production never sees"
    )
    assert _unqualified_collections(steps) == [], (
        "a tier collects tests/integration/ without saying anything about the "
        "authentication marker; it will run the proof against whatever cluster "
        "it happens to have"
    )


def test_the_authentication_tier_guard_still_bites() -> None:
    """SENSITIVITY, both failure modes.

    Planted defect 1: the trust lane stops deselecting, so it runs the proof
    against a cluster with no authentication. Planted defect 2: the dedicated
    tier is switched to `trust`, which is the exact regression that makes the
    whole proof vacuous while every job stays green. Near-miss: a timeout
    change, which must be reported by neither.
    """
    import yaml

    source = CI.read_text(encoding="utf-8")

    # Anchored on `-o`, which follows the filter in the COMMAND. The same
    # words appear in the comment above the step, and replacing the first
    # textual occurrence would tamper with prose and prove nothing — this
    # proof caught exactly that mistake in its own first draft.
    dropped = source.replace(f'-m "not {AUTHENTICATION_MARKER}" -o', "-o", 1)
    assert dropped != source, "the deselection moved; update this proof"
    assert _unqualified_collections(
        _integration_pytest_steps(yaml.safe_load(dropped))
    ), "removing the deselection was not reported"

    downgraded = source.replace(
        '          POSTGRES_INITDB_ARGS: "--auth-host=scram-sha-256"\n'
        "          POSTGRES_HOST_AUTH_METHOD: scram-sha-256\n",
        "          POSTGRES_HOST_AUTH_METHOD: trust\n",
        1,
    )
    assert downgraded != source, "the scram service moved; update this proof"
    assert _trust_selections(_integration_pytest_steps(yaml.safe_load(downgraded))), (
        "downgrading the authentication tier to trust was not reported"
    )

    near_miss = source.replace("--timeout=300", "--timeout=600", 1)
    assert near_miss != source, "the timeout moved; update this proof"
    near_steps = _integration_pytest_steps(yaml.safe_load(near_miss))
    assert _unqualified_collections(near_steps) == []
    assert _trust_selections(near_steps) == []


# ---------------------------------------------------------------------------
# Every import of a migration-contract name resolves to something that exists.
# ---------------------------------------------------------------------------
#: The prefix the migration-authority programme owns. Derived, not listed: a
#: future `app/migration_<next>.py` is covered the day it is added.
CONTRACT_MODULE_PREFIX = "app.migration_"


def _contract_imports() -> list[tuple[Path, int, str, str]]:
    """Every `from app.migration_* import NAME` in the tree."""
    found: list[tuple[Path, int, str, str]] = []
    for path in sorted(REPO_ROOT.rglob("*.py")):
        if any(part in {".venv", "node_modules", ".git"} for part in path.parts):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - a broken file fails elsewhere
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.level == 0
                and node.module
                and node.module.startswith(CONTRACT_MODULE_PREFIX)
            ):
                found.extend(
                    (path, node.lineno, node.module, alias.name)
                    for alias in node.names
                    if alias.name != "*"
                )
    return found


def _unresolved(surfaces: dict[str, set[str]]) -> list[str]:
    return [
        f"{path.relative_to(REPO_ROOT)}:{line} imports {module}.{name}, which "
        f"{module} does not declare"
        for path, line, module, name in _contract_imports()
        if module in surfaces
        and name not in surfaces[module]
        and path.resolve() != _module_path(module).resolve()
    ]


def _module_path(module: str) -> Path:
    return REPO_ROOT / (module.replace(".", "/") + ".py")


def _declared_surfaces() -> dict[str, set[str]]:
    """Each owned module's `__all__`, read STATICALLY.

    Not by importing. `app/migration_bindings.py` imports the kernel, so an
    import-based sweep answers differently depending on which kernel version
    happens to be installed — and a guard whose verdict depends on the
    environment is one that gets believed on the machine where it is green.
    `__all__` is a declaration; reading it needs nothing but the file.
    """
    surfaces: dict[str, set[str]] = {}
    for _, _, module, _ in _contract_imports():
        if module in surfaces:
            continue
        path = _module_path(module)
        if not path.exists():
            continue
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            targets = (
                node.targets
                if isinstance(node, ast.Assign)
                else [node.target]
                if isinstance(node, ast.AnnAssign)
                else []
            )
            for target in targets:
                if (
                    isinstance(target, ast.Name)
                    and target.id == "__all__"
                    and node.value is not None
                ):
                    surfaces[module] = set(ast.literal_eval(node.value))
        assert module in surfaces, (
            f"{module} is in the owned prefix and declares no __all__; the "
            "sweep cannot tell a public name from a private one there"
        )
    return surfaces


def test_every_migration_contract_import_names_something_that_exists() -> None:
    """A removed public name breaks somewhere ELSE, and that is the problem.

    Deleting `ROLE_ESCALATION_SQL` and friends passed every check in the module
    that owned them, and every check in the two callers that were updated with
    them. It failed in three unrelated test modules, on CI, in a job that takes
    fifteen minutes to tell you. Nothing in the change itself could have caught
    it — which is exactly why the guard has to sweep the tree rather than the
    diff.

    Scoped to `app.migration_*` and not to every `app.*` import: a repo-wide
    rule has 183 pre-existing hits from packages that re-export dynamically, and
    a check that starts red is a check that gets an allowlist and then gets
    deleted. This prefix has none, and it grows with the programme.
    """
    assert _contract_imports(), "the sweep found nothing to check"
    assert _unresolved(_declared_surfaces()) == []


def test_the_contract_import_sweep_still_bites() -> None:
    """SENSITIVITY. Planted defect: a name removed from a module's namespace,
    which is precisely what a refactor does. Near-miss: a name that is still
    there, and an import of a module OUTSIDE the owned prefix, neither of which
    may be reported."""
    surfaces = _declared_surfaces()
    assert "app.migration_authority" in surfaces

    planted = {
        module: (names - {"ROLE_AUTHORITY_SQL"})
        if module == "app.migration_authority"
        else names
        for module, names in surfaces.items()
    }
    reported = _unresolved(planted)
    assert reported, "removing a name that two callers import was not reported"
    assert all("ROLE_AUTHORITY_SQL" in line for line in reported)

    assert _unresolved(surfaces) == [], (
        "the unplanted sweep must stay quiet, or the proof above is about a "
        "detector that fires on everything"
    )
    assert not any(
        module.startswith("app.services") or module.startswith("app.api")
        for _, _, module, _ in _contract_imports()
    ), "the sweep widened past the prefix it can keep green"
