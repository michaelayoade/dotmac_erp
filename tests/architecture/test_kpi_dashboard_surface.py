"""Static surface guards. Runtime CSRF and PostgreSQL checks remain required."""

import ast
from pathlib import Path
import re
from jinja2 import Environment

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "templates/people/perf/kpi_dashboard.html"
ROUTE = ROOT / "app/web/people/kpi_dashboard.py"


def test_route_keeps_people_and_private_mode_guards():
    source = ROUTE.read_text()
    tree = ast.parse(source)
    assert 'prefix="/perf/kpi-dashboard"' in source
    assert "Depends(require_private_performance_mode)" in source
    functions = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
    assert len(functions) == 3
    for function in functions:
        code = ast.unparse(function)
        assert "Depends(require_hr_access)" in code
        assert "Depends(get_db_for_org)" in code
        assert "select(" not in code
        assert "db.add(" not in code


def test_template_compiles_and_post_forms_have_csrf_without_get_leaks():
    source = TEMPLATE.read_text()
    Environment().parse(source)
    forms = re.findall(r'<form\s+method="(get|post)".*?</form>', source, re.DOTALL)
    blocks = re.findall(r'<form\s+method="(?:get|post)".*?</form>', source, re.DOTALL)
    assert len(forms) == len(blocks) == 4
    for method, block in zip(forms, blocks, strict=True):
        if method == "post":
            assert "request.state.csrf_form | safe" in block
        else:
            assert "csrf_form" not in block
    assert source.count("| safe") == 2
    assert "localStorage" not in source
    assert "eval(" not in source


def test_people_navigation_and_router_are_registered():
    assert "router.include_router(kpi_dashboard_router)" in (ROOT / "app/web/people/__init__.py").read_text()
    assert '/people/perf/kpi-dashboard' in (ROOT / "templates/people/perf/index.html").read_text()


def test_no_business_writes_or_commits_in_dashboard_service():
    source = (ROOT / "app/services/people/perf/kpi_dashboard_service.py").read_text()
    tree = ast.parse(source)
    assert "db.commit(" not in source
    assert "db.add(" not in source
    assert "KPI.actual_value =" not in source
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith("app.web")
