from pathlib import Path


PAGE_TEMPLATE = Path("templates/people/hr/employees.html")
CHART_TEMPLATE = Path("templates/people/hr/_employee_movement_chart.html")
CHART_SCRIPT = Path("static/js/charts.js")


def test_employee_page_contains_isolated_movement_chart_states():
    source = PAGE_TEMPLATE.read_text(encoding="utf-8")

    assert "Employee Movement" in source
    assert "Monthly onboarding and offboarding activity" in source
    assert 'hx-get="/people/hr/employees/movement-chart"' in source
    assert 'hx-target="#employee-movement-body"' in source
    assert "Last 6 months" in source
    assert "Last 12 months" in source
    assert "This year" in source
    assert "Previous year" in source
    assert "employee-movement-loading" in source
    assert "employee-movement-error" in source
    assert "htmx:responseError" in source


def test_employee_movement_chart_is_accessible_and_uses_both_series():
    source = CHART_TEMPLATE.read_text(encoding="utf-8")

    assert 'data-chart="groupedBar"' in source
    assert '"label": "Onboarded"' in source
    assert '"label": "Offboarded"' in source
    assert '"format": "number"' in source
    assert 'role="img"' in source
    assert '<table class="sr-only">' in source
    assert 'scope="col"' in source
    assert "No employee movement data available for this period." in source


def test_grouped_bar_supports_integer_counts_and_compact_month_labels():
    source = CHART_SCRIPT.read_text(encoding="utf-8")

    assert "integer, maxTicksLimit, indexedTooltip" in source
    assert "precision: integer ? 0 : undefined" in source
    assert "maxTicksLimit: maxTicksLimit || undefined" in source
    assert "interaction: { mode: 'index', intersect: false }" in source
