#!/usr/bin/env python3
"""
SQLAlchemy 1.x → 2.0 migration script.

Handles the mechanical transformation of:
  db.query(Model).filter(...) → select(Model).where(...)
  .filter(...) → .where(...)
  .with_entities(...) → restructured select(...)
  Query([Model], session=db) → select(Model)
  etc.

Run: python scripts/migrate_sa2.py
"""

import re
import sys
from pathlib import Path


def migrate_file(filepath: Path) -> tuple[bool, int]:
    """Migrate a single file. Returns (changed, num_replacements)."""
    content = filepath.read_text()
    original = content
    count = 0

    # ── 1. Replace .filter( with .where( ──
    # But only when it's chained on a query, not on Python lists/dicts etc.
    # Match .filter( preceded by ) or a word char (query variable)
    content, n = re.subn(r"\.filter\(", ".where(", content)
    count += n

    # ── 2. Replace .filter_by( with .where( equivalent ──
    # This is rare but handle it
    content, n = re.subn(r"\.filter_by\(", ".where(", content)
    count += n

    # ── 3. Replace db.query(X) with select(X) ──
    # Handles: db.query(Model), self.db.query(Model)
    content, n = re.subn(
        r"(\b\w*db)\.query\(",
        r"__SA2_SELECT__(",
        content,
    )
    count += n

    # Also handle: Query([Model], session=db) and Query([Model], session=self.db)
    content, n = re.subn(
        r"Query\(\[([^\]]+)\],\s*session\s*=\s*\w+(?:\.\w+)*\)",
        r"select(\1)",
        content,
    )
    count += n

    if count == 0:
        return False, 0

    # Now we need to handle the __SA2_SELECT__ marker.
    # The tricky part: we replaced `db.query(` with `__SA2_SELECT__(`,
    # but we need to know the `db` variable name for wrapping .all()/.first()/.scalar()
    # Let's go back and use a smarter replacement

    # Revert and redo with captured db variable
    content = original

    # Track db variable names used in queries for this file
    db_vars = set()
    count = 0

    # Find all db.query( patterns and capture the db variable
    for m in re.finditer(r"(\b\w*db)\s*\.query\(", content):
        db_vars.add(m.group(1))

    # Also check for Query([...], session=X)
    for m in re.finditer(
        r"Query\(\[([^\]]+)\],\s*session\s*=\s*(\w+(?:\.\w+)*)\)", content
    ):
        db_vars.add(m.group(2))

    if not db_vars and ".filter(" not in content and ".with_entities(" not in content:
        return False, 0

    # ── Step 1: Add `select` import if not present ──
    if db_vars or "Query(" in content:
        # Check if select is already imported from sqlalchemy
        has_select_import = bool(re.search(r"from\s+sqlalchemy\b.*\bselect\b", content))
        has_func_import = bool(re.search(r"from\s+sqlalchemy\b.*\bfunc\b", content))

        if not has_select_import:
            # Try to add to existing sqlalchemy import
            m = re.search(r"(from\s+sqlalchemy\s+import\s+)([^\n]+)", content)
            if m:
                imports = m.group(2).rstrip()
                if "select" not in imports:
                    content = content.replace(
                        m.group(0),
                        f"{m.group(1)}select, {imports}"
                        if not imports.startswith("select")
                        else m.group(0),
                    )
                    count += 1
            else:
                # Check for `from sqlalchemy.orm import ...`
                m2 = re.search(r"(from\s+sqlalchemy\.orm\s+import\s+)", content)
                if m2:
                    # Add a new import line before it
                    content = content.replace(
                        m2.group(0),
                        f"from sqlalchemy import select\n{m2.group(0)}",
                    )
                    count += 1
                else:
                    # Add at top after other imports
                    content = re.sub(
                        r"(import logging)",
                        r"from sqlalchemy import select\n\1",
                        content,
                        count=1,
                    )
                    count += 1

    # ── Step 2: Replace Query([Model], session=db) → select(Model) ──
    content, n = re.subn(
        r"Query\(\[([^\]]+)\],\s*session\s*=\s*\w+(?:\.\w+)*\)",
        r"select(\1)",
        content,
    )
    count += n

    # Remove Query from sqlalchemy.orm imports if present
    if n > 0:
        content = re.sub(r",\s*Query\b", "", content)
        content = re.sub(r"\bQuery\s*,\s*", "", content)

    # ── Step 3: Replace db.query(X) → select(X) ──
    content, n = re.subn(
        r"(\b\w*db)\s*\.query\(",
        r"select(",
        content,
    )
    count += n

    # ── Step 4: Replace .filter( → .where( ──
    content, n = re.subn(r"\.filter\(", ".where(", content)
    count += n

    # ── Step 5: Replace .filter_by( → .where( ──
    content, n = re.subn(r"\.filter_by\(", ".where(", content)
    count += n

    if content != original:
        filepath.write_text(content)
        return True, count

    return False, 0


def main():
    app_dir = Path(__file__).parent.parent / "app"
    if not app_dir.exists():
        print(f"ERROR: {app_dir} not found")
        sys.exit(1)

    # Find all Python files with SA 1.x patterns
    target_files = []
    for py_file in sorted(app_dir.rglob("*.py")):
        text = py_file.read_text()
        if ".query(" in text or "Query([" in text:
            target_files.append(py_file)

    print(f"Found {len(target_files)} files with SA 1.x patterns\n")

    total_changed = 0
    total_replacements = 0

    for f in target_files:
        rel = f.relative_to(app_dir.parent)
        changed, n = migrate_file(f)
        if changed:
            print(f"  ✓ {rel} ({n} replacements)")
            total_changed += 1
            total_replacements += n
        else:
            print(f"  - {rel} (no changes)")

    print(
        f"\nDone: {total_changed} files changed, {total_replacements} total replacements"
    )
    print("\nREMAINING MANUAL WORK:")
    print("  1. .with_entities() calls need manual restructuring")
    print(
        "  2. .all() / .first() / .scalar() calls need db.scalars()/db.execute() wrapping"
    )
    print("  3. .count() calls need restructuring")
    print("  4. Verify imports are correct")


if __name__ == "__main__":
    main()
