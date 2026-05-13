#!/usr/bin/env python3
"""
Color-only status indicator scanner.

WCAG 2.2 SC 1.4.1 ("Use of Color") requires that information conveyed by
color is also available through another visual cue (text, icon, pattern).

This scanner flags small colored "status dots" that may be the *sole*
indicator of a status because they have:
  - empty content (no text, no SVG with title)
  - no aria-label / aria-labelledby / title
  - no aria-hidden="true" (which would declare the dot decorative)
  - and a class set that screams "status indicator": a small rounded shape
    in a status-coded color (emerald/rose/amber/blue/red/green/yellow/orange).

The fix is one of two things:
  - aria-hidden="true"   - if adjacent text already conveys the status
  - sr-only sibling      - if the dot stands alone (e.g., a connection LED)

Usage:
    python3 scripts/audit_color_only.py           # pre-commit: blocks NEW violations
    python3 scripts/audit_color_only.py --report  # report-only
    python3 scripts/audit_color_only.py --update-baseline
    python3 scripts/audit_color_only.py templates/foo/bar.html
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = REPO_ROOT / "templates"
BASELINE_PATH = REPO_ROOT / ".color-only-baseline.json"

STATUS_COLORS = (
    "emerald|rose|amber|blue|red|green|yellow|orange|violet|teal|cyan|indigo"
)

# Match <span ...> or <div ...> elements. We capture the open tag and the body
# up to the matching close tag. The attrs portion must allow `>` inside
# quoted attribute values (e.g., Alpine x-show="unread > 0").
RE_DOT = re.compile(
    r"""<(?P<tag>span|div)\b"""
    r"""(?P<attrs>(?:[^>"']|"[^"]*"|'[^']*')*?)"""
    r""">(?P<body>[^<]*?)</(?P=tag)>""",
    re.IGNORECASE,
)

# Small rounded shape signature — appears in any order in the class list.
RE_ROUNDED_FULL = re.compile(r"\brounded-full\b")
RE_SMALL_SIZE = re.compile(r"\b(?:h-[1-4]\s+w-[1-4]|w-[1-4]\s+h-[1-4])\b")
RE_STATUS_BG = re.compile(rf"\bbg-(?:{STATUS_COLORS})-(?:[3-7]00|400|500)\b")

# Things that indicate the dot is NOT color-only.
RE_CLASS = re.compile(r"""\bclass\s*=\s*['"]([^'"]+)['"]""", re.IGNORECASE)
RE_ARIA_LABEL = re.compile(
    r"""\baria-(?:label|labelledby|describedby)\s*=\s*['"]""", re.IGNORECASE
)
RE_ARIA_HIDDEN = re.compile(r"""\baria-hidden\s*=\s*['"]true['"]""", re.IGNORECASE)
RE_TITLE_ATTR = re.compile(r"""\btitle\s*=\s*['"]""", re.IGNORECASE)
RE_ROLE_ATTR = re.compile(r"""\brole\s*=\s*['"]img['"]""", re.IGNORECASE)


def looks_like_status_dot(attrs: str) -> bool:
    """True if class set matches the small-rounded-status-color shape."""
    cls_match = RE_CLASS.search(attrs)
    if not cls_match:
        return False
    classes = cls_match.group(1)
    return bool(
        RE_ROUNDED_FULL.search(classes)
        and RE_SMALL_SIZE.search(classes)
        and RE_STATUS_BG.search(classes)
    )


def has_accessible_name(attrs: str) -> bool:
    return bool(
        RE_ARIA_LABEL.search(attrs)
        or RE_ARIA_HIDDEN.search(attrs)
        or RE_TITLE_ATTR.search(attrs)
        or RE_ROLE_ATTR.search(attrs)
    )


def body_is_empty(body: str) -> bool:
    return not body.strip()


def scan_file(path: Path) -> list[dict]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    violations: list[dict] = []
    for m in RE_DOT.finditer(text):
        attrs = m.group("attrs") or ""
        body = m.group("body") or ""
        if not looks_like_status_dot(attrs):
            continue
        if has_accessible_name(attrs):
            continue
        if not body_is_empty(body):
            continue
        line = text.count("\n", 0, m.start()) + 1
        violations.append(
            {
                "line": line,
                "snippet": m.group(0)[:160],
            }
        )
    return violations


def scan_paths(paths: list[Path]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for p in paths:
        if not p.is_file() or p.suffix.lower() != ".html":
            continue
        v = scan_file(p)
        if v:
            rel = str(p.relative_to(REPO_ROOT))
            out[rel] = v
    return out


def load_baseline() -> dict[str, int]:
    if not BASELINE_PATH.exists():
        return {}
    try:
        return json.loads(BASELINE_PATH.read_text())
    except json.JSONDecodeError:
        return {}


def write_baseline(counts: dict[str, int]) -> None:
    BASELINE_PATH.write_text(json.dumps(counts, indent=2, sort_keys=True) + "\n")


def collect_templates(paths: list[str] | None) -> list[Path]:
    if paths:
        return [Path(p).resolve() for p in paths]
    return sorted(TEMPLATE_DIR.rglob("*.html"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="*", help="Specific files to scan")
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Rewrite .color-only-baseline.json from current state",
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="List violations without failing",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Print each violation"
    )
    args = parser.parse_args()

    paths = collect_templates(args.files or None)
    results = scan_paths(paths)
    counts = {f: len(v) for f, v in results.items()}

    if args.update_baseline:
        write_baseline(counts)
        total = sum(counts.values())
        print(f"Wrote baseline: {total} violations across {len(counts)} files")
        return 0

    if args.report or args.verbose:
        total = sum(counts.values())
        for f, viols in sorted(results.items()):
            print(f"{f}: {len(viols)} violation(s)")
            if args.verbose:
                for v in viols:
                    print(f"  L{v['line']}: {v['snippet']}")
        print(f"\nTotal: {total} violations across {len(counts)} files")
        return 0

    # Compare to baseline.
    baseline = load_baseline()
    regressions: list[str] = []
    for f, n in counts.items():
        prev = baseline.get(f, 0)
        if n > prev:
            regressions.append(f"{f}: {prev} -> {n}")

    if regressions:
        print("New color-only status indicator violations:", file=sys.stderr)
        for r in regressions:
            print(f"  {r}", file=sys.stderr)
        print(
            '\nFix: add aria-hidden="true" if adjacent text conveys status,\n'
            'or add <span class="sr-only">{Status}</span> sibling if standalone.\n'
            "Run --update-baseline only if intentionally accepting the violation.",
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
