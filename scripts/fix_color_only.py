#!/usr/bin/env python3
"""
Auto-fix color-only status dots by injecting aria-hidden="true".

Use only for dots that are paired with adjacent text labels — i.e. the dot
is a redundant visual cue. For truly standalone status indicators (where
color is the SOLE signal), edit by hand and add aria-label instead.

Usage:
    python3 scripts/fix_color_only.py                          # fix all flagged files
    python3 scripts/fix_color_only.py templates/foo/bar.html   # fix one file
    python3 scripts/fix_color_only.py --dry-run                # preview
    python3 scripts/fix_color_only.py --skip templates/x.html  # exclude (repeatable)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from audit_color_only import (  # noqa: E402
    has_accessible_name,
    looks_like_status_dot,
    RE_DOT,
    TEMPLATE_DIR,
    body_is_empty,
)


def fix_file(path: Path, dry_run: bool = False) -> int:
    text = path.read_text(encoding="utf-8")
    fixes = 0
    # Process matches from end to start so offsets don't shift mid-walk.
    matches = list(RE_DOT.finditer(text))
    for m in reversed(matches):
        attrs = m.group("attrs") or ""
        body = m.group("body") or ""
        if not looks_like_status_dot(attrs):
            continue
        if has_accessible_name(attrs):
            continue
        if not body_is_empty(body):
            continue
        # Inject aria-hidden="true" before the closing '>' of the open tag.
        # m.start() is the position of '<' in '<span', so the open-tag close
        # '>' is somewhere in m.group(0). Find it relative to m.start().
        full = m.group(0)
        open_close_rel = full.find(">")
        if open_close_rel == -1:
            continue
        insert_pos = m.start() + open_close_rel
        text = text[:insert_pos] + ' aria-hidden="true"' + text[insert_pos:]
        fixes += 1

    if fixes and not dry_run:
        path.write_text(text, encoding="utf-8")
    return fixes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="*", help="Specific files to fix")
    parser.add_argument(
        "--skip",
        action="append",
        default=[],
        help="Skip these template paths (repeatable)",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.files:
        paths = [Path(f).resolve() for f in args.files]
    else:
        paths = sorted(TEMPLATE_DIR.rglob("*.html"))

    skip_set = {str(Path(s).resolve()) for s in args.skip}
    total = 0
    touched = 0
    for p in paths:
        if str(p) in skip_set:
            continue
        n = fix_file(p, dry_run=args.dry_run)
        if n:
            try:
                rel = p.relative_to(REPO_ROOT)
            except ValueError:
                rel = p
            print(f"{rel}: +{n} aria-hidden")
            total += n
            touched += 1

    verb = "would inject" if args.dry_run else "injected"
    print(f"\n{verb} {total} aria-hidden across {touched} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
