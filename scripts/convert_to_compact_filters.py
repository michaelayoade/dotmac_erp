"""
Convert old-style filter forms to compact_filters macro.

Usage:
    python scripts/convert_to_compact_filters.py --dry-run   # Preview changes
    python scripts/convert_to_compact_filters.py --execute    # Apply changes

Strategy:
1. Find the old filter form block (card with <form method="get"> containing <select>)
2. Extract select fields and date inputs
3. Replace with compact_filters macro call
4. Wrap the next table card in <div id="results-container">
5. Update the macro import line
"""

from __future__ import annotations

import argparse
import os
import re
import sys


def find_filter_form_block(lines: list[str]) -> tuple[int, int] | None:
    """Find start/end line indices of the old filter form block.

    Returns (start, end) inclusive line indices, or None if not found.
    Looks for patterns like:
      <div class="card p-4">  or  <div class="card">
        <form method="get" ...>
          ...
        </form>
      </div>
    """
    form_start = None
    for i, line in enumerate(lines):
        if 'form method="get"' in line and "<select" not in line:
            form_start = i
            break

    if form_start is None:
        return None

    # Walk backwards from form to find the card wrapper
    block_start = form_start
    for i in range(form_start - 1, max(form_start - 5, -1), -1):
        stripped = lines[i].strip()
        if (
            'class="card' in stripped
            or "<!-- Filter" in stripped
            or "{# Filter" in stripped
        ):
            block_start = i
            break
        if (
            stripped
            and not stripped.startswith("{#")
            and not stripped.startswith("<!--")
        ):
            break

    # Walk forward from form to find closing </form> and </div>
    form_end = None
    depth = 0
    for i in range(form_start, min(form_start + 60, len(lines))):
        line = lines[i]
        if "<form" in line:
            depth += 1
        if "</form>" in line:
            depth -= 1
            if depth <= 0:
                form_end = i
                break

    if form_end is None:
        return None

    # Find the closing div of the card wrapper
    block_end = form_end
    for i in range(form_end + 1, min(form_end + 4, len(lines))):
        stripped = lines[i].strip()
        if stripped == "</div>":
            block_end = i
            break
        if stripped:
            break

    return (block_start, block_end)


def extract_form_action(lines: list[str], start: int, end: int) -> str:
    """Extract the form action URL."""
    for i in range(start, end + 1):
        m = re.search(r'action="([^"]*)"', lines[i])
        if m:
            return m.group(1)
    return ""


def extract_selects(lines: list[str], start: int, end: int) -> list[dict]:
    """Extract select field blocks from the filter form."""
    selects = []
    i = start
    while i <= end:
        line = lines[i]
        if "<select" in line and "name=" in line:
            # Extract name
            m = re.search(r'name="([^"]*)"', line)
            if not m:
                i += 1
                continue
            name = m.group(1)

            # Find the full select block
            # Check for label before select
            label_line = None
            for j in range(max(i - 3, start), i):
                if "<label" in lines[j]:
                    label_line = j
                    break

            # Find the wrapper div if it exists
            wrapper_start = i
            if label_line is not None:
                # Look for wrapping <div> before label
                for j in range(label_line - 1, max(label_line - 3, start), -1):
                    if "<div" in lines[j] and "card" not in lines[j]:
                        wrapper_start = j
                        break
                else:
                    wrapper_start = label_line

            # Find end of select
            select_end = i
            for j in range(i, min(i + 20, end + 1)):
                if "</select>" in lines[j]:
                    select_end = j
                    break

            # Find wrapper end
            wrapper_end = select_end
            for j in range(select_end + 1, min(select_end + 3, end + 1)):
                if lines[j].strip() == "</div>":
                    wrapper_end = j
                    break

            # Collect all lines for this select block
            block = []
            for j in range(wrapper_start, wrapper_end + 1):
                block.append(lines[j])

            selects.append(
                {
                    "name": name,
                    "block_lines": block,
                    "block_start": wrapper_start,
                    "block_end": wrapper_end,
                }
            )

            i = wrapper_end + 1
        else:
            i += 1

    return selects


def extract_dates(lines: list[str], start: int, end: int) -> list[dict]:
    """Extract date input fields from the filter form."""
    dates = []
    for i in range(start, end + 1):
        if 'type="date"' in lines[i] and "name=" in lines[i]:
            m = re.search(r'name="([^"]*)"', lines[i])
            if m:
                dates.append({"name": m.group(1), "line": i})
    return dates


def detect_base_url(lines: list[str], start: int, end: int, filepath: str) -> str:
    """Detect the base URL from form action or filepath."""
    action = extract_form_action(lines, start, end)
    if action:
        return action

    # Infer from filepath
    # templates/finance/ar/quotes.html → /finance/ar/quotes
    # templates/people/leave/applications.html → /people/leave/applications
    rel = filepath.replace("templates/", "").replace(".html", "")
    parts = rel.split("/")

    # Map template paths to URL paths
    path_map = {
        "expense/advances/list": "/expense/advances/list",
        "expense/limits/approvers": "/expense/limits/approvers",
        "expense/limits/evaluations": "/expense/limits/evaluations",
        "expense/limits/usage": "/expense/limits/usage",
        "expense/reports/by_employee": "/expense/reports/by_employee",
        "expense/reports/trends": "/expense/reports/trends",
        "admin/sync/entities": "/admin/sync/entities",
        "admin/sync/history": "/admin/sync/history",
        "fleet/documents": "/fleet/documents",
        "fleet/fuel": "/fleet/fuel",
        "fleet/incidents": "/fleet/incidents",
        "fleet/maintenance": "/fleet/maintenance",
        "fleet/reservations": "/fleet/reservations",
        "support/breached_tickets": "/support/breached-tickets",
    }

    if rel in path_map:
        return path_map[rel]

    # Default: convert to kebab-case URL
    url = "/" + "/".join(parts)
    url = url.replace("_", "-")
    # Finance special routes
    url = url.replace("/finance/ar/quotes", "/finance/quotes")
    url = url.replace("/finance/ar/sales-orders", "/finance/sales-orders")

    return url


def find_results_section(lines: list[str], after: int) -> tuple[int, int] | None:
    """Find the next card (table section) after the filter block to wrap in results-container.

    Returns (card_start, card_end) line indices.
    """
    card_start = None
    for i in range(after + 1, min(after + 10, len(lines))):
        stripped = lines[i].strip()
        if 'class="card' in stripped:
            card_start = i
            break
        # Some templates have {% if items %} before the card
        if stripped.startswith("{%") or stripped.startswith("<!--") or not stripped:
            continue

    if card_start is None:
        return None

    # Find the matching closing </div>
    # We need to track div nesting
    depth = 0
    for i in range(card_start, len(lines)):
        line = lines[i]
        depth += line.count("<div")
        depth -= line.count("</div>")
        if depth <= 0:
            return (card_start, i)

    return None


def build_compact_filters_block(
    base_url: str,
    selects: list[dict],
    has_dates: bool,
    date_names: list[str],
    indent: str = "    ",
) -> list[str]:
    """Build the compact_filters macro call lines."""
    out: list[str] = []

    # Determine date variable names from the original form
    start_date_var = "start_date"
    end_date_var = "end_date"
    for dn in date_names:
        if "from" in dn or "start" in dn:
            start_date_var = dn
        elif "to" in dn or "end" in dn:
            end_date_var = dn
        elif dn == "as_of_date":
            start_date_var = dn
            has_dates = False  # Single date, not a range — handle as custom input

    # Build macro call
    out.append(f"{indent}{{# Filters #}}")
    if selects or has_dates:
        out.append(f"{indent}{{% call(filter_attrs) compact_filters(")
        out.append(f'{indent}    base_url="{base_url}",')
        out.append(f"{indent}    active_filters=active_filters | default([])")
        if has_dates:
            out[-1] = out[-1] + ","
            out.append(f"{indent}    date_range=true,")
            out.append(f"{indent}    start_date={start_date_var} | default('', true),")
            out.append(f"{indent}    end_date={end_date_var} | default('', true)")
        out.append(f"{indent}) %}}")

        # Add select blocks
        for sel in selects:
            out.append(f"{indent}<div>")
            # Reconstruct select with proper formatting
            for raw_line in sel["block_lines"]:
                line = raw_line.rstrip()
                # Check if this line has a <label>
                if "<label" in line:
                    # Normalize label class
                    line = re.sub(r'class="[^"]*"', 'class="form-label text-xs"', line)
                    # Remove for= attribute
                    line = re.sub(r'\s*for="[^"]*"', "", line)
                elif "<select" in line:
                    # Ensure w-full class
                    if "w-full" not in line:
                        line = line.replace(
                            'class="form-select', 'class="form-select w-full'
                        )
                        line = line.replace(
                            'class="form-input form-select', 'class="form-select w-full'
                        )
                    # Remove any id= attribute
                    line = re.sub(r'\s*id="[^"]*"', "", line)
                    # Add filter_attrs
                    line = line.rstrip()
                    if line.endswith(">"):
                        line = line[:-1] + " {{ filter_attrs }}>"
                out.append(line)
            out.append(f"{indent}</div>")

        # Handle single-date fields (like as_of_date) as custom inputs
        for dn in date_names:
            if dn == "as_of_date":
                out.append(f"{indent}<div>")
                out.append(
                    f'{indent}    <label class="form-label text-xs">As Of Date</label>'
                )
                out.append(
                    f'{indent}    <input type="date" name="{dn}" value="{{{{ {dn} | default(\'\', true) }}}}"'
                )
                out.append(
                    f'{indent}           class="form-input w-full" {{{{ filter_attrs }}}}>'
                )
                out.append(f"{indent}</div>")

        out.append(f"{indent}{{% endcall %}}")
    else:
        out.append(
            f'{indent}{{{{ compact_filters(base_url="{base_url}", active_filters=active_filters | default([])) }}}}'
        )

    return out


def update_import_line(lines: list[str]) -> list[str]:
    """Add compact_filters to the import line."""
    for i, line in enumerate(lines):
        if (
            'from "components/macros.html" import' in line
            or "from 'components/macros.html' import" in line
        ):
            if "compact_filters" not in line:
                # Add compact_filters to the import
                line = line.rstrip()
                # Handle multiline imports or single line
                if "%}" in line:
                    line = line.replace(" %}", ", compact_filters %}")
                lines[i] = line + "\n"
            return lines

    # No import line found — add one after the extends line
    for i, line in enumerate(lines):
        if "{% extends" in line:
            # Check if there's already an import on the next line
            if i + 1 < len(lines) and 'from "components/' in lines[i + 1]:
                return update_import_line(lines)  # Will be caught above
            # Add a new import line
            lines.insert(
                i + 1, '{% from "components/macros.html" import compact_filters %}\n'
            )
            return lines

    return lines


def wrap_results_container(lines: list[str], after_filter: int) -> list[str]:
    """Wrap the results section in a <div id="results-container">."""
    # Find the results card section
    result = find_results_section(lines, after_filter)
    if result is None:
        return lines

    card_start, card_end = result

    # Check if already wrapped
    for i in range(card_start - 3, card_start):
        if i >= 0 and "results-container" in lines[i]:
            return lines

    # Get indentation from card start
    indent = ""
    m = re.match(r"^(\s*)", lines[card_start])
    if m:
        indent = m.group(1)

    # Insert wrapper
    lines.insert(card_start, f'{indent}<div id="results-container">\n')
    # Card end shifted by 1 due to insert
    lines.insert(card_end + 2, f"{indent}</div>\n")

    return lines


def convert_file(filepath: str, dry_run: bool = True) -> bool:
    """Convert a single template file. Returns True if changes were made."""
    with open(filepath) as f:
        content = f.read()

    if "compact_filters" in content:
        return False

    lines = content.split("\n")
    # Keep original line endings by working with \n-terminated
    lines_nl = [line + "\n" for line in lines]
    if lines_nl and lines_nl[-1] == "\n":
        lines_nl[-1] = ""

    block = find_filter_form_block(lines)
    if block is None:
        return False

    block_start, block_end = block
    base_url = detect_base_url(lines, block_start, block_end, filepath)
    selects = extract_selects(lines, block_start, block_end)
    dates = extract_dates(lines, block_start, block_end)
    has_dates = len(dates) >= 2
    date_names = [d["name"] for d in dates]

    # Detect indent level
    indent = "    "
    m = re.match(r"^(\s*)", lines[block_start])
    if m:
        indent = m.group(1)

    # Build replacement
    replacement = build_compact_filters_block(
        base_url=base_url,
        selects=selects,
        has_dates=has_dates,
        date_names=date_names,
        indent=indent,
    )

    # Replace the filter block
    new_lines = lines[:block_start] + replacement + lines[block_end + 1 :]

    # Update import line
    new_lines = update_import_line(new_lines)

    # Wrap results container
    # Find where the filter block ends in new lines
    filter_end_approx = block_start + len(replacement) - 1
    new_lines = wrap_results_container(new_lines, filter_end_approx)

    new_content = "\n".join(new_lines)

    if dry_run:
        print(f"\n{'=' * 60}")
        print(f"FILE: {filepath}")
        print(f"  base_url: {base_url}")
        print(f"  selects: {[s['name'] for s in selects]}")
        print(f"  dates: {date_names}")
        print(f"  filter block: lines {block_start + 1}-{block_end + 1}")
        print(f"  replacement lines: {len(replacement)}")
        return True
    else:
        with open(filepath, "w") as f:
            f.write(new_content)
        print(f"  CONVERTED: {filepath}")
        return True


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Preview changes")
    parser.add_argument("--execute", action="store_true", help="Apply changes")
    parser.add_argument("--file", help="Convert a single file")
    args = parser.parse_args()

    if not args.dry_run and not args.execute:
        print("Specify --dry-run or --execute")
        sys.exit(1)

    if args.file:
        files = [args.file]
    else:
        files = []
        for root, _dirs, filenames in os.walk("templates"):
            for f in filenames:
                if not f.endswith(".html"):
                    continue
                path = os.path.join(root, f)
                with open(path) as fh:
                    content = fh.read()
                if "compact_filters" in content or "live_search" in content:
                    continue
                if 'form method="get"' not in content:
                    continue
                if "<select name=" not in content:
                    continue
                files.append(path)

    files.sort()
    converted = 0
    for f in files:
        try:
            if convert_file(f, dry_run=args.dry_run):
                converted += 1
        except Exception as e:
            print(f"  ERROR: {f}: {e}")

    print(
        f"\n{'DRY RUN' if args.dry_run else 'EXECUTED'}: {converted}/{len(files)} files converted"
    )


if __name__ == "__main__":
    main()
