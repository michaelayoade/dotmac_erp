"""Tenant entity controls govern catalogs, authoring, and execution."""

import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from app.models.finance.automation import (
    ActionType,
    CustomFieldDefinition,
    CustomFieldEntityType,
    TriggerEvent,
    WorkflowEntityType,
)
from app.services.finance.automation.capabilities import capabilities_payload
from app.services.finance.automation.entity_configuration import (
    AutomationEntityConfigurationService,
    enforce_enabled_entity_writes,
    entity_configuration_service,
)
from app.services.finance.automation.workflow import (
    TriggerContext,
    WorkflowRuleInput,
    WorkflowService,
)


def test_missing_override_defaults_registered_entities_to_enabled() -> None:
    db = MagicMock()
    db.execute.return_value.scalars.return_value.all.return_value = []

    enabled = AutomationEntityConfigurationService().enabled_entity_types(
        db,
        uuid.uuid4(),
    )

    assert "INVOICE" in enabled
    assert "EMPLOYEE" in enabled


def test_explicit_disable_is_tenant_scoped_and_excluded() -> None:
    db = MagicMock()
    db.execute.return_value.scalars.return_value.all.return_value = ["INVOICE"]
    service = AutomationEntityConfigurationService()

    assert service.is_enabled(db, uuid.uuid4(), "INVOICE") is False
    assert service.is_enabled(db, uuid.uuid4(), "NOT_A_MODEL") is False


def test_unknown_entity_cannot_be_added_from_ui() -> None:
    with pytest.raises(HTTPException) as exc:
        AutomationEntityConfigurationService().set_enabled(
            MagicMock(),
            uuid.uuid4(),
            "ARBITRARY_TABLE",
            True,
            uuid.uuid4(),
        )

    assert exc.value.status_code == 404


@patch.object(
    entity_configuration_service,
    "enabled_entity_types",
    return_value=("INVOICE",),
)
def test_capability_api_returns_only_tenant_enabled_entities(_enabled) -> None:
    payload = capabilities_payload(MagicMock(), uuid.uuid4())

    assert [item["entity_type"] for item in payload] == ["INVOICE"]


@patch.object(entity_configuration_service, "is_enabled", return_value=False)
def test_disabled_entity_rejects_new_workflow(_is_enabled) -> None:
    input_data = WorkflowRuleInput(
        rule_name="Disabled invoice rule",
        entity_type=WorkflowEntityType.INVOICE,
        trigger_event=TriggerEvent.ON_CREATE,
        action_type=ActionType.BLOCK,
        trigger_conditions={},
        action_config={"message": "blocked"},
    )

    with pytest.raises(HTTPException) as exc:
        WorkflowService().create_rule(
            MagicMock(),
            uuid.uuid4(),
            input_data,
            uuid.uuid4(),
        )

    assert exc.value.status_code == 409


@patch.object(entity_configuration_service, "is_enabled", return_value=False)
def test_disabled_entity_rejects_custom_field_authoring(_is_enabled) -> None:
    with pytest.raises(HTTPException) as exc:
        entity_configuration_service.ensure_enabled(
            MagicMock(),
            uuid.uuid4(),
            CustomFieldEntityType.INVOICE.value,
        )

    assert exc.value.status_code == 409


def test_flush_guard_covers_direct_custom_field_definition_inserts() -> None:
    organization_id = uuid.uuid4()
    definition = CustomFieldDefinition(
        organization_id=organization_id,
        entity_type=CustomFieldEntityType.INVOICE,
    )
    session = MagicMock()
    session.info = {"organization_id": organization_id}
    session.new = [definition]
    session.dirty = []
    session.deleted = []

    with patch.object(entity_configuration_service, "ensure_enabled") as ensure:
        enforce_enabled_entity_writes(session, None, None)

    ensure.assert_called_once_with(session, organization_id, "INVOICE")


def test_flush_guard_allows_lifecycle_edits_to_existing_definitions() -> None:
    definition = CustomFieldDefinition(
        organization_id=uuid.uuid4(),
        entity_type=CustomFieldEntityType.INVOICE,
    )
    session = MagicMock()
    session.info = {"organization_id": definition.organization_id}
    session.new = []
    session.dirty = [definition]
    session.deleted = []

    with patch.object(entity_configuration_service, "ensure_enabled") as ensure:
        enforce_enabled_entity_writes(session, None, None)

    ensure.assert_not_called()


@patch.object(entity_configuration_service, "is_enabled", return_value=False)
def test_queued_action_for_disabled_entity_is_recorded_as_skipped(_is_enabled) -> None:
    service = WorkflowService()
    db = MagicMock()
    rule = MagicMock(
        rule_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
    )
    context = TriggerContext(
        entity_type="INVOICE",
        entity_id=uuid.uuid4(),
        event=TriggerEvent.ON_CREATE,
        organization_id=rule.organization_id,
    )
    skipped = MagicMock()

    with patch.object(service, "_record_skipped", return_value=skipped) as record:
        result = service.execute_action(db, rule, context)

    assert result is skipped
    record.assert_called_once_with(
        db,
        rule,
        context,
        "Entity is disabled in Admin Automation settings",
    )


def test_entity_configuration_template_has_csrf_and_both_actions() -> None:
    template = (
        Path(__file__).parents[4]
        / "templates"
        / "admin"
        / "automation"
        / "entity_configuration.html"
    ).read_text(encoding="utf-8")

    assert "request.state.csrf_form" in template
    assert "/disable" in template
    assert "/enable" in template
    assert "Existing rules, definitions, assignments" in template


def test_admin_entity_routes_are_isolated_from_shared_automation_router() -> None:
    routes = (
        Path(__file__).parents[4] / "app" / "web" / "admin_automation_entities.py"
    ).read_text(encoding="utf-8")
    admin_routes = (Path(__file__).parents[4] / "app" / "web" / "admin.py").read_text(
        encoding="utf-8"
    )

    assert 'prefix="/automation/entities"' in routes
    assert "router.include_router(automation_entities_router)" in admin_routes
    assert 'url="/admin/automation/entities?' in routes


def test_custom_field_write_guard_is_registered_with_model_connectors() -> None:
    connector = (
        Path(__file__).parents[4]
        / "app"
        / "services"
        / "finance"
        / "automation"
        / "model_event_connector.py"
    ).read_text(encoding="utf-8")

    assert "enforce_enabled_entity_writes" in connector
    assert '"before_flush", enforce_enabled_entity_writes' in connector
