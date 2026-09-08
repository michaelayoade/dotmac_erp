"""Truthful UI capabilities for cross-module automation."""

from dataclasses import asdict, dataclass
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.finance.automation import CustomFieldEntityType, WorkflowEntityType
from app.services.finance.automation.entity_registry import registered_entity_types


@dataclass(frozen=True)
class EntityAutomationCapability:
    entity_type: str
    module: str
    events: tuple[str, ...]
    supports_custom_fields: bool
    supports_assignment: bool = True


_STANDARD_EVENTS = (
    "ON_CREATE",
    "ON_UPDATE",
    "ON_DELETE",
    "ON_FIELD_CHANGE",
    "ON_STATUS_CHANGE",
    "ON_APPROVAL",
    "ON_REJECTION",
    "ON_DUE_DATE",
    "ON_OVERDUE",
    "ON_THRESHOLD",
    "ON_SCHEDULE",
)


def _module_for(entity_type: str) -> str:
    if entity_type.startswith("FLEET_"):
        return "Fleet"
    if entity_type == "MATERIAL_REQUEST":
        return "Inventory"
    if entity_type == "ITEM":
        return "Inventory"
    if entity_type == "PROJECT":
        return "Projects"
    if entity_type in {"ASSET", "ASSET_DISPOSAL"}:
        return "Fixed Assets"
    if entity_type in {
        "EMPLOYEE",
        "ATTENDANCE",
        "LEAVE_REQUEST",
        "DISCIPLINARY_CASE",
        "PERFORMANCE_APPRAISAL",
        "PAYROLL_RUN",
        "PAYROLL_ENTRY",
        "SALARY_SLIP",
        "LOAN",
        "RECRUITMENT",
    }:
        return "People"
    return "Finance"


def automation_capabilities() -> list[EntityAutomationCapability]:
    custom_types = {item.value for item in CustomFieldEntityType}
    workflow_types = {item.value for item in WorkflowEntityType}
    return [
        EntityAutomationCapability(
            entity_type=entity_type,
            module=_module_for(entity_type),
            events=_STANDARD_EVENTS,
            supports_custom_fields=entity_type in custom_types,
        )
        for entity_type in registered_entity_types()
        if entity_type in workflow_types
    ]


def capabilities_payload(
    db: Session | None = None,
    organization_id: UUID | None = None,
) -> list[dict[str, object]]:
    capabilities = automation_capabilities()
    if db is not None and organization_id is not None:
        from app.services.finance.automation.entity_configuration import (
            entity_configuration_service,
        )

        enabled = set(
            entity_configuration_service.enabled_entity_types(db, organization_id)
        )
        capabilities = [
            capability
            for capability in capabilities
            if capability.entity_type in enabled
        ]
    return [asdict(capability) for capability in capabilities]
