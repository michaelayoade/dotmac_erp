#!/usr/bin/env python3
"""Remove error_banner from base templates (individual templates handle errors)."""

from __future__ import annotations

from pathlib import Path

BASE_TEMPLATES = [
    "templates/finance/base_finance.html",
    "templates/people/base_people.html",
    "templates/expense/base_expense.html",
    "templates/inventory/base_inventory.html",
    "templates/procurement/base_procurement.html",
    "templates/modules/base_modules.html",
    "templates/admin/base_admin.html",
    "templates/careers/base_careers.html",
    "templates/onboarding/portal/base_onboarding.html",
]

ROOT = Path("/root/dotmac")

for rel_path in BASE_TEMPLATES:
    fpath = ROOT / rel_path
    content = fpath.read_text()

    # Remove the error_banner line
    lines = content.split("\n")
    new_lines = [line for line in lines if "error_banner" not in line]

    # Clean up import: remove error_banner from import
    new_lines = [
        line.replace(", error_banner", "").replace("error_banner, ", "")
        for line in new_lines
    ]

    if len(new_lines) != len(lines):
        fpath.write_text("\n".join(new_lines))
        print(f"  Fixed: {rel_path}")
    else:
        print(f"  Skip: {rel_path}")
