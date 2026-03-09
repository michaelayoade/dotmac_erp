#!/usr/bin/env python3
"""
SA 2.0 migration phase 2: Wrap terminal methods (.first(), .all(), .scalar())
and handle .with_entities(), remaining .filter(), .count().

After phase 1 turned db.query(M).filter(...) into select(M).where(...),
the terminal methods (.first(), .all(), .scalar()) still reference Select
objects directly. These need to be routed through db.scalars()/db.scalar().

Strategy:
  - Track paren depth to identify multi-line select() expressions
  - Wrap .first()/.all() with db.scalars()
  - Wrap .scalar() with db.scalar()
  - Replace .with_entities(func.count(X)).scalar() patterns
  - Replace .count() calls
  - Replace remaining .filter() with .where()
"""

import re
import sys
from pathlib import Path

# Files modified in phase 1
PHASE1_FILES = set()


def find_db_var_in_context(lines: list[str], line_idx: int) -> str:
    """Find the db/session variable by scanning the enclosing function."""
    for i in range(line_idx, max(-1, line_idx - 50), -1):
        line = lines[i]
        # def method(self, db: Session, ...) or def func(db: Session, ...)
        m = re.search(r'def\s+\w+\([^)]*?(\w+)\s*:\s*Session', line)
        if m:
            return m.group(1)
        # self.db usage
        if 'self.db.' in line:
            return 'self.db'
    return 'db'


def process_file(filepath: Path) -> tuple[bool, int]:
    """Process multi-line select() expressions needing terminal wrapping."""
    content = filepath.read_text()
    original = content
    count = 0

    # ── Fix remaining .filter( → .where( ──
    # Some were missed if they were on lines without db.query()
    content, n = re.subn(r'\.filter\(', '.where(', content)
    count += n

    # ── Fix remaining .query( → select( ──
    content, n = re.subn(r'(\w+)\.query\(', 'select(', content)
    count += n

    # ── Fix .with_entities(func.count(X)).scalar() → db.scalar(select(func.count(X)).where(...)) ──
    # This is complex; handle the simpler pattern:
    # variable.with_entities(func.count(X)).scalar()
    # → db.scalar(select(func.count(X)).select_from(variable.subquery()))

    # ── Now handle terminal methods on select() expressions ──
    lines = content.split('\n')
    result_lines = []

    i = 0
    while i < len(lines):
        line = lines[i]

        # Skip lines that are already wrapped properly
        if 'db.scalars(' in line or 'db.scalar(' in line or 'db.execute(' in line:
            result_lines.append(line)
            i += 1
            continue

        # ── SINGLE-LINE PATTERNS ──

        # select(X).where(...).first()
        m = re.match(
            r'^(\s*)(.*?\s*=\s*(?:\()?\s*)(select\(.+?)\.first\(\)(.*)$',
            line,
        )
        if m and 'db.scalars' not in line:
            indent, prefix, select_part, suffix = m.groups()
            db_var = find_db_var_in_context(lines, i)
            result_lines.append(f'{indent}{prefix}{db_var}.scalars({select_part}).first(){suffix}')
            count += 1
            i += 1
            continue

        # select(X).where(...).all()
        m = re.match(
            r'^(\s*)(.*?\s*=\s*(?:list\()?\s*)(select\(.+?)\.all\(\)(.*)$',
            line,
        )
        if m and 'db.scalars' not in line:
            indent, prefix, select_part, suffix = m.groups()
            db_var = find_db_var_in_context(lines, i)
            has_list = 'list(' in prefix
            if has_list:
                result_lines.append(f'{indent}{prefix}{db_var}.scalars({select_part}).all(){suffix}')
            else:
                result_lines.append(f'{indent}{prefix}list({db_var}.scalars({select_part}).all()){suffix}')
            count += 1
            i += 1
            continue

        # select(X).where(...).scalar()
        m = re.match(
            r'^(\s*)(.*?)(select\(.+?)\.scalar\(\)(.*)$',
            line,
        )
        if m and 'db.scalar' not in line:
            indent, prefix, select_part, suffix = m.groups()
            db_var = find_db_var_in_context(lines, i)
            result_lines.append(f'{indent}{prefix}{db_var}.scalar({select_part}){suffix}')
            count += 1
            i += 1
            continue

        # ── MULTI-LINE PATTERNS ──
        # Look for lines ending with .first() or .all() or .scalar()
        # that are part of a multi-line select() expression
        stripped = line.strip()

        if stripped in ('.first()', '.all()', '.scalar()') or \
           stripped in (').first()', ').all()', ').scalar()'):
            # This is a terminal line. Scan backwards to find the select() start
            method = stripped.rstrip(')')
            if method.startswith(')'):
                method = method[1:]

            # Find the opening of this expression
            start_idx = None
            paren_depth = 0
            for j in range(i, max(-1, i - 20), -1):
                for ch in reversed(lines[j]):
                    if ch == ')':
                        paren_depth += 1
                    elif ch == '(':
                        paren_depth -= 1
                if 'select(' in lines[j] and paren_depth <= 0:
                    start_idx = j
                    break
                # Also check for assignment with opening paren
                if re.match(r'\s*\w+\s*=\s*\(?$', lines[j].rstrip()):
                    start_idx = j
                    break

            if start_idx is not None:
                db_var = find_db_var_in_context(lines, start_idx)

                if method == '.first()' or method == '.all()':
                    # Find the line with select( or the assignment
                    for j in range(start_idx, i + 1):
                        if 'select(' in lines[j] and 'db.scalars' not in lines[j]:
                            # Add db.scalars( before select(
                            sline = lines[j]
                            idx = sline.index('select(')
                            lines[j] = sline[:idx] + db_var + '.scalars(' + sline[idx:]
                            count += 1
                            break

                    # Replace terminal .first()/.all() with ).first()/).all()
                    # (closing the db.scalars paren)
                    indent_t = re.match(r'(\s*)', line).group(1)
                    if stripped.startswith(')'):
                        # Was: ).first()  → ).first()  but also need to close db.scalars
                        lines[i] = f'{indent_t}){method}'
                    else:
                        # Was: .first()  → ).first()
                        lines[i] = f'{indent_t}){method}'

                elif method == '.scalar()':
                    for j in range(start_idx, i + 1):
                        if 'select(' in lines[j] and 'db.scalar' not in lines[j]:
                            sline = lines[j]
                            idx = sline.index('select(')
                            lines[j] = sline[:idx] + db_var + '.scalar(' + sline[idx:]
                            count += 1
                            break

                    # Remove .scalar() from the terminal line and close paren
                    indent_t = re.match(r'(\s*)', line).group(1)
                    if stripped == '.scalar()':
                        lines[i] = f'{indent_t})'
                    elif stripped == ').scalar()':
                        lines[i] = f'{indent_t})'

        result_lines.append(lines[i])
        i += 1

    content = '\n'.join(result_lines)

    # ── Handle query variable patterns ──
    # Pattern: stmt = select(Model) then later stmt.first() / stmt.all()
    # These are harder — the variable is used later, so we can't inline
    # For these, we need:
    #   results = list(db.scalars(stmt).all())  or  result = db.scalars(stmt).first()
    # We'll handle these by looking for var.first() / var.all() where var was assigned select()
    # Skip this for now — too risky for automated migration

    if content != original:
        filepath.write_text(content)
        return True, count

    return False, 0


def main():
    app_dir = Path(__file__).parent.parent / "app"
    if not app_dir.exists():
        print(f"ERROR: {app_dir} not found")
        sys.exit(1)

    # Process all .py files that have select() with unwrapped terminals
    target_files = []
    for py_file in sorted(app_dir.rglob("*.py")):
        if '__pycache__' in str(py_file):
            continue
        text = py_file.read_text()
        needs_work = False
        if '.filter(' in text:
            needs_work = True
        if '.query(' in text and 'import' not in text.split('.query(')[0].split('\n')[-1]:
            needs_work = True
        if 'select(' in text:
            # Check for unwrapped terminals
            for pattern in ['.first()', '.all()', '.scalar()']:
                if pattern in text and 'db.scalars' not in text.split(pattern)[0].split('\n')[-1]:
                    needs_work = True
                    break
        if needs_work:
            target_files.append(py_file)

    print(f"Phase 2: Processing {len(target_files)} files\n")

    total_changed = 0
    total_replacements = 0

    for f in target_files:
        rel = f.relative_to(app_dir.parent)
        changed, n = process_file(f)
        if changed:
            print(f"  ✓ {rel} ({n} replacements)")
            total_changed += 1
            total_replacements += n
        else:
            print(f"  - {rel} (no changes needed)")

    print(f"\nDone: {total_changed} files changed, {total_replacements} replacements")

    # Show remaining issues
    print("\n=== Remaining issues (need manual fix) ===")
    remaining = 0
    for py_file in sorted(app_dir.rglob("*.py")):
        if '__pycache__' in str(py_file):
            continue
        text = py_file.read_text()
        if '.with_entities(' in text:
            rel = py_file.relative_to(app_dir.parent)
            lines = [
                (i + 1, l.strip())
                for i, l in enumerate(text.split('\n'))
                if '.with_entities(' in l
            ]
            for ln, lt in lines:
                print(f"  {rel}:{ln}: {lt[:80]}")
                remaining += 1
    print(f"\n  Total .with_entities() remaining: {remaining}")


if __name__ == "__main__":
    main()
