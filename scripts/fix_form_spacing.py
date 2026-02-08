#!/usr/bin/env python3
"""Fix form field spacing and responsive grid breakpoints in Jinja2 templates.

Fixes:
  1. Bare `grid-cols-2` → `grid-cols-1 sm:grid-cols-2` (mobile-first)
  2. Bare `grid-cols-3` → `grid-cols-1 sm:grid-cols-2 lg:grid-cols-3` (mobile-first)
  3. Non-standard form spacing: space-y-3/5/8 → space-y-4 or space-y-6

Usage:
    python scripts/fix_form_spacing.py --dry-run
    python scripts/fix_form_spacing.py
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

SKIP_DIRS = {"email", "emails", "payslip", "payslips"}

# Files where grid-cols-2/3 is intentional (stat grids, dashboards, non-form layouts)
SKIP_GRID_FILES = {
    "index.html",
    "components/macros.html",
    "components/settings_macros.html",
}


def find_template_files() -> list[Path]:
    """Find all HTML template files, excluding skip directories."""
    files = []
    for html_file in sorted(TEMPLATES_DIR.rglob("*.html")):
        rel = html_file.relative_to(TEMPLATES_DIR)
        parts = rel.parts
        if any(skip_dir in parts for skip_dir in SKIP_DIRS):
            continue
        files.append(html_file)
    return files


def is_inside_form(lines: list[str], line_idx: int) -> bool:
    """Check if a line is inside a <form> tag by scanning backwards."""
    form_depth = 0
    for i in range(line_idx - 1, -1, -1):
        line = lines[i]
        # Count form closings (going backwards, these increase depth needed)
        form_depth += len(re.findall(r"</form", line, re.IGNORECASE))
        # Count form openings (going backwards, these decrease depth needed)
        form_depth -= len(re.findall(r"<form[\s>]", line, re.IGNORECASE))
        if form_depth < 0:
            return True
    return False


def is_stat_grid_context(lines: list[str], line_idx: int) -> bool:
    """Check if a grid is a stat card grid (not a form grid)."""
    lookahead = "\n".join(lines[line_idx : line_idx + 10])
    # Stat cards typically contain p-5, stat-card, or metric-type content
    if re.search(r"stat-card|stat_card|p-5.*rounded|metric|dashboard", lookahead):
        return True
    # Check if inside a dl (definition list) context
    line = lines[line_idx].strip()
    return "<dl" in line


def fix_bare_grid_cols(lines: list[str], filepath: Path, dry_run: bool) -> list[dict]:
    """Fix bare grid-cols-2 and grid-cols-3 to be mobile-first responsive."""
    changes: list[dict] = []
    rel_path = filepath.relative_to(TEMPLATES_DIR)

    if str(rel_path) in SKIP_GRID_FILES:
        return changes

    for i, line in enumerate(lines):
        stripped = line.strip()

        # Skip Jinja comments and non-HTML
        if stripped.startswith("{#") or stripped.startswith("<!--"):
            continue

        # Pattern 1: bare grid-cols-2 (not preceded by sm:, md:, lg:, xl:)
        match_2 = re.search(r"(?<![a-z]:)grid-cols-2(?!\d)", line)
        if match_2:
            # Verify it's not already responsive
            if "sm:grid-cols-2" in line or "md:grid-cols-2" in line:
                continue
            # Check it has "grid" class
            if "grid" not in line:
                continue
            # Skip stat grids and non-form contexts
            if is_stat_grid_context(lines, i):
                continue

            # Replace grid-cols-2 with grid-cols-1 sm:grid-cols-2
            new_line = line.replace("grid-cols-2", "grid-cols-1 sm:grid-cols-2", 1)
            if new_line != line:
                changes.append(
                    {
                        "file": str(rel_path),
                        "line": i + 1,
                        "type": "grid-cols-2 → responsive",
                        "old": "grid-cols-2",
                        "new": "grid-cols-1 sm:grid-cols-2",
                    }
                )
                if not dry_run:
                    lines[i] = new_line

        # Pattern 2: bare grid-cols-3 (not preceded by breakpoint prefix)
        match_3 = re.search(r"(?<![a-z]:)grid-cols-3(?!\d)", line)
        if match_3:
            if (
                "sm:grid-cols-3" in line
                or "md:grid-cols-3" in line
                or "lg:grid-cols-3" in line
            ):
                continue
            if "grid" not in line:
                continue
            if is_stat_grid_context(lines, i):
                continue

            # Replace grid-cols-3 with grid-cols-1 sm:grid-cols-2 lg:grid-cols-3
            new_line = line.replace(
                "grid-cols-3", "grid-cols-1 sm:grid-cols-2 lg:grid-cols-3", 1
            )
            if new_line != line:
                changes.append(
                    {
                        "file": str(rel_path),
                        "line": i + 1,
                        "type": "grid-cols-3 → responsive",
                        "old": "grid-cols-3",
                        "new": "grid-cols-1 sm:grid-cols-2 lg:grid-cols-3",
                    }
                )
                if not dry_run:
                    lines[i] = new_line

    return changes


def fix_form_spacing(lines: list[str], filepath: Path, dry_run: bool) -> list[dict]:
    """Normalize non-standard space-y values in form contexts."""
    changes: list[dict] = []
    rel_path = filepath.relative_to(TEMPLATES_DIR)

    for i, line in enumerate(lines):
        stripped = line.strip()

        # Only fix <form> tags and <div> tags inside forms
        if not (stripped.startswith("<form") or stripped.startswith("<div")):
            continue

        # Fix space-y-3 → space-y-4 (too tight for form fields)
        if "space-y-3" in line:
            if stripped.startswith("<form") or is_inside_form(lines, i):
                new_line = line.replace("space-y-3", "space-y-4", 1)
                if new_line != line:
                    changes.append(
                        {
                            "file": str(rel_path),
                            "line": i + 1,
                            "type": "space-y-3 → space-y-4",
                            "old": "space-y-3",
                            "new": "space-y-4",
                        }
                    )
                    if not dry_run:
                        lines[i] = new_line

        # Fix space-y-5 → space-y-6 (non-standard, use 6 for sections)
        if "space-y-5" in line:
            if stripped.startswith("<form") or is_inside_form(lines, i):
                new_line = line.replace("space-y-5", "space-y-6", 1)
                if new_line != line:
                    changes.append(
                        {
                            "file": str(rel_path),
                            "line": i + 1,
                            "type": "space-y-5 → space-y-6",
                            "old": "space-y-5",
                            "new": "space-y-6",
                        }
                    )
                    if not dry_run:
                        lines[i] = new_line

        # Fix space-y-8 → space-y-6 (too loose for forms)
        if "space-y-8" in line:
            if stripped.startswith("<form") or is_inside_form(lines, i):
                new_line = line.replace("space-y-8", "space-y-6", 1)
                if new_line != line:
                    changes.append(
                        {
                            "file": str(rel_path),
                            "line": i + 1,
                            "type": "space-y-8 → space-y-6",
                            "old": "space-y-8",
                            "new": "space-y-6",
                        }
                    )
                    if not dry_run:
                        lines[i] = new_line

    return changes


def process_file(filepath: Path, dry_run: bool = False) -> list[dict]:
    """Process a single template file."""
    try:
        content = filepath.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        logger.warning("Cannot read %s: %s", filepath, e)
        return []

    lines = content.split("\n")
    all_changes: list[dict] = []

    # Fix 1: Responsive grid breakpoints
    grid_changes = fix_bare_grid_cols(lines, filepath, dry_run)
    all_changes.extend(grid_changes)

    # Fix 2: Form spacing normalization
    spacing_changes = fix_form_spacing(lines, filepath, dry_run)
    all_changes.extend(spacing_changes)

    if all_changes and not dry_run:
        filepath.write_text("\n".join(lines), encoding="utf-8")

    return all_changes


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fix form spacing and responsive grid breakpoints"
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
        logger.info("No changes needed.")
        return 0

    changes_by_file: dict[str, list[dict]] = {}
    for change in all_changes:
        changes_by_file.setdefault(change["file"], []).append(change)

    # Group by fix type for summary
    by_type: dict[str, int] = {}
    for change in all_changes:
        by_type[change["type"]] = by_type.get(change["type"], 0) + 1

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
            logger.info(
                "    Line %s: %s → %s", change["line"], change["old"], change["new"]
            )
        logger.info("")

    logger.info("Summary by fix type:")
    for fix_type, count in sorted(by_type.items()):
        logger.info("  %s: %d", fix_type, count)

    if args.dry_run:
        logger.info("\nRun without --dry-run to apply %d change(s).", len(all_changes))
    else:
        logger.info(
            "\nApplied %d change(s) to %d file(s).",
            len(all_changes),
            len(changes_by_file),
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
