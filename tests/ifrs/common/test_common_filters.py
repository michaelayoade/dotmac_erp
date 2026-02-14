from app.services.common_filters import build_active_filters


def test_build_active_filters_skips_empty_and_keeps_false_zero():
    filters = build_active_filters(
        params={
            "status": "open",
            "is_primary": False,
            "page": 0,
            "empty": "",
            "none": None,
        },
        labels={
            "status": "Status",
            "is_primary": "Primary",
            "page": "Page",
        },
        options={"status": {"open": "Open Items"}},
    )

    assert filters == [
        {"name": "status", "value": "open", "display_value": "Status: Open Items"},
        {"name": "is_primary", "value": "False", "display_value": "Primary: False"},
        {"name": "page", "value": "0", "display_value": "Page: 0"},
    ]


def test_build_active_filters_title_cases_when_no_option_match():
    filters = build_active_filters(
        params={"workflow_state": "in_progress"},
    )

    assert filters == [
        {
            "name": "workflow_state",
            "value": "in_progress",
            "display_value": "In Progress",
        }
    ]


def test_build_active_filters_uses_option_lookup_without_label_prefix():
    filters = build_active_filters(
        params={"customer_id": "c-001"},
        options={"customer_id": {"c-001": "Acme Corp"}},
    )

    assert filters == [
        {
            "name": "customer_id",
            "value": "c-001",
            "display_value": "Acme Corp",
        }
    ]
