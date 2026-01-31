"""
Operations Settings Web Service.

Provides context and update functions for Operations settings UI pages.
Handles: Support/SLA, Inventory, and Projects settings.
"""

import uuid
import logging
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models.domain_settings import SettingDomain, SettingScope, SettingValueType
from app.services.settings_spec import (
    DOMAIN_SETTINGS_SERVICE,
    list_specs,
    resolve_value,
    get_spec,
)
from app.schemas.settings import DomainSettingUpdate

logger = logging.getLogger(__name__)


# Hub sections configuration
OPERATIONS_SETTINGS_SECTIONS = [
    {
        "title": "Support",
        "description": "SLA configuration and ticket defaults",
        "url": "/operations/settings/support",
        "icon": "ticket",
    },
    {
        "title": "Inventory",
        "description": "Stock thresholds and warehouse defaults",
        "url": "/operations/settings/inventory",
        "icon": "cube",
    },
    {
        "title": "Projects",
        "description": "Project workflow and task defaults",
        "url": "/operations/settings/projects",
        "icon": "clipboard-document-list",
    },
]


# Settings specs for operations module (could be added to settings_spec.py later)
OPERATIONS_SETTINGS = {
    # Support settings
    "support_default_sla_response_hours": {
        "type": "integer",
        "default": 24,
        "min": 1,
        "max": 168,
        "label": "Default SLA Response Time (hours)",
        "description": "Time allowed for initial response to tickets",
    },
    "support_default_sla_resolution_hours": {
        "type": "integer",
        "default": 72,
        "min": 1,
        "max": 720,
        "label": "Default SLA Resolution Time (hours)",
        "description": "Time allowed for ticket resolution",
    },
    "support_auto_assignment_enabled": {
        "type": "boolean",
        "default": False,
        "label": "Auto-assignment Enabled",
        "description": "Automatically assign tickets to available agents",
    },
    "support_ticket_prefix": {
        "type": "string",
        "default": "TKT",
        "label": "Ticket Number Prefix",
        "description": "Prefix for support ticket numbers",
    },
    # Inventory settings
    "inventory_low_stock_threshold_percent": {
        "type": "integer",
        "default": 20,
        "min": 1,
        "max": 100,
        "label": "Low Stock Threshold (%)",
        "description": "Percentage of minimum stock level to trigger alerts",
    },
    "inventory_default_warehouse_id": {
        "type": "uuid",
        "default": None,
        "label": "Default Warehouse",
        "description": "Default warehouse for new inventory transactions",
    },
    "inventory_enable_lot_tracking": {
        "type": "boolean",
        "default": False,
        "label": "Enable Lot Tracking",
        "description": "Track inventory items by lot/batch number",
    },
    "inventory_enable_serial_tracking": {
        "type": "boolean",
        "default": False,
        "label": "Enable Serial Tracking",
        "description": "Track inventory items by serial number",
    },
    # Project settings
    "project_default_status": {
        "type": "string",
        "default": "PLANNING",
        "allowed": ["PLANNING", "ACTIVE", "ON_HOLD"],
        "label": "Default Project Status",
        "description": "Initial status for new projects",
    },
    "project_enable_time_tracking": {
        "type": "boolean",
        "default": True,
        "label": "Enable Time Tracking",
        "description": "Allow time entries on project tasks",
    },
    "project_task_prefix": {
        "type": "string",
        "default": "TASK",
        "label": "Task Number Prefix",
        "description": "Prefix for task numbers",
    },
}


class OperationsSettingsWebService:
    """Service for Operations Settings UI."""

    # ========== Hub ==========

    def get_hub_context(self, organization_id: uuid.UUID) -> dict[str, Any]:
        """Get context for settings hub page."""
        return {
            "settings_sections": OPERATIONS_SETTINGS_SECTIONS,
        }

    # ========== Support Settings ==========

    def get_support_context(
        self, db: Session, organization_id: uuid.UUID
    ) -> dict[str, Any]:
        """Get support/SLA settings for the form."""
        settings = {}

        support_keys = [
            "support_default_sla_response_hours",
            "support_default_sla_resolution_hours",
            "support_auto_assignment_enabled",
            "support_ticket_prefix",
        ]

        for key in support_keys:
            spec = OPERATIONS_SETTINGS.get(key, {})
            # Try to get from domain settings, fallback to default
            value = self._get_setting_value(db, organization_id, key, spec.get("default"))
            settings[key] = {
                "value": value,
                "default": spec.get("default"),
                "type": spec.get("type", "string"),
                "label": spec.get("label", key),
                "description": spec.get("description", ""),
                "min": spec.get("min"),
                "max": spec.get("max"),
            }

        return {"settings": settings}

    def update_support_settings(
        self,
        db: Session,
        organization_id: uuid.UUID,
        data: dict[str, Any],
    ) -> tuple[bool, Optional[str]]:
        """Update support settings."""
        support_keys = [
            "support_default_sla_response_hours",
            "support_default_sla_resolution_hours",
            "support_auto_assignment_enabled",
            "support_ticket_prefix",
        ]

        for key in support_keys:
            if key in data:
                value = data[key]
                spec = OPERATIONS_SETTINGS.get(key, {})

                # Type coercion
                if spec.get("type") == "integer":
                    try:
                        value = int(value) if value else spec.get("default")
                    except (ValueError, TypeError):
                        value = spec.get("default")
                elif spec.get("type") == "boolean":
                    value = str(value).lower() in ("true", "1", "on", "yes")

                self._set_setting_value(db, organization_id, key, value, spec.get("type", "string"))

        db.commit()
        return True, None

    # ========== Inventory Settings ==========

    def get_inventory_context(
        self, db: Session, organization_id: uuid.UUID
    ) -> dict[str, Any]:
        """Get inventory settings for the form."""
        settings = {}

        inventory_keys = [
            "inventory_low_stock_threshold_percent",
            "inventory_default_warehouse_id",
            "inventory_enable_lot_tracking",
            "inventory_enable_serial_tracking",
        ]

        for key in inventory_keys:
            spec = OPERATIONS_SETTINGS.get(key, {})
            value = self._get_setting_value(db, organization_id, key, spec.get("default"))
            settings[key] = {
                "value": value,
                "default": spec.get("default"),
                "type": spec.get("type", "string"),
                "label": spec.get("label", key),
                "description": spec.get("description", ""),
                "min": spec.get("min"),
                "max": spec.get("max"),
            }

        # Get warehouses for dropdown
        warehouses = []
        try:
            from sqlalchemy import select
            from app.models.finance.inv.warehouse import Warehouse
            result = db.execute(
                select(Warehouse).where(
                    Warehouse.organization_id == organization_id,
                    Warehouse.is_active.is_(True),
                ).order_by(Warehouse.warehouse_name)
            )
            warehouses = result.scalars().all()
        except Exception as e:
            logger.debug("Could not load warehouses: %s", e)

        return {"settings": settings, "warehouses": warehouses}

    def update_inventory_settings(
        self,
        db: Session,
        organization_id: uuid.UUID,
        data: dict[str, Any],
    ) -> tuple[bool, Optional[str]]:
        """Update inventory settings."""
        inventory_keys = [
            "inventory_low_stock_threshold_percent",
            "inventory_default_warehouse_id",
            "inventory_enable_lot_tracking",
            "inventory_enable_serial_tracking",
        ]

        for key in inventory_keys:
            if key in data:
                value = data[key]
                spec = OPERATIONS_SETTINGS.get(key, {})

                if spec.get("type") == "integer":
                    try:
                        value = int(value) if value else spec.get("default")
                    except (ValueError, TypeError):
                        value = spec.get("default")
                elif spec.get("type") == "boolean":
                    value = str(value).lower() in ("true", "1", "on", "yes")
                elif spec.get("type") == "uuid":
                    value = value if value else None

                self._set_setting_value(db, organization_id, key, value, spec.get("type", "string"))

        db.commit()
        return True, None

    # ========== Projects Settings ==========

    def get_projects_context(
        self, db: Session, organization_id: uuid.UUID
    ) -> dict[str, Any]:
        """Get project settings for the form."""
        settings = {}

        project_keys = [
            "project_default_status",
            "project_enable_time_tracking",
            "project_task_prefix",
        ]

        for key in project_keys:
            spec = OPERATIONS_SETTINGS.get(key, {})
            value = self._get_setting_value(db, organization_id, key, spec.get("default"))
            settings[key] = {
                "value": value,
                "default": spec.get("default"),
                "type": spec.get("type", "string"),
                "label": spec.get("label", key),
                "description": spec.get("description", ""),
                "allowed": spec.get("allowed"),
            }

        # Status options for dropdown (as objects with value/label)
        class StatusOption:
            def __init__(self, value: str, label: str):
                self.value = value
                self.label = label

        project_statuses = [
            StatusOption("PLANNING", "Planning"),
            StatusOption("ACTIVE", "Active"),
            StatusOption("ON_HOLD", "On Hold"),
        ]

        return {"settings": settings, "project_statuses": project_statuses}

    def update_projects_settings(
        self,
        db: Session,
        organization_id: uuid.UUID,
        data: dict[str, Any],
    ) -> tuple[bool, Optional[str]]:
        """Update project settings."""
        project_keys = [
            "project_default_status",
            "project_enable_time_tracking",
            "project_task_prefix",
        ]

        for key in project_keys:
            if key in data:
                value = data[key]
                spec = OPERATIONS_SETTINGS.get(key, {})

                if spec.get("type") == "boolean":
                    value = str(value).lower() in ("true", "1", "on", "yes")

                # Validate allowed values
                if spec.get("allowed") and value not in spec["allowed"]:
                    value = spec.get("default")

                self._set_setting_value(db, organization_id, key, value, spec.get("type", "string"))

        db.commit()
        return True, None

    # ========== Helper Methods ==========

    def _get_setting_value(
        self,
        db: Session,
        organization_id: uuid.UUID,
        key: str,
        default: Any = None,
    ) -> Any:
        """Get a setting value from DomainSetting."""
        try:
            from app.models.domain_settings import DomainSetting
            from sqlalchemy import select

            result = db.execute(
                select(DomainSetting).where(
                    DomainSetting.domain == SettingDomain.operations,
                    DomainSetting.key == key,
                    DomainSetting.organization_id == organization_id,
                )
            )
            setting = result.scalar_one_or_none()

            if setting:
                if setting.value_text is not None:
                    return setting.value_text
                if setting.value_json is not None:
                    return setting.value_json
        except Exception as e:
            logger.debug("Could not get setting %s: %s", key, e)

        return default

    def _set_setting_value(
        self,
        db: Session,
        organization_id: uuid.UUID,
        key: str,
        value: Any,
        value_type: str = "string",
    ) -> None:
        """Set a setting value in DomainSetting."""
        try:
            from app.models.domain_settings import DomainSetting
            from sqlalchemy import select

            result = db.execute(
                select(DomainSetting).where(
                    DomainSetting.domain == SettingDomain.operations,
                    DomainSetting.key == key,
                    DomainSetting.organization_id == organization_id,
                )
            )
            setting = result.scalar_one_or_none()

            if setting:
                setting.value_text = str(value) if value is not None else None
                if value_type == "boolean":
                    setting.value_type = SettingValueType.boolean
                elif value_type == "integer":
                    setting.value_type = SettingValueType.integer
                else:
                    setting.value_type = SettingValueType.string
            else:
                new_setting = DomainSetting(
                    domain=SettingDomain.operations,
                    key=key,
                    organization_id=organization_id,
                    scope=SettingScope.ORG_SPECIFIC,
                    value_text=str(value) if value is not None else None,
                    value_type=SettingValueType.string,
                    is_active=True,
                )
                if value_type == "boolean":
                    new_setting.value_type = SettingValueType.boolean
                elif value_type == "integer":
                    new_setting.value_type = SettingValueType.integer
                db.add(new_setting)
        except Exception as e:
            logger.exception("Failed to set setting %s: %s", key, e)


# Singleton instance
operations_settings_web_service = OperationsSettingsWebService()
