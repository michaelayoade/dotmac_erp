"""Ensure the report is reachable only through the inventory module gate."""

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest
from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader

ROOT = Path(__file__).resolve().parents[1]


def test_report_router_is_mounted_once_inside_inventory_enablement_gate():
    tree = ast.parse((ROOT / "app/main.py").read_text())
    imports = [
        node
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        and node.module == "app.web.inventory_weekly_purchases"
    ]
    assert len(imports) == 1
    alias = imports[0].names[0].asname
    mounts = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "include_router"
        and node.args
        and isinstance(node.args[0], ast.Name)
        and node.args[0].id == alias
    ]
    assert len(mounts) == 1
    guards = [
        node
        for node in tree.body
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Call)
        and isinstance(node.test.func, ast.Name)
        and node.test.func.id == "is_module_enabled"
        and node.test.args
        and isinstance(node.test.args[0], ast.Constant)
        and node.test.args[0].value == "inventory"
    ]
    assert len(guards) == 1
    assert mounts[0] in list(ast.walk(guards[0]))


@pytest.mark.parametrize("allowed", [False, True])
def test_reports_hub_card_respects_stock_read_permission(allowed):
    hub = (ROOT / "templates/inventory/reports.html").read_text()
    assert '{% include "inventory/_weekly_purchases_card.html" %}' in hub
    env = Environment(
        loader=ChoiceLoader(
            [
                DictLoader(
                    {
                        "components/macros.html": (
                            "{% macro icon_svg(name, classes='') %}{% endmacro %}"
                        )
                    }
                ),
                FileSystemLoader(str(ROOT / "templates")),
            ]
        ),
        autoescape=True,
    )
    user = SimpleNamespace(
        has_permission=lambda permission: (
            allowed and permission == "inventory:stock:read"
        )
    )
    html = env.get_template("inventory/_weekly_purchases_card.html").render(auth=user)
    assert ("/inventory/reports/weekly-purchases" in html) is allowed
