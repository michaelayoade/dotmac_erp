#!/usr/bin/env python3
"""
Add scope="col" to all <th> elements inside <thead> blocks
that don't already have a scope= attribute.

This is for WCAG 2.2 AA accessibility compliance.

Only modifies <th> inside <thead> — leaves <th> in <tbody> alone.
"""

import os
import re
import sys

TEMPLATES_DIR = "/root/dotmac/templates"

# Regex to match a <th tag that does NOT already have scope=
# Captures: <th followed by > or space+attributes then >
# We process line-by-line within thead blocks, so no multiline needed.
TH_WITHOUT_SCOPE = re.compile(
    r"<th"  # literal <th
    r"(?="  # lookahead: must be followed by
    r"(?:\s|>)"  #   whitespace or >
    r")"
    r"(?!"  # negative lookahead: must NOT contain scope=
    r"[^>]*"  #   any chars before >
    r"\bscope\s*="  #   the word scope followed by =
    r")"
)


def process_file(filepath: str) -> int:
    """Process a single HTML file, adding scope='col' to <th> in <thead>.

    Returns the number of <th> elements modified.
    """
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    # Quick check: skip files with no <thead
    if "<thead" not in content:
        return 0

    lines = content.split("\n")
    in_thead = False
    thead_depth = 0
    modifications = 0
    modified_lines: list[str] = []

    for line in lines:
        # Track thead open/close tags
        # Count opening <thead tags
        thead_opens = len(re.findall(r"<thead[\s>]", line)) + len(
            re.findall(r"<thead$", line)
        )
        # Count closing </thead> tags
        thead_closes = len(re.findall(r"</thead\s*>", line))

        thead_depth += thead_opens
        if thead_depth > 0:
            in_thead = True

        if in_thead:
            # Replace <th> and <th  with <th scope="col"> and <th scope="col"
            new_line = line
            count = 0

            def replace_th(match: re.Match[str]) -> str:
                nonlocal count
                count += 1
                return '<th scope="col"'

            new_line = TH_WITHOUT_SCOPE.sub(replace_th, new_line)
            modifications += count
            modified_lines.append(new_line)
        else:
            modified_lines.append(line)

        thead_depth -= thead_closes
        if thead_depth <= 0:
            thead_depth = 0
            in_thead = False

    if modifications > 0:
        new_content = "\n".join(modified_lines)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(new_content)

    return modifications


def main() -> None:
    templates_dir = TEMPLATES_DIR
    if not os.path.isdir(templates_dir):
        print(f"Error: {templates_dir} is not a directory")
        sys.exit(1)

    total_files_modified = 0
    total_th_updated = 0
    files_scanned = 0

    for root, _dirs, files in os.walk(templates_dir):
        for filename in sorted(files):
            if not filename.endswith(".html"):
                continue

            filepath = os.path.join(root, filename)
            files_scanned += 1

            count = process_file(filepath)
            if count > 0:
                rel = os.path.relpath(filepath, templates_dir)
                print(f"  {rel}: {count} <th> elements updated")
                total_files_modified += 1
                total_th_updated += count

    print()
    print(f"Files scanned:  {files_scanned}")
    print(f"Files modified: {total_files_modified}")
    print(f"<th> elements updated: {total_th_updated}")


if __name__ == "__main__":
    main()
