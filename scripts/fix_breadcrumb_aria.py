#!/usr/bin/env python3
"""Add ARIA attributes to breadcrumb navigation elements.

Adds:
1. aria-label="Breadcrumb" to <nav> elements inside {% block breadcrumbs %}
2. aria-current="page" to the last <span> in each breadcrumb chain
"""

import re
import sys
from pathlib import Path

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"


def process_file(filepath: Path, dry_run: bool = False) -> bool:
    """Process a single template file. Returns True if modified."""
    content = filepath.read_text(encoding="utf-8")

    # Only process files with breadcrumb blocks
    if "{% block breadcrumbs %}" not in content:
        return False

    original = content

    # Extract the breadcrumb block content
    # Pattern: {% block breadcrumbs %} ... {% endblock %}
    block_pattern = re.compile(
        r"(\{%\s*block\s+breadcrumbs\s*%\})(.*?)(\{%\s*endblock\s*%\})",
        re.DOTALL,
    )

    def fix_breadcrumb_block(m: re.Match) -> str:
        block_start = m.group(1)
        block_content = m.group(2)
        block_end = m.group(3)

        # 1. Add aria-label="Breadcrumb" to <nav> if missing
        if 'aria-label="Breadcrumb"' not in block_content:
            # Match <nav with optional existing attributes
            block_content = re.sub(
                r"<nav\b([^>]*?)>",
                lambda nav_m: f'<nav{nav_m.group(1)} aria-label="Breadcrumb">',
                block_content,
                count=1,  # Only first <nav> in block
            )

        # 2. Add aria-current="page" to the last <span> in the breadcrumb
        if 'aria-current="page"' not in block_content:
            # Find all <span ...> tags in the block
            # The last span with text (not separator spans like <span class="mx-2">/</span>)
            # is the current page indicator
            spans = list(
                re.finditer(
                    r"<span\b([^>]*?)>(.*?)</span>",
                    block_content,
                    re.DOTALL,
                )
            )

            if spans:
                # Find the last span that is NOT a separator (not just "/" or "›")
                last_content_span = None
                for span in reversed(spans):
                    inner = span.group(2).strip()
                    if inner not in ("/", "›", "»", "|"):
                        last_content_span = span
                        break

                if last_content_span:
                    attrs = last_content_span.group(1)
                    inner = last_content_span.group(2)
                    old = last_content_span.group(0)
                    new = f'<span{attrs} aria-current="page">{inner}</span>'
                    # Replace only the last occurrence
                    idx = block_content.rfind(old)
                    if idx >= 0:
                        block_content = (
                            block_content[:idx] + new + block_content[idx + len(old) :]
                        )

        return block_start + block_content + block_end

    content = block_pattern.sub(fix_breadcrumb_block, content)

    if content != original:
        if not dry_run:
            filepath.write_text(content, encoding="utf-8")
            print(f"  {filepath.relative_to(TEMPLATES_DIR)}")
        return True
    return False


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    if dry_run:
        print("DRY RUN — no files will be modified\n")

    modified = 0
    for html_file in sorted(TEMPLATES_DIR.rglob("*.html")):
        if process_file(html_file, dry_run=dry_run):
            modified += 1

    print(f"\nTotal: {modified} files updated with breadcrumb ARIA attributes")


if __name__ == "__main__":
    main()
