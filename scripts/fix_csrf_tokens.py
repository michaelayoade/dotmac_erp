#!/usr/bin/env python3
"""
Add CSRF tokens to all POST forms missing them.

Finds <form method="POST"> tags without {{ request.state.csrf_form | safe }}
and inserts the CSRF token line right after the opening <form> tag.
"""

import os
import re
import sys

TEMPLATES_DIR = "templates"
CSRF_LINE = "    {{ request.state.csrf_form | safe }}"

# Pattern to find opening <form> tags with method="POST" (case-insensitive)
FORM_OPEN_RE = re.compile(
    r'(<form\b[^>]*\bmethod=["\'](?:POST|post)["\'][^>]*>)',
    re.IGNORECASE,
)

# Check if csrf_form or csrf_token exists anywhere between form open and close
CSRF_CHECK_RE = re.compile(r"csrf_form|csrf_token", re.IGNORECASE)


def find_form_blocks(content: str) -> list[tuple[int, int, str]]:
    """Find all form blocks: (start, end, opening_tag)."""
    blocks = []
    for m in FORM_OPEN_RE.finditer(content):
        form_start = m.start()
        opening_tag = m.group(1)
        # Find the closing </form>
        close_pos = content.find("</form>", m.end())
        if close_pos == -1:
            close_pos = len(content)
        blocks.append((form_start, close_pos, opening_tag))
    return blocks


def needs_csrf(content: str, form_start: int, form_end: int) -> bool:
    """Check if a form block is missing CSRF token."""
    form_content = content[form_start:form_end]
    return not CSRF_CHECK_RE.search(form_content)


def fix_file(filepath: str, dry_run: bool = False) -> int:
    """Fix CSRF tokens in a single file. Returns number of fixes."""
    with open(filepath, encoding="utf-8") as f:
        content = f.read()

    blocks = find_form_blocks(content)
    if not blocks:
        return 0

    fixes = 0
    # Process in reverse order to preserve positions
    for form_start, form_end, _opening_tag in reversed(blocks):
        if needs_csrf(content, form_start, form_end):
            # Find the end of the opening <form> tag
            tag_end = content.index(">", form_start) + 1

            # Detect indentation from the form tag
            line_start = content.rfind("\n", 0, form_start) + 1
            indent = ""
            for ch in content[line_start:form_start]:
                if ch in (" ", "\t"):
                    indent += ch
                else:
                    break

            csrf_insert = f"\n{indent}    {{{{ request.state.csrf_form | safe }}}}"
            content = content[:tag_end] + csrf_insert + content[tag_end:]
            fixes += 1

    if fixes > 0 and not dry_run:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

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
                print(f"{action} {fixes} form(s) in {filepath}")
                total_fixes += fixes
                fixed_files += 1

    action = "Would fix" if dry_run else "Fixed"
    print(f"\n{action} {total_fixes} forms across {fixed_files} files")


if __name__ == "__main__":
    main()
