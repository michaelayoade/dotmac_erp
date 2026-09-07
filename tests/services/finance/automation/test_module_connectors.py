"""The Admin catalog must not advertise entities without runtime wiring."""

from app.models.finance.automation import CustomFieldEntityType, WorkflowEntityType
from app.services.finance.automation.capabilities import automation_capabilities
from app.services.finance.automation.entity_registry import registered_entity_types
from app.services.finance.automation.model_event_connector import _derived_events


def test_every_workflow_entity_has_a_model_connector() -> None:
    advertised = {item.value for item in WorkflowEntityType}
    assert advertised == set(registered_entity_types())


def test_every_workflow_entity_supports_custom_fields() -> None:
    custom_fields = {item.value for item in CustomFieldEntityType}
    assert {item.value for item in WorkflowEntityType} <= custom_fields


def test_capability_catalog_is_backed_by_registry() -> None:
    capabilities = automation_capabilities()
    assert {item.entity_type for item in capabilities} == set(registered_entity_types())
    assert all(item.supports_assignment for item in capabilities)
    assert all(item.supports_custom_fields for item in capabilities)


def test_status_updates_derive_semantic_events() -> None:
    record = {
        "event": "ON_UPDATE",
        "changed_fields": ["status", "approved_by"],
        "new_values": {"status": "APPROVED"},
    }
    assert _derived_events(record) == [
        "ON_UPDATE",
        "ON_FIELD_CHANGE",
        "ON_THRESHOLD",
        "ON_STATUS_CHANGE",
        "ON_APPROVAL",
    ]
