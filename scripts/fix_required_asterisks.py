#!/usr/bin/env python3
"""Add required field asterisks to form labels in Jinja2 templates.

Scans all HTML templates for <input>, <select>, and <textarea> elements
that have a `required` attribute but whose associated <label> does not
include a required-field indicator asterisk.

Usage:
    python scripts/fix_required_asterisks.py --dry-run
    python scripts/fix_required_asterisks.py
"""

from __future__ import annotations

import argparse
import os
import re
import sys

SKIP_DIRS = {"email", "emails", "payslip", "payslips"}

ALREADY_REQUIRED_PATTERNS = [
    "text-rose-500",
    "text-red-500",
    "text-red-400",
    'class="required"',
    "form-label-required",
    "form-label required",
]

ASTERISK_SPAN = ' <span class="text-rose-500">*</span>'


def find_template_files(templates_dir: str) -> list[str]:
    files = []
    for root, dirs, filenames in os.walk(templates_dir):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fn in filenames:
            if fn.endswith(".html"):
                files.append(os.path.join(root, fn))
    return sorted(files)


def label_has_required_indicator(label_text: str) -> bool:
    return any(p in label_text for p in ALREADY_REQUIRED_PATTERNS)


def extract_full_tag(lines: list[str], start_idx: int, start_col: int) -> str:
    """Extract a complete HTML tag spanning multiple lines."""
    combined = ""
    for i in range(start_idx, min(start_idx + 12, len(lines))):
        if i == start_idx:
            combined += lines[i][start_col:]
        else:
            combined += "\n" + lines[i]
        in_quote = None
        found_close = False
        for ch in combined:
            if in_quote:
                if ch == in_quote:
                    in_quote = None
                continue
            if ch in ('"', "'"):
                in_quote = ch
                continue
            if ch == ">":
                found_close = True
                break
        if found_close:
            return combined
    return combined


def tag_has_required(tag_text: str) -> bool:
    return bool(re.search(r"(?<![\w:-])\brequired\b", tag_text))


def find_label_end(lines: list[str], start_idx: int) -> tuple[int, int] | None:
    for i in range(start_idx, min(start_idx + 5, len(lines))):
        pos = lines[i].find("</label>")
        if pos >= 0:
            return (i, pos)
    return None


def find_associated_field(lines: list[str], label_end_line: int) -> str | None:
    """Look ahead from label end to find the next input/select/textarea."""
    for i in range(label_end_line, min(label_end_line + 6, len(lines))):
        line = lines[i]
        match = re.search(r"<(input|select|textarea)\b", line, re.IGNORECASE)
        if match:
            tag_text = extract_full_tag(lines, i, match.start())
            return tag_text
        if i > label_end_line:
            stripped = line.strip()
            if "<label" in line:
                break
            if stripped in ("</div>", "</td>", "</section>"):
                break
    return None


def process_file(filepath: str, dry_run: bool) -> list[str]:
    with open(filepath, encoding="utf-8") as f:
        content = f.read()

    lines = content.split("\n")
    fixes: list[tuple[int, int, str]] = []

    i = 0
    while i < len(lines):
        line = lines[i]
        search_start = 0
        while True:
            label_pos = line.find("<label", search_start)
            if label_pos < 0:
                break
            search_start = label_pos + 6

            label_end = find_label_end(lines, i)
            if label_end is None:
                continue

            end_line, end_char = label_end

            # Extract full label text
            label_parts = []
            for j in range(i, end_line + 1):
                if j == i and j == end_line:
                    label_parts.append(lines[j][label_pos : end_char + 8])
                elif j == i:
                    label_parts.append(lines[j][label_pos:])
                elif j == end_line:
                    label_parts.append(lines[j][: end_char + 8])
                else:
                    label_parts.append(lines[j])
            label_text = "\n".join(label_parts)

            # Skip labels that already have an indicator
            if label_has_required_indicator(label_text):
                continue
            if "sr-only" in label_text:
                continue
            # Skip labels with a plain-text asterisk (e.g. "Bank Name *")
            inner = re.search(r">\s*(.*?)\s*</label>", label_text, re.DOTALL)
            if inner and inner.group(1).rstrip().endswith("*"):
                continue

            # Only fix labels with form-label or block text-* classes
            has_form_label = "form-label" in label_text
            has_block_label = bool(re.search(r'class="block\s+text-', label_text))
            if not has_form_label and not has_block_label:
                if 'class="' in label_text:
                    continue

            # Check if associated field has required
            field_text = find_associated_field(lines, end_line)
            if field_text is None:
                continue
            if not tag_has_required(field_text):
                continue

            # Extract display label for reporting
            inner_match = re.search(r">\s*(.*?)\s*</label>", label_text, re.DOTALL)
            label_display = ""
            if inner_match:
                label_display = re.sub(r"<[^>]+>", "", inner_match.group(1)).strip()
                label_display = " ".join(label_display.split())

            fixes.append((end_line, end_char, label_display))
        i += 1

    if not fixes:
        return []

    modified_lines = list(lines)
    changes = []
    for end_line, end_char, label_display in reversed(fixes):
        old_line = modified_lines[end_line]
        new_line = old_line[:end_char] + ASTERISK_SPAN + old_line[end_char:]
        modified_lines[end_line] = new_line
        changes.append(f"L{end_line + 1}: {label_display!r}")

    changes.reverse()

    if not dry_run:
        new_content = "\n".join(modified_lines)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(new_content)

    return changes


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Add required-field asterisks to form labels."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without modifying files.",
    )
    parser.add_argument(
        "--templates-dir", default="templates", help="Path to templates directory."
    )
    args = parser.parse_args()

    templates_dir = args.templates_dir
    if not os.path.isdir(templates_dir):
        print(f"ERROR: Templates directory not found: {templates_dir}")
        sys.exit(1)

    mode = "DRY RUN" if args.dry_run else "APPLY"
    print(f"[{mode}] Scanning {templates_dir}/ for missing required asterisks...\n")

    files = find_template_files(templates_dir)
    total_fixes = 0
    files_changed = 0

    for filepath in files:
        relpath = os.path.relpath(filepath, ".")
        changes = process_file(filepath, args.dry_run)
        if changes:
            files_changed += 1
            total_fixes += len(changes)
            suffix = "es" if len(changes) != 1 else ""
            print(f"{relpath} ({len(changes)} fix{suffix}):")
            for change in changes:
                print(f"  {change}")
            print()

    print("=" * 60)
    dry_run_suffix = " (dry run)" if args.dry_run else ""
    print(
        f"Total: {total_fixes} labels fixed across {files_changed} files{dry_run_suffix}"
    )


if __name__ == "__main__":
    main()
