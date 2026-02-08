#!/usr/bin/env python3
"""
Find {% for %} loops inside <tbody> that lack {% else %} empty states.

Reports files and line numbers for manual or semi-automated fixing.
When --fix is passed, adds a basic {% else %} block with empty_state macro.
"""

import os
import re
import sys

TEMPLATES_DIR = "templates"

# Skip document/email templates
SKIP_PATTERNS = ["documents/", "email/", "_pdf", "print_", "components/macros.html"]

MACRO_IMPORT = '{% from "components/macros.html" import empty_state %}'


def should_skip(filepath: str) -> bool:
    return any(pat in filepath for pat in SKIP_PATTERNS)


def find_table_for_loops(content: str) -> list[dict]:
    """Find {% for %} loops inside <tbody> that lack {% else %}."""
    results = []
    lines = content.split("\n")

    in_tbody = False
    for_depth = 0
    for_start_line = -1
    for_var = ""
    has_else = False

    for i, line in enumerate(lines):
        stripped = line.strip()

        if "<tbody" in stripped:
            in_tbody = True

        if "</tbody" in stripped:
            in_tbody = False

        if in_tbody and for_depth == 0:
            # Look for {% for ... in ... %}
            for_match = re.search(r"\{%[-\s]*for\s+(\w+)\s+in\s+(\w+)", stripped)
            if for_match:
                for_depth = 1
                for_start_line = i
                for_var = for_match.group(2)  # The collection name
                has_else = False

        elif for_depth > 0:
            # Track nested fors
            if re.search(r"\{%[-\s]*for\s+", stripped):
                for_depth += 1
            if re.search(r"\{%[-\s]*else\s*[-]?%\}", stripped):
                if for_depth == 1:
                    has_else = True
            if re.search(r"\{%[-\s]*endfor\s*[-]?%\}", stripped):
                for_depth -= 1
                if for_depth == 0 and not has_else:
                    results.append(
                        {
                            "line": for_start_line + 1,
                            "endfor_line": i + 1,
                            "collection": for_var,
                        }
                    )

    return results


def get_entity_name(filepath: str, collection: str) -> str:
    """Derive a friendly entity name for empty state title."""
    # Use the collection variable name
    name_map = {
        "items": "items",
        "invoices": "invoices",
        "receipts": "receipts",
        "customers": "customers",
        "suppliers": "suppliers",
        "payments": "payments",
        "journals": "journals",
        "entries": "entries",
        "accounts": "accounts",
        "employees": "employees",
        "records": "records",
        "requests": "requests",
        "applications": "applications",
        "tickets": "tickets",
        "assets": "assets",
        "categories": "categories",
        "runs": "runs",
        "slips": "slips",
        "cases": "cases",
        "contracts": "contracts",
        "orders": "orders",
        "warehouses": "warehouses",
        "lots": "lots",
        "boms": "BOMs",
        "counts": "counts",
        "tasks": "tasks",
        "projects": "projects",
        "events": "events",
        "programs": "programs",
        "allocations": "allocations",
        "holidays": "holidays",
        "types": "types",
        "shifts": "shifts",
        "schedules": "schedules",
        "plans": "plans",
        "vendors": "vendors",
        "evaluations": "evaluations",
        "requisitions": "requisitions",
        "rfqs": "RFQs",
        "transactions": "transactions",
        "batches": "batches",
        "templates": "templates",
        "fields": "fields",
        "workflows": "workflows",
        "rules": "rules",
        "appraisals": "appraisals",
        "kpis": "KPIs",
        "feedbacks": "feedbacks",
        "loans": "loans",
        "structures": "structures",
        "transfers": "transfers",
        "promotions": "promotions",
    }
    return name_map.get(collection, collection)


def fix_file(filepath: str, dry_run: bool = False) -> int:
    with open(filepath, encoding="utf-8") as f:
        content = f.read()

    loops = find_table_for_loops(content)
    if not loops:
        return 0

    lines = content.split("\n")
    fixes = 0
    needs_import = False

    # Process in reverse to maintain line numbers
    for loop in reversed(loops):
        endfor_idx = loop["endfor_line"] - 1
        entity = get_entity_name(filepath, loop["collection"])
        endfor_line = lines[endfor_idx]

        # Detect indentation
        indent = ""
        for ch in endfor_line:
            if ch in (" ", "\t"):
                indent += ch
            else:
                break

        # Build the else block
        else_block = [
            f"{indent}{{% else %}}",
            f"{indent}<tr>",
            f'{indent}    <td colspan="99" class="p-0">',
            f'{indent}        {{{{ empty_state("No {entity} found", "There are no {entity} to display.") }}}}',
            f"{indent}    </td>",
            f"{indent}</tr>",
        ]

        # Insert before the {% endfor %} line
        for j, else_line in enumerate(else_block):
            lines.insert(endfor_idx + j, else_line)

        fixes += 1
        needs_import = True

    if fixes > 0 and not dry_run:
        new_content = "\n".join(lines)

        # Add empty_state import if needed
        if needs_import and "import empty_state" not in new_content:
            # Check if there's already an import from macros.html
            import_match = re.search(
                r'(\{%\s*from\s+"components/macros\.html"\s+import\s+)([^%]+?)(\s*%\})',
                new_content,
            )
            if import_match:
                # Add to existing import
                existing_imports = import_match.group(2).strip()
                if "empty_state" not in existing_imports:
                    new_imports = existing_imports + ", empty_state"
                    new_content = (
                        new_content[: import_match.start(2)]
                        + new_imports
                        + new_content[import_match.end(2) :]
                    )
            else:
                # Add new import after extends
                extends_match = re.search(
                    r"(\{%\s*extends\s+[^%]+%\})\s*\n", new_content
                )
                if extends_match:
                    insert_pos = extends_match.end()
                    new_content = (
                        new_content[:insert_pos]
                        + MACRO_IMPORT
                        + "\n"
                        + new_content[insert_pos:]
                    )
                else:
                    new_content = MACRO_IMPORT + "\n" + new_content

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(new_content)

    return fixes


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    total_fixes = 0
    fixed_files = 0

    for root, _dirs, files in os.walk(TEMPLATES_DIR):
        for fname in sorted(files):
            if not fname.endswith(".html"):
                continue
            filepath = os.path.join(root, fname)
            if should_skip(filepath):
                continue
            fixes = fix_file(filepath, dry_run=dry_run)
            if fixes > 0:
                action = "Would fix" if dry_run else "Fixed"
                print(f"{action} {fixes} table(s) in {filepath}")
                total_fixes += fixes
                fixed_files += 1

    action = "Would fix" if dry_run else "Fixed"
    print(f"\n{action} {total_fixes} tables across {fixed_files} files")


if __name__ == "__main__":
    main()
