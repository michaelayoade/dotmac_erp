from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from app.models.finance.core_org import PerformanceMode
from app.services.admin.settings_web import AdminSettingsWebService


class TestAdminSettingsWebServiceUpdateOrganization:
    def test_enabling_pms_updates_org_and_seeds_defaults(self) -> None:
        db = MagicMock()
        org = SimpleNamespace(
            pms_ohcsf_enabled=False,
            performance_mode=PerformanceMode.PRIVATE,
            legal_name="DotMac",
            functional_currency_code="NGN",
            presentation_currency_code="NGN",
        )
        db.get.return_value = org

        with (
            patch(
                "app.services.people.perf.pms_config_service.PMSConfigService"
            ) as mock_pms_config,
            patch(
                "app.services.admin.settings_web.reconcile_organization_tenant"
            ) as reconcile_projection,
        ):
            service = AdminSettingsWebService()

            success, error = service.update_organization(
                db,
                uuid4(),
                {
                    "legal_name": "DotMac ERP",
                    "pms_ohcsf_enabled": "true",
                },
            )

        assert success is True
        assert error is None
        assert org.legal_name == "DotMac ERP"
        assert org.pms_ohcsf_enabled is True
        assert org.performance_mode == PerformanceMode.GOVERNMENT_PMS
        mock_pms_config.assert_called_once_with(db)
        mock_pms_config.return_value.activate_ohcsf_pms.assert_called_once()
        reconcile_projection.assert_called_once_with(db, org)
        db.commit.assert_called_once()

    def test_disabling_pms_updates_org_without_seeding(self) -> None:
        db = MagicMock()
        org = SimpleNamespace(
            pms_ohcsf_enabled=True,
            performance_mode=PerformanceMode.GOVERNMENT_PMS,
            legal_name="DotMac",
            functional_currency_code="NGN",
            presentation_currency_code="NGN",
        )
        db.get.return_value = org

        with (
            patch(
                "app.services.people.perf.pms_config_service.PMSConfigService"
            ) as mock_pms_config,
            patch(
                "app.services.admin.settings_web.reconcile_organization_tenant"
            ) as reconcile_projection,
        ):
            service = AdminSettingsWebService()

            success, error = service.update_organization(
                db,
                uuid4(),
                {
                    "pms_ohcsf_enabled": "false",
                },
            )

        assert success is True
        assert error is None
        assert org.pms_ohcsf_enabled is False
        assert org.performance_mode == PerformanceMode.PRIVATE
        mock_pms_config.assert_not_called()
        reconcile_projection.assert_called_once_with(db, org)
        db.commit.assert_called_once()

    def test_setting_performance_mode_updates_org(self) -> None:
        db = MagicMock()
        org = SimpleNamespace(
            pms_ohcsf_enabled=False,
            performance_mode=PerformanceMode.PRIVATE,
            legal_name="DotMac",
            functional_currency_code="NGN",
            presentation_currency_code="NGN",
        )
        db.get.return_value = org

        with patch(
            "app.services.admin.settings_web.reconcile_organization_tenant"
        ) as reconcile_projection:
            service = AdminSettingsWebService()
            success, error = service.update_organization(
                db,
                uuid4(),
                {"performance_mode": "HYBRID"},
            )

        assert success is True
        assert error is None
        assert org.performance_mode == PerformanceMode.HYBRID
        assert org.pms_ohcsf_enabled is True
        reconcile_projection.assert_called_once_with(db, org)
        db.commit.assert_called_once()


class TestAdminSettingsWebServiceFeatures:
    def test_get_features_context_includes_pms_toggle(self) -> None:
        db = MagicMock()
        org_id = uuid4()
        db.get.return_value = SimpleNamespace(
            pms_ohcsf_enabled=False, performance_mode=PerformanceMode.GOVERNMENT_PMS
        )

        mock_flag = SimpleNamespace(
            flag_key="example_flag",
            label="Example Flag",
            description="Example description",
            enabled=False,
            default_enabled=False,
            is_org_override=False,
            status=SimpleNamespace(value="ACTIVE"),
            owner=None,
            expires_at=None,
            category=SimpleNamespace(value="MODULE"),
        )

        with patch(
            "app.services.feature_flag_service.FeatureFlagService"
        ) as mock_feature_flags:
            mock_feature_flags.return_value.get_all_flags.return_value = [mock_flag]
            service = AdminSettingsWebService()
            result = service.get_features_context(db, org_id)

        features_by_key = {feature["key"]: feature for feature in result["features"]}
        assert "pms_ohcsf_enabled" in features_by_key
        assert features_by_key["pms_ohcsf_enabled"]["enabled"] is True
        assert features_by_key["pms_ohcsf_enabled"]["label"] == "PMS (OHCSF)"

    def test_toggle_feature_enables_pms_and_seeds_defaults(self) -> None:
        db = MagicMock()
        org_id = uuid4()
        org = SimpleNamespace(
            pms_ohcsf_enabled=False, performance_mode=PerformanceMode.PRIVATE
        )
        db.get.return_value = org

        with patch(
            "app.services.people.perf.pms_config_service.PMSConfigService"
        ) as mock_pms_config:
            service = AdminSettingsWebService()
            success, error = service.toggle_feature(
                db,
                org_id,
                "pms_ohcsf_enabled",
                True,
            )

        assert success is True
        assert error is None
        assert org.pms_ohcsf_enabled is True
        assert org.performance_mode == PerformanceMode.GOVERNMENT_PMS
        mock_pms_config.assert_called_once_with(db)
        mock_pms_config.return_value.activate_ohcsf_pms.assert_called_once_with(org_id)
        db.commit.assert_called_once()
