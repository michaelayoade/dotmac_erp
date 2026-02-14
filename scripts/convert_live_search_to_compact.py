#!/usr/bin/env python3
"""Convert live_search macro calls to compact_filters in People module templates.

Usage:
    python scripts/convert_live_search_to_compact.py --dry-run
    python scripts/convert_live_search_to_compact.py --execute
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

TEMPLATES_DIR = Path("templates/people")

# Already converted - skip
SKIP_FILES: set[str] = {
    "hr/employees.html",
    "leave/applications.html",
    "leave/allocations.html",
    "leave/holidays.html",
    "attendance/records.html",
    "attendance/requests.html",
}

stats: dict[str, int] = {
    "files_scanned": 0,
    "files_modified": 0,
    "skipped": 0,
    "simple": 0,
    "with_filters": 0,
    "call_block": 0,
}


def fix_import(content: str) -> str:
    """Replace live_search in import with compact_filters + helpers."""
    pattern = r'{%\s*from\s*"components/macros\.html"\s*import\s*(.*?)\s*%}'

    def replacer(m: re.Match[str]) -> str:
        imports_str = m.group(1).strip()
        parts = [p.strip().rstrip(",") for p in imports_str.split(",")]
        # Remove live_search
        parts = [p for p in parts if p != "live_search"]
        # Prepend compact_filters + helpers
        new_parts = ["compact_filters", "filter_select_field"]
        for p in parts:
            if p not in new_parts:
                new_parts.append(p)
        return '{%% from "components/macros.html" import %s %%}' % ", ".join(new_parts)

    return re.sub(pattern, replacer, content)


def convert_simple(content: str) -> tuple[str, int]:
    """Convert {{ live_search(search=search, base_url="...", placeholder="...") }}."""
    count = 0

    pattern = (
        r"\{\{\s*live_search\(\s*"
        r"search\s*=\s*search\s*,\s*"
        r'base_url\s*=\s*"([^"]+)"\s*,\s*'
        r'placeholder\s*=\s*"([^"]+)"\s*'
        r"\)\s*\}\}"
    )

    def repl(m: re.Match[str]) -> str:
        nonlocal count
        count += 1
        url = m.group(1)
        ph = m.group(2)
        return (
            f"{{% call(filter_attrs) compact_filters(\n"
            f'        base_url="{url}",\n'
            f"        active_filters=active_filters | default([]),\n"
            f"        show_search=true,\n"
            f"        search=search,\n"
            f'        search_placeholder="{ph}"\n'
            f"    ) %}}\n"
            f"    {{% endcall %}}"
        )

    content = re.sub(pattern, repl, content)
    return content, count


def convert_with_inline_filters(content: str) -> tuple[str, int]:
    """Convert {{ live_search(search=search, filters=[...], base_url=..., placeholder=...) }}."""
    count = 0

    # Match multiline live_search with filters= parameter (no call block)
    pattern = (
        r"\{\{\s*live_search\(\s*\n?"
        r"\s*search\s*=\s*search\s*,\s*\n?"
        r"\s*filters\s*=\s*\[(.*?)\]\s*,\s*\n?"
        r'\s*base_url\s*=\s*"([^"]+)"\s*,?\s*\n?'
        r'\s*placeholder\s*=\s*"([^"]+)"\s*\n?'
        r"\s*\)\s*\}\}"
    )

    def repl(m: re.Match[str]) -> str:
        nonlocal count
        count += 1
        filters_raw = m.group(1)
        url = m.group(2)
        ph = m.group(3)

        # Parse filter dicts
        fields = _parse_filters(filters_raw)

        lines = [
            f"{{% call(filter_attrs) compact_filters(\n"
            f'        base_url="{url}",\n'
            f"        active_filters=active_filters | default([]),\n"
            f"        show_search=true,\n"
            f"        search=search,\n"
            f'        search_placeholder="{ph}"\n'
            f"    ) %}}"
        ]
        for f in fields:
            lines.append(f"        {f}")
        lines.append("    {% endcall %}")
        return "\n".join(lines)

    content = re.sub(pattern, repl, content, flags=re.DOTALL)
    return content, count


def _parse_filters(raw: str) -> list[str]:
    """Parse inline filter dicts into filter_select_field calls."""
    results: list[str] = []
    filter_pat = (
        r'\{\s*"name"\s*:\s*"([^"]+)"\s*,\s*'
        r'"label"\s*:\s*"([^"]+)"\s*,\s*'
        r'"value"\s*:\s*(\w+)\s*,\s*\n?\s*'
        r'"options"\s*:\s*\[(.*?)\]\s*\}'
    )
    for fm in re.finditer(filter_pat, raw, re.DOTALL):
        name = fm.group(1)
        _label = fm.group(2)
        val = fm.group(3)
        opts_raw = fm.group(4)

        # Human label
        label = name.replace("_", " ").title()
        if name == "is_active":
            label = "Status"

        # Parse options
        opts: list[str] = []
        for om in re.finditer(
            r'\{"value":\s*"([^"]+)",\s*"label":\s*"([^"]+)"\}', opts_raw
        ):
            opts.append(f'{{"value": "{om.group(1)}", "label": "{om.group(2)}"}}')

        opts_str = ", ".join(opts)
        results.append(
            f'{{{{ filter_select_field("{name}", "{label}", {val}, [{opts_str}], filter_attrs) }}}}'
        )
    return results


def convert_call_block(content: str) -> tuple[str, int]:
    """Convert {% call(search_attrs) live_search(...) %} ... {% endcall %}."""
    count = 0

    # Match the full call block
    pattern = (
        r"{%\s*call\s*\(\s*search_attrs\s*\)\s*live_search\(\s*"
        r"(.*?)"  # all params
        r"\)\s*%\}"
        r"(.*?)"  # block content
        r"{%\s*endcall\s*%\}"
    )

    def repl(m: re.Match[str]) -> str:
        nonlocal count
        count += 1
        params_raw = m.group(1)
        block = m.group(2)

        # Extract base_url
        url_m = re.search(r'base_url\s*=\s*"([^"]+)"', params_raw)
        url = url_m.group(1) if url_m else "/unknown"

        # Extract placeholder
        ph_m = re.search(r'placeholder\s*=\s*"([^"]+)"', params_raw)
        ph = ph_m.group(1) if ph_m else "Search..."

        # Check for inline filters= in params
        extra_fields: list[str] = []
        filters_m = re.search(r"filters\s*=\s*\[(.*?)\]", params_raw, re.DOTALL)
        if filters_m:
            extra_fields = _parse_filters(filters_m.group(1))

        # Replace search_attrs → filter_attrs in block content
        block = block.replace("search_attrs", "filter_attrs")

        header = (
            f"{{% call(filter_attrs) compact_filters(\n"
            f'        base_url="{url}",\n'
            f"        active_filters=active_filters | default([]),\n"
            f"        show_search=true,\n"
            f"        search=search,\n"
            f'        search_placeholder="{ph}"\n'
            f"    ) %}}"
        )

        # Insert extra filter fields at start of block
        if extra_fields:
            extra = "\n".join(f"        {f}" for f in extra_fields)
            block = "\n" + extra + block

        return header + block + "{% endcall %}"

    content = re.sub(pattern, repl, content, flags=re.DOTALL)
    return content, count


def process_file(fpath: Path, execute: bool) -> bool:
    """Process one file. Returns True if modified."""
    rel = str(fpath.relative_to(TEMPLATES_DIR))
    stats["files_scanned"] += 1

    if rel in SKIP_FILES:
        stats["skipped"] += 1
        return False

    content = fpath.read_text()
    if "live_search" not in content:
        return False

    original = content

    # Fix import
    content = fix_import(content)

    # Try conversions in order of specificity
    content, n = convert_call_block(content)
    stats["call_block"] += n

    content, n = convert_with_inline_filters(content)
    stats["with_filters"] += n

    content, n = convert_simple(content)
    stats["simple"] += n

    if content == original:
        print(f"  UNCHANGED: {rel}")
        return False

    if execute:
        fpath.write_text(content)
        print(f"  MODIFIED: {rel}")
    else:
        print(f"  WOULD MODIFY: {rel}")

    stats["files_modified"] += 1
    return True


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in ("--dry-run", "--execute"):
        print(
            "Usage: python scripts/convert_live_search_to_compact.py --dry-run|--execute"
        )
        sys.exit(1)

    execute = sys.argv[1] == "--execute"
    mode = "EXECUTE" if execute else "DRY RUN"
    print(f"[{mode}] Converting live_search → compact_filters in {TEMPLATES_DIR}\n")

    for fpath in sorted(TEMPLATES_DIR.rglob("*.html")):
        process_file(fpath, execute)

    print(f"\n{'=' * 50}")
    print(f"Scanned:    {stats['files_scanned']}")
    print(f"Modified:   {stats['files_modified']}")
    print(f"Skipped:    {stats['skipped']}")
    print(f"Simple:     {stats['simple']}")
    print(f"W/Filters:  {stats['with_filters']}")
    print(f"Call Block: {stats['call_block']}")


if __name__ == "__main__":
    main()
