"""Tests for module settings web helpers."""

from app.models.domain_settings import SettingDomain
from app.services.module_settings_web import ModuleSettingsWebService


class TestModuleSettingsWebService:
    """Tests for module settings key/domain resolution."""

    def test_fa_settings_keys_use_automation_domain(self) -> None:
        """Fixed-assets module settings should persist to automation settings."""
        domain = ModuleSettingsWebService._domain_for_key(
            "fa_depreciation_auto_run_enabled"
        )

        assert domain == SettingDomain.automation

    def test_general_hub_excludes_admin_expense_settings(self) -> None:
        from uuid import uuid4

        sections = ModuleSettingsWebService().get_hub_context(uuid4())[
            "settings_sections"
        ]
        urls = {section["url"] for section in sections}
        assert "/settings/inventory" in urls
        assert "/settings/expense" not in urls
        assert "/admin/settings/expense" not in urls
