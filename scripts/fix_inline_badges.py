#!/usr/bin/env python3
"""
Migrate inline status badge HTML to the {{ status_badge() }} macro.

Handles:
1. If/elif chains: {% if expr == "X" %}<span class="...badge...">X</span>{% elif...%}...{% endif %}
2. Single <span> with conditional classes for status display
3. Simple inline badges: <span class="inline-flex rounded-full bg-*-100...">{{ status }}</span>

Usage:
    poetry run python scripts/fix_inline_badges.py --dry-run
    poetry run python scripts/fix_inline_badges.py --execute
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

SKIP_FILES = {
    "components/macros.html",
    "components/_badges.html",
}

# Color class prefixes that indicate a status badge
BADGE_COLOR_PREFIXES = (
    "bg-emerald-",
    "bg-amber-",
    "bg-rose-",
    "bg-blue-",
    "bg-slate-1",  # bg-slate-100
    "bg-sky-",
    "bg-violet-",
    "bg-green-",
    "bg-red-",
    "bg-yellow-",
    "bg-teal-",
    "bg-indigo-",
    "bg-orange-",
)


def find_matching_endif(content: str, if_start: int) -> int:
    """Find the end position of the matching {%% endif %%}."""
    depth = 0
    pos = if_start
    while pos < len(content):
        tag_start = content.find("{%", pos)
        if tag_start == -1:
            return -1
        tag_end = content.find("%}", tag_start + 2)
        if tag_end == -1:
            return -1
        tag_end += 2

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


def is_badge_html(html: str) -> bool:
    """Check if HTML snippet contains badge-like content.

    A badge is a <span> with rounded-full and a status-color background.
    """
    has_span = "<span" in html
    has_rounded = "rounded-full" in html or "rounded" in html
    has_color = any(prefix in html for prefix in BADGE_COLOR_PREFIXES)
    has_inline = "inline-flex" in html or "inline-block" in html
    has_badge_class = "badge-" in html  # semantic badge class
    return has_span and ((has_rounded and has_color and has_inline) or has_badge_class)


def extract_status_variable(block: str) -> str | None:
    """Extract the variable tested in the if/elif chain.

    Looks for: {% if app.status.value == "APPROVED" %}
    Returns: app.status.value
    """
    match = re.search(r"\{%[-\s]*if\s+([\w.]+)\s*==\s*[\"']", block)
    if match:
        return match.group(1)
    return None


def get_indent(content: str, pos: int) -> str:
    """Get the whitespace indentation at a position."""
    line_start = content.rfind("\n", 0, pos)
    if line_start == -1:
        line_start = 0
    else:
        line_start += 1
    indent_text = content[line_start:pos]
    if indent_text.strip():
        return ""
    return indent_text


def split_if_branches(block: str) -> list[str]:
    """Split an if/elif/else block into individual branch contents.

    Returns the HTML content of each branch (stripping the Jinja2 tags).
    """
    branches: list[str] = []
    # Split on {% elif ... %}, {% else %}, {% endif %}
    parts = re.split(
        r"\{%[-\s]*(?:elif\s+[^%]*|else|endif)[-\s]*%\}",
        block,
    )
    for part in parts:
        # Strip the opening {% if ... %} from the first part
        cleaned = re.sub(r"\{%[-\s]*if\s+[^%]*%\}", "", part).strip()
        if cleaned:
            branches.append(cleaned)
    return branches


def find_badge_if_blocks(content: str) -> list[tuple[int, int, str]]:
    """Find all if/elif chains that render status badges.

    Returns list of (start, end, variable_expression) tuples.
    """
    results: list[tuple[int, int, str]] = []
    pos = 0

    while pos < len(content):
        match = re.search(r"\{%[-\s]*if\s+", content[pos:])
        if not match:
            break

        start = pos + match.start()
        end = find_matching_endif(content, start)
        if end == -1:
            pos = start + 2
            continue

        block = content[start:end]
        variable = extract_status_variable(block)

        if variable is None:
            pos = start + 2
            continue

        # Only process status-like variables
        if "status" not in variable.lower() and "state" not in variable.lower():
            pos = start + 2
            continue

        # Check each branch for badge content
        branches = split_if_branches(block)
        if not branches:
            pos = start + 2
            continue

        badge_count = 0
        non_badge_count = 0
        for branch in branches:
            if is_badge_html(branch):
                badge_count += 1
            elif "{{ status_badge(" in branch:
                badge_count += 1  # Already using macro in one branch
            elif branch.strip():
                # Check if it's a simple span (might be a badge without our color prefixes)
                if re.match(r"^\s*<span[^>]*>[^<]*</span>\s*$", branch, re.DOTALL):
                    # Could be a badge with unusual colors — count it if it has rounded
                    if "rounded" in branch:
                        badge_count += 1
                    else:
                        non_badge_count += 1
                else:
                    non_badge_count += 1

        # Only replace if ALL branches are badges (or the block only has badge content)
        if badge_count > 0 and non_badge_count == 0:
            results.append((start, end, variable))

        pos = start + 2

    # Remove nested blocks
    results.sort(key=lambda b: (b[0], -b[1]))
    outermost: list[tuple[int, int, str]] = []
    for block_info in results:
        is_nested = any(
            block_info[0] >= outer[0] and block_info[1] <= outer[1]
            for outer in outermost
        )
        if not is_nested:
            outermost.append(block_info)

    return outermost


def find_inline_class_badges(content: str) -> list[tuple[int, int, str]]:
    """Find single <span> elements with conditional classes for status.

    Pattern:
        <span class="inline-flex items-center px-2 py-0.5 rounded-full
            {% if task.status == 'PENDING' %}bg-amber-100 text-amber-700...
            {% elif ... %}...{% endif %}">
            {{ task.status | replace("_", " ") | title }}
        </span>
    """
    results: list[tuple[int, int, str]] = []

    pattern = re.compile(
        r"<span\s+class=\"[^\"]*"
        r"\{%[-\s]*if\s+([\w.]+)\s*==\s*['\"]"  # {% if EXPR == '...'
        r"[^\"]*"  # rest of class attribute
        r"\{%[-\s]*endif[-\s]*%\}"  # {% endif %}
        r"[^\"]*\">\s*"  # end of class + >
        r"\{\{[^}]*\}\}\s*"  # {{ expression }}
        r"</span>",
        re.DOTALL,
    )

    for m in pattern.finditer(content):
        variable = m.group(1)
        if "status" in variable.lower():
            results.append((m.start(), m.end(), variable))

    return results


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
            return content

        new_imports = imports_str.rstrip() + ", " + macro_name + " "
        return content[: match.start(2)] + new_imports + content[match.end(2) :]

    extends_match = re.search(r'(\{%\s*extends\s+"[^"]+"\s*%\})\n', content)
    if extends_match:
        insert_pos = extends_match.end()
        import_line = f'{{% from "components/macros.html" import {macro_name} %}}\n'
        return content[:insert_pos] + import_line + content[insert_pos:]

    import_line = f'{{% from "components/macros.html" import {macro_name} %}}\n'
    return import_line + content


def process_file(filepath: Path, templates_dir: Path) -> tuple[str, int]:
    """Process a template file and return (new_content, replacements_count)."""
    content = filepath.read_text(encoding="utf-8")
    rel_path = str(filepath.relative_to(templates_dir))

    if rel_path in SKIP_FILES:
        return content, 0

    replacements = 0

    # Find if/elif badge chains
    badge_blocks = find_badge_if_blocks(content)

    # Find inline class badges (span with conditional classes)
    inline_badges = find_inline_class_badges(content)

    # Combine and sort by position (process from end to start to preserve positions)
    all_badges: list[tuple[int, int, str]] = badge_blocks + inline_badges
    all_badges.sort(key=lambda b: b[0], reverse=True)

    for start, end, variable in all_badges:
        indent = get_indent(content, start)
        replacement = f"{indent}{{{{ status_badge({variable}, 'sm') }}}}"
        # Replace from beginning of the line to avoid double-indent
        line_start = content.rfind("\n", 0, start)
        if line_start != -1:
            line_start += 1  # After the newline
        else:
            line_start = 0
        content = content[:line_start] + replacement + content[end:]
        replacements += 1

    if replacements > 0:
        content = ensure_import(content, "status_badge")

    return content, replacements


def scan_templates(templates_dir: Path, dry_run: bool) -> dict[str, int]:
    """Scan all template files and migrate inline badges."""
    results: dict[str, int] = {}

    for filepath in sorted(templates_dir.rglob("*.html")):
        rel_path = str(filepath.relative_to(templates_dir))
        if rel_path in SKIP_FILES:
            continue

        content = filepath.read_text(encoding="utf-8")

        # Quick checks to skip files without potential inline badges
        has_status_if = re.search(
            r"\{%[-\s]*if\s+[\w.]*status[\w.]*\s*==\s*[\"']", content
        )
        has_inline_class_badge = re.search(
            r'class="[^"]*\{%[-\s]*if\s+[\w.]*status', content
        )

        if not has_status_if and not has_inline_class_badge:
            continue

        new_content, count = process_file(filepath, templates_dir)
        if count > 0:
            results[rel_path] = count
            if not dry_run:
                filepath.write_text(new_content, encoding="utf-8")

    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate inline badges to macro")
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
            print(f"  {path}: {count} badge block(s)")

    if args.dry_run and results:
        print("\nRun with --execute to apply changes.")


if __name__ == "__main__":
    main()
