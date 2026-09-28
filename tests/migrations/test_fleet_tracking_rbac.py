import importlib.util
from pathlib import Path
import sys
from types import ModuleType


kernel_cache = ModuleType("dotmac_kernel.cache")
kernel_cache.TenantScope = lambda tenant_id: tenant_id
sys.modules.setdefault("dotmac_kernel", ModuleType("dotmac_kernel"))
sys.modules.setdefault("dotmac_kernel.cache", kernel_cache)

from scripts.seed_rbac import ROLE_PERMISSIONS


MIGRATION_PATH = (
    Path(__file__).parents[2]
    / "alembic"
    / "versions"
    / "20260928_seed_fleet_tracking_rbac.py"
)
spec = importlib.util.spec_from_file_location("fleet_tracking_rbac", MIGRATION_PATH)
assert spec and spec.loader
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


def test_tracking_permissions_and_seed_grants_match():
    defined = {key for key, _description in migration.TRACKING_PERMISSIONS}

    assert defined == {
        "fleet:tracking:read",
        "fleet:tracking:manage",
        "fleet:commands:send",
    }
    assert set(migration.OPERATIONS_MANAGER_GRANTS) <= set(
        ROLE_PERMISSIONS["operations_manager"]
    )
    assert "fleet:commands:send" not in ROLE_PERMISSIONS["operations_manager"]
    assert defined.isdisjoint(ROLE_PERMISSIONS["driver"])
