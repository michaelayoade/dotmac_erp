#!/usr/bin/env python3
"""
PostToolUse hook: enforce frontend template quality standards.

Checks Jinja2/HTML templates for anti-patterns documented in CLAUDE.md
and .claude/rules/templates.md, design-system.md, security.md:

  Security        — CSRF tokens in POST forms, no unsafe | safe
  Alpine.js       — single quotes for x-data with tojson
  Accessibility   — no text below minimum size (12px)
  Tailwind        — no dynamic class interpolation (gets purged)
  UX patterns     — no alert() (use showToast), no inline badges
  Jinja2          — no default('') for None values
  Component macros — enforce empty_state, file_upload_zone, pagination,
                     icon_svg, currency instead of inline HTML

Usage:  python3 check-template-style.py <file_path>
Exit 0 = clean, exit 2 = violations found (shown to Claude).
"""

from __future__ import annotations

import re
import sys

# ── Check Functions ─────────────────────────────────────────────


def check_alert_usage(lines: list[str]) -> list[str]:
    """Flag alert() — must use window.showToast() instead."""
    violations: list[str] = []
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        # Skip Jinja2 comments and HTML comments
        if stripped.startswith("{#") or stripped.startswith("<!--"):
            continue
        # Match alert( but not window.alert( (also bad, but less common false positive)
        if re.search(r"\balert\s*\(", line):
            # Skip if inside a Jinja2 comment block or a macro definition for alert types
            if "showToast" in line or "alert_type" in line or "alert-" in line:
                continue
            violations.append(
                f"line {i}: `alert()` — use `window.showToast(message, 'success'|'warning'|'error')` instead"
            )
    return violations


def check_xdata_quoting(lines: list[str]) -> list[str]:
    """Flag x-data with double quotes when tojson is used — breaks Alpine.js."""
    violations: list[str] = []
    for i, line in enumerate(lines, 1):
        if 'x-data="' in line and "tojson" in line:
            violations.append(
                f"line {i}: `x-data=\"...tojson...\"` — use SINGLE quotes: `x-data='{{ ... | tojson }}'`"
            )
    return violations


def check_csrf_token(source: str) -> list[str]:
    """Flag POST forms missing {{ request.state.csrf_form | safe }}."""
    violations: list[str] = []
    form_re = re.compile(r"<form[^>]*method\s*=\s*[\"']?POST[\"']?", re.IGNORECASE)
    for match in form_re.finditer(source):
        start = match.start()
        # Find the closing </form> or end of file
        end_form = source.find("</form>", start)
        if end_form == -1:
            end_form = len(source)
        form_block = source[start:end_form]
        if "csrf_form" not in form_block:
            line_num = source[:start].count("\n") + 1
            violations.append(
                f"line {line_num}: POST form missing CSRF token — add `{{{{ request.state.csrf_form | safe }}}}`"
            )
    return violations


def check_unsafe_safe(lines: list[str]) -> list[str]:
    """Flag | safe on non-whitelisted content — XSS risk."""
    violations: list[str] = []
    # Whitelisted patterns that are safe to use with | safe
    safe_whitelist = ("csrf_form", "tojson", ".css", "branding", "icon_svg", "svg")
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("{#") or stripped.startswith("<!--"):
            continue
        # Find all | safe occurrences on this line
        for match in re.finditer(r"\|\s*safe\s*}}", line):
            # Get the expression preceding | safe
            preceding = line[: match.start()]
            # Check if any whitelisted pattern is nearby
            if any(kw in preceding.lower() for kw in safe_whitelist):
                continue
            violations.append(
                f"line {i}: `| safe` on non-whitelisted content — use `| sanitize_html` for user content"
            )
    return violations


def check_minimum_text_size(lines: list[str]) -> list[str]:
    """Flag text-[10px], text-[11px] — minimum is text-xs (12px)."""
    violations: list[str] = []
    pattern = re.compile(r"text-\[1[01]px\]")
    for i, line in enumerate(lines, 1):
        match = pattern.search(line)
        if match:
            violations.append(
                f"line {i}: `{match.group()}` below minimum — use `text-xs` (12px) as the smallest size"
            )
    return violations


def check_dynamic_tailwind(lines: list[str]) -> list[str]:
    """Flag dynamic Tailwind class interpolation — gets purged at build time."""
    violations: list[str] = []
    # Matches patterns like bg-{{ color }}-50, text-{{ level }}-600
    pattern = re.compile(r"(bg|text|border|ring|shadow|from|to|via)-\{\{")
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("{#") or stripped.startswith("<!--"):
            continue
        if pattern.search(line):
            violations.append(
                f"line {i}: dynamic Tailwind class — will be purged. Use Jinja2 dict lookup or safelist"
            )
    return violations


def check_none_default_filter(lines: list[str]) -> list[str]:
    """Flag {{ var | default('') }} for None values — doesn't work for None."""
    violations: list[str] = []
    # Match {{ something | default('') }} or {{ something | default("") }}
    pattern = re.compile(r"\{\{[^}]*\|\s*default\s*\(\s*['\"]['\"]")
    for i, line in enumerate(lines, 1):
        if pattern.search(line):
            violations.append(
                f"line {i}: `| default('')` doesn't handle None — use `{{{{ var if var else '' }}}}`"
            )
    return violations


def check_missing_dark_mode(lines: list[str]) -> list[str]:
    """Flag color classes without dark: variants on key elements."""
    violations: list[str] = []
    # Only check lines that have bg- or text- color classes
    # and appear to be on HTML elements (contain class=)
    color_re = re.compile(
        r"\b(bg-white|bg-slate-50|bg-gray-50|text-slate-900|text-gray-900)\b"
    )
    dark_re = re.compile(r"\bdark:")
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("{#") or stripped.startswith("<!--"):
            continue
        if "class=" not in line:
            continue
        if color_re.search(line) and not dark_re.search(line):
            match = color_re.search(line)
            violations.append(
                f"line {i}: `{match.group()}` without `dark:` variant — add dark mode pairing"
            )
    return violations


def check_inline_status_badge(lines: list[str]) -> list[str]:
    """Flag inline badge HTML that should use the status_badge() macro."""
    violations: list[str] = []
    # Pattern: span with badge-like classes containing status text
    badge_re = re.compile(
        r'<span[^>]*class="[^"]*'
        r"(bg-emerald|bg-amber|bg-rose|bg-blue|bg-green|bg-yellow|bg-red)"
        r'[^"]*"[^>]*>'
    )
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        # Skip macro definitions themselves
        if "macro status_badge" in line or "macro " in line:
            continue
        if badge_re.search(line):
            # Check it's not already using the macro
            if "status_badge(" not in line:
                violations.append(
                    f"line {i}: inline status badge HTML — use `{{{{ status_badge(status, 'sm') }}}}` macro"
                )
    return violations


# ── Component Macro Enforcement ─────────────────────────────────

# Jinja2 block tags for the for/else stack parser
_FOR_RE = re.compile(r"\{%-?\s*for\s")
_ELSE_RE = re.compile(r"\{%-?\s*else\s*-?%}")
_ENDFOR_RE = re.compile(r"\{%-?\s*endfor\s*-?%}")
_IF_RE = re.compile(r"\{%-?\s*if\s")
_ENDIF_RE = re.compile(r"\{%-?\s*endif\s*-?%}")


def check_for_without_else(lines: list[str]) -> list[str]:
    """Flag {% for %} blocks without {% else %} — missing empty state.

    Uses a stack to correctly attribute {% else %} to its enclosing block
    (for vs if) even with arbitrary nesting.
    """
    violations: list[str] = []
    # Stack entries: (block_type, line_number, has_else)
    stack: list[tuple[str, int, bool]] = []

    # Patterns where the iterable is a literal (can never be empty)
    literal_iter_re = re.compile(r"\bin\s+[\[\(]|\bin\s+range\s*\(|\bin\s+\{")
    # Extract iterable variable name from {% for x in ITERABLE %}
    iter_var_re = re.compile(r"\bin\s+(\w+)")

    for i, line in enumerate(lines, 1):
        if _FOR_RE.search(line):
            is_literal = False
            # Check 1: inline literal — {% for x in [1, 2, 3] %}
            if literal_iter_re.search(line):
                is_literal = True
            # Check 2: variable set from literal nearby — {% set items = [...] %}
            if not is_literal:
                var_match = iter_var_re.search(line)
                if var_match:
                    var_name = var_match.group(1)
                    # Scan backwards up to 30 lines for {% set var = [...] %}
                    for j in range(max(0, i - 31), i - 1):
                        prev = lines[j]
                        if f"set {var_name}" in prev and "[" in prev:
                            is_literal = True
                            break
            stack.append(("for", i, is_literal))

        elif _IF_RE.search(line):
            stack.append(("if", i, False))

        elif _ELSE_RE.search(line):
            # {% else %} belongs to the innermost open block
            if stack:
                typ, ln, _ = stack[-1]
                stack[-1] = (typ, ln, True)

        elif _ENDFOR_RE.search(line):
            if stack and stack[-1][0] == "for":
                _, for_line, has_else = stack.pop()
                if not has_else:
                    violations.append(
                        f"line {for_line}: `{{% for %}}` without `{{% else %}}` "
                        f"— add `{{{{ empty_state(...) }}}}` for empty lists"
                    )
            elif stack:
                stack.pop()  # mismatched, pop anyway to avoid cascade

        elif _ENDIF_RE.search(line):
            if stack and stack[-1][0] == "if" or stack:
                stack.pop()

    return violations


def check_file_upload_macro(source: str) -> list[str]:
    """Flag <input type="file"> — must use file_upload_zone() macro."""
    if "file_upload_zone" in source:
        return []
    matches = list(
        re.finditer(r'<input[^>]*type=["\']file["\']', source, re.IGNORECASE)
    )
    if not matches:
        return []
    violations: list[str] = []
    for m in matches:
        line_num = source[: m.start()].count("\n") + 1
        violations.append(
            f'line {line_num}: raw `<input type="file">` '
            f"— use `{{{{ file_upload_zone(name, label, accept, max_size_mb) }}}}` macro"
        )
    return violations


def check_inline_pagination(source: str) -> list[str]:
    """Flag hand-rolled pagination links — must use pagination() macro."""
    if "pagination(" in source:
        return []
    matches = list(re.finditer(r"\?page=\{\{|\bpage={{ *page", source))
    if not matches:
        return []
    violations: list[str] = []
    for m in matches:
        line_num = source[: m.start()].count("\n") + 1
        violations.append(
            f"line {line_num}: inline pagination — "
            f"use `{{{{ pagination(page, total_pages, total_count) }}}}` macro"
        )
    return violations


def check_inline_svg_icons(source: str, lines: list[str]) -> list[str]:
    """Flag inline <svg> icons — must use icon_svg() macro."""
    if "icon_svg(" in source:
        # File already uses the macro; only flag additional inline SVGs
        pass
    violations: list[str] = []
    # Match <svg with small icon dimension classes
    svg_icon_re = re.compile(r"<svg[^>]*(h-[3-6]\s|w-[3-6]\s|h-[3-6]\"|w-[3-6]\")")
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if "macro " in line:
            continue
        if svg_icon_re.search(line):
            violations.append(
                f"line {i}: inline `<svg>` icon — use `{{{{ icon_svg(name, size) }}}}` macro"
            )
    return violations


def check_inline_currency(lines: list[str]) -> list[str]:
    """Flag raw naira symbol (₦) — must use currency() macro or format_currency filter."""
    violations: list[str] = []
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("{#") or stripped.startswith("<!--"):
            continue
        # Skip macro definitions
        if "macro " in line:
            continue
        if "₦" in line:
            # Allow if already using format_currency or currency() macro
            if "format_currency" in line or "currency(" in line:
                continue
            violations.append(
                f"line {i}: raw `₦` symbol — use `| format_currency` filter "
                f"or `{{{{ currency(amount) }}}}` macro"
            )
    return violations


# ── Main ────────────────────────────────────────────────────────


def check_file(filepath: str) -> dict[str, list[str]]:
    """Run all checks, return violations grouped by category."""
    try:
        with open(filepath) as f:
            source = f.read()
    except (OSError, UnicodeDecodeError):
        return {}

    lines = source.splitlines()
    groups: dict[str, list[str]] = {}

    # Security
    v = check_csrf_token(source) + check_unsafe_safe(lines)
    if v:
        groups["Security"] = v

    # Alpine.js
    v = check_xdata_quoting(lines)
    if v:
        groups["Alpine.js"] = v

    # UX patterns
    v = check_alert_usage(lines) + check_inline_status_badge(lines)
    if v:
        groups["UX patterns"] = v

    # Tailwind / CSS
    v = (
        check_minimum_text_size(lines)
        + check_dynamic_tailwind(lines)
        + check_missing_dark_mode(lines)
    )
    if v:
        groups["Tailwind / CSS"] = v

    # Jinja2
    v = check_none_default_filter(lines)
    if v:
        groups["Jinja2"] = v

    # Component macros
    v = (
        check_for_without_else(lines)
        + check_file_upload_macro(source)
        + check_inline_pagination(source)
        + check_inline_svg_icons(source, lines)
        + check_inline_currency(lines)
    )
    if v:
        groups["Component macros"] = v

    return groups


def main() -> int:
    if len(sys.argv) < 2:
        return 0

    filepath = sys.argv[1]

    if not filepath:
        return 0

    # Only check HTML template files
    if not filepath.endswith(".html"):
        return 0

    # Only check templates/ directory
    if "/templates/" not in filepath:
        return 0

    # Skip component macro definitions (they define the patterns, not consume them)
    basename = filepath.rsplit("/", 1)[-1]
    if basename.startswith("_") and basename != "_base.html":
        return 0

    groups = check_file(filepath)
    if not groups:
        return 0

    total = sum(len(v) for v in groups.values())
    print(
        f"TEMPLATE STYLE: {total} issue{'s' if total != 1 else ''} in {filepath}:",
        file=sys.stderr,
    )
    for category, violations in groups.items():
        print(f"  {category}:", file=sys.stderr)
        for v in violations:
            print(f"    {v}", file=sys.stderr)
    print(
        "  -> Fix these to match project standards (see .claude/rules/templates.md).",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
