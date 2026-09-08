"""Organization-level availability controls for registered automation entities."""

from collections import defaultdict
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.finance.automation import (
    AutomationEntityConfiguration,
    CustomFieldDefinition,
    WorkflowRule,
)
from app.services.finance.automation.capabilities import automation_capabilities
from app.services.finance.automation.entity_registry import registered_entity_types


class AutomationEntityConfigurationService:
    """Expose safe enable/disable controls over the code-backed entity catalog."""

    @staticmethod
    def _supported_types() -> frozenset[str]:
        return frozenset(registered_entity_types())

    def disabled_entity_types(
        self,
        db: Session,
        organization_id: UUID,
    ) -> frozenset[str]:
        """Return explicit tenant disables; missing rows default to enabled."""
        rows = (
            db.execute(
                select(AutomationEntityConfiguration.entity_type).where(
                    AutomationEntityConfiguration.organization_id == organization_id,
                    AutomationEntityConfiguration.is_enabled.is_(False),
                )
            )
            .scalars()
            .all()
        )
        return frozenset(rows)

    def enabled_entity_types(
        self,
        db: Session,
        organization_id: UUID,
    ) -> tuple[str, ...]:
        """Return registered entity types currently available to a tenant."""
        disabled = self.disabled_entity_types(db, organization_id)
        return tuple(
            entity_type
            for entity_type in registered_entity_types()
            if entity_type not in disabled
        )

    def is_enabled(
        self,
        db: Session,
        organization_id: UUID,
        entity_type: str,
    ) -> bool:
        """Check both the immutable connector allowlist and tenant override."""
        if entity_type not in self._supported_types():
            return False
        return entity_type not in self.disabled_entity_types(db, organization_id)

    def set_enabled(
        self,
        db: Session,
        organization_id: UUID,
        entity_type: str,
        enabled: bool,
        actor_id: UUID,
    ) -> AutomationEntityConfiguration:
        """Upsert a tenant override without accepting arbitrary model names."""
        if entity_type not in self._supported_types():
            raise HTTPException(
                status_code=404,
                detail="Entity is not registered for automation",
            )

        configuration = db.scalar(
            select(AutomationEntityConfiguration).where(
                AutomationEntityConfiguration.organization_id == organization_id,
                AutomationEntityConfiguration.entity_type == entity_type,
            )
        )
        if configuration is None:
            configuration = AutomationEntityConfiguration(
                organization_id=organization_id,
                entity_type=entity_type,
                is_enabled=enabled,
                created_by=actor_id,
                updated_by=actor_id,
            )
            db.add(configuration)
        else:
            configuration.is_enabled = enabled
            configuration.updated_by = actor_id
        db.flush()
        return configuration

    def ensure_enabled(
        self,
        db: Session,
        organization_id: UUID,
        entity_type: str,
    ) -> None:
        """Reject authoring or value writes for an unavailable entity."""
        if not self.is_enabled(db, organization_id, entity_type):
            raise HTTPException(
                status_code=409,
                detail="Entity is disabled in Admin Automation settings",
            )

    def catalog(
        self,
        db: Session,
        organization_id: UUID,
    ) -> list[dict[str, Any]]:
        """Return UI catalog state and the records affected by each toggle."""
        disabled = self.disabled_entity_types(db, organization_id)
        workflow_counts = {
            getattr(entity_type, "value", str(entity_type)): count
            for entity_type, count in db.execute(
                select(WorkflowRule.entity_type, func.count(WorkflowRule.rule_id))
                .where(WorkflowRule.organization_id == organization_id)
                .group_by(WorkflowRule.entity_type)
            ).all()
        }
        field_counts = {
            getattr(entity_type, "value", str(entity_type)): count
            for entity_type, count in db.execute(
                select(
                    CustomFieldDefinition.entity_type,
                    func.count(CustomFieldDefinition.field_id),
                )
                .where(CustomFieldDefinition.organization_id == organization_id)
                .group_by(CustomFieldDefinition.entity_type)
            ).all()
        }

        items: list[dict[str, Any]] = []
        for capability in automation_capabilities():
            items.append(
                {
                    "entity_type": capability.entity_type,
                    "label": capability.entity_type.replace("_", " ").title(),
                    "module": capability.module,
                    "events": capability.events,
                    "event_count": len(capability.events),
                    "supports_custom_fields": capability.supports_custom_fields,
                    "supports_assignment": capability.supports_assignment,
                    "is_enabled": capability.entity_type not in disabled,
                    "workflow_count": workflow_counts.get(capability.entity_type, 0),
                    "field_count": field_counts.get(capability.entity_type, 0),
                }
            )
        return items

    def grouped_catalog(
        self,
        db: Session,
        organization_id: UUID,
    ) -> list[dict[str, Any]]:
        """Group the stable catalog by ERP module for a readable Admin page."""
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in self.catalog(db, organization_id):
            groups[item["module"]].append(item)
        return [
            {
                "module": module,
                "entities": sorted(items, key=lambda item: item["label"]),
            }
            for module, items in sorted(groups.items())
        ]


entity_configuration_service = AutomationEntityConfigurationService()


def enforce_enabled_entity_writes(
    session: Session,
    _flush_context: Any,
    _instances: Any,
) -> None:
    """Prevent direct custom-field authoring/value writes for disabled entities."""
    if session.info.get("allow_cross_org"):
        return
    organization_id = session.info.get("organization_id")
    if organization_id is None:
        return

    from app.models.finance.automation import CustomFieldValue

    candidates = [
        *(
            item
            for item in session.new
            if isinstance(item, (CustomFieldDefinition, CustomFieldValue))
        ),
        *(item for item in session.dirty if isinstance(item, CustomFieldValue)),
        *(item for item in session.deleted if isinstance(item, CustomFieldValue)),
    ]
    checked: set[str] = set()
    for item in candidates:
        entity_type = getattr(item.entity_type, "value", str(item.entity_type))
        if entity_type in checked:
            continue
        entity_configuration_service.ensure_enabled(
            session,
            organization_id,
            entity_type,
        )
        checked.add(entity_type)
