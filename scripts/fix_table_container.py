#!/usr/bin/env python3
"""
Replace overflow-x-auto table wrappers with table-container class.

The table-container class provides overflow-x-auto PLUS border, background,
and border-radius per the design system.

Handles patterns:
1. <div class="overflow-x-auto"> wrapping a <table> → <div class="table-container">
2. Standalone <table> without any wrapper → adds <div class="table-container"> wrapper
"""

import os
import sys

TEMPLATES_DIR = "templates"

# Skip document/PDF templates that intentionally use inline styling
SKIP_PATTERNS = [
    "documents/",
    "email/",
    "_pdf",
    "print_",
    "contract_document",
    "letter_",
    "offer_",
]


def should_skip(filepath: str) -> bool:
    return any(pat in filepath for pat in SKIP_PATTERNS)


def fix_overflow_to_container(content: str) -> tuple[str, int]:
    """Replace <div class="overflow-x-auto"> with <div class="table-container">
    when followed by a <table> tag within the next few lines."""
    fixes = 0
    lines = content.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Pattern: <div class="overflow-x-auto"> followed by <table
        if (
            'class="overflow-x-auto"' in stripped
            or "class='overflow-x-auto'" in stripped
        ):
            # Check next 3 lines for a <table
            has_table = False
            for j in range(i, min(i + 4, len(lines))):
                if "<table" in lines[j]:
                    has_table = True
                    break
            if has_table:
                lines[i] = line.replace("overflow-x-auto", "table-container")
                fixes += 1

        i += 1

    return "\n".join(lines), fixes


def fix_file(filepath: str, dry_run: bool = False) -> int:
    with open(filepath, encoding="utf-8") as f:
        content = f.read()

    new_content, fixes = fix_overflow_to_container(content)

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
