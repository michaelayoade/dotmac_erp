#!/usr/bin/env python3
"""Replace inline pagination blocks with the reusable pagination macro.

Handles two cases:
1. Templates with inline pagination (marked by {# Pagination #} comment) → replace with macro
2. Templates with no pagination → add macro after results-container close or table close

Also adds 'pagination' to the macros.html import if not already present.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path("/root/dotmac")

# Templates with live_search but no pagination macro
TARGETS = [
    "templates/admin/audit_logs.html",
    "templates/admin/data_changes.html",
    "templates/admin/organizations.html",
    "templates/admin/permissions.html",
    "templates/admin/roles.html",
    "templates/admin/settings.html",
    "templates/admin/sync/crm/entities.html",
    "templates/admin/tasks.html",
    "templates/admin/users.html",
    "templates/careers/job_list.html",
    "templates/expense/claims_list.html",
    "templates/expense/list.html",
    "templates/finance/banking/rules.html",
    "templates/finance/gl/journals.html",
    "templates/finance/gl/ledger.html",
    "templates/finance/lease/contracts.html",
    "templates/fixed_assets/categories.html",
    "templates/people/attendance/shifts.html",
    "templates/people/hr/competencies.html",
    "templates/people/hr/departments.html",
    "templates/people/hr/designations.html",
    "templates/people/hr/employees.html",
    "templates/people/hr/employment_types.html",
    "templates/people/hr/grades.html",
    "templates/people/hr/handbook/documents.html",
    "templates/people/hr/job_descriptions.html",
    "templates/people/hr/locations.html",
    "templates/people/leave/types.html",
    "templates/people/payroll/assignments.html",
    "templates/people/payroll/components.html",
    "templates/people/payroll/loan_types.html",
    "templates/people/payroll/loans.html",
    "templates/people/payroll/run_detail.html",
    "templates/people/payroll/slips.html",
    "templates/people/payroll/structures.html",
    "templates/people/payroll/tax_profiles.html",
    "templates/people/perf/appraisal_cycles.html",
    "templates/people/perf/appraisal_templates.html",
    "templates/people/perf/kpis.html",
    "templates/people/perf/kras.html",
    "templates/people/recruit/applicants.html",
    "templates/people/recruit/job_openings.html",
    "templates/people/scheduling/patterns.html",
    "templates/people/training/events.html",
    "templates/people/training/programs.html",
    "templates/procurement/contracts/list.html",
    "templates/procurement/evaluations/list.html",
    "templates/procurement/plans/list.html",
    "templates/procurement/requisitions/list.html",
    "templates/procurement/rfqs/list.html",
    "templates/projects/list.html",
    "templates/support/archived_tickets.html",
    "templates/support/tickets.html",
]

PAGINATION_MACRO = """            {{ pagination(
                page=page,
                total_pages=total_pages,
                total_count=total_count,
                limit=limit,
                search=search
            ) }}"""


def add_pagination_import(lines: list[str]) -> bool:
    """Add pagination to macros import. Returns True if modified."""
    for i, line in enumerate(lines):
        if "components/macros.html" in line and "import" in line:
            if "pagination" in line:
                return False  # already imported
            # Add pagination to existing import
            # Pattern: {% from "components/macros.html" import X, Y %}
            line_stripped = line.rstrip()
            if line_stripped.endswith("%}"):
                # Insert before the closing %}
                new_line = line_stripped[:-2].rstrip() + ", pagination %}"
                lines[i] = new_line
                return True
    return False


def find_inline_pagination(lines: list[str]) -> tuple[int, int] | None:
    """Find the start and end of inline pagination block.

    Looks for {# Pagination #} comment followed by {% if total_pages > 1 %}
    and finds the matching {% endif %}.

    Returns (start_line, end_line) or None.
    """
    start = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if "{# Pagination #}" in stripped or "{# Paging #}" in stripped:
            start = i
            break
        # Also match <!-- Pagination --> style comments
        if "<!-- Pagination" in stripped:
            start = i
            break

    if start is None:
        # Try to find inline pagination without comment marker
        for i, line in enumerate(lines):
            stripped = line.strip()
            if (
                stripped == "{% if total_pages > 1 %}"
                or stripped == "{% if has_next or has_prev %}"
            ):
                # Check if this looks like a pagination block (has page links nearby)
                block_text = "\n".join(lines[i : min(i + 30, len(lines))])
                if (
                    "page - 1" in block_text
                    or "page + 1" in block_text
                    or "Previous" in block_text
                ):
                    start = i
                    break

    if start is None:
        return None

    # Find the matching endif
    # Count if/endif nesting from start
    nesting = 0
    for i in range(start, min(start + 50, len(lines))):
        stripped = lines[i].strip()
        # Count {% if %} blocks (not inline {% if x %}...{% endif %} on same line)
        if_count = len(re.findall(r"\{%\s*if\s", stripped))
        endif_count = len(re.findall(r"\{%\s*endif\s*%\}", stripped))

        # Handle same-line if/endif (don't count these for nesting)
        if if_count == endif_count and if_count > 0:
            continue

        nesting += if_count - endif_count

        if nesting <= 0 and i > start:
            return (start, i)

    return None


def find_table_end(lines: list[str]) -> int | None:
    """Find the line after </table></div> (table-container close) for inserting pagination."""
    # Look for </table> followed by </div> (table-container closing)
    for i in range(len(lines) - 1, -1, -1):
        if "</table>" in lines[i]:
            # Find the next </div> after </table>
            for j in range(i + 1, min(i + 5, len(lines))):
                if "</div>" in lines[j]:
                    return j + 1
            return i + 1
    return None


def process_template(filepath: Path, dry_run: bool = True) -> list[str]:
    """Process a single template file."""
    content = filepath.read_text()
    if "pagination(" in content and "macros.html" in content:
        return []  # Already has pagination macro

    lines = content.split("\n")
    changes: list[str] = []

    # Step 1: Add pagination import
    if add_pagination_import(lines):
        changes.append("  Added pagination to macro import")

    # Step 2: Find and replace inline pagination
    inline = find_inline_pagination(lines)
    if inline:
        start, end = inline
        # Detect indentation from context
        indent = "            "  # default 12 spaces
        for k in range(start - 1, max(start - 5, -1), -1):
            if "</div>" in lines[k] or "</table>" in lines[k]:
                indent = lines[k][: len(lines[k]) - len(lines[k].lstrip())]
                break

        # Build replacement
        replacement = f"""{indent}{{# Pagination #}}
{indent}{{{{ pagination(
{indent}    page=page,
{indent}    total_pages=total_pages,
{indent}    total_count=total_count,
{indent}    limit=limit,
{indent}    search=search
{indent}) }}}}"""

        # Replace lines
        lines[start : end + 1] = replacement.split("\n")
        changes.append(
            f"  Replaced inline pagination (L{start + 1}-L{end + 1}) with macro"
        )
    else:
        # No inline pagination found — try to add after table
        insert_pos = find_table_end(lines)
        if insert_pos:
            indent = "            "  # 12 spaces
            macro_call = f"""
{indent}{{# Pagination #}}
{indent}{{{{ pagination(
{indent}    page=page,
{indent}    total_pages=total_pages,
{indent}    total_count=total_count,
{indent}    limit=limit,
{indent}    search=search
{indent}) }}}}"""
            lines.insert(insert_pos, macro_call)
            changes.append(f"  Added pagination macro at L{insert_pos + 1}")

    if changes and not dry_run:
        filepath.write_text("\n".join(lines))

    return changes


def main() -> None:
    dry_run = "--execute" not in sys.argv

    if dry_run:
        print("DRY RUN — pass --execute to apply changes\n")

    total_files = 0
    total_changes = 0

    for rel_path in TARGETS:
        filepath = ROOT / rel_path
        if not filepath.exists():
            print(f"  SKIP (not found): {rel_path}")
            continue

        changes = process_template(filepath, dry_run=dry_run)
        if changes:
            total_files += 1
            total_changes += len(changes)
            print(f"{rel_path}:")
            for c in changes:
                print(c)

    print(
        f"\n{'Would modify' if dry_run else 'Modified'}: {total_changes} changes across {total_files} files"
    )


if __name__ == "__main__":
    main()
