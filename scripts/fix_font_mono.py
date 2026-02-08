#!/usr/bin/env python3
"""
Add font-mono tabular-nums to elements containing financial values.

Targets elements that use format_currency or format_number filters
and don't already have font-mono class.
"""

import os
import re
import sys

TEMPLATES_DIR = "templates"
SKIP_PATTERNS = ["documents/", "email/", "_pdf", "print_", "components/macros.html"]

FINANCIAL_FILTERS = re.compile(
    r"format_currency|format_number|format_amount|format_money|format_decimal"
    r"|format_currency_compact"
)


def should_skip(filepath: str) -> bool:
    return any(pat in filepath for pat in SKIP_PATTERNS)


def fix_file(filepath: str, dry_run: bool = False) -> int:
    with open(filepath, encoding="utf-8") as f:
        content = f.read()

    lines = content.split("\n")
    fixes = 0

    for i, line in enumerate(lines):
        # Skip if no financial filter on this line
        if not FINANCIAL_FILTERS.search(line):
            continue

        # Skip if already has font-mono
        if "font-mono" in line:
            continue

        # Pattern 1: <td ...>content with filter</td> on same line
        td_match = re.search(r"<td\b([^>]*)>(.*?)</td>", line)
        if td_match:
            attrs = td_match.group(1)
            if 'class="' in attrs:
                new_attrs = attrs.replace('class="', 'class="font-mono tabular-nums ')
            elif "class='" in attrs:
                new_attrs = attrs.replace("class='", "class='font-mono tabular-nums ")
            else:
                new_attrs = ' class="font-mono tabular-nums"' + attrs
            lines[i] = line[: td_match.start(1)] + new_attrs + line[td_match.end(1) :]
            fixes += 1
            continue

        # Pattern 2: <p ...>content with filter</p> on same line
        p_match = re.search(r"<p\b([^>]*)>(.*?)</p>", line)
        if p_match and FINANCIAL_FILTERS.search(p_match.group(2)):
            attrs = p_match.group(1)
            if 'class="' in attrs:
                new_attrs = attrs.replace('class="', 'class="font-mono tabular-nums ')
            elif "class='" in attrs:
                new_attrs = attrs.replace("class='", "class='font-mono tabular-nums ")
            else:
                new_attrs = ' class="font-mono tabular-nums"' + attrs
            lines[i] = line[: p_match.start(1)] + new_attrs + line[p_match.end(1) :]
            fixes += 1
            continue

        # Pattern 3: <span ...>content with filter</span> on same line
        span_match = re.search(r"<span\b([^>]*)>(.*?)</span>", line)
        if span_match and FINANCIAL_FILTERS.search(span_match.group(2)):
            attrs = span_match.group(1)
            if 'class="' in attrs:
                new_attrs = attrs.replace('class="', 'class="font-mono tabular-nums ')
            elif "class='" in attrs:
                new_attrs = attrs.replace("class='", "class='font-mono tabular-nums ")
            else:
                new_attrs = ' class="font-mono tabular-nums"' + attrs
            lines[i] = (
                line[: span_match.start(1)] + new_attrs + line[span_match.end(1) :]
            )
            fixes += 1
            continue

        # Pattern 4: standalone {{ value | format_currency }} — check if parent
        # element on previous line can be modified
        stripped = line.strip()
        if (
            stripped.startswith("{{")
            and stripped.endswith("}}")
            and FINANCIAL_FILTERS.search(stripped)
        ):
            # Look at previous non-empty line for a <td> or element
            for j in range(i - 1, max(i - 4, -1), -1):
                prev = lines[j].strip()
                if re.search(r"<td\b", prev) and "font-mono" not in prev:
                    if 'class="' in lines[j]:
                        lines[j] = lines[j].replace(
                            'class="', 'class="font-mono tabular-nums ', 1
                        )
                    elif "class='" in lines[j]:
                        lines[j] = lines[j].replace(
                            "class='", "class='font-mono tabular-nums ", 1
                        )
                    else:
                        lines[j] = lines[j].replace(
                            "<td", '<td class="font-mono tabular-nums"', 1
                        )
                    fixes += 1
                    break
                elif re.search(r"<(?:p|span|div)\b", prev) and "font-mono" not in prev:
                    if 'class="' in lines[j]:
                        lines[j] = lines[j].replace(
                            'class="', 'class="font-mono tabular-nums ', 1
                        )
                    elif "class='" in lines[j]:
                        lines[j] = lines[j].replace(
                            "class='", "class='font-mono tabular-nums ", 1
                        )
                    fixes += 1
                    break
                elif prev:
                    break  # Hit non-empty non-element line, stop looking

    new_content = "\n".join(lines)

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
                print(f"{action} {fixes} cell(s) in {filepath}")
                total_fixes += fixes
                fixed_files += 1

    action = "Would fix" if dry_run else "Fixed"
    print(f"\n{action} {total_fixes} cells across {fixed_files} files")


if __name__ == "__main__":
    main()
