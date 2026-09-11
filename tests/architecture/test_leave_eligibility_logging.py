from __future__ import annotations

import ast
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[2] / "app/services/people/leave/web.py"


def test_expected_leave_ineligibility_is_not_logged_as_an_exception() -> None:
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    method = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "create_application_response"
    )
    handler = next(
        node
        for node in ast.walk(method)
        if isinstance(node, ast.ExceptHandler)
        and isinstance(node.type, ast.Name)
        and node.type.id == "LeaveEligibilityError"
    )
    log_methods = {
        node.func.attr
        for node in ast.walk(handler)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "logger"
    }

    assert "warning" in log_methods
    assert "error" not in log_methods
    assert "exception" not in log_methods
