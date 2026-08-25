"""The retired CRM application has no executable ERP runtime.

Sub's independently-owned material-support and AP-status adapters stay mounted,
but no CRM alias, credential, checkpoint, retry loop, route, or DTO survives.
Historical rows are sealed by the retirement migration rather than kept live.
"""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

RETIRED_RUNTIME_PATHS = (
    "app/api/crm.py",
    "app/api/sync/dotmac_crm.py",
    "app/models/sync/dotmac_crm_sync.py",
    "app/schemas/sync/dotmac_crm.py",
    "app/services/admin/crm_sync_web.py",
    "app/services/crm",
    "app/services/sync/crm",
    "app/services/sync/crm_mappings.py",
    "app/services/sync/dotmac_crm_sync_service.py",
    "app/services/sync/inventory_push_service.py",
    "app/tasks/crm.py",
    "app/web/admin_crm_sync.py",
    "docs/crm_integration.md",
    "templates/admin/sync/crm",
)


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_direct_crm_runtime_files_are_deleted() -> None:
    present = [path for path in RETIRED_RUNTIME_PATHS if (ROOT / path).exists()]
    assert present == []


def test_the_assembly_no_longer_composes_a_crm_connector_runtime() -> None:
    source = _source("app/main.py")
    for forbidden in (
        "app.api.crm",
        "app.api.sync.dotmac_crm",
        "app.web.admin_crm_sync",
        '"crm"',
        "crm_router",
        "crm_webhook_router",
        "crm_sync_router",
        "admin_crm_sync_router",
    ):
        assert forbidden not in source

    # The Sub domain adapter no longer depends on the retired CRM module flag.
    assert "_include_api_router(sub_sync_router)" in source


def test_sync_package_initializers_do_not_reexport_the_retired_runtime() -> None:
    for path in (
        "app/api/sync/__init__.py",
        "app/services/sync/__init__.py",
    ):
        assert "crm" not in _source(path).lower()


def test_no_crm_service_principal_scope_remains() -> None:
    offenders: list[str] = []
    for path in sorted((ROOT / "app").rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        relative = path.relative_to(ROOT).as_posix()
        for marker in ('"crm:', "'crm:"):
            if marker in source:
                offenders.append(f"{relative}: {marker}")
    assert offenders == [], "CRM service scopes remain:\n  " + "\n  ".join(offenders)


def test_crm_credentials_health_and_retry_configuration_are_absent() -> None:
    config = _source("app/config.py")
    health = _source("app/dependency_health.py")

    for name in (
        "crm_api_url",
        "crm_api_token",
        "crm_api_key",
        "crm_webhook_secret",
        "crm_sync_interval_minutes",
        "crm_request_timeout",
        "crm_max_retries",
        "crm_inventory_webhook_url",
        "crm_inventory_push_threshold_percent",
    ):
        assert name not in config
    for env_name in (
        "CRM_API_URL",
        "CRM_API_TOKEN",
        "CRM_API_KEY",
        "CRM_WEBHOOK_SECRET",
        "CRM_SYNC_INTERVAL_MINUTES",
        "CRM_REQUEST_TIMEOUT",
        "CRM_MAX_RETRIES",
        "CRM_INVENTORY_WEBHOOK_URL",
        "CRM_INVENTORY_PUSH_THRESHOLD_PERCENT",
    ):
        assert env_name not in config
    assert "app.services.crm" not in health
    assert "_check_crm" not in health


def test_sub_routes_delegate_only_to_source_neutral_ports() -> None:
    source = _source("app/api/sync/dotmac_sub.py")
    for forbidden in ("crm", "CRM"):
        assert forbidden not in source
    assert "MaterialSupportService" in source
    assert "get_purchase_invoice_status" in source


def test_no_crm_runtime_identity_is_hidden_elsewhere_in_app() -> None:
    forbidden = (
        "app.api.crm",
        "app.api.sync.dotmac_crm",
        "app.services.crm",
        "app.services.sync.crm",
        "dotmac_crm_sync",
        "DotMacCRM",
        "CRMSync",
        "CRMEntity",
        "crm_id",
        "crm_status",
        "crm_data",
    )
    offenders: list[str] = []
    for path in sorted((ROOT / "app").rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        relative = path.relative_to(ROOT).as_posix()
        offenders.extend(
            f"{relative}: {token}" for token in forbidden if token in source
        )
    assert offenders == [], "CRM runtime identity remains:\n  " + "\n  ".join(offenders)


def test_live_domain_models_have_no_crm_identity_columns() -> None:
    for path in (
        "app/models/inventory/material_request.py",
        "app/models/expense/expense_claim.py",
        "app/models/finance/ar/customer.py",
    ):
        source = _source(path)
        assert "crm_id" not in source


def test_retirement_migration_seals_credentials_and_historical_mappings() -> None:
    migration = _source("alembic/versions/20260825_retire_dotmac_crm.py")
    for proof in (
        "DOTMAC_CRM",
        "dotmac-crm-service-%",
        "api_key = NULL",
        "api_secret = NULL",
        "retired_crm_records",
        "REVOKE ALL PRIVILEGES",
        "source_system = 'crm'",
    ):
        assert proof in migration
    assert "key_hash" not in migration
