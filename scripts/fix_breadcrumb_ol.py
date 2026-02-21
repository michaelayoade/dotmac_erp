#!/usr/bin/env python3
"""Fix breadcrumb <nav> blocks to use semantic <ol>/<li> structure (WCAG 2.2).

Transforms:
    <nav class="flex items-center gap-2 text-sm ..." aria-label="Breadcrumb">
        <a href="/dashboard" class="...">Dashboard</a>
        {{ icon_svg("chevron-right", "h-4 w-4") }}
        <span class="..." aria-current="page">Current</span>
    </nav>

Into:
    <nav aria-label="Breadcrumb">
        <ol class="flex items-center gap-2 text-sm ...">
            <li><a href="/dashboard" class="...">Dashboard</a></li>
            <li class="flex items-center gap-2">{{ icon_svg("chevron-right", "h-4 w-4") }} <span ...>Current</span></li>
        </ol>
    </nav>

Handles both chevron-right icon and '/' text separators.
Idempotent: skips files already containing <ol> inside the breadcrumb nav.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

TEMPLATES_DIR = Path("templates")

# Separators we recognise inside breadcrumb navs
SEPARATOR_PATTERNS = [
    re.compile(
        r'^\s*\{\{\s*icon_svg\(\s*["\']chevron-right["\']'
    ),  # {{ icon_svg("chevron-right", ...) }}
    re.compile(r'^\s*<span\s+class="mx-2">/</span>'),  # <span class="mx-2">/</span>
]

# Classes that belong on the <ol> (layout), vs staying on <nav> (text/color)
LAYOUT_CLASSES = {"flex", "inline-flex", "items-center"}
# gap-* classes also move to ol
GAP_RE = re.compile(r"^gap-\d")
# mt-* or other margin/position classes stay on nav
KEEP_ON_NAV = re.compile(r"^(mt-|mb-|ml-|mr-|m-|p-|pt-|pb-)")


def is_separator_line(line: str) -> bool:
    """Return True if this line is a breadcrumb separator."""
    stripped = line.strip()
    return any(p.match(stripped) for p in SEPARATOR_PATTERNS)


def split_nav_classes(all_classes: str) -> tuple[str, str]:
    """Split classes into (nav_classes, ol_classes).

    Layout classes (flex, items-center, gap-*) go on the <ol>.
    Text/color/spacing classes stay on the <nav>.
    """
    nav_parts: list[str] = []
    ol_parts: list[str] = []

    for cls in all_classes.split():
        if cls in LAYOUT_CLASSES or GAP_RE.match(cls):
            ol_parts.append(cls)
        else:
            nav_parts.append(cls)

    return " ".join(nav_parts), " ".join(ol_parts)


def transform_file(filepath: Path) -> bool:
    """Transform breadcrumb in a single file. Returns True if changed."""
    text = filepath.read_text()

    # Quick idempotency: already has <ol inside a Breadcrumb nav
    # Look for <ol after aria-label="Breadcrumb" and before </nav>
    bc_idx = text.find('aria-label="Breadcrumb"')
    if bc_idx == -1:
        return False
    nav_end_idx = text.find("</nav>", bc_idx)
    if nav_end_idx == -1:
        return False
    bc_section = text[bc_idx:nav_end_idx]
    if "<ol" in bc_section:
        return False  # Already fixed

    lines = text.split("\n")
    result_lines: list[str] = []
    i = 0
    changed = False

    while i < len(lines):
        line = lines[i]

        # Look for the breadcrumb nav opening
        if 'aria-label="Breadcrumb"' in line and "<nav" in line:
            # Parse the nav opening tag
            nav_line = line

            # Extract the class attribute
            class_match = re.search(r'class="([^"]*)"', nav_line)
            if not class_match:
                result_lines.append(line)
                i += 1
                continue

            all_classes = class_match.group(1)
            nav_classes, ol_classes = split_nav_classes(all_classes)

            # Rebuild the nav tag with only nav_classes
            new_nav_line = re.sub(
                r'class="[^"]*"',
                f'class="{nav_classes}"' if nav_classes else "",
                nav_line,
            )
            # Clean up double-spaces from removed class attr
            new_nav_line = re.sub(r"  +", " ", new_nav_line)
            # Ensure aria-label is preserved
            if 'class=""' in new_nav_line:
                new_nav_line = new_nav_line.replace('class="" ', "")

            # Detect indentation from the nav line
            nav_indent = len(nav_line) - len(nav_line.lstrip())
            base_indent = " " * nav_indent
            inner_indent = base_indent + "    "
            item_indent = inner_indent + "    "

            # Collect inner lines until </nav>
            inner_lines: list[str] = []
            i += 1
            nav_close_line = ""
            while i < len(lines):
                if "</nav>" in lines[i]:
                    nav_close_line = lines[i]
                    break
                inner_lines.append(lines[i])
                i += 1

            # Parse inner lines into breadcrumb items
            # Each item is either a separator line or a content line
            # Group them: first content, then (separator + content) pairs
            items: list[tuple[str | None, str]] = []  # (separator, content)
            pending_sep: str | None = None

            for inner_line in inner_lines:
                stripped = inner_line.strip()
                if not stripped:
                    continue
                if is_separator_line(inner_line):
                    pending_sep = stripped
                else:
                    items.append((pending_sep, stripped))
                    pending_sep = None

            if not items:
                # Empty breadcrumb - leave as-is
                result_lines.append(line)
                for il in inner_lines:
                    result_lines.append(il)
                result_lines.append(nav_close_line)
                i += 1
                continue

            # Build new structure
            result_lines.append(new_nav_line)

            ol_class_attr = f' class="{ol_classes}"' if ol_classes else ""
            result_lines.append(f"{inner_indent}<ol{ol_class_attr}>")

            for idx, (sep, content) in enumerate(items):
                if idx == 0:
                    # First item - no separator
                    result_lines.append(f"{item_indent}<li>{content}</li>")
                else:
                    # Subsequent items - separator inside <li>
                    li_classes = "flex items-center gap-2"
                    # For / separator breadcrumbs, use simpler layout
                    if sep and "<span" in sep and "/" in sep:
                        li_classes = "flex items-center"
                    result_lines.append(
                        f'{item_indent}<li class="{li_classes}">{sep} {content}</li>'
                    )

            result_lines.append(f"{inner_indent}</ol>")
            result_lines.append(nav_close_line)
            changed = True
            i += 1
        else:
            result_lines.append(line)
            i += 1

    if changed:
        filepath.write_text("\n".join(result_lines))

    return changed


def main() -> None:
    """Run the breadcrumb fix across all templates."""
    templates_dir = TEMPLATES_DIR
    if not templates_dir.exists():
        print(f"ERROR: {templates_dir} not found. Run from project root.")
        sys.exit(1)

    # Find all template files with breadcrumbs
    candidates = sorted(
        f
        for f in templates_dir.rglob("*.html")
        if 'aria-label="Breadcrumb"' in f.read_text()
    )

    total = len(candidates)
    fixed = 0
    skipped = 0

    for filepath in candidates:
        try:
            if transform_file(filepath):
                fixed += 1
                print(f"  FIXED: {filepath}")
            else:
                skipped += 1
        except Exception as e:
            print(f"  ERROR: {filepath}: {e}")

    print(f"\nDone: {fixed} fixed, {skipped} skipped (already OK), {total} total")


if __name__ == "__main__":
    main()
