"""
Remove db.commit() and db.rollback() from route files.

Now that get_db() auto-commits on success and auto-rolls-back on exception,
routes no longer need explicit commit/rollback calls.

Preserves:
- auth_db.commit() calls (SSO auth database)
- Any commit/rollback inside service files
- Comments mentioning commit/rollback
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Only process route files
ROUTE_DIRS = [
    Path("app/api"),
    Path("app/web"),
]

# Patterns to remove (standalone lines only)
# Match: optional whitespace + db.commit() or db.rollback() + optional comment + newline
COMMIT_RE = re.compile(r"^(\s+)db\.commit\(\)\s*(?:#.*)?\n", re.MULTILINE)
ROLLBACK_RE = re.compile(r"^(\s+)db\.rollback\(\)\s*(?:#.*)?\n", re.MULTILINE)

# Files containing get_db() definitions — their commit/rollback is intentional
SKIP_FILES = {
    Path("app/api/deps.py"),
    Path("app/web/deps.py"),
    Path("app/api/finance/fx.py"),
}

# Do NOT touch these patterns
SKIP_PATTERNS = [
    "auth_db.commit()",
    "auth_db.rollback()",
]


def should_skip_line(line: str) -> bool:
    """Check if line contains a pattern we should preserve."""
    return any(pat in line for pat in SKIP_PATTERNS)


def process_file(path: Path, dry_run: bool = False) -> int:
    """Remove db.commit()/db.rollback() from a file. Returns count of removals."""
    content = path.read_text()
    original = content

    removals = 0

    # Process line by line to handle SKIP_PATTERNS correctly
    lines = content.splitlines(keepends=True)
    new_lines: list[str] = []

    # Track if we're inside a get_db() generator (between 'yield db' and 'finally:')
    in_get_db_gen = False

    for i, line in enumerate(lines):
        stripped = line.strip()

        # Detect entry into a get_db generator body
        if stripped == "yield db":
            in_get_db_gen = True
            new_lines.append(line)
            continue

        # Detect exit from get_db generator (finally: or a new def)
        if in_get_db_gen and (
            stripped.startswith("finally:") or stripped.startswith("def ")
        ):
            in_get_db_gen = False

        # Preserve commit/rollback inside get_db generators
        if in_get_db_gen:
            new_lines.append(line)
            continue

        # Skip lines we should preserve
        if should_skip_line(line):
            new_lines.append(line)
            continue

        # Remove standalone db.commit() lines
        if stripped == "db.commit()" or stripped.startswith("db.commit()  #"):
            removals += 1
            continue

        # Remove standalone db.rollback() lines
        if stripped == "db.rollback()" or stripped.startswith("db.rollback()  #"):
            removals += 1
            continue

        new_lines.append(line)

    if removals > 0 and not dry_run:
        path.write_text("".join(new_lines))

    return removals


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    verbose = "--verbose" in sys.argv or "-v" in sys.argv

    total_files = 0
    total_removals = 0

    for route_dir in ROUTE_DIRS:
        if not route_dir.exists():
            print(f"  SKIP {route_dir} (not found)")
            continue

        for py_file in sorted(route_dir.rglob("*.py")):
            # Skip files that contain get_db() definitions
            if py_file in SKIP_FILES:
                if verbose or dry_run:
                    print(f"  SKIP {py_file} (contains get_db definition)")
                continue

            content = py_file.read_text()

            # Quick check: does the file contain db.commit() or db.rollback()?
            if "db.commit()" not in content and "db.rollback()" not in content:
                continue

            count = process_file(py_file, dry_run=dry_run)
            if count > 0:
                total_files += 1
                total_removals += count
                if verbose or dry_run:
                    action = "would remove" if dry_run else "removed"
                    print(f"  {action} {count:3d} calls from {py_file}")

    mode = "[DRY RUN] " if dry_run else ""
    print(f"\n{mode}Total: {total_removals} removals across {total_files} files")


if __name__ == "__main__":
    main()
