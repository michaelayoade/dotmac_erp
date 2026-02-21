#!/usr/bin/env python3
"""
Downgrade <h1> to <h2> in templates that already define {% block page_title %}.

The topbar macro renders page_title as the primary <h1>. Any additional <h1>
inside {% block content %} creates duplicate h1 elements (bad for semantics
and accessibility). This script downgrades them to <h2>.

Idempotent: re-running produces 0 changes.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

TEMPLATES_DIR = Path("/root/dotmac/templates")

# Skip these directories — macros, base templates, components
SKIP_DIRS = {"components", "partials"}

# Skip these specific files (base templates that define the topbar itself)
SKIP_FILES = {
    "admin/base_admin.html",
    "finance/base_finance.html",
    "people/base_people.html",
    "modules/base_modules.html",
    "inventory/base_inventory.html",
    "procurement/base_procurement.html",
    "expense/base_expense.html",
}

H1_OPEN = re.compile(r"<h1(\s|>)")
H1_CLOSE = re.compile(r"</h1>")


def should_skip(path: Path) -> bool:
    rel = path.relative_to(TEMPLATES_DIR)
    parts = rel.parts

    # Skip files in excluded directories
    if any(part in SKIP_DIRS for part in parts):
        return True

    # Skip specific base template files
    rel_str = str(rel).replace("\\", "/")
    if rel_str in SKIP_FILES:
        return True

    return False


def process_file(path: Path) -> int:
    """Process a single file. Returns number of h1 tags replaced."""
    content = path.read_text(encoding="utf-8")

    # Only process files that define {% block page_title %}
    if "{% block page_title %}" not in content:
        return 0

    # Check if there are any <h1> tags to replace
    if not H1_OPEN.search(content):
        return 0

    count = 0
    new_content = content

    # Replace <h1 with <h2 (preserving attributes)
    new_content, n = H1_OPEN.subn(r"<h2\1", new_content)
    count += n

    # Replace </h1> with </h2>
    new_content, n = H1_CLOSE.subn("</h2>", new_content)
    count += n

    if new_content != content:
        path.write_text(new_content, encoding="utf-8")
        return count
    return 0


def main() -> None:
    files = sorted(TEMPLATES_DIR.rglob("*.html"))
    total_files = 0
    total_replacements = 0
    changed_files: list[str] = []

    for path in files:
        if should_skip(path):
            continue

        replacements = process_file(path)
        if replacements > 0:
            rel = path.relative_to(TEMPLATES_DIR)
            changed_files.append(f"  {rel} ({replacements} tags)")
            total_files += 1
            total_replacements += replacements

    if changed_files:
        print(
            f"Changed {total_files} files ({total_replacements} h1→h2 replacements):\n"
        )
        for line in changed_files:
            print(line)
    else:
        print("No changes needed (0 files with duplicate h1 tags).")

    sys.exit(0)


if __name__ == "__main__":
    main()
