"""E8's Organization-to-Tenant adapter stays singular and persistence-free."""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = PROJECT_ROOT / "app"
MAPPING = APP_ROOT / "tenancy.py"
CANONICAL_GUC_WRITER = APP_ROOT / "rls.py"

GUC_WRITE_MARKERS = (
    "set_config('app.current_tenant'",
    'set_config("app.current_tenant"',
    "SET LOCAL app.current_tenant",
    "SET app.current_tenant",
)


def _tenant_guc_writers(root: Path) -> list[Path]:
    writers: list[Path] = []
    for path in sorted(root.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        if any(marker in source for marker in GUC_WRITE_MARKERS):
            writers.append(path)
    return writers


def test_mapping_adapter_has_no_database_dependency() -> None:
    source = MAPPING.read_text(encoding="utf-8")
    assert "sqlalchemy" not in source
    assert "Session" not in source
    assert "OrganizationTenantContext" in source


def test_app_rls_is_the_only_module_scope_guc_writer() -> None:
    assert _tenant_guc_writers(APP_ROOT) == [CANONICAL_GUC_WRITER]


def test_guc_writer_guard_is_sensitive(tmp_path: Path) -> None:
    offender = tmp_path / "bad.py"
    offender.write_text(
        "db.execute(\"SET LOCAL app.current_tenant = 'bad'\")\n",
        encoding="utf-8",
    )
    assert _tenant_guc_writers(tmp_path) == [offender]
