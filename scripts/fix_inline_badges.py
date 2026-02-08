#!/usr/bin/env python3
"""
Replace inline status badge HTML with status_badge() macro calls.

Handles:
1. Simple inline <span class="inline-flex rounded-full bg-*-100 ...">{{ entity.status }}</span>
2. Conditional badge blocks: {% if status == "X" %}<span...>{% elif...%}...{% endif %}
"""

import os
import re
import sys

TEMPLATES_DIR = "templates"

# Macro import line to add if not present
MACRO_IMPORT = '{% from "components/macros.html" import status_badge %}'

# Pattern 1: Simple inline span badges
# Matches: <span class="inline-flex...rounded-full...bg-...">{{ var }}</span>
# or: <span class="inline-flex...rounded-full...bg-...">{{ var | replace('_', ' ') | title }}</span>
INLINE_BADGE_RE = re.compile(
    r'<span\s+class="[^"]*(?:inline-flex|rounded-full)[^"]*(?:rounded-full|inline-flex)[^"]*">'
    r"\s*\{\{\s*"
    r"([^}]+?)"  # capture the variable expression
    r"\s*\}\}"
    r"\s*</span>",
    re.DOTALL,
)

# Pattern for badge CSS class combos that indicate a status badge
BADGE_CLASS_INDICATORS = re.compile(
    r"(?:rounded-full.*(?:bg-(?:slate|emerald|amber|rose|blue|green|red|yellow)-(?:100|50))|"
    r"badge-(?:draft|pending|approved|paid|overdue|rejected|posted|cancelled))"
)


def strip_display_filters(expr: str) -> str:
    """Remove display formatting filters from a template expression.

    '{{ x.status | replace("_", " ") | title }}' → 'x.status'
    '{{ x.status.value | replace("_", " ") | title }}' → 'x.status.value'
    '{{ x.status }}' → 'x.status'
    """
    # Remove | replace('_', ' ') | title and variations
    expr = re.sub(
        r"""\s*\|\s*replace\s*\(\s*['"]_['"]\s*,\s*['"] ['"]\s*\)""",
        "",
        expr,
    )
    expr = re.sub(r"\s*\|\s*title\b", "", expr)
    expr = re.sub(r"\s*\|\s*upper\b", "", expr)
    expr = re.sub(r"\s*\|\s*lower\b", "", expr)
    return expr.strip()


def determine_size(context_line: str) -> str:
    """Determine badge size from surrounding context."""
    if "text-xs" in context_line or "py-0.5" in context_line or "px-2" in context_line:
        return 'size="sm"'
    return ""


def fix_inline_badges(content: str) -> tuple[str, int]:
    """Replace inline badge spans with status_badge() macro calls."""
    fixes = 0

    def replacer(match: re.Match) -> str:
        nonlocal fixes
        full_match = match.group(0)

        # Only replace if it looks like a badge (has badge-indicator CSS classes)
        if not BADGE_CLASS_INDICATORS.search(full_match):
            return full_match

        expr = match.group(1).strip()
        var_name = strip_display_filters(expr)

        if not var_name or "%" in var_name:
            return full_match

        size = determine_size(full_match)
        size_arg = f", {size}" if size else ""

        fixes += 1
        return f"{{{{ status_badge({var_name}{size_arg}) }}}}"

    content = INLINE_BADGE_RE.sub(replacer, content)
    return content, fixes


def fix_conditional_badges(content: str) -> tuple[str, int]:
    """Replace conditional badge blocks with status_badge() macro calls.

    Handles patterns like:
    {% if entity.status == "ACTIVE" %}<span class="badge-approved">Active</span>
    {% elif entity.status == "DRAFT" %}<span class="badge-draft">Draft</span>
    {% else %}<span class="badge-draft">{{ entity.status }}</span>{% endif %}
    """
    fixes = 0

    # Pattern for multi-line conditional badge blocks
    # This handles the common structure:
    # {% if var == "X" %}<span class="badge-*">X</span>{% elif var == "Y" %}...{% endif %}
    cond_badge_re = re.compile(
        r'\{%[-\s]*if\s+(\S+?)\s*==\s*["\'](\w+)["\']\s*[-]?%\}'  # {% if var == "STATUS" %}
        r'\s*<span\s+class="[^"]*badge-\w+[^"]*">[^<]*</span>\s*'  # <span class="badge-*">text</span>
        r'(?:\{%[-\s]*elif\s+\1\s*==\s*["\'](\w+)["\']\s*[-]?%\}'  # {% elif var == "STATUS2" %}
        r'\s*<span\s+class="[^"]*badge-\w+[^"]*">[^<]*</span>\s*)*'  # repeated badge spans
        r"(?:\{%[-\s]*else\s*[-]?%\}"  # {% else %}
        r'\s*<span\s+class="[^"]*badge-\w+[^"]*">[^<]*</span>\s*)?'  # optional else badge
        r"\{%[-\s]*endif\s*[-]?%\}",  # {% endif %}
        re.DOTALL,
    )

    def cond_replacer(match: re.Match) -> str:
        nonlocal fixes
        var_name = match.group(1)
        fixes += 1
        return f"{{{{ status_badge({var_name}) }}}}"

    content = cond_badge_re.sub(cond_replacer, content)
    return content, fixes


def ensure_macro_import(content: str) -> str:
    """Add status_badge macro import if not present."""
    if "status_badge" not in content:
        return content  # Not using status_badge, skip

    if "import status_badge" in content:
        return content  # Already imported

    # Find the right place to add the import
    # After {% extends %} if present, otherwise at the top
    extends_match = re.search(r"(\{%\s*extends\s+[^%]+%\})\s*\n", content)
    if extends_match:
        insert_pos = extends_match.end()
        return content[:insert_pos] + MACRO_IMPORT + "\n" + content[insert_pos:]

    # At the top if no extends
    return MACRO_IMPORT + "\n" + content


def fix_file(filepath: str, dry_run: bool = False) -> int:
    with open(filepath, encoding="utf-8") as f:
        content = f.read()

    original = content
    total_fixes = 0

    content, inline_fixes = fix_inline_badges(content)
    total_fixes += inline_fixes

    content, cond_fixes = fix_conditional_badges(content)
    total_fixes += cond_fixes

    if total_fixes > 0:
        content = ensure_macro_import(content)

    if total_fixes > 0 and not dry_run and content != original:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

    return total_fixes


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    total_fixes = 0
    fixed_files = 0

    for root, _dirs, files in os.walk(TEMPLATES_DIR):
        for fname in sorted(files):
            if not fname.endswith(".html"):
                continue
            filepath = os.path.join(root, fname)
            fixes = fix_file(filepath, dry_run=dry_run)
            if fixes > 0:
                action = "Would fix" if dry_run else "Fixed"
                print(f"{action} {fixes} badge(s) in {filepath}")
                total_fixes += fixes
                fixed_files += 1

    action = "Would fix" if dry_run else "Fixed"
    print(f"\n{action} {total_fixes} badges across {fixed_files} files")


if __name__ == "__main__":
    main()
