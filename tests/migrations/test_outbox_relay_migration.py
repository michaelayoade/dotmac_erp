"""Static contract for ERP's ``outbox_relay.v1`` provider migration."""

from __future__ import annotations

import ast
from pathlib import Path

from app.migration_database_roles import RELAY_DISPATCHER_CONTRACT

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIGRATION = PROJECT_ROOT / "alembic" / "versions" / "20260824_outbox_relay.py"


def _executed_sql() -> str:
    """Every literal string this migration hands to `op.execute`.

    Prose and SQL both mention CREATE ROLE and platform.event_outbox, for
    opposite reasons — the docstring explains what the migration must NOT do.
    Searching the whole file would make the explanation fail the check that the
    explanation exists to describe, so the assertions below read the statements
    the migration actually runs.
    """
    tree = ast.parse(MIGRATION.read_text(encoding="utf-8"))
    statements: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        if not (isinstance(function, ast.Attribute) and function.attr == "execute"):
            continue
        for argument in node.args:
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                statements.append(argument.value)
            elif isinstance(argument, ast.JoinedStr):
                statements.append(ast.unparse(argument))
    return "\n".join(statements)


def _assignment(name: str) -> ast.expr:
    tree = ast.parse(MIGRATION.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name
            for target in node.targets
        ):
            return node.value
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == name
            and node.value is not None
        ):
            return node.value
    raise AssertionError(f"{name} is not assigned in {MIGRATION.name}")


def test_migration_follows_the_party_projection_and_supplies_the_effect() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert 'revision = "20260824_outbox_relay"' in source
    assert 'down_revision = "20260824_party_person_projection"' in source
    assert 'REQUIRES = ("outbox_relay.v1",)' in source
    assert "require_prerequisites(op.get_bind(), REQUIRES)" in source


def test_the_dispatcher_contract_copy_matches_the_runtime_one() -> None:
    """A migration keeps a point-in-time copy; the copy must be the same one."""
    names = {
        target.id: node.value.value
        for node in ast.parse(MIGRATION.read_text(encoding="utf-8")).body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    }
    contract = _assignment("DISPATCHER_CONTRACT")
    assert isinstance(contract, ast.Dict)
    copied = {
        names[key.id]
        if isinstance(key, ast.Name)
        else ast.literal_eval(key): ast.literal_eval(value)
        for key, value in zip(contract.keys, contract.values, strict=True)
        if key is not None
    }
    assert copied == {
        "outbox_dispatcher": (False, False),
        "platform_outbox_dispatcher": (False, False),
    }
    assert copied == dict(RELAY_DISPATCHER_CONTRACT)


def test_the_migration_verifies_roles_and_never_creates_one() -> None:
    """ERP's convention, deliberately unlike the kernel's own 0011/0012."""
    assert "CREATE ROLE" not in _executed_sql()
    source = MIGRATION.read_text(encoding="utf-8")
    assert "_assert_dispatcher_roles_exist" in source
    assert "a migration must not create a role" in source


def test_the_create_role_detector_reads_the_statements() -> None:
    """Sensitivity: the prose explains CREATE ROLE, so the file always has it."""
    assert "CREATE ROLE" in MIGRATION.read_text(encoding="utf-8")
    assert "CREATE ROLE" not in _executed_sql()
    assert "CREATE OR REPLACE FUNCTION" in _executed_sql()


def test_the_migration_imports_no_mutable_runtime_code() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert "from app." not in source
    assert "import app." not in source


def test_every_definer_function_pins_an_empty_search_path() -> None:
    """An unpinned path on a SECURITY DEFINER body is privilege escalation."""
    sql = _executed_sql()
    assert sql.count("CREATE OR REPLACE FUNCTION") == 4
    assert sql.count("SECURITY DEFINER") == 4
    assert sql.count("SET search_path = ''") == 4


def test_execute_is_revoked_from_public_before_it_is_granted() -> None:
    """CREATE FUNCTION grants EXECUTE to PUBLIC; the order is the whole point."""
    source = MIGRATION.read_text(encoding="utf-8")
    revoke = source.index("REVOKE ALL ON FUNCTION")
    grant = source.index("GRANT EXECUTE ON FUNCTION")
    assert revoke < grant


def test_every_function_is_owned_by_the_migrator_not_the_superuser() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert source.count("OWNER TO app_admin") == 1
    assert "ALTER FUNCTION {signature} OWNER TO app_admin" in source


def test_the_tenant_plane_is_forced_and_keyed_and_the_platform_plane_is_not() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert "ALTER TABLE public.outbox_events ENABLE ROW LEVEL SECURITY" in source
    assert "ALTER TABLE public.outbox_events FORCE ROW LEVEL SECURITY" in source
    assert "tenant_id = public.app_current_tenant_id()" in source
    assert "public.platform_outbox_events ENABLE ROW LEVEL SECURITY" not in source
    assert (
        "REVOKE ALL PRIVILEGES ON TABLE public.platform_outbox_events FROM app_user"
        in source
    )


def test_the_platform_plane_revokes_column_privileges_too() -> None:
    """has_table_privilege cannot see a column grant, and the verifier checks both."""
    source = MIGRATION.read_text(encoding="utf-8")
    assert 'for privilege in ("SELECT", "INSERT", "UPDATE", "REFERENCES"):' in source
    columns = ast.literal_eval(_assignment("_RELAY_COLUMNS"))
    assert set(columns) == {
        "id",
        "event_type",
        "payload",
        "status",
        "attempts",
        "available_at",
        "correlation_id",
        "sent_at",
        "last_error",
        "leased_by",
        "leased_at",
        "created_at",
        "updated_at",
    }


def test_both_claim_path_indexes_exist_on_both_planes() -> None:
    """Missing either arm turns a claim into a full scan holding FOR UPDATE."""
    source = MIGRATION.read_text(encoding="utf-8")
    for table in ("outbox_events", "platform_outbox_events"):
        assert f'"ix_{table}_status_available_at"' in source
        assert f'"ix_{table}_status_leased_at"' in source


def test_the_relay_does_not_touch_erps_business_outbox() -> None:
    """platform.event_outbox keeps ERP's event authority; this is module traffic."""
    sql = _executed_sql()
    assert "event_outbox" not in sql.replace("platform_outbox_events", "")
    assert "platform." not in sql.replace("platform_outbox_events", "").replace(
        "platform_api", ""
    ).replace("platform_outbox_dispatcher", "")
    assert "This is NOT ERP's business-event outbox" in MIGRATION.read_text(
        encoding="utf-8"
    )


def test_the_binding_names_this_revision() -> None:
    from app.migration_bindings import ASSEMBLY_PREREQUISITE_BINDINGS

    bindings = {
        binding.prerequisite: binding.provider_revision
        for binding in ASSEMBLY_PREREQUISITE_BINDINGS
    }
    assert bindings["outbox_relay.v1"] == "20260824_outbox_relay"
