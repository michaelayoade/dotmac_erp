#!/usr/bin/env python3
"""
Migrate inline pagination HTML to the {{ pagination() }} macro.

Finds {% if total_pages > 1 %} or {% if total and total > 0 %} blocks
that contain manual Previous/Next links, and replaces them with the
centralized pagination() macro call.

Usage:
    poetry run python scripts/fix_inline_pagination.py --dry-run
    poetry run python scripts/fix_inline_pagination.py --execute
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

# Files to skip (macro definitions, partials that define pagination)
SKIP_FILES = {
    "components/macros.html",
}


def find_matching_endif(content: str, if_start: int) -> int:
    """Find the position (end) of the matching {%% endif %%} for a {%% if %%} at if_start."""
    depth = 0
    pos = if_start
    while pos < len(content):
        tag_start = content.find("{%", pos)
        if tag_start == -1:
            return -1
        tag_end = content.find("%}", tag_start + 2)
        if tag_end == -1:
            return -1
        tag_end += 2  # Include the %}

        tag_body = (
            content[tag_start + 2 : tag_end - 2].strip().lstrip("-").rstrip("-").strip()
        )

        if (
            tag_body.startswith("if ")
            or tag_body.startswith("if\t")
            or tag_body.startswith("if\n")
        ):
            depth += 1
        elif tag_body == "endif" or tag_body.startswith("endif "):
            depth -= 1
            if depth == 0:
                return tag_end

        pos = tag_end

    return -1


def is_pagination_condition(content: str, if_start: int) -> bool:
    """Check if the {% if %} condition is specifically about pagination.

    Only match blocks whose condition tests pagination variables like
    total_pages, total, has_prev, has_next, has_more, has_previous.
    Reject blocks that test data presence like {% if runs %} or {% if applications %}.
    """
    # Extract the condition text from {% if CONDITION %}
    tag_end = content.find("%}", if_start)
    if tag_end == -1:
        return False
    condition = content[if_start:tag_end].strip()
    # Remove {% if and whitespace
    condition = re.sub(r"^\{%[-\s]*if\s+", "", condition).strip().rstrip("-").strip()

    # Must reference pagination-related variables
    pagination_vars = (
        "total_pages",
        "total ",  # "total and total > 0" but not "totalitems"
        "total>",
        "has_prev",
        "has_next",
        "has_previous",
        "has_more",
        "page_count",
    )
    return any(var in condition for var in pagination_vars)


def find_pagination_block(content: str) -> tuple[int, int] | None:
    """Find the innermost inline pagination block in the content.

    Only matches {% if %} blocks whose condition tests pagination variables
    (total_pages, total, has_prev, etc.) — not data presence blocks like {% if runs %}.

    Returns (start, end) tuple or None.
    """
    candidates: list[tuple[int, int]] = []

    pos = 0
    while pos < len(content):
        # Find next {% if ... %}
        match = re.search(r"\{%[-\s]*if\s+", content[pos:])
        if not match:
            break
        start = pos + match.start()
        end = find_matching_endif(content, start)
        if end == -1:
            pos = start + 2
            continue

        block = content[start:end]

        # Must contain pagination URL patterns
        has_page_url = "?page=" in block or "&page=" in block
        # Must contain Previous or Next link text
        has_nav = "Previous" in block or "Next" in block
        # Must NOT already use the pagination macro
        uses_macro = "{{ pagination(" in block
        # Opening condition must be about pagination, not data presence
        is_pagination = is_pagination_condition(content, start)

        if has_page_url and has_nav and not uses_macro and is_pagination:
            candidates.append((start, end))

        pos = start + 2

    if not candidates:
        return None

    # Keep the OUTERMOST pagination-specific block (e.g., {% if total and total > 0 %}
    # wrapping {% if total_pages > 1 %} — we want the outer one since the macro
    # handles both "Showing X to Y" and page navigation)
    candidates.sort(key=lambda b: (b[0], -b[1]))
    outermost: list[tuple[int, int]] = []
    for block in candidates:
        is_nested = False
        for outer in outermost:
            if block[0] >= outer[0] and block[1] <= outer[1]:
                is_nested = True
                break
        if not is_nested:
            outermost.append(block)

    # Return the first (and usually only) outermost pagination block
    return outermost[0] if outermost else None


def extract_per_page_limit(block: str) -> int:
    """Extract the per-page limit from math like '(page - 1) * 20'."""
    # Look for multiplication pattern in "Showing" calculations
    match = re.search(r"\*\s*(\d+)", block)
    if match:
        limit = int(match.group(1))
        if limit in (10, 12, 15, 20, 25, 50, 100):
            return limit
    return 20


def extract_total_var(block: str) -> str:
    """Determine if the template uses 'total' or 'total_count'."""
    if "total_count" in block:
        return "total_count"
    return "total"


def extract_filter_params(block: str) -> dict[str, str]:
    """Extract filter URL parameters from the pagination URLs.

    Looks for patterns like:
      &status={{ status }}
      {% if status %}&status={{ status }}{% endif %}
      {% if current_status %}status={{ current_status }}&{% endif %}
    """
    filters: dict[str, str] = {}

    # Pattern 1: &param={{ var }} or ?param={{ var }} (unconditional)
    for m in re.finditer(r"[&?](\w+)=\{\{\s*(\w+)(?:\s*\|[^}]*)?\s*\}\}", block):
        param_name = m.group(1)
        var_name = m.group(2)
        if param_name not in ("page",):
            filters[param_name] = var_name

    # Pattern 2: {% if var %}...&var={{ var }}...{% endif %} (conditional)
    for m in re.finditer(
        r"\{%[-\s]*if\s+(\w+)\s*[-\s]*%\}[^{]*[&?]\1=\{\{\s*\1\s*\}\}", block
    ):
        var_name = m.group(1)
        if var_name not in ("page", "has_prev", "has_next", "has_previous", "has_more"):
            filters[var_name] = var_name

    # Pattern 3: {% if var %}var={{ var }}&{% endif %} (param before &)
    for m in re.finditer(
        r"\{%[-\s]*if\s+(\w+)\s*[-\s]*%\}\s*\1=\{\{\s*\1\s*\}\}&?", block
    ):
        var_name = m.group(1)
        if var_name not in ("page", "has_prev", "has_next", "has_previous", "has_more"):
            filters[var_name] = var_name

    # Rename current_status/current_priority to use the URL param name
    renamed: dict[str, str] = {}
    for param, var in filters.items():
        # URL param might differ from variable name
        renamed[param] = var

    return renamed


def has_search_param(block: str) -> bool:
    """Check if the pagination block passes a search parameter."""
    return "&search=" in block or "search={{ search" in block


def build_pagination_call(
    indent: str,
    total_var: str,
    limit: int,
    filters: dict[str, str],
    search: bool,
) -> str:
    """Build the {{ pagination(...) }} macro call."""
    lines: list[str] = []
    lines.append(f"{indent}{{{{ pagination(")
    lines.append(f"{indent}    page=page | default(1),")
    lines.append(f"{indent}    total_pages=total_pages | default(1),")
    lines.append(f"{indent}    total_count={total_var} | default(0),")

    # Non-standard limits (not 25 or 50) disable the size selector
    if limit not in (25, 50):
        lines.append(f"{indent}    limit={limit},")
        lines.append(f"{indent}    show_size_selector=false,")
    else:
        lines.append(f"{indent}    limit=limit | default({limit}),")

    if search:
        lines.append(f"{indent}    search=search | default(''),")

    if filters:
        filter_entries = ", ".join(f'"{k}": {v}' for k, v in sorted(filters.items()))
        lines.append(f"{indent}    filters={{{{{filter_entries}}}}}")
    else:
        # Remove trailing comma from last line
        lines[-1] = lines[-1].rstrip(",")

    lines.append(f"{indent}) }}}}")
    return "\n".join(lines)


def ensure_import(content: str, macro_name: str) -> str:
    """Ensure the macro is imported from components/macros.html."""
    import_pattern = re.compile(
        r'(\{%[-\s]*from\s+"components/macros\.html"\s+import\s+)([^%]+)([-\s]*%\})'
    )
    match = import_pattern.search(content)

    if match:
        imports_str = match.group(2).strip()
        imported = [s.strip() for s in imports_str.split(",")]
        if macro_name in imported:
            return content  # Already imported

        # Add to existing import (keep trailing space before %})
        new_imports = imports_str.rstrip() + ", " + macro_name + " "
        return content[: match.start(2)] + new_imports + content[match.end(2) :]
    else:
        # No existing import from macros.html — add one after extends
        extends_match = re.search(r'(\{%\s*extends\s+"[^"]+"\s*%\})\n', content)
        if extends_match:
            insert_pos = extends_match.end()
            import_line = f'{{% from "components/macros.html" import {macro_name} %}}\n'
            return content[:insert_pos] + import_line + content[insert_pos:]
        else:
            # No extends — add at top
            import_line = f'{{% from "components/macros.html" import {macro_name} %}}\n'
            return import_line + content

    return content


def get_block_indent(content: str, block_start: int) -> str:
    """Get the indentation of the line where the block starts."""
    line_start = content.rfind("\n", 0, block_start)
    if line_start == -1:
        line_start = 0
    else:
        line_start += 1
    return content[line_start:block_start]


def process_file(filepath: Path, templates_dir: Path) -> tuple[str, int]:
    """Process a template file and return (new_content, replacements_count)."""
    content = filepath.read_text(encoding="utf-8")
    rel_path = str(filepath.relative_to(templates_dir))

    if rel_path in SKIP_FILES:
        return content, 0

    # Check if file already uses {{ pagination( macro exclusively
    # (no inline pagination to replace)
    block_info = find_pagination_block(content)
    if block_info is None:
        return content, 0

    start, end = block_info
    block = content[start:end]

    # Skip files that don't have total_pages (need backend changes)
    if "total_pages" not in block and "total_pages" not in content:
        print(f"  SKIP {rel_path}: no total_pages variable (needs backend change)")
        return content, 0

    # Extract parameters from the inline block
    limit = extract_per_page_limit(block)
    total_var = extract_total_var(block)
    filters = extract_filter_params(block)
    search = has_search_param(block)
    indent = get_block_indent(content, start)

    # Build the macro call
    macro_call = build_pagination_call(indent, total_var, limit, filters, search)

    # Replace the block — start from beginning of the line to avoid double-indent
    # (content[:start] already has trailing whitespace, and macro_call includes its own)
    line_start = content.rfind("\n", 0, start)
    if line_start != -1:
        line_start += 1  # After the newline
    else:
        line_start = 0

    new_content = content[:line_start] + macro_call + "\n" + content[end:]

    # Ensure pagination is imported
    new_content = ensure_import(new_content, "pagination")

    return new_content, 1


def scan_templates(templates_dir: Path, dry_run: bool) -> dict[str, int]:
    """Scan all template files and migrate inline pagination."""
    results: dict[str, int] = {}
    total_replacements = 0
    skipped: list[str] = []

    for filepath in sorted(templates_dir.rglob("*.html")):
        rel_path = str(filepath.relative_to(templates_dir))
        if rel_path in SKIP_FILES:
            continue

        content = filepath.read_text(encoding="utf-8")

        # Quick check: skip files without inline pagination indicators
        if "?page=" not in content and "&page=" not in content:
            continue
        if "Previous" not in content and "Next" not in content:
            continue
        if "{{ pagination(" in content:
            # Check if file ALSO has inline pagination
            # (partial migration — file uses macro somewhere but has inline elsewhere)
            pass

        new_content, count = process_file(filepath, templates_dir)
        if count > 0:
            results[rel_path] = count
            total_replacements += count
            if not dry_run:
                filepath.write_text(new_content, encoding="utf-8")

    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate inline pagination to macro")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="Show what would change")
    group.add_argument("--execute", action="store_true", help="Apply changes")
    args = parser.parse_args()

    print(f"Scanning templates in {TEMPLATES_DIR}...")
    results = scan_templates(TEMPLATES_DIR, dry_run=args.dry_run)

    mode = "DRY RUN" if args.dry_run else "EXECUTED"
    print(f"\n{'=' * 60}")
    print(f"  {mode}: {len(results)} files, {sum(results.values())} replacements")
    print(f"{'=' * 60}")

    if results:
        for path, count in sorted(results.items()):
            print(f"  {path}: {count} pagination block(s)")

    if args.dry_run and results:
        print("\nRun with --execute to apply changes.")


if __name__ == "__main__":
    main()
