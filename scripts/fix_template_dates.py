#!/usr/bin/env python3
"""
Replace hard-coded .strftime() calls in Jinja2 templates with org-aware filters.

Replaces:
  {{ value.strftime('%b %d, %Y') }}    → {{ value | format_date }}
  {{ value.strftime('%b %d, %Y %H:%M') }} → {{ value | format_datetime }}
  {{ value.strftime('%Y-%m-%d %H:%M') }}   → {{ value | format_datetime }}
  etc.

Preserves:
  - Form input values: value="{{ date.strftime('%Y-%m-%d') }}"  (HTML date inputs)
  - Partial formats used intentionally: strftime('%b %d'), strftime('%H:%M')
  - Conditional patterns: {{ x.strftime(...) if x else '-' }} → {{ x | format_date if x else '-' }}

Idempotent: re-running produces 0 changes.
"""

from __future__ import annotations

import os
import re
import sys

# Date-only strftime patterns that should become | format_date
DATE_PATTERNS = [
    r"%b %d, %Y",  # Jan 10, 2025
    r"%B %d, %Y",  # January 10, 2025
    r"%d/%m/%Y",  # 10/01/2025
    r"%m/%d/%Y",  # 01/10/2025
    r"%d %b %Y",  # 10 Jan 2025
    r"%d %B %Y",  # 10 January 2025
    r"%Y-%m-%d",  # 2025-01-10
    r"%d-%m-%Y",  # 10-01-2025
]

# Datetime strftime patterns that should become | format_datetime
DATETIME_PATTERNS = [
    r"%b %d, %Y %H:%M",  # Jan 10, 2025 14:30
    r"%b %d, %Y at %H:%M",  # Jan 10, 2025 at 14:30
    r"%B %d, %Y %H:%M",  # January 10, 2025 14:30
    r"%B %d, %Y at %H:%M",  # January 10, 2025 at 14:30
    r"%B %d, %Y at %I:%M %p",  # January 10, 2025 at 2:30 PM
    r"%Y-%m-%d %H:%M:%S",  # 2025-01-10 14:30:00
    r"%Y-%m-%d %H:%M",  # 2025-01-10 14:30
    r"%d/%m/%Y %H:%M",  # 10/01/2025 14:30
    r"%d/%m/%Y %H:%M:%S",  # 10/01/2025 14:30:00
    r"%d %b %Y %H:%M",  # 10 Jan 2025 14:30
    r"%d %b %Y at %H:%M",  # 10 Jan 2025 at 14:30
    r"%d %b, %Y",  # 10 Jan, 2025 (with comma variant)
    r"%A, %B %d, %Y",  # Monday, January 10, 2025
]

# Patterns to SKIP (form input values, special partial formats)
SKIP_CONTEXTS = [
    r'value="',  # HTML input value attribute
    r"value='",  # Single-quoted value attribute
    r"min=\"",  # HTML min attribute
    r"max=\"",  # HTML max attribute
]


def is_in_value_attr(line: str, match_start: int) -> bool:
    """Check if the strftime call is inside an HTML value= attribute."""
    prefix = line[:match_start]
    for ctx in SKIP_CONTEXTS:
        # Check if we're inside an unclosed value="..." attribute
        last_val = prefix.rfind(ctx)
        if last_val >= 0:
            # Check if the attribute hasn't been closed yet
            quote_char = ctx[-1]
            after_val = prefix[last_val + len(ctx) :]
            if quote_char not in after_val:
                return True
    return False


def process_line(line: str) -> tuple[str, int]:
    """Process a single line, returning (new_line, change_count)."""
    changes = 0

    # Match patterns like: expr.strftime('format')
    # Also handles conditional: expr.strftime('format') if expr else 'fallback'
    pattern = re.compile(
        r"""
        (\{\{[^}]*?)                    # Opening {{ and prefix
        ([\w.]+)                        # The expression (e.g., obj.date_field)
        \.strftime\(                    # .strftime(
        ['"]([^'"]+)['"]               # 'format_string' or "format_string"
        \)                              # )
        ([^}]*?\}\})                    # Suffix and closing }}
        """,
        re.VERBOSE,
    )

    def replace_match(m: re.Match[str]) -> str:
        nonlocal changes
        prefix = m.group(1)
        expr = m.group(2)
        fmt = m.group(3)
        suffix = m.group(4)

        # Check if inside a value= attribute (skip for form inputs)
        if is_in_value_attr(line, m.start()):
            return m.group(0)

        # Determine filter
        filt = None
        if fmt in DATETIME_PATTERNS:
            filt = "format_datetime"
        elif fmt in DATE_PATTERNS:
            filt = "format_date"

        if filt is None:
            # Unknown format pattern — skip
            return m.group(0)

        # Handle conditional: expr.strftime(...) if expr else 'x'
        # The suffix might contain: " if expr else '-' }}"
        # We need: expr | format_date if expr else '-'
        cond_match = re.match(r"\s+if\s+", suffix)
        if cond_match:
            # Conditional pattern: keep the if/else
            changes += 1
            return f"{prefix}{expr} | {filt}{suffix}"
        else:
            changes += 1
            return f"{prefix}{expr} | {filt}{suffix}"

    new_line = pattern.sub(replace_match, line)
    return new_line, changes


def process_file(filepath: str) -> int:
    """Process a single template file. Returns number of changes made."""
    with open(filepath, encoding="utf-8") as f:
        lines = f.readlines()

    new_lines = []
    total_changes = 0
    for line in lines:
        new_line, changes = process_line(line)
        new_lines.append(new_line)
        total_changes += changes

    if total_changes > 0:
        with open(filepath, "w", encoding="utf-8") as f:
            f.writelines(new_lines)

    return total_changes


def main() -> None:
    templates_dir = os.path.join(os.path.dirname(__file__), "..", "templates")
    templates_dir = os.path.abspath(templates_dir)

    if not os.path.isdir(templates_dir):
        print(f"Templates directory not found: {templates_dir}")
        sys.exit(1)

    total_files = 0
    total_changes = 0

    for root, _dirs, files in os.walk(templates_dir):
        for fname in sorted(files):
            if not fname.endswith(".html"):
                continue
            filepath = os.path.join(root, fname)
            changes = process_file(filepath)
            if changes > 0:
                rel = os.path.relpath(filepath, templates_dir)
                print(f"  {rel}: {changes} replacement(s)")
                total_files += 1
                total_changes += changes

    print(f"\nTotal: {total_changes} replacements in {total_files} files")


if __name__ == "__main__":
    main()
