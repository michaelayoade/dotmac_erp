"""The retired CRM application has no executable ERP runtime.

Sub's independently-owned material-support and AP-status adapters stay mounted,
but no CRM alias, credential, checkpoint, retry loop, route, or DTO survives.
Historical rows are sealed by the retirement migration rather than kept live.
"""

from __future__ import annotations

import ast
from pathlib import Path

from app.schemas.sync.sub_operational import (
    BulkSyncResponse,
    SubExpenseClaimPayload,
    SubMaterialRequestPayload,
    SubPurchaseInvoiceResponse,
    SubWorkOrderPayload,
    SyncError,
)
from app.models.finance.ar.external_sync import ExternalSource, ExternalSync


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
    "templates/admin/sync/crm",
)

RETIRED_LIVE_ALIASES = frozenset(
    {
        "customer_crm_id",
        "project_crm_id",
        "ticket_crm_id",
        "crm_id",
        "crm_invoice_id",
        "omni_id",
        "omni_work_order_id",
        "omni_quote_id",
        "omni_project_id",
    }
)

SUB_CONTRACT_PATHS = (
    "app/api/sync/dotmac_sub.py",
    "app/schemas/sync/sub_operational.py",
    "app/services/inventory/material_support.py",
    "app/services/sync/dotmac_sub_sync_service.py",
    "app/services/sync/sub/base.py",
    "app/services/sync/sub/expenses.py",
    "app/services/sync/sub/inventory.py",
    "app/services/sync/sub/procurement.py",
    "app/services/sync/sub/projects.py",
    "app/services/sync/sub_mappings.py",
)


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _retired_alias_hits(source: str) -> set[str]:
    """Find retired identifiers in declarations, aliases, paths and calls."""
    hits: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        candidates: list[str] = []
        if isinstance(node, ast.Name):
            candidates.append(node.id)
        elif isinstance(node, ast.Attribute):
            candidates.append(node.attr)
        elif isinstance(node, ast.arg):
            candidates.append(node.arg)
        elif isinstance(node, ast.keyword) and node.arg is not None:
            candidates.append(node.arg)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            candidates.append(node.value)
        for candidate in candidates:
            hits.update(alias for alias in RETIRED_LIVE_ALIASES if alias in candidate)
    return hits


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
    assert "app.services.crm" not in health
    assert "_check_crm" not in health


def test_sub_routes_delegate_only_to_source_neutral_ports() -> None:
    source = _source("app/api/sync/dotmac_sub.py")
    for forbidden in (
        "app.api.sync.dotmac_crm",
        "app.services.sync.crm",
        '"crm:',
        "'crm:",
    ):
        assert forbidden not in source
    assert "MaterialSupportService" in source
    assert "get_purchase_invoice_status" in source
    assert source.count("@router.") == 17
    assert '"/purchase-orders/variations"' not in source


def test_sub_wire_contract_uses_only_source_neutral_references() -> None:
    work_order = SubWorkOrderPayload.model_validate(
        {
            "source_id": "work-1",
            "title": "Install",
            "project_source_reference": "project-1",
            "ticket_source_reference": "ticket-1",
        }
    )
    material = SubMaterialRequestPayload.model_validate(
        {
            "source_request_id": "material-1",
            "request_type": "ISSUE",
            "status": "submitted",
            "project_source_reference": "project-1",
            "ticket_source_reference": "ticket-1",
            "items": [
                {
                    "item_code": "CABLE",
                    "quantity": "1",
                    "from_warehouse_code": "WH-1",
                }
            ],
        }
    )
    expense = SubExpenseClaimPayload.model_validate(
        {
            "source_claim_id": "expense-1",
            "purpose": "Field visit",
            "claim_date": "2026-08-25",
            "requested_by_email": "field@example.com",
            "project_source_reference": "project-1",
            "ticket_source_reference": "ticket-1",
            "items": [
                {
                    "category_code": "TRAVEL",
                    "description": "Travel",
                    "claimed_amount": "10.00",
                }
            ],
        }
    )
    response = BulkSyncResponse(
        errors=[
            SyncError(
                entity_type="project",
                source_reference="project-1",
                error="rejected",
            )
        ]
    ).model_dump(mode="json", by_alias=True)
    invoice = SubPurchaseInvoiceResponse(
        purchase_invoice_id="PINV-1",
        invoice_id="00000000-0000-0000-0000-000000000001",
        invoice_number="PINV-1",
        status="draft",
        source_invoice_id="source-1",
    ).model_dump(mode="json", by_alias=True)

    assert work_order.project_source_reference == "project-1"
    assert work_order.ticket_source_reference == "ticket-1"
    assert material.project_source_reference == "project-1"
    assert material.ticket_source_reference == "ticket-1"
    assert expense.project_source_reference == "project-1"
    assert expense.ticket_source_reference == "ticket-1"
    assert response["errors"][0]["source_reference"] == "project-1"
    assert invoice["source_invoice_id"] == "source-1"

    hits = {
        path: sorted(found)
        for path in SUB_CONTRACT_PATHS
        if (found := _retired_alias_hits(_source(path)))
    }
    assert hits == {}


def test_retired_alias_detector_catches_every_live_pydantic_shape() -> None:
    planted = """
class Payload(BaseModel):
    customer_crm_id: str
    value: str = Field(validation_alias=AliasChoices("project_crm_id", "value"))

Payload(ticket_crm_id="ticket", omni_id="request")
router.get("/records/{crm_id}")
Field(serialization_alias="crm_invoice_id")
"""
    assert _retired_alias_hits(planted) == {
        "customer_crm_id",
        "project_crm_id",
        "ticket_crm_id",
        "crm_id",
        "crm_invoice_id",
        "omni_id",
    }


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
        "source_correlation",
        "legacy_unknown",
        "ENABLE ROW LEVEL SECURITY",
        "FORCE ROW LEVEL SECURITY",
        "source_correlation_tenant_isolation",
        "source_system = 'crm'",
    ):
        assert proof in migration
    assert "key_hash" not in migration


def test_reserved_historical_enum_values_have_no_executable_consumer() -> None:
    dotmac_crm_readers: list[str] = []
    external_crm_readers: list[str] = []
    for path in sorted((ROOT / "app").rglob("*.py")):
        relative = path.relative_to(ROOT).as_posix()
        source = path.read_text(encoding="utf-8")
        if "DOTMAC_CRM" in source and relative != "app/models/sync/integration_config.py":
            dotmac_crm_readers.append(relative)
        if "ExternalSource.CRM" in source:
            external_crm_readers.append(relative)
    assert dotmac_crm_readers == []
    assert external_crm_readers == []
    assert ExternalSource.CRM.value == "CRM"
    assert ExternalSync.__table__.c.source.type.enums == [
        "SPLYNX",
        "ERPNEXT",
        "CRM",
        "DOTMAC_SUB",
    ]
