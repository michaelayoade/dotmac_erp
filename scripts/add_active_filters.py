"""
Add active_filters to web service list context methods.

Patches web service files so they return `active_filters` for the
compact_filters macro to render chips and count badges.

Usage:
    python scripts/add_active_filters.py --dry-run   # Preview
    python scripts/add_active_filters.py --execute    # Apply
"""

from __future__ import annotations

import argparse
import re
import sys

# ── Configuration ─────────────────────────────────────────────────────
# Each entry defines how to patch a specific method:
#   file: path relative to project root
#   method: method name to find
#   params: Python expression for build_active_filters(params=...)
#   labels: optional dict of param → human-readable label prefix
#   options: optional Python expression for options= argument

PATCHES: list[dict] = [
    # ── Finance / AR ──────────────────────────────────────────────────
    {
        "file": "app/services/finance/ar/web.py",
        "method": "list_customers_context",
        "params": '{"status": status}',
    },
    {
        "file": "app/services/finance/ar/web.py",
        "method": "list_invoices_context",
        "params": '{"status": status, "customer_id": customer_id, "start_date": start_date, "end_date": end_date}',
        "labels": {"start_date": "From", "end_date": "To"},
        "options": '{"customer_id": {str(c["id"]): c["name"] for c in customers_list}}',
    },
    {
        "file": "app/services/finance/ar/web.py",
        "method": "list_receipts_context",
        "params": '{"status": status, "customer_id": customer_id, "start_date": start_date, "end_date": end_date}',
        "labels": {"start_date": "From", "end_date": "To"},
    },
    {
        "file": "app/services/finance/ar/web/quote_web.py",
        "method": "list_context",
        "params": '{"status": status, "customer_id": customer_id, "start_date": start_date, "end_date": end_date}',
        "labels": {"start_date": "From", "end_date": "To"},
        "options": '{"customer_id": {str(c.id): c.name for c in customers}}',
    },
    {
        "file": "app/services/finance/ar/web/sales_order_web.py",
        "method": "list_context",
        "params": '{"status": status, "customer_id": customer_id, "start_date": start_date, "end_date": end_date}',
        "labels": {"start_date": "From", "end_date": "To"},
        "options": '{"customer_id": {str(c.id): c.name for c in customers}}',
    },
    # ── Finance / AP ──────────────────────────────────────────────────
    {
        "file": "app/services/finance/ap/web.py",
        "method": "list_suppliers_context",
        "params": '{"status": status}',
    },
    {
        "file": "app/services/finance/ap/web.py",
        "method": "list_invoices_context",
        "params": '{"status": status, "supplier_id": supplier_id, "start_date": start_date, "end_date": end_date}',
        "labels": {"start_date": "From", "end_date": "To"},
        "options": '{"supplier_id": {str(s["supplier_id"]): s["supplier_name"] for s in suppliers_list}}',
    },
    {
        "file": "app/services/finance/ap/web.py",
        "method": "list_payments_context",
        "params": '{"status": status, "supplier_id": supplier_id, "start_date": start_date, "end_date": end_date}',
        "labels": {"start_date": "From", "end_date": "To"},
        "options": '{"supplier_id": {str(s["supplier_id"]): s["supplier_name"] for s in suppliers_list}}',
    },
    {
        "file": "app/services/finance/ap/web.py",
        "method": "list_purchase_orders_context",
        "params": '{"status": status, "supplier_id": supplier_id, "start_date": start_date, "end_date": end_date}',
        "labels": {"start_date": "From", "end_date": "To"},
    },
    {
        "file": "app/services/finance/ap/web.py",
        "method": "list_goods_receipts_context",
        "params": '{"status": status, "supplier_id": supplier_id, "start_date": start_date, "end_date": end_date}',
        "labels": {"start_date": "From", "end_date": "To"},
    },
    # ── Finance / Automation ──────────────────────────────────────────
    {
        "file": "app/services/finance/automation/web.py",
        "method": "list_recurring_context",
        "params": '{"entity_type": entity_type, "status": status}',
    },
    {
        "file": "app/services/finance/automation/web.py",
        "method": "list_workflows_context",
        "params": '{"entity_type": entity_type, "status": status}',
    },
    {
        "file": "app/services/finance/automation/web.py",
        "method": "list_custom_fields_context",
        "params": '{"entity_type": entity_type}',
    },
    {
        "file": "app/services/finance/automation/web.py",
        "method": "list_templates_context",
        "params": '{"entity_type": entity_type}',
    },
    # ── Finance / Expenditure ─────────────────────────────────────────
    {
        "file": "app/services/finance/exp/web.py",
        "method": "list_context",
        "params": '{"status": status, "start_date": start_date, "end_date": end_date}',
        "labels": {"start_date": "From", "end_date": "To"},
    },
    # ── Finance / Tax ─────────────────────────────────────────────────
    {
        "file": "app/services/finance/tax/web.py",
        "method": "list_tax_periods_response",
        "params": '{"status": status}',
    },
    {
        "file": "app/services/finance/tax/web.py",
        "method": "list_tax_returns_response",
        "params": '{"status": status, "tax_period_id": tax_period_id}',
    },
    # ── Finance / Reports ─────────────────────────────────────────────
    {
        "file": "app/services/finance/rpt/web.py",
        "method": "ap_aging_response",
        "params": '{"as_of_date": as_of_date}',
        "labels": {"as_of_date": "As Of"},
    },
    {
        "file": "app/services/finance/rpt/web.py",
        "method": "ar_aging_response",
        "params": '{"as_of_date": as_of_date}',
        "labels": {"as_of_date": "As Of"},
    },
    # ── Fleet ─────────────────────────────────────────────────────────
    {
        "file": "app/services/fleet/web/fleet_web.py",
        "method": "maintenance_list_context",
        "params": '{"status": status, "maintenance_type": maintenance_type, "vehicle_id": str(vehicle_id) if vehicle_id else None}',
    },
    {
        "file": "app/services/fleet/web/fleet_web.py",
        "method": "fuel_list_context",
        "params": '{"vehicle_id": str(vehicle_id) if vehicle_id else None}',
    },
    {
        "file": "app/services/fleet/web/fleet_web.py",
        "method": "document_list_context",
        "params": '{"vehicle_id": str(vehicle_id) if vehicle_id else None, "document_type": document_type}',
    },
    {
        "file": "app/services/fleet/web/fleet_web.py",
        "method": "incident_list_context",
        "params": '{"vehicle_id": str(vehicle_id) if vehicle_id else None, "status": status, "severity": severity}',
    },
    {
        "file": "app/services/fleet/web/fleet_web.py",
        "method": "reservation_list_context",
        "params": '{"vehicle_id": str(vehicle_id) if vehicle_id else None, "status": status}',
    },
    # ── Expense ───────────────────────────────────────────────────────
    {
        "file": "app/services/expense/web.py",
        "method": "claims_list_response",
        "params": '{"status": status, "view": view, "start_date": start_date, "end_date": end_date}',
        "labels": {"start_date": "From", "end_date": "To"},
    },
    {
        "file": "app/services/expense/web.py",
        "method": "cash_advances_list_response",
        "params": '{"status": status}',
    },
    {
        "file": "app/services/expense/limit_web.py",
        "method": "list_rules_response",
        "params": '{"scope_type": scope_type, "is_active": is_active}',
    },
    {
        "file": "app/services/expense/limit_web.py",
        "method": "evaluations_list_response",
        "params": '{"status": status}',
    },
    # ── People / HR ───────────────────────────────────────────────────
    {
        "file": "app/services/people/hr/web/employee_web.py",
        "method": "list_employees_response",
        "params": '{"status": status, "department_id": department_id, "designation_id": designation_id}',
    },
    {
        "file": "app/services/people/hr/web/lifecycle_web.py",
        "method": "list_transfers_response",
        "params": '{"status": status}',
    },
    {
        "file": "app/services/people/hr/web/lifecycle_web.py",
        "method": "list_promotions_response",
        "params": '{"status": status}',
    },
    # ── People / Leave ────────────────────────────────────────────────
    {
        "file": "app/services/people/leave/web.py",
        "method": "leave_applications_response",
        "params": '{"status": status, "employee_id": employee_id, "leave_type_id": leave_type_id, "start_date": start_date, "end_date": end_date}',
        "labels": {"start_date": "From", "end_date": "To"},
    },
    # ── People / Attendance ───────────────────────────────────────────
    {
        "file": "app/services/people/attendance/web.py",
        "method": "attendance_requests_list_response",
        "params": '{"status": status, "start_date": start_date, "end_date": end_date}',
        "labels": {"start_date": "From", "end_date": "To"},
    },
    # ── People / Performance ──────────────────────────────────────────
    {
        "file": "app/services/people/perf/web/perf_web.py",
        "method": "list_appraisals_response",
        "params": '{"status": status, "cycle_id": cycle_id}',
    },
    # ── People / Recruitment ──────────────────────────────────────────
    {
        "file": "app/services/people/recruit/web/interview_web.py",
        "method": "list_interviews_context",
        "params": '{"status": status, "job_opening_id": job_opening_id, "applicant_id": applicant_id, "start_date": start_date, "end_date": end_date}',
        "labels": {"start_date": "From", "end_date": "To"},
    },
    {
        "file": "app/services/people/recruit/web/offer_web.py",
        "method": "list_offers_context",
        "params": '{"status": status, "job_opening_id": job_opening_id, "applicant_id": applicant_id}',
    },
    {
        "file": "app/services/people/recruit/web/report_web.py",
        "method": "pipeline_report_context",
        "params": '{"start_date": start_date, "end_date": end_date}',
        "labels": {"start_date": "From", "end_date": "To"},
    },
    # ── People / Scheduling ───────────────────────────────────────────
    {
        "file": "app/services/people/scheduling/web.py",
        "method": "swap_requests_list_response",
        "params": '{"status": status}',
    },
    # ── People / Self-Service ─────────────────────────────────────────
    {
        "file": "app/services/people/self_service_web.py",
        "method": "payslips_response",
        "params": '{"year": str(year) if year else None}',
        "labels": {"year": "Year"},
    },
    # ── People / Discipline ───────────────────────────────────────────
    {
        "file": "app/services/people/discipline/web/discipline_web.py",
        "method": "list_cases_response",
        "params": '{"status": status}',
    },
    # ── Admin / Sync ──────────────────────────────────────────────────
    {
        "file": "app/services/admin/sync_web.py",
        "method": "entities_response",
        "params": '{"entity_type": entity_type, "status": status}',
    },
    {
        "file": "app/services/admin/sync_web.py",
        "method": "history_response",
        "params": '{"entity_type": entity_type, "status": status}',
    },
    # ── Procurement ───────────────────────────────────────────────────
    {
        "file": "app/services/procurement/web/procurement_web.py",
        "method": "plan_list_context",
        "params": '{"status": status, "fiscal_year": fiscal_year}',
    },
    {
        "file": "app/services/procurement/web/procurement_web.py",
        "method": "requisition_list_context",
        "params": '{"status": status, "urgency": urgency}',
    },
    {
        "file": "app/services/procurement/web/procurement_web.py",
        "method": "rfq_list_context",
        "params": '{"status": status}',
    },
    {
        "file": "app/services/procurement/web/procurement_web.py",
        "method": "evaluation_list_context",
        "params": '{"status": status}',
    },
    {
        "file": "app/services/procurement/web/procurement_web.py",
        "method": "contract_list_context",
        "params": '{"status": status}',
    },
    {
        "file": "app/services/procurement/web/procurement_web.py",
        "method": "vendor_list_context",
        "params": '{"status": status}',
    },
    # ── Support ───────────────────────────────────────────────────────
    {
        "file": "app/services/support/web.py",
        "method": "breached_tickets_response",
        "params": '{"breach_type": breach_type}',
    },
    # ── Inventory ─────────────────────────────────────────────────────
    {
        "file": "app/services/inventory/material_request_web.py",
        "method": "list_context",
        "params": '{"status": status}',
    },
]

IMPORT_LINE = "from app.services.common_filters import build_active_filters"


def add_import(lines: list[str]) -> list[str]:
    """Add the import line if not already present."""
    for line in lines:
        if "from app.services.common_filters import" in line:
            return lines

    # Find the right place to insert — after the last import
    last_import_idx = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("import ") or stripped.startswith("from "):
            last_import_idx = i
        elif (
            stripped.startswith("class ")
            or stripped.startswith("def ")
            or stripped.startswith("logger")
            or stripped.startswith("Logger")
        ):
            break

    # Insert after last import, with blank line if needed
    insert_idx = last_import_idx + 1
    lines.insert(insert_idx, IMPORT_LINE + "\n")

    return lines


def find_method_bounds(lines: list[str], method_name: str) -> tuple[int, int] | None:
    """Find method start line and end line (exclusive).

    Returns (method_start, method_end) or None.
    """
    method_start = None
    method_indent = ""

    for i, line in enumerate(lines):
        if re.search(rf"\bdef {re.escape(method_name)}\s*\(", line):
            method_start = i
            m = re.match(r"^(\s*)", line)
            method_indent = m.group(1) if m else ""
            break

    if method_start is None:
        return None

    method_end = len(lines)
    for i in range(method_start + 1, len(lines)):
        line = lines[i]
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            line_indent = len(line) - len(line.lstrip())
            if line_indent <= len(method_indent) and (
                stripped.startswith("def ")
                or stripped.startswith("class ")
                or stripped.startswith("@")
            ):
                method_end = i
                break

    return (method_start, method_end)


def find_insertion_point(
    lines: list[str], method_start: int, method_end: int
) -> tuple[int, int, str] | None:
    """Find where to insert active_filters code and dict entry.

    Returns (call_insert_line, dict_start_line, pattern) or None.
    pattern is "return_dict" or "context_update".
    """
    # Find the last return statement in the method
    last_return = None
    for i in range(method_start + 1, method_end):
        stripped = lines[i].strip()
        if stripped.startswith("return ") or stripped == "return":
            last_return = i

    if last_return is None:
        return None

    return_line_text = lines[last_return].strip()

    # Pattern 1: return { ... }
    if "return {" in return_line_text or "return{" in return_line_text:
        return (last_return, last_return, "return_dict")

    # Pattern 2: context.update({...}) or context = {...} before return TemplateResponse
    if "TemplateResponse" in return_line_text or "return templates" in return_line_text:
        # Look backward for context.update({ or context = {
        for i in range(last_return - 1, max(method_start, last_return - 40), -1):
            stripped = lines[i].strip()
            # context.update({ on same line
            if ".update(" in stripped and "{" in stripped:
                return (i, i, "context_update")
            # context.update(\n    {  split across lines
            if ".update(" in stripped:
                if i + 1 < len(lines) and lines[i + 1].strip().startswith("{"):
                    return (i, i + 1, "context_update")
            # context = { ... }  (dict assignment)
            if re.match(r"context\s*=\s*\{", stripped):
                return (i, i, "context_assign")

    return None


def build_active_filters_call(patch: dict, indent: str) -> list[str]:
    """Build the active_filters = build_active_filters(...) code lines."""
    params_code = patch["params"]
    labels = patch.get("labels", {})
    options_code = patch.get("options")

    result = []
    if labels or options_code:
        result.append(f"{indent}active_filters = build_active_filters(\n")
        result.append(f"{indent}    params={params_code},\n")
        if labels:
            labels_str = repr(labels)
            result.append(f"{indent}    labels={labels_str},\n")
        if options_code:
            result.append(f"{indent}    options={options_code},\n")
        result.append(f"{indent})\n")
    else:
        result.append(
            f"{indent}active_filters = build_active_filters(params={params_code})\n"
        )

    return result


def add_to_dict(lines: list[str], dict_start: int) -> list[str]:
    """Add 'active_filters': active_filters to a dict literal starting at dict_start.

    dict_start should point to the line containing the opening {.
    """
    # Check if already present in the dict
    depth = 0
    for i in range(dict_start, min(dict_start + 100, len(lines))):
        if '"active_filters"' in lines[i] or "'active_filters'" in lines[i]:
            return lines
        depth += lines[i].count("{") - lines[i].count("}")
        if depth <= 0:
            break

    # Find appropriate indent for dict entries — look for first "key": line
    entry_indent = ""
    for i in range(dict_start, min(dict_start + 8, len(lines))):
        m = re.match(r'^(\s+)"', lines[i])
        if m:
            entry_indent = m.group(1)
            break

    if not entry_indent:
        dict_line = lines[dict_start]
        base_indent = len(dict_line) - len(dict_line.lstrip())
        entry_indent = " " * (base_indent + 4)

    # Find the closing } of the dict
    depth = 0
    dict_end = None
    for i in range(dict_start, min(dict_start + 100, len(lines))):
        depth += lines[i].count("{") - lines[i].count("}")
        if depth <= 0:
            dict_end = i
            break

    if dict_end is None:
        return lines

    # Ensure trailing comma on previous entry
    prev_idx = dict_end - 1
    prev_line = lines[prev_idx].rstrip()
    if (
        prev_line
        and not prev_line.endswith(",")
        and not prev_line.strip().startswith("#")
        and not prev_line.strip().startswith("{")
    ):
        lines[prev_idx] = prev_line + ",\n"

    insert_line = f'{entry_indent}"active_filters": active_filters,\n'
    lines.insert(dict_end, insert_line)

    return lines


def patch_file(filepath: str, patches: list[dict], dry_run: bool = True) -> int:
    """Patch a single file with multiple method patches."""
    try:
        with open(filepath) as f:
            content = f.read()
    except FileNotFoundError:
        print(f"  SKIP (not found): {filepath}")
        return 0

    lines = content.splitlines(keepends=True)
    patched = 0

    # Add import first
    if IMPORT_LINE not in content:
        lines = add_import(lines)

    # Collect all method patches with their insertion points
    method_patches = []
    for patch in patches:
        bounds = find_method_bounds(lines, patch["method"])
        if bounds is None:
            print(f"  WARN: method {patch['method']} not found in {filepath}")
            continue
        method_start, method_end = bounds

        # Check if already patched
        already_has = False
        for i in range(method_start, method_end):
            if "active_filters = build_active_filters" in lines[i]:
                already_has = True
                break
        if already_has:
            continue

        insertion = find_insertion_point(lines, method_start, method_end)
        if insertion is None:
            print(f"  WARN: no insertion point for {patch['method']} in {filepath}")
            continue

        call_insert, dict_start, pattern = insertion
        method_patches.append((call_insert, dict_start, pattern, patch))

    # Sort by line number descending to avoid offset issues
    method_patches.sort(key=lambda x: x[0], reverse=True)

    for call_insert, dict_start, _pattern, patch in method_patches:
        # Determine indent
        m = re.match(r"^(\s*)", lines[call_insert])
        indent = m.group(1) if m else "        "

        # Step 1: Add to the dict
        lines = add_to_dict(lines, dict_start)

        # Step 2: Insert the build_active_filters call before the dict/return
        call_lines = build_active_filters_call(patch, indent)
        for j, cl in enumerate(call_lines):
            lines.insert(call_insert + j, cl)

        patched += 1

    if patched == 0:
        return 0

    new_content = "".join(lines)

    if dry_run:
        print(f"  DRY RUN: {filepath} — {patched} method(s)")
    else:
        with open(filepath, "w") as f:
            f.write(new_content)
        print(f"  PATCHED: {filepath} — {patched} method(s)")

    return patched


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--file", help="Patch a single file")
    args = parser.parse_args()

    if not args.dry_run and not args.execute:
        print("Specify --dry-run or --execute")
        sys.exit(1)

    # Group patches by file
    file_patches: dict[str, list[dict]] = {}
    for patch in PATCHES:
        filepath = patch["file"]
        if args.file and filepath != args.file:
            continue
        file_patches.setdefault(filepath, []).append(patch)

    total_patched = 0
    total_files = 0
    for filepath, patches in sorted(file_patches.items()):
        count = patch_file(filepath, patches, dry_run=args.dry_run)
        if count > 0:
            total_files += 1
            total_patched += count

    action = "DRY RUN" if args.dry_run else "EXECUTED"
    print(f"\n{action}: {total_patched} methods in {total_files} files")


if __name__ == "__main__":
    main()
