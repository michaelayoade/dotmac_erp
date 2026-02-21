"""
Fix SQLAlchemy 1.x → 2.0 mock patterns in test files.

The services migrated from db.query().filter().first() to db.scalar(select(...))
and from db.query().filter().all() to db.scalars(select(...)).all(), but the tests
still use old mock patterns.

This script:
1. Updates conftest.py mock_db fixtures to include scalar/scalars
2. Replaces old mock patterns in test files with new ones

Usage:
    python scripts/fix_test_mocks.py --dry-run     # Preview changes
    python scripts/fix_test_mocks.py --execute      # Apply changes
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# ========== Pattern 1: conftest mock_db fixture updates ==========

CONFTEST_ADDITIONS = """    # SQLAlchemy 2.0: db.scalar() for single results
    session.scalar = MagicMock(return_value=None)
    # SQLAlchemy 2.0: db.scalars().all() for multiple results
    _scalars_result = MagicMock()
    _scalars_result.all = MagicMock(return_value=[])
    _scalars_result.first = MagicMock(return_value=None)
    _scalars_result.unique = MagicMock(return_value=_scalars_result)
    session.scalars = MagicMock(return_value=_scalars_result)"""


def fix_conftest(path: Path, dry_run: bool) -> int:
    """Add scalar/scalars to mock_db fixture in conftest.py."""
    content = path.read_text()

    # Already has scalar mock
    if "session.scalar = " in content:
        return 0

    # Find the mock_db fixture and add scalar/scalars after session.execute
    pattern = r"(    session\.execute = MagicMock\(\))"
    replacement = r"\1\n" + CONFTEST_ADDITIONS

    new_content = re.sub(pattern, replacement, content)
    if new_content == content:
        # Try after session.get
        pattern = r"(    session\.get = MagicMock\(return_value=None\))"
        replacement = r"\1\n" + CONFTEST_ADDITIONS
        new_content = re.sub(pattern, replacement, content)

    if new_content == content:
        # Try after session.delete
        pattern = r"(    session\.delete = MagicMock\(\))"
        replacement = r"\1\n" + CONFTEST_ADDITIONS
        new_content = re.sub(pattern, replacement, content)

    if new_content == content:
        print(f"  SKIP {path} — couldn't find insertion point")
        return 0

    if not dry_run:
        path.write_text(new_content)
    print(f"  {'WOULD FIX' if dry_run else 'FIXED'} {path}")
    return 1


# ========== Pattern 2: Replace old mock patterns in tests ==========

# Pattern: mock_db.query.return_value.filter.return_value.first.return_value = X
# Replace: mock_db.scalar.return_value = X
QUERY_FIRST_PATTERN = re.compile(
    r"(\w+)\.query\.return_value\.filter\.return_value\.first\.return_value\s*=\s*(.+)"
)

# Pattern: mock_db.query.return_value.filter.return_value.count.return_value = X
# Replace: mock_db.scalar.return_value = X
QUERY_COUNT_PATTERN = re.compile(
    r"(\w+)\.query\.return_value\.filter\.return_value\.count\.return_value\s*=\s*(.+)"
)

# Pattern: mock_db.query.return_value.filter.return_value.all.return_value = X
# Replace: mock_db.scalars.return_value.all.return_value = X
QUERY_ALL_PATTERN = re.compile(
    r"(\w+)\.query\.return_value\.filter\.return_value\.all\.return_value\s*=\s*(.+)"
)

# Pattern: mock_db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.offset.return_value.all.return_value = X
# Replace: mock_db.scalars.return_value.all.return_value = X
QUERY_ORDER_LIMIT_ALL_PATTERN = re.compile(
    r"(\w+)\.query\.return_value"
    r"(?:\.filter\.return_value)?"
    r"(?:\.order_by\.return_value)?"
    r"(?:\.limit\.return_value)?"
    r"(?:\.offset\.return_value)?"
    r"\.all\.return_value\s*=\s*(.+)"
)

# Pattern: mock_db.query.return_value.filter.return_value.order_by.return_value.first.return_value = X
# Replace: mock_db.scalar.return_value = X
QUERY_ORDER_FIRST_PATTERN = re.compile(
    r"(\w+)\.query\.return_value"
    r"(?:\.filter\.return_value)?"
    r"(?:\.order_by\.return_value)?"
    r"\.first\.return_value\s*=\s*(.+)"
)

# Pattern: mock_db.query.return_value.filter.return_value.limit.return_value.all.return_value = X
# Replace: mock_db.scalars.return_value.all.return_value = X
QUERY_FILTER_LIMIT_ALL_PATTERN = re.compile(
    r"(\w+)\.query\.return_value\.filter\.return_value\.limit\.return_value\.all\.return_value\s*=\s*(.+)"
)

# Pattern: mock_db.query.return_value.filter.return_value.one_or_none.return_value = X
# Replace: mock_db.scalar.return_value = X
QUERY_ONE_OR_NONE_PATTERN = re.compile(
    r"(\w+)\.query\.return_value\.filter\.return_value\.one_or_none\.return_value\s*=\s*(.+)"
)

# Pattern: mock_db.query.return_value.filter.return_value.options.return_value...first.return_value = X
# Replace: mock_db.scalar.return_value = X
QUERY_OPTIONS_FIRST_PATTERN = re.compile(
    r"(\w+)\.query\.return_value"
    r"(?:\.filter\.return_value)?"
    r"(?:\.options\.return_value)?"
    r"\.first\.return_value\s*=\s*(.+)"
)

# Pattern: mock_db.query.return_value.join.return_value.filter.return_value.first.return_value = X
# Replace: mock_db.scalar.return_value = X
QUERY_JOIN_FIRST_PATTERN = re.compile(
    r"(\w+)\.query\.return_value\.join\.return_value\.filter\.return_value\.first\.return_value\s*=\s*(.+)"
)

# Pattern: mock_db.query.return_value.join.return_value.filter.return_value.all.return_value = X
# Replace: mock_db.scalars.return_value.all.return_value = X
QUERY_JOIN_ALL_PATTERN = re.compile(
    r"(\w+)\.query\.return_value\.join\.return_value\.filter\.return_value\.all\.return_value\s*=\s*(.+)"
)


def fix_test_file(path: Path, dry_run: bool) -> int:
    """Replace old mock patterns in a test file."""
    content = path.read_text()
    original = content
    changes = 0

    # Order matters — more specific patterns first

    # join + filter + all → scalars
    for m in QUERY_JOIN_ALL_PATTERN.finditer(content):
        old = m.group(0)
        var, val = m.group(1), m.group(2)
        new = f"{var}.scalars.return_value.all.return_value = {val}"
        content = content.replace(old, new, 1)
        changes += 1

    # join + filter + first → scalar
    for m in QUERY_JOIN_FIRST_PATTERN.finditer(content):
        old = m.group(0)
        var, val = m.group(1), m.group(2)
        new = f"{var}.scalar.return_value = {val}"
        content = content.replace(old, new, 1)
        changes += 1

    # options + first → scalar
    for m in QUERY_OPTIONS_FIRST_PATTERN.finditer(content):
        old = m.group(0)
        var, val = m.group(1), m.group(2)
        new = f"{var}.scalar.return_value = {val}"
        content = content.replace(old, new, 1)
        changes += 1

    # order_by + limit + offset + all → scalars
    for m in QUERY_ORDER_LIMIT_ALL_PATTERN.finditer(content):
        old = m.group(0)
        var, val = m.group(1), m.group(2)
        new = f"{var}.scalars.return_value.all.return_value = {val}"
        content = content.replace(old, new, 1)
        changes += 1

    # filter + limit + all → scalars
    for m in QUERY_FILTER_LIMIT_ALL_PATTERN.finditer(content):
        old = m.group(0)
        var, val = m.group(1), m.group(2)
        new = f"{var}.scalars.return_value.all.return_value = {val}"
        content = content.replace(old, new, 1)
        changes += 1

    # filter + all → scalars
    for m in QUERY_ALL_PATTERN.finditer(content):
        old = m.group(0)
        var, val = m.group(1), m.group(2)
        new = f"{var}.scalars.return_value.all.return_value = {val}"
        content = content.replace(old, new, 1)
        changes += 1

    # order_by + first → scalar
    for m in QUERY_ORDER_FIRST_PATTERN.finditer(content):
        old = m.group(0)
        var, val = m.group(1), m.group(2)
        new = f"{var}.scalar.return_value = {val}"
        content = content.replace(old, new, 1)
        changes += 1

    # filter + count → scalar
    for m in QUERY_COUNT_PATTERN.finditer(content):
        old = m.group(0)
        var, val = m.group(1), m.group(2)
        new = f"{var}.scalar.return_value = {val}"
        content = content.replace(old, new, 1)
        changes += 1

    # filter + one_or_none → scalar
    for m in QUERY_ONE_OR_NONE_PATTERN.finditer(content):
        old = m.group(0)
        var, val = m.group(1), m.group(2)
        new = f"{var}.scalar.return_value = {val}"
        content = content.replace(old, new, 1)
        changes += 1

    # filter + first → scalar (most common, last)
    for m in QUERY_FIRST_PATTERN.finditer(content):
        old = m.group(0)
        var, val = m.group(1), m.group(2)
        new = f"{var}.scalar.return_value = {val}"
        content = content.replace(old, new, 1)
        changes += 1

    if content != original:
        if not dry_run:
            path.write_text(content)
        print(
            f"  {'WOULD FIX' if dry_run else 'FIXED'} {path} — {changes} replacements"
        )

    return changes


def main() -> None:
    parser = argparse.ArgumentParser(description="Fix SQLAlchemy 1.x → 2.0 test mocks")
    parser.add_argument(
        "--dry-run", action="store_true", help="Preview without writing"
    )
    parser.add_argument("--execute", action="store_true", help="Apply changes")
    parser.add_argument("--path", default="tests/ifrs", help="Test directory to scan")
    args = parser.parse_args()

    if not args.dry_run and not args.execute:
        print("Must specify --dry-run or --execute")
        sys.exit(1)

    dry_run = args.dry_run
    base = Path(args.path)

    print("=" * 60)
    print("Step 1: Fix conftest.py fixtures")
    print("=" * 60)

    conftest_count = 0
    for conftest in sorted(base.rglob("conftest.py")):
        conftest_count += fix_conftest(conftest, dry_run)

    print(f"\n  Total conftest fixes: {conftest_count}")

    print("\n" + "=" * 60)
    print("Step 2: Fix test mock patterns")
    print("=" * 60)

    total_changes = 0
    files_changed = 0
    for test_file in sorted(base.rglob("test_*.py")):
        n = fix_test_file(test_file, dry_run)
        if n > 0:
            total_changes += n
            files_changed += 1

    print(f"\n  Total: {total_changes} replacements in {files_changed} files")

    # Check remaining old patterns
    remaining = 0
    for test_file in base.rglob("test_*.py"):
        content = test_file.read_text()
        remaining += content.count(".query.return_value")
    print(f"  Remaining old patterns: {remaining}")


if __name__ == "__main__":
    main()
