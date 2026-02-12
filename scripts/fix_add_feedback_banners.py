#!/usr/bin/env python3
"""Add success/error feedback banners to base templates.

Inserts {% from "components/macros.html" import success_banner, error_banner %} at top
and banner display before {% block content %}{% endblock %} in each base template.

This gives ALL pages extending these bases automatic saved/error feedback.
"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_TEMPLATES = [
    Path("/root/dotmac/templates/finance/base_finance.html"),
    Path("/root/dotmac/templates/people/base_people.html"),
    Path("/root/dotmac/templates/expense/base_expense.html"),
    Path("/root/dotmac/templates/inventory/base_inventory.html"),
    Path("/root/dotmac/templates/procurement/base_procurement.html"),
    Path("/root/dotmac/templates/modules/base_modules.html"),
    Path("/root/dotmac/templates/admin/base_admin.html"),
    Path("/root/dotmac/templates/careers/base_careers.html"),
    Path("/root/dotmac/templates/onboarding/portal/base_onboarding.html"),
]

BANNER_BLOCK = """\
                {% if saved %}{{ success_banner("Record saved successfully.") }}{% endif %}
                {% if error %}{{ error_banner(error) }}{% endif %}
"""

# For templates with different indentation
BANNER_BLOCK_NO_INDENT = """\
        {% if saved %}{{ success_banner("Record saved successfully.") }}{% endif %}
        {% if error %}{{ error_banner(error) }}{% endif %}
"""

MACRO_IMPORT = '{% from "components/macros.html" import success_banner, error_banner %}'


def process_template(filepath: Path, dry_run: bool = True) -> list[str]:
    """Add feedback banners to a single base template."""
    content = filepath.read_text()
    changes: list[str] = []

    # Skip if already has success_banner import
    if "success_banner" in content:
        return []

    lines = content.split("\n")

    # Step 1: Add macro import after {% extends %}
    import_added = False
    for i, line in enumerate(lines):
        if "{% extends" in line:
            lines.insert(i + 1, MACRO_IMPORT)
            changes.append(f"  L{i + 2}: Added macro import")
            import_added = True
            break

    if not import_added:
        # No extends found — add at top
        lines.insert(0, MACRO_IMPORT)
        changes.append("  L1: Added macro import at top")

    # Step 2: Add banner block before {% block content %}{% endblock %}
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped == "{% block content %}{% endblock %}":
            # Detect indentation
            indent = line[: len(line) - len(line.lstrip())]
            banner = f'{indent}{{% if saved %}}{{{{ success_banner("Record saved successfully.") }}}}{{% endif %}}\n'
            banner += (
                f"{indent}{{% if error %}}{{{{ error_banner(error) }}}}{{% endif %}}"
            )
            lines.insert(i, banner)
            changes.append(f"  L{i + 1}: Added feedback banners before content block")
            break

    if changes and not dry_run:
        filepath.write_text("\n".join(lines))

    return changes


def main() -> None:
    dry_run = "--execute" not in sys.argv

    if dry_run:
        print("DRY RUN — pass --execute to apply changes\n")

    total = 0
    for template in BASE_TEMPLATES:
        if not template.exists():
            print(f"  SKIP (not found): {template}")
            continue
        changes = process_template(template, dry_run=dry_run)
        if changes:
            total += len(changes)
            print(f"{template.relative_to(Path('/root/dotmac'))}:")
            for c in changes:
                print(c)
        else:
            print(f"  (no changes) {template.relative_to(Path('/root/dotmac'))}")

    print(f"\n{'Would modify' if dry_run else 'Modified'}: {total} changes")


if __name__ == "__main__":
    main()
