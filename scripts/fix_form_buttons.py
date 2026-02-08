#!/usr/bin/env python3
"""Standardize form button layouts across all Jinja2 templates.

Canonical pattern:
  <div class="flex items-center justify-end gap-3 pt-6 border-t border-slate-200 dark:border-slate-700">
      <a href="/cancel-url" class="btn btn-secondary">Cancel</a>
      <button type="submit" class="btn btn-primary">Save</button>
  </div>

Usage:
    python scripts/fix_form_buttons.py --dry-run
    python scripts/fix_form_buttons.py
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

SKIP_DIRS = {"email", "emails", "payslip"}

SKIP_FILES = {
    "finance/two_factor.html",
    "finance/profile.html",
    "base.html",
    "components/settings_macros.html",
}


def is_form_button_container(lines: list[str], div_line_idx: int) -> bool:
    """Check if a flex div is a form button container with Cancel + Submit."""
    lookahead = "\n".join(lines[div_line_idx : div_line_idx + 20])
    has_submit = bool(
        re.search(r'type="submit"', lookahead)
        or re.search(r"btn btn-primary|btn btn-danger", lookahead)
    )
    has_cancel = bool(re.search(r"btn btn-secondary|btn btn-ghost", lookahead))
    if not (has_submit and has_cancel):
        return False
    # Exclude modal action buttons
    modal_pat = (
        r"""@click\s*=\s*["'].*(?:false|close|hidden)"""
        r"""|onclick\s*=.*(?:close|hidden|classList)"""
    )
    if re.search(modal_pat, lookahead):
        return False
    # Exclude pagination containers
    if "Showing" in lookahead:
        return False
    # Exclude headers
    if re.search(r"<h[1-6]", lookahead):
        return False
    # Exclude justify-between containers with special left-side content
    div_line = lines[div_line_idx].strip()
    if "justify-between" in div_line:
        for i in range(div_line_idx + 1, min(div_line_idx + 5, len(lines))):
            next_line = lines[i].strip()
            if not next_line:
                continue
            # "Required fields" text
            if next_line.startswith("<p ") or next_line.startswith("<p>"):
                return False
            # Nested flex with delete button
            if (
                next_line.startswith("<div")
                and "flex" in next_line
                and "gap-" in next_line
            ):
                return False
            # Display text spans (e.g. "Total Weightage:")
            if next_line.startswith("<span"):
                return False
            # Checkbox label
            if next_line.startswith("<label"):
                return False
            # Jinja conditional
            if next_line.startswith("{%"):
                return False
            break
    return True


def extract_classes(line: str) -> str | None:
    """Extract the class attribute value from a div line."""
    match = re.search(r'class="([^"]*)"', line)
    return match.group(1) if match else None


def needs_fix(class_str: str) -> bool:
    """Check if a form button container needs fixing."""
    classes = set(class_str.split())
    if "justify-between" in classes:
        return True
    if "gap-2" in classes or "gap-4" in classes:
        return True
    if "border-t" not in classes:
        return True
    if "border-slate-200" not in classes:
        return True
    if "dark:border-slate-700" not in classes:
        return True
    if "pt-6" not in classes:
        return True
    if "items-center" not in classes:
        return True
    return "py-4" in classes


def fix_classes(class_str: str) -> str:
    """Fix the classes to match the canonical pattern."""
    classes = class_str.split()
    new_classes = []
    for cls in classes:
        if cls == "justify-between":
            if "justify-end" not in classes:
                new_classes.append("justify-end")
            continue
        if cls in ("gap-2", "gap-4"):
            if "gap-3" not in new_classes:
                new_classes.append("gap-3")
            continue
        if cls == "py-4":
            continue
        if cls == "pt-4":
            continue
        new_classes.append(cls)
    # Ensure all required classes present in order
    required = [
        ("flex", None),
        ("items-center", "flex"),
        ("justify-end", "items-center"),
        ("gap-3", "justify-end"),
        ("pt-6", "gap-3"),
        ("border-t", "pt-6"),
        ("border-slate-200", "border-t"),
        ("dark:border-slate-700", "border-slate-200"),
    ]
    for cls_name, after in required:
        if cls_name not in new_classes:
            if after is None:
                new_classes.insert(0, cls_name)
            elif after in new_classes:
                idx = new_classes.index(after) + 1
                new_classes.insert(idx, cls_name)
            else:
                new_classes.append(cls_name)
    return " ".join(new_classes)


def process_file(filepath: Path, dry_run: bool = False) -> list[dict]:
    """Process a single template file and fix form button containers."""
    changes: list[dict] = []
    try:
        content = filepath.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        logger.warning("Cannot read %s: %s", filepath, e)
        return changes
    lines = content.split("\n")
    modified = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith("<div"):
            continue
        class_str = extract_classes(stripped)
        if not class_str:
            continue
        classes = set(class_str.split())
        if "flex" not in classes:
            continue
        if "justify-between" not in classes and "justify-end" not in classes:
            continue
        if not is_form_button_container(lines, i):
            continue
        if not needs_fix(class_str):
            continue
        new_class_str = fix_classes(class_str)
        if new_class_str != class_str:
            old_attr = 'class="' + class_str + '"'
            new_attr = 'class="' + new_class_str + '"'
            new_line = line.replace(old_attr, new_attr)
            rel_path = filepath.relative_to(TEMPLATES_DIR)
            changes.append(
                {
                    "file": str(rel_path),
                    "line": i + 1,
                    "old": class_str,
                    "new": new_class_str,
                }
            )
            if not dry_run:
                lines[i] = new_line
                modified = True
    if modified and not dry_run:
        filepath.write_text("\n".join(lines), encoding="utf-8")
    return changes


def find_template_files() -> list[Path]:
    """Find all HTML template files, excluding skip directories."""
    files = []
    for html_file in sorted(TEMPLATES_DIR.rglob("*.html")):
        rel = html_file.relative_to(TEMPLATES_DIR)
        parts = rel.parts
        skip = any(skip_dir in parts for skip_dir in SKIP_DIRS)
        if str(rel) in SKIP_FILES:
            skip = True
        if not skip:
            files.append(html_file)
    return files


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Standardize form button layouts across templates"
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview changes")
    parser.add_argument("--verbose", "-v", action="store_true", help="Detailed output")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(message)s",
    )
    if not TEMPLATES_DIR.exists():
        logger.error("Templates directory not found: %s", TEMPLATES_DIR)
        return 1
    files = find_template_files()
    logger.info("Scanning %d template files...", len(files))
    all_changes: list[dict] = []
    for filepath in files:
        changes = process_file(filepath, dry_run=args.dry_run)
        all_changes.extend(changes)
    if not all_changes:
        logger.info("No changes needed. All form button containers already match.")
        return 0
    changes_by_file: dict[str, list[dict]] = {}
    for change in all_changes:
        fname = str(change["file"])
        changes_by_file.setdefault(fname, []).append(change)
    mode = "DRY RUN - " if args.dry_run else ""
    logger.info(
        "\n%s%d fix(es) across %d file(s):\n",
        mode,
        len(all_changes),
        len(changes_by_file),
    )
    for fname, file_changes in sorted(changes_by_file.items()):
        logger.info("  %s:", fname)
        for change in file_changes:
            logger.info("    Line %s:", change["line"])
            logger.info("      - %s", change["old"])
            logger.info("      + %s", change["new"])
        logger.info("")
    if args.dry_run:
        logger.info("Run without --dry-run to apply %d change(s).", len(all_changes))
    else:
        logger.info(
            "Applied %d change(s) to %d file(s).",
            len(all_changes),
            len(changes_by_file),
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
