#!/usr/bin/env python3
"""Fix <th> elements without uppercase styling in non-.table tables.

Tables using class="table" get uppercase from CSS automatically.
Other tables need inline uppercase classes on <th>.

Standard pattern:
  <th scope="col" class="px-4 py-3 text-left text-xs font-semibold uppercase
      tracking-wider text-slate-500 dark:text-slate-400">

This script:
1. Finds files with <th scope="col"> lacking 'uppercase'
2. Skips files where all tables use class="table" (CSS handles it)
3. Adds/fixes uppercase styling on remaining <th> elements
"""

import os
import re
import sys

TEMPLATES_DIR = "/root/dotmac/templates"
DRY_RUN = "--dry-run" in sys.argv

# Skip patterns
SKIP_DIRS = {"email", "payslip"}
SKIP_FILES = {"payslip_compact.html", "payslip_detailed.html"}

# Standard th class
STANDARD_TH = (
    "text-xs font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400"
)

# Procurement-style th: font-medium text-slate-600 dark:text-slate-400
PROC_PATTERN = re.compile(
    r'(<th\s[^>]*class="[^"]*?)'
    r"font-medium\s+text-slate-600\s+dark:text-slate-400"
    r'([^"]*")',
    re.DOTALL,
)

# Generic th with font-medium but no uppercase
GENERIC_FM_PATTERN = re.compile(
    r'(<th\s[^>]*class="[^"]*?)'
    r"font-medium"
    r'([^"]*")',
    re.DOTALL,
)


def should_skip(filepath: str) -> bool:
    for d in SKIP_DIRS:
        if f"/{d}/" in filepath or filepath.endswith(f"/{d}"):
            return True
    basename = os.path.basename(filepath)
    return bool(basename in SKIP_FILES or "_pdf" in basename)


def file_has_table_class_only(content: str) -> bool:
    """Check if ALL tables in file use class='table'."""
    tables = re.findall(r'<table\s[^>]*class="([^"]*)"', content)
    if not tables:
        return False
    return all("table" in cls.split() for cls in tables)


def fix_th_in_line(line: str) -> str:
    """Fix a single <th> line to include uppercase styling."""
    if "<th" not in line or "uppercase" in line:
        return line

    if 'scope="col"' not in line and 'scope="row"' not in line:
        return line

    # Pattern 1: Procurement style — font-medium text-slate-600 dark:text-slate-400
    if "font-medium" in line and "text-slate-600" in line:
        line = line.replace(
            "font-medium text-slate-600 dark:text-slate-400", STANDARD_TH
        )
        return line

    # Pattern 2: Has font-medium but different colors
    if "font-medium" in line and 'class="' in line:
        line = line.replace(
            "font-medium", "text-xs font-semibold uppercase tracking-wider"
        )
        return line

    # Pattern 3: Has class but no font-weight
    if 'class="' in line and "font-semibold" not in line and "font-medium" not in line:
        # Add uppercase tracking-wider before closing quote
        match = re.search(r'(class="[^"]*)"', line)
        if match:
            existing = match.group(1)
            if "text-xs" not in existing:
                line = line.replace(
                    match.group(0),
                    f'{existing} text-xs font-semibold uppercase tracking-wider"',
                )
            else:
                line = line.replace(
                    match.group(0),
                    f'{existing} font-semibold uppercase tracking-wider"',
                )
        return line

    return line


def process_file(filepath: str) -> int:
    """Process a single file. Returns number of changes."""
    with open(filepath) as f:
        content = f.read()

    # Skip if file only uses class="table" tables
    if file_has_table_class_only(content):
        return 0

    # Check if there are any th needing fix
    lines = content.split("\n")
    changes = 0
    new_lines = []

    for line in lines:
        if "<th" in line and 'scope="col"' in line and "uppercase" not in line:
            # Check if this th is inside a class="table" table
            # Simple heuristic: we already filtered whole-file, now fix individual
            new_line = fix_th_in_line(line)
            if new_line != line:
                changes += 1
            new_lines.append(new_line)
        else:
            new_lines.append(line)

    if changes > 0 and not DRY_RUN:
        with open(filepath, "w") as f:
            f.write("\n".join(new_lines))

    return changes


def main() -> None:
    total_changes = 0
    total_files = 0

    for root, _dirs, files in os.walk(TEMPLATES_DIR):
        for fname in sorted(files):
            if not fname.endswith(".html"):
                continue
            filepath = os.path.join(root, fname)
            if should_skip(filepath):
                continue

            changes = process_file(filepath)
            if changes > 0:
                rel = os.path.relpath(filepath, TEMPLATES_DIR)
                print(f"  {'[DRY] ' if DRY_RUN else ''}Fixed {changes} <th> in {rel}")
                total_changes += changes
                total_files += 1

    print(
        f"\nTotal: {total_changes} <th> elements in {total_files} files"
        f"{' (dry run)' if DRY_RUN else ''}"
    )


if __name__ == "__main__":
    main()
