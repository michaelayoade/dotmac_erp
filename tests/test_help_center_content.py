from app.models.domain_settings import SettingDomain
from app.services.help_center import (
    EXPECTED_MODULE_KEYS,
    MODULE_GUIDES,
    build_help_center_payload,
    build_help_module_hub,
    get_help_article_by_slug,
    get_help_track_by_slug,
    search_help_articles,
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


def test_payload_accepts_article_and_track_overrides():
    payload = build_help_center_payload(
        accessible_modules=["finance"],
        roles=[],
        scopes=[],
        is_admin=False,
        overrides={
            "articles": [
                {
                    "slug": "custom-finance-article",
                    "href": "/help/articles/custom-finance-article",
                    "title": "Custom Finance Guide",
                    "summary": "Custom summary",
                    "module_key": "finance",
                    "module_title": "Finance",
                    "content_type": "workflow",
                    "content_type_label": "Workflow",
                    "audience": "Finance operators",
                    "estimated_minutes": 5,
                    "prerequisites": [],
                    "sections": [],
                    "related_links": [],
                    "search_blob": "custom finance guide",
                }
            ],
            "tracks": [
                {
                    "slug": "custom-finance-track",
                    "title": "Custom Track",
                    "summary": "Custom track summary",
                    "audience": "Finance operators",
                    "steps": [],
                    "step_count": 0,
                }
            ],
        },
    )

    assert payload["articles"][0]["slug"] == "custom-finance-article"
    assert payload["tracks"][0]["slug"] == "custom-finance-track"


def test_help_center_setting_spec_exists():
    spec = get_spec(SettingDomain.settings, "help_center_content_json")
    assert spec is not None


def test_help_center_experience_payload_includes_articles_and_tracks():
    payload = build_help_center_payload(
        accessible_modules=["finance", "people", "support", "settings"],
        roles=["admin"],
        scopes=[],
        is_admin=True,
    )

    assert payload["featured_articles"]
    assert any(article["slug"] == "finance-month-end-close" for article in payload["articles"])
    assert any(track["slug"] == "finance-operations-foundations" for track in payload["tracks"])


def test_get_help_article_by_slug_filters_to_accessible_modules():
    article = get_help_article_by_slug(
        accessible_modules=["finance"],
        roles=[],
        scopes=[],
        is_admin=False,
        slug="finance-month-end-close",
    )
    missing = get_help_article_by_slug(
        accessible_modules=["finance"],
        roles=[],
        scopes=[],
        is_admin=False,
        slug="support-ticket-triage",
    )

    assert article is not None
    assert article["module_key"] == "finance"
    assert missing is None


def test_search_help_articles_returns_module_filtered_results():
    results = search_help_articles(
        accessible_modules=["finance", "support"],
        roles=[],
        scopes=[],
        is_admin=False,
        query="ticket",
        module_key="support",
    )

    assert results["result_count"] > 0
    assert all(item["module_key"] == "support" for item in results["results"])


def test_build_help_module_hub_returns_article_listing():
    hub = build_help_module_hub(
        accessible_modules=["people"],
        roles=[],
        scopes=[],
        is_admin=False,
        module_key="people",
    )

    assert hub is not None
    assert hub["module_title"] == "People & HR"
    assert any(article["slug"] == "people-payroll-execution" for article in hub["module_articles"])


def test_get_help_track_by_slug_returns_track_with_steps():
    track = get_help_track_by_slug(
        accessible_modules=["finance"],
        roles=[],
        scopes=[],
        is_admin=False,
        slug="finance-operations-foundations",
    )

    assert track is not None
    assert track["steps"]
