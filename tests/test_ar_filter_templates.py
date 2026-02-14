from pathlib import Path


def _read_template(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_sales_orders_results_container_wraps_full_results_region():
    html = _read_template("templates/finance/ar/sales_orders.html")

    assert html.count('id="results-container"') == 1
    assert html.index('id="results-container"') < html.index("<!-- Status Summary -->")
    assert html.index('id="results-container"') < html.index("<!-- Orders List -->")


def test_aging_results_container_wraps_full_filtered_content():
    html = _read_template("templates/finance/ar/aging.html")

    assert html.count('id="results-container"') == 1
    assert html.index('id="results-container"') < html.index("{% if aging_summary %}")
