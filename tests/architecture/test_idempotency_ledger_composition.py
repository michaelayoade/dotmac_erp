"""Static contract for ERP's ``idempotency_ledger.v1`` provider slice."""

from __future__ import annotations

import ast
from pathlib import Path

import tomllib

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION = REPO_ROOT / "alembic" / "versions" / "20260820_idempotency_ledger.py"
PYPROJECT = REPO_ROOT / "pyproject.toml"


def _assignment(path: Path, name: str) -> str | tuple[str, ...]:
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        value: ast.expr | None
        if isinstance(node, ast.Assign):
            targets = node.targets
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
            value = node.value
        else:
            continue
        if any(
            isinstance(target, ast.Name) and target.id == name for target in targets
        ):
            assert value is not None
            literal = ast.literal_eval(value)
            assert isinstance(literal, (str, tuple))
            assert isinstance(literal, str) or all(
                isinstance(member, str) for member in literal
            )
            return literal
    raise AssertionError(f"{path.name} declares no literal {name}")


def test_kernel_pin_contains_the_published_prerequisite_contract() -> None:
    pyproject = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    dependency = pyproject["tool"]["poetry"]["dependencies"]["dotmac-kernel"]
    assert dependency == {"version": "0.1.0a94", "source": "forgejo"}


def test_provider_revision_merges_every_preexisting_erp_head() -> None:
    assert _assignment(MIGRATION, "revision") == "20260820_idempotency_ledger"
    down_revision = _assignment(MIGRATION, "down_revision")
    assert isinstance(down_revision, tuple)
    assert set(down_revision) == {
        "20260815_academy_course_projection",
        "20260815_academy_learning_sync",
        "20260816_platform_owned_webhook_ssrf_policy",
        "20260818_dotmac_sub_customer_metrics",
    }


def test_migration_hosts_both_kernel_ledger_planes_and_verifies_them() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    for table in ("idempotency_records", "platform_idempotency_records"):
        assert f'"{table}"' in source
        assert f"ix_{table}_expires_at" in source
    for column in (
        "id",
        "scope",
        "key",
        "fingerprint",
        "operation",
        "status",
        "result",
        "correlation_id",
        "expires_at",
        "created_at",
        "updated_at",
    ):
        assert f'"{column}"' in source
    assert "uq_idempotency_records_tenant_scope_key" in source
    assert "uq_platform_idempotency_records_scope_key" in source
    assert "require_prerequisites(op.get_bind(), REQUIRES)" in source


def test_migration_declares_the_two_plane_isolation_posture() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert "public.idempotency_records ENABLE ROW LEVEL SECURITY" in source
    assert "public.idempotency_records FORCE ROW LEVEL SECURITY" in source
    assert "CREATE POLICY idempotency_records_tenant_isolation" in source
    assert "tenant_id = public.app_current_tenant_id()" in source
    assert "platform_idempotency_records ENABLE ROW LEVEL SECURITY" not in source
    assert "platform_idempotency_records FORCE ROW LEVEL SECURITY" not in source
    assert (
        "REVOKE ALL PRIVILEGES ON TABLE public.platform_idempotency_records" in source
    )
    assert '"FROM app_user"' in source
    assert "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE" in source


def test_provider_never_runs_or_stamps_the_kernel_lineage() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert "stamp(" not in source
    assert "0001_initial_tenant_schema" not in source
    assert "dotmac_kernel.migrations.versions" not in source
