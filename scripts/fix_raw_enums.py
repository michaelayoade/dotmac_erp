#!/usr/bin/env python3
"""
Find and fix raw enum display in Jinja2 templates.

Enums render as raw uppercase (e.g., "PENDING_APPROVAL") unless filtered.
This script adds `| replace('_', ' ') | title` to common enum patterns.

Targets template expressions that:
1. End in .status, .type, .category, .priority, .method, .frequency, .mode
2. End in .value (enum .value accessor)
3. Are NOT already filtered with replace/title/status_badge
4. Are NOT in Alpine.js expressions (x-text, x-show, @click)
5. Are NOT in href/src/action attributes
"""

import os
import re
import sys

TEMPLATES_DIR = "templates"

# Enum-like field names that should be display-formatted
ENUM_FIELDS = (
    "status",
    "type",
    "category",
    "priority",
    "method",
    "frequency",
    "mode",
    "level",
    "severity",
    "source",
    "channel",
    "role",
    "stage",
    "phase",
    "state",
    "kind",
    "action_type",
    "payment_method",
    "payment_type",
    "leave_type",
    "claim_type",
    "transaction_type",
    "entity_type",
    "notification_type",
    "shift_type",
    "contract_type",
    "expense_type",
)

# Pattern: {{ something.enum_field }} without any filter
# Must NOT be followed by | replace or | title or inside an attribute
ENUM_DISPLAY_RE = re.compile(
    r"\{\{\s*"
    r"(\w+(?:\.\w+)*\.(?:" + "|".join(ENUM_FIELDS) + r"))"
    r"\s*\}\}",
)

# Patterns that indicate the value is already handled
ALREADY_FILTERED = re.compile(
    r"replace|title|upper|lower|status_badge|tojson|int|float|format_"
)

# Patterns indicating we're in a non-display context
NON_DISPLAY_CONTEXTS = re.compile(
    r"(?:href|src|action|value|name|id|x-text|x-show|x-bind|@|hx-|data-)\s*="
)


def fix_raw_enums(content: str) -> tuple[str, int]:
    """Add display filters to raw enum template expressions."""
    fixes = 0
    lines = content.split("\n")
    new_lines = []

    for line in lines:
        # Skip lines that are in non-display contexts
        if NON_DISPLAY_CONTEXTS.search(line):
            new_lines.append(line)
            continue

        # Skip lines inside Alpine.js attributes
        if "x-data" in line or "x-init" in line:
            new_lines.append(line)
            continue

        def replacer(match: re.Match) -> str:
            nonlocal fixes
            full = match.group(0)
            var = match.group(1)

            # Check surrounding context for existing filters
            # Look for the expression in the full line
            match.start()
            end = match.end()

            # Check if there's more to this expression (pipe, etc.)
            remaining = content[end : end + 50] if end < len(content) else ""
            if remaining.startswith("|") or remaining.startswith(" |"):
                return full  # Already has a filter

            fixes += 1
            return f"{{{{ {var} | replace('_', ' ') | title }}}}"

        line = ENUM_DISPLAY_RE.sub(replacer, line)
        new_lines.append(line)

    return "\n".join(new_lines), fixes


def fix_file(filepath: str, dry_run: bool = False) -> int:
    with open(filepath, encoding="utf-8") as f:
        content = f.read()

    new_content, fixes = fix_raw_enums(content)

    if fixes > 0 and not dry_run:
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
            fixes = fix_file(filepath, dry_run=dry_run)
            if fixes > 0:
                action = "Would fix" if dry_run else "Fixed"
                print(f"{action} {fixes} enum(s) in {filepath}")
                total_fixes += fixes
                fixed_files += 1

    action = "Would fix" if dry_run else "Fixed"
    print(f"\n{action} {total_fixes} enums across {fixed_files} files")


if __name__ == "__main__":
    main()
