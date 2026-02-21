from app.models.domain_settings import SettingDomain
from app.services.help_center import (
    EXPECTED_MODULE_KEYS,
    MODULE_GUIDES,
    build_help_center_payload,
)
from app.services.settings_spec import get_spec


def test_expected_modules_have_guides():
    for module_key in EXPECTED_MODULE_KEYS:
        assert module_key in MODULE_GUIDES
        guide = MODULE_GUIDES[module_key]
        assert guide["manual_links"]
        assert guide["journeys"]
        assert guide["troubleshooting"]


def test_payload_filters_by_accessible_modules():
    payload = build_help_center_payload(
        accessible_modules=["finance", "support"],
        roles=[],
        scopes=[],
        is_admin=False,
    )

    assert payload["coverage_modules"] == ["finance", "support"]
    assert all(
        item["module_key"] in {"finance", "support"} for item in payload["manuals"]
    )
    assert all(
        item["module_key"] in {"finance", "support"} for item in payload["journeys"]
    )
    assert all(
        item["module_key"] in {"finance", "support"}
        for item in payload["troubleshooting"]
    )


def test_payload_includes_admin_playbook_for_admins():
    payload = build_help_center_payload(
        accessible_modules=["settings"],
        roles=["admin"],
        scopes=[],
        is_admin=True,
    )
    titles = [item["title"] for item in payload["role_playbooks"]]
    assert "System Administrator" in titles


def test_payload_accepts_json_overrides():
    payload = build_help_center_payload(
        accessible_modules=["finance"],
        roles=[],
        scopes=[],
        is_admin=False,
        overrides={
            "manuals": [
                {
                    "module_key": "finance",
                    "title": "Custom Manual",
                    "summary": "Custom",
                    "links": [{"label": "X", "href": "/finance/dashboard"}],
                    "search_blob": "custom",
                }
            ]
        },
    )
    assert payload["manuals"][0]["title"] == "Custom Manual"


def test_help_center_setting_spec_exists():
    spec = get_spec(SettingDomain.settings, "help_center_content_json")
    assert spec is not None
