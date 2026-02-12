"""
Merge duplicate {% from "components/macros.html" import ... %} lines
into a single import line per file.
"""

from __future__ import annotations

import re
from pathlib import Path

IMPORT_PATTERN = re.compile(
    r'^\s*\{%[-\s]+from\s+"components/macros\.html"\s+import\s+(.+?)\s*[-]?%\}\s*$'
)

FILES = [
    "templates/expense/limits/list.html",
    "templates/expense/limits/evaluations.html",
    "templates/expense/advances/list.html",
    "templates/procurement/requisitions/list.html",
    "templates/finance/gl/journals.html",
    "templates/admin/audit_logs.html",
    "templates/admin/settings.html",
    "templates/admin/data_changes.html",
    "templates/inventory/material_requests.html",
    "templates/projects/list.html",
    "templates/people/payroll/loans.html",
    "templates/people/payroll/components.html",
]


def merge_imports(filepath: Path) -> tuple[bool, list[str], list[str]]:
    """Merge duplicate macro imports in a single file.

    Returns (changed, old_lines, new_lines).
    """
    lines = filepath.read_text().splitlines(keepends=True)

    # Find all import lines and their indices
    import_lines: list[tuple[int, list[str]]] = []
    for i, line in enumerate(lines):
        m = IMPORT_PATTERN.match(line)
        if m:
            names = [n.strip() for n in m.group(1).split(",")]
            import_lines.append((i, names))

    if len(import_lines) <= 1:
        print("  SKIP (0-1 import lines found)")
        return False, [], []

    # Collect all unique names, preserving first-seen order
    seen: set[str] = set()
    ordered_names: list[str] = []
    for _, names in import_lines:
        for name in names:
            if name not in seen:
                seen.add(name)
                ordered_names.append(name)

    # Build merged import line
    merged = (
        '{%- from "components/macros.html" import ' + ", ".join(ordered_names) + " %}\n"
    )

    # Replace: keep first occurrence position, remove all others
    first_idx = import_lines[0][0]
    indices_to_remove = {idx for idx, _ in import_lines[1:]}

    old_lines = [lines[idx] for idx, _ in import_lines]

    new_lines: list[str] = []
    for i, line in enumerate(lines):
        if i == first_idx:
            new_lines.append(merged)
        elif i in indices_to_remove:
            continue  # skip duplicate import lines
        else:
            new_lines.append(line)

    filepath.write_text("".join(new_lines))
    return True, old_lines, [merged]


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    changed_count = 0

    for rel_path in FILES:
        filepath = root / rel_path
        print(f"\n--- {rel_path} ---")
        if not filepath.exists():
            print("  FILE NOT FOUND")
            continue

        changed, old, new = merge_imports(filepath)
        if changed:
            changed_count += 1
            print("  BEFORE:")
            for line in old:
                print(f"    {line.rstrip()}")
            print("  AFTER:")
            for line in new:
                print(f"    {line.rstrip()}")

    print(f"\n=== Done: {changed_count}/{len(FILES)} files modified ===")


if __name__ == "__main__":
    main()
