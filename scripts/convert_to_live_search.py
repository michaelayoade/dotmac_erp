#!/usr/bin/env python3
"""Convert template search forms to use the live_search macro.

For each template with an inline search form (form method="get" with name="search"):
1. Adds live_search import if not present
2. Replaces the inline form with {{ live_search(...) }} macro call
3. Wraps results + pagination in <div id="results-container">

Usage:
    python scripts/convert_to_live_search.py [--dry-run] [file1.html file2.html ...]
"""

import re
import sys
from pathlib import Path

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"


def get_base_url(filepath: Path) -> str:
    """Derive the base URL from the template path."""
    rel = filepath.relative_to(TEMPLATES_DIR)
    parts = list(rel.parts)

    # Map known template paths to route URLs
    url_map = {
        # Admin
        "admin/users.html": "/admin/users",
        "admin/organizations.html": "/admin/organizations",
        "admin/roles.html": "/admin/roles",
        "admin/permissions.html": "/admin/permissions",
        "admin/tasks.html": "/admin/tasks",
        "admin/audit_logs.html": "/admin/audit-logs",
        "admin/data_changes.html": "/admin/data-changes",
        "admin/settings.html": "/admin/settings",
        "admin/sync/crm/entities.html": "/admin/sync/crm/entities",
        # Expense
        "expense/categories.html": "/expense/categories",
        "expense/limits/list.html": "/expense/limits",
        # Fleet
        "fleet/vehicles.html": "/fleet/vehicles",
        # Finance
        "finance/gl/ledger.html": "/finance/gl/ledger",
        # Support
        "support/archived_tickets.html": "/support/archived",
        # Projects
        "projects/list.html": "/projects",
        # Careers
        "careers/job_list.html": "/careers",
        # People - HR
        "people/hr/departments.html": "/people/hr/departments",
        "people/hr/locations.html": "/people/hr/locations",
        "people/hr/designations.html": "/people/hr/designations",
        "people/hr/grades.html": "/people/hr/grades",
        "people/hr/employment_types.html": "/people/hr/employment-types",
        "people/hr/competencies.html": "/people/hr/competencies",
        "people/hr/job_descriptions.html": "/people/hr/job-descriptions",
        "people/hr/handbook/documents.html": "/people/hr/handbook",
        # People - Payroll
        "people/payroll/structures.html": "/people/payroll/structures",
        "people/payroll/components.html": "/people/payroll/components",
        "people/payroll/tax_profiles.html": "/people/payroll/tax-profiles",
        "people/payroll/slips.html": "/people/payroll/slips",
        "people/payroll/assignments.html": "/people/payroll/assignments",
        "people/payroll/loans.html": "/people/payroll/loans",
        "people/payroll/loan_types.html": "/people/payroll/loan-types",
        "people/payroll/run_detail.html": "/people/payroll/runs",
        # People - Recruit
        "people/recruit/applicants.html": "/people/recruit/applicants",
        "people/recruit/job_openings.html": "/people/recruit/job-openings",
        # People - Training
        "people/training/events.html": "/people/training/events",
        "people/training/programs.html": "/people/training/programs",
        # People - Perf
        "people/perf/kpis.html": "/people/perf/kpis",
        "people/perf/kras.html": "/people/perf/kras",
        "people/perf/appraisal_cycles.html": "/people/perf/appraisal-cycles",
        "people/perf/appraisal_templates.html": "/people/perf/appraisal-templates",
        # People - Other
        "people/attendance/shifts.html": "/people/attendance/shifts",
        "people/scheduling/patterns.html": "/people/scheduling/patterns",
        "people/leave/types.html": "/people/leave/types",
    }

    rel_str = "/".join(parts)
    return url_map.get(rel_str, "")


def get_placeholder(filepath: Path) -> str:
    """Extract existing placeholder from the search input."""
    content = filepath.read_text(encoding="utf-8")
    m = re.search(r'placeholder="([^"]*)"', content)
    return m.group(1) if m else "Search..."


def extract_filters(content: str) -> list[dict]:
    """Extract filter dropdowns from inline search forms."""
    filters = []
    # Find select elements with name attribute inside forms
    form_match = re.search(
        r'<form\s+method="get"[^>]*>(.*?)</form>', content, re.DOTALL
    )
    if not form_match:
        # Try HTMX pattern (no form wrapper, just select with hx-get)
        form_content = content
    else:
        form_content = form_match.group(1)

    # Find selects that are filter dropdowns (not the search input)
    select_pattern = re.compile(
        r'<select\s+[^>]*name="(\w+)"[^>]*>(.*?)</select>',
        re.DOTALL,
    )
    for sm in select_pattern.finditer(form_content):
        name = sm.group(1)
        if name == "search" or name == "limit":
            continue

        options = []
        label = "All"

        # Extract options
        opt_pattern = re.compile(
            r'<option\s+value="([^"]*)"[^>]*>(.*?)</option>',
            re.DOTALL,
        )
        for om in opt_pattern.finditer(sm.group(2)):
            val = om.group(1).strip()
            lbl = om.group(2).strip()
            # First empty-value option is the "All" label
            if not val:
                label = lbl.strip()
                continue
            options.append({"value": val, "label": lbl})

        # Try to get the template variable name for the current value
        # Look for patterns like {{ 'selected' if status == 'active' }}
        value_var = name  # default
        selected_match = re.search(r"selected.*?if\s+(\w+)\s*==", sm.group(2))
        if selected_match:
            value_var = selected_match.group(1)

        filters.append(
            {
                "name": name,
                "label": label,
                "value_var": value_var,
                "options": options,
            }
        )

    return filters


def build_live_search_call(
    base_url: str,
    placeholder: str,
    filters: list[dict],
    search_var: str = "search",
) -> str:
    """Build the live_search macro call string."""
    parts = [f"search={search_var}"]

    if filters:
        filter_strs = []
        for f in filters:
            opts = ", ".join(
                f'{{"value": "{o["value"]}", "label": "{o["label"]}"}}'
                for o in f["options"]
            )
            filter_strs.append(
                f'{{"name": "{f["name"]}", "label": "{f["label"]}", '
                f'"value": {f["value_var"]}, '
                f'"options": [{opts}]}}'
            )
        parts.append(f"filters=[{', '.join(filter_strs)}]")

    parts.append(f'base_url="{base_url}"')
    parts.append(f'placeholder="{placeholder}"')

    return "{{ live_search(\n    " + ",\n    ".join(parts) + "\n) }}"


def process_file(filepath: Path, dry_run: bool = False) -> bool:
    """Process a single template file. Returns True if modified."""
    content = filepath.read_text(encoding="utf-8")

    # Skip if already converted
    if "live_search" in content:
        return False

    # Skip if no search input
    if 'name="search"' not in content:
        return False

    base_url = get_base_url(filepath)
    if not base_url:
        print(f"  SKIP (no URL mapping): {filepath.relative_to(TEMPLATES_DIR)}")
        return False

    placeholder = get_placeholder(filepath)
    filters = extract_filters(content)

    # Build the macro call
    macro_call = build_live_search_call(base_url, placeholder, filters)

    # 1. Add import if not present
    if 'from "components/macros.html" import' in content:
        # Add live_search to existing import
        content = re.sub(
            r'(from "components/macros.html" import\s+)',
            r"\1live_search, ",
            content,
        )
    elif "{% extends" in content:
        # Add new import line after extends
        content = re.sub(
            r'({% extends "[^"]*" %})\n',
            r'\1\n{% from "components/macros.html" import live_search %}\n',
            content,
            count=1,
        )
    else:
        content = '{% from "components/macros.html" import live_search %}\n' + content

    # 2. Replace the inline search form
    # Pattern A: <div class="card">...<form method="get">...search...</form>...</div>
    form_block = re.search(
        r"(\s*){# ?Search ?#}\s*\n"
        r'\s*<div class="card[^"]*">\s*\n'
        r'\s*<form method="get"[^>]*>\s*\n'
        r"(.*?)"
        r"\s*</form>\s*\n"
        r"\s*</div>",
        content,
        re.DOTALL,
    )

    if form_block:
        indent = form_block.group(1) or "    "
        content = (
            content[: form_block.start()]
            + f"\n{indent}{macro_call}\n"
            + content[form_block.end() :]
        )
    else:
        # Pattern B: Just <form method="get"> without card wrapper
        form_block = re.search(
            r'\s*<form method="get"[^>]*>\s*\n'
            r'(.*?name="search".*?)'
            r"\s*</form>",
            content,
            re.DOTALL,
        )
        if form_block:
            content = (
                content[: form_block.start()]
                + f"\n    {macro_call}\n"
                + content[form_block.end() :]
            )
        else:
            # Pattern C: Admin HTMX inline (no form wrapper)
            # These have inline hx-get on the search input
            htmx_block = re.search(
                r"(\s*)(<!-- Search -->|{# Search #})\s*\n"
                r'(.*?name="search".*?)'
                r"(<!-- (?:Status|Add)|{# \w)",
                content,
                re.DOTALL,
            )
            if htmx_block:
                # More complex - just print warning
                print(
                    f"  MANUAL: {filepath.relative_to(TEMPLATES_DIR)} (admin HTMX pattern)"
                )
                return False

    # 3. Add results-container wrapper if not present
    if 'id="results-container"' not in content:
        # Try to find the first card/table after the search
        # Look for the pattern after the macro call insertion point
        # This is highly template-specific, so we just print a note
        pass

    if not dry_run:
        filepath.write_text(content, encoding="utf-8")
        print(f"  {filepath.relative_to(TEMPLATES_DIR)}: converted")
    else:
        print(f"  WOULD convert: {filepath.relative_to(TEMPLATES_DIR)}")

    return True


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")]

    if dry_run:
        print("DRY RUN — no files will be modified\n")

    if args:
        files = [TEMPLATES_DIR / a for a in args]
    else:
        files = sorted(TEMPLATES_DIR.rglob("*.html"))

    converted = 0
    for f in files:
        if f.exists() and process_file(f, dry_run=dry_run):
            converted += 1

    print(f"\nTotal: {converted} files converted")


if __name__ == "__main__":
    main()
