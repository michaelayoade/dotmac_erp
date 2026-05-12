#!/usr/bin/env python3
"""
Template accessibility scanner.

Flags two systemic a11y issues in Jinja2 templates:
  1. Form controls without an accessible label
  2. Icon-only buttons/links without an accessible name

Designed to run as a pre-commit hook with a baseline file (`.a11y-baseline.json`).
Existing violations are allowed; only NEW violations fail the hook.

Usage:
    # CI / pre-commit (uses baseline)
    python3 scripts/audit_template_a11y.py

    # Scan a specific file
    python3 scripts/audit_template_a11y.py templates/foo/bar.html

    # Regenerate baseline (run after legitimately fixing violations)
    python3 scripts/audit_template_a11y.py --update-baseline

    # Report-only mode (no exit code)
    python3 scripts/audit_template_a11y.py --report
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = REPO_ROOT / "templates"
BASELINE_PATH = REPO_ROOT / ".a11y-baseline.json"

# Patterns
RE_INPUT = re.compile(r"<(input|select|textarea)\b([^>]*?)/?>", re.IGNORECASE)
RE_LABEL_FOR = re.compile(
    r"""<label\b[^>]*?\bfor\s*=\s*['"]([^'"]+)['"]""", re.IGNORECASE
)
RE_BUTTON = re.compile(r"<button\b([^>]*?)>(.*?)</button>", re.IGNORECASE | re.DOTALL)
RE_ANCHOR = re.compile(r"<a\b([^>]*?)>(.*?)</a>", re.IGNORECASE | re.DOTALL)
RE_ID = re.compile(r"""\bid\s*=\s*['"]([^'"]+)['"]""", re.IGNORECASE)
RE_ARIA = re.compile(
    r"""\baria-(label|labelledby|describedby)\s*=\s*['"]""", re.IGNORECASE
)
RE_TITLE = re.compile(r"""\btitle\s*=\s*['"]""", re.IGNORECASE)
RE_HIDDEN = re.compile(r"""\btype\s*=\s*['"]hidden['"]""", re.IGNORECASE)
RE_CSRF = re.compile(r"csrf_form|csrfmiddlewaretoken", re.IGNORECASE)
RE_SR_ONLY = re.compile(r"\bsr-only\b|\bvisually-hidden\b", re.IGNORECASE)
RE_SVG_OR_ICON = re.compile(r"<svg\b|\bicon_svg\s*\(", re.IGNORECASE)
RE_NAME_TOKEN = re.compile(
    r"""\bname\s*=\s*['"]?(?:_token|csrf_token|csrfmiddlewaretoken)""", re.IGNORECASE
)
# Jinja2 line comment `{# ... #}` — content inside is not rendered HTML.
RE_JINJA_COMMENT = re.compile(r"\{#.*?#\}", re.DOTALL)


def _strip_jinja_comments(text: str) -> str:
    """Replace Jinja2 `{# ... #}` blocks with equal-length whitespace.

    Equal-length replacement keeps character offsets / line numbers stable
    so violations point at the right line in the original file.
    """
    return RE_JINJA_COMMENT.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)


def has_inner_text(inner_html: str) -> bool:
    """Check if anchor/button has visible text content.

    Counts as text:
      - Plain words
      - {{ jinja expressions }} (resolve to text at render time)
      - {% if/for %} blocks containing text
      - Alpine x-text bindings
      - sr-only spans with text
    Does NOT count as text:
      - Pure SVG/icon elements
      - Empty tags
    """
    # Alpine.js x-text binding always renders text content — short-circuit.
    if re.search(r"""\bx-text\s*=\s*['"][^'"]+['"]""", inner_html):
        return True
    s = inner_html
    # Jinja expressions become text at runtime — preserve as placeholder
    s = re.sub(r"\{\{.*?\}\}", "TEXT", s, flags=re.DOTALL)
    # Strip Jinja control blocks but keep their inner content
    s = re.sub(r"\{%[^%]*%\}", "", s)
    # Strip HTML tags but keep their content
    s = re.sub(r"<[^>]+>", "", s)
    # Strip entities
    s = s.replace("&nbsp;", "").replace("&amp;", "")
    return bool(s.strip())


def has_attr(attrs: str, *patterns: re.Pattern[str]) -> bool:
    return any(p.search(attrs) for p in patterns)


def _find_implicit_label_ranges(text: str) -> list[tuple[int, int]]:
    """Find character ranges of <label>...</label> elements.

    A control nested inside a <label> is implicitly labeled (HTML standard).
    This catches the common pattern:
        <label class="...">
          <input type="checkbox" name="...">
          <span>Visible label</span>
        </label>
    """
    ranges: list[tuple[int, int]] = []
    depth = 0
    start = -1
    for m in re.finditer(r"<(/?)label\b[^>]*>", text, re.IGNORECASE):
        is_close = bool(m.group(1))
        if not is_close:
            if depth == 0:
                start = m.start()
            depth += 1
        else:
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    ranges.append((start, m.end()))
                    start = -1
    return ranges


def find_unlabeled_controls(text: str) -> list[tuple[int, str]]:
    """Find form controls without an accessible label.

    Returns list of (line_number, snippet) tuples.
    """
    label_targets = set(RE_LABEL_FOR.findall(text))
    label_ranges = _find_implicit_label_ranges(text)
    results: list[tuple[int, str]] = []

    for m in RE_INPUT.finditer(text):
        tag, attrs = m.group(1), m.group(2)
        # Skip hidden inputs and CSRF tokens — they're not user-facing controls.
        if (
            RE_HIDDEN.search(attrs)
            or RE_CSRF.search(attrs)
            or RE_NAME_TOKEN.search(attrs)
        ):
            continue
        # Has aria-label / aria-labelledby?
        if RE_ARIA.search(attrs):
            continue
        # Has a <label for=id> pointing to it?
        id_match = RE_ID.search(attrs)
        if id_match and id_match.group(1) in label_targets:
            continue
        # Nested inside a <label> element? (implicit labeling)
        if any(lo <= m.start() < hi for lo, hi in label_ranges):
            continue
        line = text.count("\n", 0, m.start()) + 1
        # Snippet: tag + attrs trimmed
        snippet = f"<{tag}{attrs[:80].rstrip()}>"
        results.append((line, snippet))

    return results


def find_nameless_icon_actions(text: str) -> list[tuple[int, str]]:
    """Find icon-only buttons/anchors without an accessible name.

    Returns list of (line_number, snippet) tuples.
    """
    results: list[tuple[int, str]] = []

    for pattern, tag in ((RE_BUTTON, "button"), (RE_ANCHOR, "a")):
        for m in pattern.finditer(text):
            attrs, inner = m.group(1), m.group(2)
            # Accessible name from attrs?
            if RE_ARIA.search(attrs) or RE_TITLE.search(attrs):
                continue
            # No icon inside? Not the pattern we're flagging.
            if not RE_SVG_OR_ICON.search(inner):
                continue
            # Visible text content (including Jinja expressions)?
            if has_inner_text(inner):
                continue
            # sr-only text inside provides accessible name
            if RE_SR_ONLY.search(inner):
                continue
            line = text.count("\n", 0, m.start()) + 1
            snippet = f"<{tag}{attrs[:60].rstrip()}>...</{tag}>"
            results.append((line, snippet))

    return results


def scan_file(path: Path) -> dict[str, list[tuple[int, str]]]:
    text = _strip_jinja_comments(path.read_text())
    return {
        "unlabeled_controls": find_unlabeled_controls(text),
        "nameless_icon_actions": find_nameless_icon_actions(text),
    }


def scan_all(files: list[Path]) -> dict[str, dict[str, list[tuple[int, str]]]]:
    results: dict[str, dict[str, list[tuple[int, str]]]] = {}
    for f in files:
        if not f.exists():
            continue
        # Skip files we don't care about
        if any(x in str(f) for x in (".min.html", "__pycache__")):
            continue
        findings = scan_file(f)
        if findings["unlabeled_controls"] or findings["nameless_icon_actions"]:
            results[str(f.relative_to(REPO_ROOT))] = findings
    return results


def summarize_counts(
    results: dict[str, dict[str, list[tuple[int, str]]]],
) -> tuple[int, int]:
    unlabeled = sum(len(v["unlabeled_controls"]) for v in results.values())
    nameless = sum(len(v["nameless_icon_actions"]) for v in results.values())
    return unlabeled, nameless


def load_baseline() -> dict[str, dict[str, int]]:
    """Baseline format: {filepath: {"unlabeled": N, "nameless": N}}.

    Stores counts per file, not specific line numbers — line numbers shift
    constantly with edits, but the per-file count is a stable signal of
    "did this file get worse."
    """
    if not BASELINE_PATH.exists():
        return {}
    return json.loads(BASELINE_PATH.read_text())


def build_baseline(
    results: dict[str, dict[str, list[tuple[int, str]]]],
) -> dict[str, dict[str, int]]:
    return {
        f: {
            "unlabeled": len(v["unlabeled_controls"]),
            "nameless": len(v["nameless_icon_actions"]),
        }
        for f, v in results.items()
    }


def compare_against_baseline(
    current: dict[str, dict[str, list[tuple[int, str]]]],
    baseline: dict[str, dict[str, int]],
) -> dict[str, dict[str, int]]:
    """Return {file: {"unlabeled": delta, "nameless": delta}} for files that got worse."""
    regressions: dict[str, dict[str, int]] = {}
    for f, v in current.items():
        cur_u = len(v["unlabeled_controls"])
        cur_n = len(v["nameless_icon_actions"])
        base_u = baseline.get(f, {}).get("unlabeled", 0)
        base_n = baseline.get(f, {}).get("nameless", 0)
        delta_u = cur_u - base_u
        delta_n = cur_n - base_n
        if delta_u > 0 or delta_n > 0:
            regressions[f] = {"unlabeled": delta_u, "nameless": delta_n}
    return regressions


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", help="Specific files to scan")
    parser.add_argument(
        "--update-baseline", action="store_true", help="Write current state as baseline"
    )
    parser.add_argument(
        "--report", action="store_true", help="Report only, no exit code"
    )
    parser.add_argument(
        "--verbose", action="store_true", help="Print per-violation details"
    )
    args = parser.parse_args()

    # Determine files to scan
    if args.paths:
        files = [Path(p).resolve() for p in args.paths if p.endswith(".html")]
    else:
        files = sorted(TEMPLATE_DIR.rglob("*.html"))

    results = scan_all(files)
    total_unlabeled, total_nameless = summarize_counts(results)

    if args.update_baseline:
        baseline = build_baseline(results)
        BASELINE_PATH.write_text(json.dumps(baseline, indent=2, sort_keys=True) + "\n")
        print(
            f"Wrote baseline to {BASELINE_PATH.relative_to(REPO_ROOT)}: "
            f"{len(baseline)} files, {total_unlabeled} unlabeled controls, "
            f"{total_nameless} nameless icon actions."
        )
        return 0

    print(
        f"A11y scan: {total_unlabeled} unlabeled controls, "
        f"{total_nameless} nameless icon actions across {len(results)} files."
    )

    if args.verbose:
        for f, v in sorted(results.items()):
            u, n = len(v["unlabeled_controls"]), len(v["nameless_icon_actions"])
            if u or n:
                print(f"\n  {f}: {u} unlabeled, {n} nameless")
                for line, snippet in v["unlabeled_controls"][:5]:
                    print(f"    L{line}: {snippet}")
                for line, snippet in v["nameless_icon_actions"][:5]:
                    print(f"    L{line}: {snippet}")

    if args.report:
        return 0

    # Compare against baseline
    baseline = load_baseline()
    if not baseline:
        print(
            f"\nNo baseline at {BASELINE_PATH.relative_to(REPO_ROOT)}. "
            f"Run with --update-baseline to seed it."
        )
        return 0

    regressions = compare_against_baseline(results, baseline)
    if not regressions:
        return 0

    print(f"\n❌ A11y regression: {len(regressions)} file(s) got worse:")
    for f, d in sorted(regressions.items()):
        bits = []
        if d["unlabeled"] > 0:
            bits.append(f"+{d['unlabeled']} unlabeled controls")
        if d["nameless"] > 0:
            bits.append(f"+{d['nameless']} nameless icon actions")
        print(f"  {f}: {', '.join(bits)}")

    print(
        "\nFix by adding aria-label/aria-labelledby to controls, or visible text to "
        "icon-only buttons. If a violation is legitimate (e.g., the label is rendered "
        "elsewhere via JS), update the baseline:"
        "\n  python3 scripts/audit_template_a11y.py --update-baseline"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
