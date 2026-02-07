#!/usr/bin/env python3
"""Replace alert() calls in templates with toast notification dispatches.

Pattern mapping:
- alert('Error: ...')  / alert('Please ...') → type 'error'
- alert('Success...') / alert('...saved successfully') / alert('...copied...') → type 'success'
- Other alert(...) → type 'warning'
"""

import re
import sys
from pathlib import Path

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

# Patterns that indicate error toasts
ERROR_PATTERNS = [
    r"^['\"]Error",
    r"^['\"]Please ",
    r"failed",
    r"Failed",
    r"error",
    r"required",
    r"Unable to",
]

# Patterns that indicate success toasts
SUCCESS_PATTERNS = [
    r"success",
    r"Success",
    r"saved",
    r"Saved",
    r"copied",
    r"Copied",
]


def classify_alert(msg_content: str) -> str:
    """Classify the alert message type."""
    for pat in SUCCESS_PATTERNS:
        if re.search(pat, msg_content):
            return "success"
    for pat in ERROR_PATTERNS:
        if re.search(pat, msg_content):
            return "error"
    return "warning"


def find_matching_paren(text: str, start: int) -> int:
    """Find the closing paren matching the open paren at `start`.

    Handles nested parens and string literals (single, double, backtick).
    Returns index of the closing paren, or -1 if not found.
    """
    depth = 0
    i = start
    in_string = None  # None, "'", '"', or '`'

    while i < len(text):
        ch = text[i]

        # Handle escape sequences inside strings
        if in_string and ch == "\\" and i + 1 < len(text):
            i += 2
            continue

        # Toggle string state
        if ch in ("'", '"', "`"):
            if in_string is None:
                in_string = ch
            elif in_string == ch:
                in_string = None
            i += 1
            continue

        if in_string:
            i += 1
            continue

        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i

        i += 1

    return -1


def replace_alerts_in_line(line: str) -> tuple[str, int]:
    """Replace alert() calls in a single line.

    Returns (new_line, count_of_replacements).
    """
    count = 0
    result = []
    i = 0

    while i < len(line):
        # Look for 'alert(' — but not part of a larger identifier
        if line[i : i + 6] == "alert(":
            # Check it's not part of a larger identifier (e.g. "myalert(")
            if i > 0 and (line[i - 1].isalnum() or line[i - 1] == "_"):
                result.append(line[i])
                i += 1
                continue

            open_paren = i + 5  # index of '('
            close_paren = find_matching_paren(line, open_paren)

            if close_paren < 0:
                # No matching paren found, skip
                result.append(line[i])
                i += 1
                continue

            inner = line[open_paren + 1 : close_paren].strip()
            toast_type = classify_alert(inner)
            count += 1

            replacement = (
                f"window.dispatchEvent(new CustomEvent('show-toast', "
                f"{{ detail: {{ message: {inner}, type: '{toast_type}' }} }}))"
            )
            result.append(replacement)
            i = close_paren + 1
        else:
            result.append(line[i])
            i += 1

    return "".join(result), count


def process_file(filepath: Path, dry_run: bool = False) -> int:
    """Process a single file. Returns number of replacements."""
    content = filepath.read_text(encoding="utf-8")
    lines = content.split("\n")
    total = 0
    new_lines = []

    for line in lines:
        if "alert(" in line:
            new_line, count = replace_alerts_in_line(line)
            total += count
            new_lines.append(new_line)
        else:
            new_lines.append(line)

    if total > 0 and not dry_run:
        filepath.write_text("\n".join(new_lines), encoding="utf-8")
        print(f"  {filepath.relative_to(TEMPLATES_DIR)}: {total} replacement(s)")

    return total


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    if dry_run:
        print("DRY RUN — no files will be modified\n")

    total_replacements = 0
    total_files = 0

    for html_file in sorted(TEMPLATES_DIR.rglob("*.html")):
        count = process_file(html_file, dry_run=dry_run)
        if count > 0:
            total_files += 1
            total_replacements += count

    print(
        f"\nTotal: {total_replacements} alert() calls replaced in {total_files} files"
    )


if __name__ == "__main__":
    main()
