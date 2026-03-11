"""
Tests for AP route permission guards.

Verifies that AP routes use granular permissions instead of the
broad require_finance_access gate. Tests that each route endpoint
has the correct permission dependency.
"""

from __future__ import annotations

import ast
from pathlib import Path


def _extract_route_permissions(filepath: str) -> dict[str, list[str]]:
    """Parse the AP routes file and extract permission strings from Depends() calls.

    Returns a dict mapping function_name -> list of permission strings found.
    """
    source = Path(filepath).read_text()
    tree = ast.parse(source)

    results: dict[str, list[str]] = {}

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # Walk the function's default args looking for Depends() calls
            perms: list[str] = []
            for default in node.args.defaults:
                _collect_permission_strings(default, perms)
            results[node.name] = perms

    return results


def _collect_permission_strings(node: ast.AST, perms: list[str]) -> None:
    """Recursively find string literals inside require_web_permission / require_any_web_permission calls."""
    if isinstance(node, ast.Call):
        func_name = ""
        if isinstance(node.func, ast.Name):
            func_name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            func_name = node.func.attr

        if func_name in ("require_web_permission", "require_any_web_permission"):
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    perms.append(arg.value)
                elif isinstance(arg, ast.List):
                    for elt in arg.elts:
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                            perms.append(elt.value)
        # Recurse into nested calls (e.g., Depends(require_web_permission(...)))
        for child in ast.iter_child_nodes(node):
            _collect_permission_strings(child, perms)
    elif isinstance(node, (ast.List, ast.Tuple)):
        for elt in node.elts:
            _collect_permission_strings(elt, perms)


AP_ROUTES_FILE = str(
    Path(__file__).resolve().parent.parent.parent / "app" / "web" / "finance" / "ap.py"
)


class TestAPRoutePermissionsExist:
    """Every AP route must have a granular permission, not require_finance_access."""

    def test_no_route_uses_require_finance_access(self) -> None:
        """No route should still use the broad require_finance_access guard."""
        source = Path(AP_ROUTES_FILE).read_text()
        assert "require_finance_access" not in source, (
            "AP routes should use granular permissions "
            "(require_web_permission / require_any_web_permission), "
            "not require_finance_access"
        )

    def test_all_routes_have_permission_guard(self) -> None:
        """Every route function must have at least one permission string."""
        route_perms = _extract_route_permissions(AP_ROUTES_FILE)

        # Filter to only actual route handlers (skip helper functions)
        source = Path(AP_ROUTES_FILE).read_text()
        tree = ast.parse(source)

        route_functions = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # Check if it has a @router decorator
                for decorator in node.decorator_list:
                    if isinstance(decorator, ast.Call) and isinstance(
                        decorator.func, ast.Attribute
                    ):
                        if isinstance(decorator.func.value, ast.Name):
                            if decorator.func.value.id == "router":
                                route_functions.add(node.name)

        for func_name in route_functions:
            perms = route_perms.get(func_name, [])
            assert len(perms) > 0, (
                f"Route '{func_name}' has no permission guard. "
                f"Expected require_web_permission or require_any_web_permission."
            )


class TestAPRoutePermissionMapping:
    """Verify specific routes use correct permissions."""

    def setup_method(self) -> None:
        self.route_perms = _extract_route_permissions(AP_ROUTES_FILE)

    # Suppliers
    def test_list_suppliers_requires_read(self) -> None:
        assert "ap:suppliers:read" in self.route_perms.get("list_suppliers", [])

    def test_create_supplier_requires_create(self) -> None:
        assert "ap:suppliers:create" in self.route_perms.get("create_supplier", [])

    def test_delete_supplier_requires_delete(self) -> None:
        assert "ap:suppliers:delete" in self.route_perms.get("delete_supplier", [])

    def test_edit_supplier_requires_update(self) -> None:
        assert "ap:suppliers:update" in self.route_perms.get("edit_supplier_form", [])

    # Invoices
    def test_list_invoices_requires_read(self) -> None:
        assert "ap:invoices:read" in self.route_perms.get("list_invoices", [])

    def test_create_invoice_requires_create(self) -> None:
        assert "ap:invoices:create" in self.route_perms.get("create_invoice", [])

    def test_submit_invoice_requires_submit(self) -> None:
        assert "ap:invoices:submit" in self.route_perms.get("submit_invoice", [])

    def test_approve_invoice_requires_approve(self) -> None:
        assert "ap:invoices:approve" in self.route_perms.get("approve_invoice", [])

    def test_post_invoice_requires_post(self) -> None:
        assert "ap:invoices:post" in self.route_perms.get("post_invoice", [])

    def test_void_invoice_requires_void(self) -> None:
        assert "ap:invoices:void" in self.route_perms.get("void_invoice", [])

    def test_bulk_approve_requires_approve(self) -> None:
        assert "ap:invoices:approve" in self.route_perms.get(
            "bulk_approve_invoices", []
        )

    def test_bulk_post_requires_post(self) -> None:
        assert "ap:invoices:post" in self.route_perms.get("bulk_post_invoices", [])

    # Payments
    def test_list_payments_requires_read(self) -> None:
        assert "ap:payments:read" in self.route_perms.get("list_payments", [])

    def test_create_payment_requires_create(self) -> None:
        assert "ap:payments:create" in self.route_perms.get("create_payment", [])

    def test_approve_payment_requires_tier_perms(self) -> None:
        perms = self.route_perms.get("approve_payment", [])
        assert "ap:payments:approve:tier1" in perms
        assert "ap:payments:approve:tier2" in perms
        assert "ap:payments:approve:tier3" in perms

    def test_post_payment_requires_post(self) -> None:
        assert "ap:payments:post" in self.route_perms.get("post_payment", [])

    def test_void_payment_requires_void(self) -> None:
        assert "ap:payments:void" in self.route_perms.get("void_payment", [])

    # Purchase Orders
    def test_list_pos_requires_read(self) -> None:
        assert "ap:purchase_orders:read" in self.route_perms.get(
            "list_purchase_orders", []
        )

    def test_create_po_requires_create(self) -> None:
        assert "ap:purchase_orders:create" in self.route_perms.get(
            "create_purchase_order", []
        )

    def test_submit_po_requires_submit(self) -> None:
        assert "ap:purchase_orders:submit" in self.route_perms.get(
            "submit_purchase_order", []
        )

    def test_approve_po_requires_approve(self) -> None:
        assert "ap:purchase_orders:approve" in self.route_perms.get(
            "approve_purchase_order", []
        )

    def test_cancel_po_requires_void(self) -> None:
        assert "ap:purchase_orders:void" in self.route_perms.get(
            "cancel_purchase_order", []
        )

    # Goods Receipts
    def test_list_grn_requires_read(self) -> None:
        assert "ap:goods_receipts:read" in self.route_perms.get(
            "list_goods_receipts", []
        )

    def test_create_grn_requires_create(self) -> None:
        assert "ap:goods_receipts:create" in self.route_perms.get(
            "create_goods_receipt", []
        )

    def test_accept_grn_requires_approve(self) -> None:
        assert "ap:goods_receipts:approve" in self.route_perms.get("accept_all", [])

    # Payment Batches
    def test_list_batches_requires_read(self) -> None:
        assert "ap:payment_batches:read" in self.route_perms.get(
            "list_payment_batches", []
        )

    # Aging
    def test_aging_requires_read(self) -> None:
        assert "ap:aging:read" in self.route_perms.get("aging_report", [])


class TestSoDIntegrity:
    """Verify Separation of Duties is maintained in permission assignments."""

    def setup_method(self) -> None:
        self.route_perms = _extract_route_permissions(AP_ROUTES_FILE)

    def test_approve_and_create_use_different_permissions(self) -> None:
        """Creator and approver must require different permissions (SoD)."""
        create_perms = set(self.route_perms.get("create_invoice", []))
        approve_perms = set(self.route_perms.get("approve_invoice", []))
        assert create_perms.isdisjoint(approve_perms), (
            "Invoice create and approve should require different permissions for SoD"
        )

    def test_po_create_and_approve_are_separate(self) -> None:
        """PO creator and approver must require different permissions."""
        create_perms = set(self.route_perms.get("create_purchase_order", []))
        approve_perms = set(self.route_perms.get("approve_purchase_order", []))
        assert create_perms.isdisjoint(approve_perms)

    def test_payment_create_and_approve_are_separate(self) -> None:
        """Payment creator and approver must require different permissions."""
        create_perms = set(self.route_perms.get("create_payment", []))
        approve_perms = set(self.route_perms.get("approve_payment", []))
        assert create_perms.isdisjoint(approve_perms)
