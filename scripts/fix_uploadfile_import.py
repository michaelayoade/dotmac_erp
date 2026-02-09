"""Fix UploadFile import: fastapi.UploadFile → starlette.datastructures.UploadFile.

When code does `await request.form()`, Starlette returns its native UploadFile objects.
isinstance() checks against fastapi.UploadFile fail silently in newer FastAPI versions
because they are different classes at runtime.

This script fixes all files that do isinstance(..., UploadFile) to import from starlette.
"""

from __future__ import annotations

import re
from pathlib import Path


def fix_file(path: Path) -> bool:
    """Fix UploadFile import in a single file. Returns True if changed."""
    content = path.read_text()

    # Check if file does isinstance(..., UploadFile)
    if "isinstance(" not in content or "UploadFile)" not in content:
        return False

    # Already imports from starlette
    if "from starlette.datastructures import UploadFile" in content:
        return False

    lines = content.split("\n")
    new_lines: list[str] = []
    starlette_import_added = False
    changed = False

    for line in lines:
        # Match: from fastapi import ..., UploadFile, ...
        m = re.match(r"^(\s*from fastapi import )(.+)$", line)
        if m and "UploadFile" in m.group(2):
            prefix = m.group(1)
            imports_str = m.group(2)

            # Parse the imports list
            imports = [i.strip() for i in imports_str.split(",")]
            imports = [i for i in imports if i]  # remove empty

            # Remove UploadFile
            new_imports = [i for i in imports if i != "UploadFile"]

            if len(new_imports) < len(imports):
                changed = True
                if new_imports:
                    new_line = prefix + ", ".join(new_imports)
                    new_lines.append(new_line)
                # else: entire line was just UploadFile, skip it

                # Add starlette import right after
                if not starlette_import_added:
                    indent = m.group(1).split("from")[0]  # preserve indentation
                    new_lines.append(
                        f"{indent}from starlette.datastructures import UploadFile"
                    )
                    starlette_import_added = True
            else:
                new_lines.append(line)
        else:
            new_lines.append(line)

    if changed:
        path.write_text("\n".join(new_lines))
        return True
    return False


def main() -> None:
    root = Path(__file__).parent.parent
    targets = [
        "app/services/people/leave/web.py",
        "app/services/people/scheduling/web.py",
        "app/services/operations/inv_web.py",
        "app/services/people/hr/web/employee_web.py",
        "app/services/people/payroll/web/structure_web.py",
        "app/services/people/payroll/web/component_web.py",
        "app/services/people/payroll/web/tax_web.py",
        "app/services/people/attendance/web.py",
        "app/services/fixed_assets/web.py",
        "app/services/people/perf/web/perf_web.py",
        "app/services/people/recruit/web/interview_web.py",
        "app/services/people/recruit/web/applicant_web.py",
        "app/services/people/recruit/web/offer_web.py",
        "app/services/finance/tax/web.py",
        "app/web/admin.py",
        "app/web/profile.py",
        "app/web/projects.py",
        "app/services/people/self_service_web.py",
    ]

    fixed = 0
    for rel in targets:
        path = root / rel
        if not path.exists():
            print(f"  SKIP (not found): {rel}")
            continue
        if fix_file(path):
            print(f"  FIXED: {rel}")
            fixed += 1
        else:
            print(f"  OK (no change needed): {rel}")

    print(f"\nFixed {fixed} files")


if __name__ == "__main__":
    main()
