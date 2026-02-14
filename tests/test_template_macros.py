"""
Regression tests for shared Jinja UI macros.

These tests validate both:
- behavior for high-impact macros used across Finance list pages
- render-smoke coverage for most macros in templates/components/macros.html
"""

from __future__ import annotations

from app.templates import templates


def _render(snippet: str, **context) -> str:
    wrapped = "{% autoescape true %}" + snippet + "{% endautoescape %}"
    return templates.env.from_string(wrapped).render(**context)


def test_compact_filters_renders_expected_filter_inputs_and_hx_attrs():
    html = _render(
        """
{% from "components/macros.html" import compact_filters, filter_select_field %}
{% call(filter_attrs) compact_filters(
    base_url="/finance/ar/invoices",
    active_filters=[],
    show_search=true,
    search="INV",
    date_range=true,
    start_date="2026-01-01",
    end_date="2026-01-31"
) %}
{{ filter_select_field("status", "Status", "POSTED", [{"value": "POSTED", "label": "Posted"}], filter_attrs) }}
{% endcall %}
"""
    )

    assert 'name="search"' in html
    assert 'name="start_date"' in html
    assert 'name="end_date"' in html
    assert 'name="status"' in html
    assert 'hx-get="/finance/ar/invoices"' in html
    assert 'hx-target="#results-container"' in html


def test_compact_filters_renders_active_filter_chips_and_clear_link():
    html = _render(
        """
{% from "components/macros.html" import compact_filters %}
{{ compact_filters(
    base_url="/finance/ar/receipts",
    active_filters=[{"name": "status", "value": "POSTED", "display_value": "Status: Posted"}]
) }}
"""
    )

    assert "Status: Posted" in html
    assert 'data-chip-remove="status"' in html
    assert 'href="/finance/ar/receipts"' in html
    assert "Clear all" in html


def test_pagination_preserves_search_and_filter_query_params():
    html = _render(
        """
{% from "components/macros.html" import pagination %}
{{ pagination(
    page=2,
    total_pages=4,
    total_count=80,
    limit=20,
    search="acme",
    filters={"status": "POSTED", "customer_id": "c-001"}
) }}
"""
    )

    assert "Showing" in html
    assert "of" in html
    assert "search=acme" in html
    assert "status=POSTED" in html
    assert "customer_id=c-001" in html
    assert "page=1" in html
    assert "page=3" in html


def test_status_badge_and_empty_state_render_expected_content():
    html = _render(
        """
{% from "components/macros.html" import status_badge, empty_state %}
{{ status_badge("PARTIALLY_PAID") }}
{{ empty_state("No Invoices", "Create one", "document", "New Invoice", "/finance/ar/invoices/new") }}
"""
    )

    assert "Partially Paid" in html
    assert "badge-pending" in html
    assert "No Invoices" in html
    assert "/finance/ar/invoices/new" in html


def test_action_buttons_and_header_actions_render_links():
    html = _render(
        """
{% from "components/macros.html" import action_buttons, list_header_actions %}
{{ list_header_actions("/finance/ar/invoices/export", "/finance/ar/invoices/new", "New Invoice") }}
{{ action_buttons(
    view_href="/finance/ar/invoices/1",
    edit_href="/finance/ar/invoices/1/edit",
    extra_actions=[{"href": "/finance/ar/receipts/new?invoice_id=1", "title": "Record Payment", "icon": "credit-card"}]
) }}
"""
    )

    assert "exportAll('/finance/ar/invoices/export')" in html
    assert 'href="/finance/ar/invoices/new"' in html
    assert 'href="/finance/ar/invoices/1"' in html
    assert 'href="/finance/ar/invoices/1/edit"' in html
    assert "Record Payment" in html


def test_bulk_macros_render_expected_markers():
    html = _render(
        """
{% from "components/macros.html" import bulk_select_header, bulk_select_cell, bulk_action_bar, bulk_icon_delete %}
<table><thead><tr>{{ bulk_select_header() }}</tr></thead><tbody><tr>{{ bulk_select_cell("abc-1") }}</tr></tbody></table>
{{ bulk_action_bar(
    actions=[{"name": "delete", "label": "Delete", "endpoint": "/finance/ar/invoices/bulk-delete", "icon": bulk_icon_delete()}],
    entity_name="invoices"
) }}
"""
    )

    assert "bulk-checkbox" in html
    assert 'data-bulk-id="abc-1"' in html
    assert "bulk-action-bar" in html
    assert "/finance/ar/invoices/bulk-delete" in html


def test_macro_render_smoke_for_broad_component_set():
    snippets = [
        """{% from "components/macros.html" import stats_card %}{{ stats_card("Total", "100", icon="chart", color="teal") }}""",
        """{% from "components/macros.html" import aging_bar %}{{ aging_bar(10, 20, 30, 40, show_legend=true) }}""",
        """{% from "components/macros.html" import progress_bar %}{{ progress_bar("Progress", "40/100", 40, color="amber") }}""",
        """{% from "components/macros.html" import section_card %}{% call section_card("Invoices", "Open items", "/finance/ar/invoices") %}<p>Body</p>{% endcall %}""",
        """{% from "components/macros.html" import sparkline %}{{ sparkline([1,2,3,4], color="blue") }}""",
        """{% from "components/macros.html" import icon_svg %}{{ icon_svg("document") }}""",
        """{% from "components/macros.html" import chart_canvas %}{{ chart_canvas("bar", {"labels":["A"],"datasets":[]}, "chart-1") }}""",
        """{% from "components/macros.html" import search_autosuggest %}{{ search_autosuggest("abc", "customer", [], "/finance/ar/customers") }}""",
        """{% from "components/macros.html" import search_filter_bar %}{{ search_filter_bar("abc", [], "/finance/ap/suppliers") }}""",
        """{% from "components/macros.html" import live_search %}{{ live_search("abc", [], "/finance/gl/accounts") }}""",
        """{% from "components/macros.html" import filter_entity_select_field %}{{ filter_entity_select_field("customer_id","Customer","1",[{"id":"1","name":"Acme"}],"id","name","") }}""",
        """{% from "components/macros.html" import filter_custom_select_field %}{% call filter_custom_select_field("kind","Kind") %}<option value="A">A</option>{% endcall %}""",
        """{% from "components/macros.html" import filter_date_field %}{{ filter_date_field("as_of_date","As Of","2026-02-14") }}""",
        """{% from "components/macros.html" import data_table %}{% call data_table(["Code","Name"]) %}<tr><td>A</td><td>Acme</td></tr>{% endcall %}""",
        """{% from "components/macros.html" import currency %}{{ currency(1234.5, "USD ") }}""",
        """{% from "components/macros.html" import bulk_select_table %}{% call bulk_select_table("items", []) %}<table><tbody><tr><td>R</td></tr></tbody></table>{% endcall %}""",
        """{% from "components/macros.html" import bulk_icon_export, bulk_icon_activate, bulk_icon_deactivate, bulk_icon_archive %}{{ bulk_icon_export() }}{{ bulk_icon_activate() }}{{ bulk_icon_deactivate() }}{{ bulk_icon_archive() }}""",
        """{% from "components/macros.html" import topbar %}{{ topbar("<h1>Finance</h1>", "<a href='/dashboard'>Dashboard</a>", "<a href='/finance/ar/invoices/new'>New</a>") }}""",
        """{% from "components/macros.html" import success_banner, error_banner %}{{ success_banner("Saved") }}{{ error_banner("Failed") }}""",
    ]

    for snippet in snippets:
        html = _render(snippet)
        assert html.strip()


def test_topbar_renders_back_button_with_fallback():
    html = _render(
        """
{% from "components/macros.html" import topbar %}
{{ topbar("Title", "<nav>Crumbs</nav>", "<button>Action</button>", accent="teal", back_fallback="/finance/dashboard") }}
"""
    )

    assert 'aria-label="Go back"' in html
    assert "Back</span>" in html
    assert "window.location.href='/finance/dashboard'" in html


def test_topbar_hides_back_button_when_fallback_is_blank():
    html = _render(
        """
{% from "components/macros.html" import topbar %}
{{ topbar("Title", "", "", accent="teal", back_fallback="") }}
"""
    )

    assert 'aria-label="Go back"' not in html


def test_compact_filters_escapes_untrusted_search_and_filter_values():
    html = _render(
        """
{% from "components/macros.html" import compact_filters %}
{{ compact_filters(
    base_url="/finance/ar/invoices",
    show_search=true,
    search="<script>alert(1)</script>",
    active_filters=[{"name":"search","value":"<x>","display_value":"Search: <x>"}]
) }}
"""
    )

    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "Search: &lt;x&gt;" in html


def test_filter_select_and_entity_helpers_handle_placeholder_selection_and_titleize():
    html = _render(
        """
{% from "components/macros.html" import filter_select_field, filter_entity_select_field %}
{{ filter_select_field("status", "Status", "IN_PROGRESS", ["IN_PROGRESS", "CLOSED"], "", none, true) }}
{{ filter_entity_select_field("customer_id", "Customer", "2", [{"id":"1","name":"Acme"},{"id":"2","name":"Zen"}], "id", "name", "", "Any Customer") }}
"""
    )

    assert "All Status" in html
    assert "In Progress" in html
    assert '<option value="IN_PROGRESS" selected>' in html
    assert "Any Customer" in html
    assert '<option value="2" selected>' in html


def test_action_buttons_escape_text_and_skip_actions_without_href():
    html = _render(
        """
{% from "components/macros.html" import action_buttons %}
{{ action_buttons(
  view_href="/a",
  extra_actions=[
    {"title":"<b>Unsafe</b>", "aria_label":"<img src=x onerror=1>"},
    {"href":"/safe", "title":"Safe <ok>", "aria_label":"Read <ok>", "icon":"document"}
  ]
) }}
"""
    )

    assert '<a href="/safe"' in html
    assert "&lt;ok&gt;" in html
    assert "<img src=x onerror=1>" not in html
    # Action without href should not render as a link.
    assert "&lt;b&gt;Unsafe&lt;/b&gt;" not in html


def test_pagination_renders_nothing_for_empty_results():
    html = _render(
        """
{% from "components/macros.html" import pagination %}
{{ pagination(page=1, total_pages=1, total_count=0, limit=50, search="", filters={}) }}
"""
    )

    assert html.strip() == ""


def test_bulk_macros_include_accessibility_attributes():
    html = _render(
        """
{% from "components/macros.html" import bulk_select_header, bulk_select_cell, bulk_action_bar %}
<table><thead><tr>{{ bulk_select_header() }}</tr></thead><tbody><tr>{{ bulk_select_cell("id-1") }}</tr></tbody></table>
{{ bulk_action_bar(actions=[{"name":"export","label":"Export","endpoint":"/bulk/export"}], entity_name="rows") }}
"""
    )

    assert 'aria-label="Select all rows"' in html
    assert 'aria-label="Select row"' in html
    assert 'title="Clear selection"' in html


def test_status_badge_unknown_status_falls_back_to_draft_style():
    html = _render(
        """
{% from "components/macros.html" import status_badge %}
{{ status_badge("NOT_A_REAL_STATUS") }}
"""
    )

    assert "badge-draft" in html
    assert "Not A Real Status" in html
