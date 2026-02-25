"""
Module Settings Web Service.

Provides context and update functions for module settings UI pages.
Handles: Support, Inventory, Projects, Fleet, and Procurement settings.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import Session

from app.models.domain_settings import SettingDomain, SettingScope, SettingValueType
from app.services.settings_spec import SettingSpec, coerce_value, get_spec

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from app.models.inventory.warehouse import Warehouse


@dataclass(frozen=True)
class ModuleSettingsConfig:
    key: str
    title: str
    description: str
    url: str
    icon: str
    page_title: str
    template: str
    setting_keys: list[str]
    extra_context: Callable[[Session, uuid.UUID], dict[str, Any]] | None = None


MODULE_SETTINGS_CONFIGS = [
    ModuleSettingsConfig(
        key="support",
        title="Support",
        description="SLA configuration and ticket defaults",
        url="/settings/support",
        icon="ticket",
        page_title="Support Settings",
        template="settings/support.html",
        setting_keys=[
            "support_default_sla_response_hours",
            "support_default_sla_resolution_hours",
            "support_auto_assignment_enabled",
            "support_ticket_prefix",
        ],
    ),
    ModuleSettingsConfig(
        key="inventory",
        title="Inventory",
        description="Stock thresholds and warehouse defaults",
        url="/settings/inventory",
        icon="cube",
        page_title="Inventory Settings",
        template="settings/inventory.html",
        setting_keys=[
            "inventory_low_stock_threshold_percent",
            "inventory_default_warehouse_id",
            "inventory_enable_lot_tracking",
            "inventory_enable_serial_tracking",
            "stock_reservation_enabled",
            "stock_reservation_expiry_hours",
            "stock_reservation_allow_partial",
            "stock_reservation_auto_on_confirm",
        ],
    ),
    ModuleSettingsConfig(
        key="projects",
        title="Projects",
        description="Project workflow and task defaults",
        url="/settings/projects",
        icon="clipboard-document-list",
        page_title="Projects Settings",
        template="settings/projects.html",
        setting_keys=[
            "project_default_status",
            "project_enable_time_tracking",
            "project_task_prefix",
        ],
    ),
    ModuleSettingsConfig(
        key="fleet",
        title="Fleet",
        description="Reservation rules and maintenance defaults",
        url="/settings/fleet",
        icon="truck",
        page_title="Fleet Settings",
        template="settings/fleet.html",
        setting_keys=[
            "fleet_reservation_lead_days",
            "fleet_reservation_default_duration_hours",
            "fleet_require_driver_license",
        ],
    ),
    ModuleSettingsConfig(
        key="procurement",
        title="Procurement",
        description="RFQ and approval defaults",
        url="/settings/procurement",
        icon="shopping-bag",
        page_title="Procurement Settings",
        template="settings/procurement.html",
        setting_keys=[
            "procurement_default_payment_terms_days",
            "procurement_require_rfq_for_po",
            "procurement_threshold_direct_max",
            "procurement_threshold_selective_max",
            "procurement_threshold_ministerial_max",
        ],
    ),
]


MODULE_SETTINGS_BY_KEY = {config.key: config for config in MODULE_SETTINGS_CONFIGS}


# Hub sections configuration
MODULE_SETTINGS_SECTIONS = [
    {
        "title": config.title,
        "description": config.description,
        "url": config.url,
        "icon": config.icon,
    }
    for config in MODULE_SETTINGS_CONFIGS
]


class ModuleSettingsWebService:
    """Service for Module Settings UI."""

    # ========== Hub ==========

    def get_hub_context(self, organization_id: uuid.UUID) -> dict[str, Any]:
        """Get context for settings hub page."""
        return {
            "settings_sections": MODULE_SETTINGS_SECTIONS,
        }

    # ========== Support Settings ==========

    def get_support_context(
        self, db: Session, organization_id: uuid.UUID
    ) -> dict[str, Any]:
        """Get support/SLA settings for the form."""
        return self._build_settings_context(
            db, organization_id, MODULE_SETTINGS_BY_KEY["support"].setting_keys
        )

    def update_support_settings(
        self,
        db: Session,
        organization_id: uuid.UUID,
        data: dict[str, Any],
    ) -> tuple[bool, str | None]:
        """Update support settings."""
        return self._update_settings(
            db, organization_id, data, MODULE_SETTINGS_BY_KEY["support"].setting_keys
        )

    # ========== Inventory Settings ==========

    def get_inventory_context(
        self, db: Session, organization_id: uuid.UUID
    ) -> dict[str, Any]:
        """Get inventory settings for the form."""
        context = self._build_settings_context(
            db, organization_id, MODULE_SETTINGS_BY_KEY["inventory"].setting_keys
        )
        context.update(self._inventory_extra_context(db, organization_id))
        return context

    def update_inventory_settings(
        self,
        db: Session,
        organization_id: uuid.UUID,
        data: dict[str, Any],
    ) -> tuple[bool, str | None]:
        """Update inventory settings."""
        return self._update_settings(
            db, organization_id, data, MODULE_SETTINGS_BY_KEY["inventory"].setting_keys
        )

    # ========== Projects Settings ==========

    def get_projects_context(
        self, db: Session, organization_id: uuid.UUID
    ) -> dict[str, Any]:
        """Get project settings for the form."""
        context = self._build_settings_context(
            db, organization_id, MODULE_SETTINGS_BY_KEY["projects"].setting_keys
        )
        context.update(self._projects_extra_context())
        return context

    def update_projects_settings(
        self,
        db: Session,
        organization_id: uuid.UUID,
        data: dict[str, Any],
    ) -> tuple[bool, str | None]:
        """Update project settings."""
        return self._update_settings(
            db, organization_id, data, MODULE_SETTINGS_BY_KEY["projects"].setting_keys
        )

    # ========== Fleet Settings ==========

    def get_fleet_context(
        self, db: Session, organization_id: uuid.UUID
    ) -> dict[str, Any]:
        """Get fleet settings for the form."""
        return self._build_settings_context(
            db, organization_id, MODULE_SETTINGS_BY_KEY["fleet"].setting_keys
        )

    def update_fleet_settings(
        self,
        db: Session,
        organization_id: uuid.UUID,
        data: dict[str, Any],
    ) -> tuple[bool, str | None]:
        """Update fleet settings."""
        return self._update_settings(
            db, organization_id, data, MODULE_SETTINGS_BY_KEY["fleet"].setting_keys
        )

    # ========== Procurement Settings ==========

    def get_procurement_context(
        self, db: Session, organization_id: uuid.UUID
    ) -> dict[str, Any]:
        """Get procurement settings for the form."""
        return self._build_settings_context(
            db, organization_id, MODULE_SETTINGS_BY_KEY["procurement"].setting_keys
        )

    def update_procurement_settings(
        self,
        db: Session,
        organization_id: uuid.UUID,
        data: dict[str, Any],
    ) -> tuple[bool, str | None]:
        """Update procurement settings."""
        return self._update_settings(
            db,
            organization_id,
            data,
            MODULE_SETTINGS_BY_KEY["procurement"].setting_keys,
        )

    def _build_settings_context(
        self,
        db: Session,
        organization_id: uuid.UUID,
        keys: list[str],
    ) -> dict[str, Any]:
        settings: dict[str, Any] = {}
        for key in keys:
            spec = self._spec_for_key(key)
            value = self._resolve_org_value(db, organization_id, spec)
            settings[key] = {
                "value": value,
                "default": spec.default,
                "type": spec.value_type.value,
                "label": spec.label or key,
                "description": spec.description or "",
                "min": spec.min_value,
                "max": spec.max_value,
                "allowed": sorted(spec.allowed) if spec.allowed else None,
            }
        return {"settings": settings}

    def _update_settings(
        self,
        db: Session,
        organization_id: uuid.UUID,
        data: dict[str, Any],
        keys: list[str],
    ) -> tuple[bool, str | None]:
        for key in keys:
            if key in data:
                spec = self._spec_for_key(key)
                value, error = self._coerce_value(data[key], spec)
                if error:
                    return False, error
                try:
                    self._set_setting_value(
                        db, organization_id, key, value, spec.value_type
                    )
                except Exception as e:
                    logger.exception("Failed to set setting %s", key)
                    return False, f"Failed to update {spec.label or key}: {e}"

        db.commit()
        return True, None

    def _coerce_value(self, value: Any, spec: SettingSpec) -> tuple[Any, str | None]:
        default = spec.default
        if value in (None, ""):
            return default, None
        parsed, error = coerce_value(spec, value)
        if error:
            return default, error
        if spec.allowed and parsed not in spec.allowed:
            return default, f"Invalid value for {spec.label or spec.key}"
        if isinstance(parsed, int):
            if spec.min_value is not None and parsed < spec.min_value:
                return default, f"Value too small for {spec.label or spec.key}"
            if spec.max_value is not None and parsed > spec.max_value:
                return default, f"Value too large for {spec.label or spec.key}"
        return parsed, None

    def _inventory_extra_context(
        self, db: Session, organization_id: uuid.UUID
    ) -> dict[str, Any]:
        warehouses: list[Warehouse] = []
        try:
            from sqlalchemy import select

            from app.models.inventory.warehouse import Warehouse

            result = db.execute(
                select(Warehouse)
                .where(
                    Warehouse.organization_id == organization_id,
                    Warehouse.is_active.is_(True),
                )
                .order_by(Warehouse.warehouse_name)
            )
            warehouses = list(result.scalars().all())
        except Exception as e:
            logger.debug("Could not load warehouses: %s", e)
        return {"warehouses": warehouses}

    def _projects_extra_context(self) -> dict[str, Any]:
        class StatusOption:
            def __init__(self, value: str, label: str):
                self.value = value
                self.label = label

        project_statuses = [
            StatusOption("PLANNING", "Planning"),
            StatusOption("ACTIVE", "Active"),
            StatusOption("ON_HOLD", "On Hold"),
        ]
        return {"project_statuses": project_statuses}

    # ========== Helper Methods ==========

    def _set_setting_value(
        self,
        db: Session,
        organization_id: uuid.UUID,
        key: str,
        value: Any,
        value_type: SettingValueType | str = SettingValueType.string,
    ) -> None:
        """Set a setting value in DomainSetting."""
        domain = self._domain_for_key(key)
        normalized_type = value_type
        if isinstance(value_type, str):
            normalized_type = SettingValueType(value_type)
        try:
            from sqlalchemy import select

            from app.models.domain_settings import DomainSetting

            result = db.execute(
                select(DomainSetting).where(
                    DomainSetting.domain == domain,
                    DomainSetting.key == key,
                    DomainSetting.organization_id == organization_id,
                )
            )
            setting = result.scalar_one_or_none()

            if setting:
                setting.value_text = str(value) if value is not None else None
                if normalized_type == SettingValueType.boolean:
                    setting.value_type = SettingValueType.boolean
                elif normalized_type == SettingValueType.integer:
                    setting.value_type = SettingValueType.integer
                else:
                    setting.value_type = SettingValueType.string
            else:
                new_setting = DomainSetting(
                    domain=domain,
                    key=key,
                    organization_id=organization_id,
                    scope=SettingScope.ORG_SPECIFIC,
                    value_text=str(value) if value is not None else None,
                    value_type=SettingValueType.string,
                    is_active=True,
                )
                if normalized_type == SettingValueType.boolean:
                    new_setting.value_type = SettingValueType.boolean
                elif normalized_type == SettingValueType.integer:
                    new_setting.value_type = SettingValueType.integer
                db.add(new_setting)
        except Exception:
            raise

    def _spec_for_key(self, key: str) -> SettingSpec:
        domain = self._domain_for_key(key)
        spec = get_spec(domain, key)
        if not spec:
            raise ValueError(f"Unknown setting spec for {domain.value}/{key}")
        return spec

    def _resolve_org_value(
        self,
        db: Session,
        organization_id: uuid.UUID,
        spec: SettingSpec,
    ) -> Any:
        from sqlalchemy import select

        from app.models.domain_settings import DomainSetting
        from app.services.settings_spec import extract_db_value

        result = db.execute(
            select(DomainSetting).where(
                DomainSetting.domain == spec.domain,
                DomainSetting.key == spec.key,
                DomainSetting.organization_id == organization_id,
            )
        )
        setting = result.scalar_one_or_none()
        raw = extract_db_value(setting)
        if raw is None:
            raw = spec.default

        value, error = coerce_value(spec, raw)
        if error:
            return spec.default
        if spec.allowed and value is not None and value not in spec.allowed:
            return spec.default
        return value

    @staticmethod
    def _domain_for_key(key: str) -> SettingDomain:
        """Resolve a settings domain based on key prefix."""
        if key.startswith("support_"):
            return SettingDomain.support
        if key.startswith("inventory_"):
            return SettingDomain.inventory
        if key.startswith("project_"):
            return SettingDomain.projects
        if key.startswith("fleet_"):
            return SettingDomain.fleet
        if key.startswith("procurement_"):
            return SettingDomain.procurement
        return SettingDomain.settings


# Singleton instance
module_settings_web_service = ModuleSettingsWebService()
