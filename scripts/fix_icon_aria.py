#!/usr/bin/env python3
"""
Add aria-label to icon-only buttons and links in templates.

Identifies <button> and <a> tags that:
1. Don't already have aria-label
2. Contain only an SVG (no visible text)
3. Can be matched to a known icon pattern

Only modifies elements where the purpose is clear from SVG path data.
"""

import os
import re

TEMPLATES_DIR = "/root/dotmac/templates"

# Known SVG path patterns mapped to aria-labels
# These are substring matches on the `d=` attribute of SVG paths
ICON_PATTERNS: dict[str, str] = {
    # Delete / trash
    "M19 7l-.867 12": "Delete",
    "m-5 4v6m4-6v6m1-10V4a1": "Delete",
    # Edit / pencil
    "M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11": "Edit",
    "M15.232 5.232": "Edit",
    # Close / X
    "M6 18L18 6M6 6l12 12": "Close",
    # Plus / add
    "M12 4v16m8-8H4": "Add",
    "M12 6v6m0 0v6m0-6h6m-6 0H6": "Add",
    # Eye / view
    "M15 12a3 3 0 11-6 0 3 3 0 016 0z": "View",
    "M2.458 12C3.732 7.943": "View",
    # Download
    "M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4": "Download",
    # Upload
    "M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12": "Upload",
    # Refresh / reload
    "M4 4v5h.582m15.356 2A8.001": "Refresh",
    # Copy
    "M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8": "Copy",
    # External link
    "M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14": "Open in new window",
    # Print
    "M17 17h2a2 2 0 002-2v-4a2 2 0 00-2-2H5a2 2 0 00-2 2v4a2 2 0 002 2h2": "Print",
    # Settings / cog
    "M10.325 4.317c.426-1.756 2.924-1.756 3.35 0": "Settings",
    # Filter
    "M3 4a1 1 0 011-1h16a1 1 0 011 1v2.586a1 1 0 01-.293.707l-6.414 6.414": "Filter",
    # More / dots
    "M12 5v.01M12 12v.01M12 19v.01": "More options",
    "M5 12h.01M12 12h.01M19 12h.01": "More options",
}

# Regex to find <button or <a tags that contain SVG but no text
# We use a multi-line approach: find the opening tag, check for aria-label,
# then look at content until closing tag

TAG_PATTERN = re.compile(
    r"(<(?:button|a)\b)"  # opening <button or <a
    r"([^>]*)"  # attributes
    r"(>)"  # close of opening tag
    r"(.*?)"  # content between tags
    r"(</(?:button|a)>)",  # closing tag
    re.DOTALL,
)

SVG_PATTERN = re.compile(r"<svg\b.*?</svg>", re.DOTALL)
PATH_D_PATTERN = re.compile(r'd="([^"]*)"')


def has_visible_text(content: str) -> bool:
    """Check if content has visible text outside SVG tags."""
    # Remove SVGs
    without_svg = SVG_PATTERN.sub("", content)
    # Remove HTML tags
    without_tags = re.sub(r"<[^>]+>", "", without_svg)
    # Check for meaningful text (not just whitespace)
    text = without_tags.strip()
    return len(text) > 0


def identify_icon(content: str) -> str | None:
    """Try to identify the icon from SVG path data."""
    paths = PATH_D_PATTERN.findall(content)
    for path_d in paths:
        for pattern, label in ICON_PATTERNS.items():
            if pattern in path_d:
                return label
    return None


def process_file(filepath: str) -> int:
    """Process a single HTML file. Returns number of elements modified."""
    with open(filepath, encoding="utf-8") as f:
        content = f.read()

    # Quick checks
    if "<svg" not in content:
        return 0
    if "<button" not in content and "<a " not in content:
        return 0

    modifications = 0

    def replace_tag(match: re.Match[str]) -> str:
        nonlocal modifications
        tag_open = match.group(1)  # <button or <a
        attrs = match.group(2)  # attributes
        close = match.group(3)  # >
        inner = match.group(4)  # content
        tag_close = match.group(5)  # </button> or </a>

        # Skip if already has aria-label
        if "aria-label" in attrs:
            return match.group(0)

        # Skip if has visible text
        if has_visible_text(inner):
            return match.group(0)

        # Skip if no SVG
        if "<svg" not in inner:
            return match.group(0)

        # Try to identify the icon
        label = identify_icon(inner)
        if not label:
            return match.group(0)

        # Check if there's a title attribute we should prefer
        title_match = re.search(r'title="([^"]*)"', attrs)
        if title_match:
            label = title_match.group(1)

        # Add aria-label
        modifications += 1
        return f'{tag_open}{attrs} aria-label="{label}"{close}{inner}{tag_close}'

    new_content = TAG_PATTERN.sub(replace_tag, content)

    if modifications > 0:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(new_content)

    return modifications


def main() -> None:
    total_files_modified = 0
    total_elements_updated = 0
    files_scanned = 0

    for root, _dirs, files in os.walk(TEMPLATES_DIR):
        for filename in sorted(files):
            if not filename.endswith(".html"):
                continue

            filepath = os.path.join(root, filename)
            files_scanned += 1

            count = process_file(filepath)
            if count > 0:
                rel = os.path.relpath(filepath, TEMPLATES_DIR)
                print(f"  {rel}: {count} elements updated")
                total_files_modified += 1
                total_elements_updated += count

    print()
    print(f"Files scanned:  {files_scanned}")
    print(f"Files modified: {total_files_modified}")
    print(f"Elements updated: {total_elements_updated}")


if __name__ == "__main__":
    main()
