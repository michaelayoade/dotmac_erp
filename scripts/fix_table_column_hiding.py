#!/usr/bin/env python3
"""Add 'hidden sm:table-cell' to secondary table columns for mobile responsiveness.

For each configured table, hides specified column positions on mobile
(<640px) and shows them on sm+ breakpoint.

Usage:
    python scripts/fix_table_column_hiding.py          # Dry run
    python scripts/fix_table_column_hiding.py --apply   # Apply changes
"""

import re
import sys
from pathlib import Path

# Configuration: file path → list of 0-indexed column positions to hide
# Only secondary/metadata columns; never hide primary ID, amount, status, or actions
COLUMN_HIDING_CONFIG: dict[str, list[int]] = {
    # Procurement
    "templates/procurement/plans/list.html": [2],  # Fiscal Year
    "templates/procurement/requisitions/list.html": [1, 2],  # Date, Urgency
    "templates/procurement/rfqs/list.html": [2, 3],  # Closing Date, Method
    "templates/procurement/contracts/list.html": [1],  # Title
    # Expense
    "templates/expense/list.html": [3, 4, 6],  # Payee, Account, Method
    "templates/expense/claims_list.html": [2],  # Date
    # Finance AR
    "templates/finance/ar/invoices.html": [2, 3],  # Invoice Date, Due Date
    "templates/finance/ar/receipts.html": [2, 3, 4],  # Receipt Date, Method, Reference
    "templates/finance/ar/customers.html": [2, 3, 7],  # Tax ID, Terms, Created
    # Finance AP
    "templates/finance/ap/invoices.html": [2, 3],  # Invoice Date, Due Date
    "templates/finance/ap/suppliers.html": [2, 3, 6],  # Tax ID, Terms, Created
    "templates/finance/ap/payments.html": [2, 3, 4],  # Payment Date, Method, Reference
    # Finance GL
    "templates/finance/gl/accounts.html": [
        2,
        3,
        6,
    ],  # Category, Normal Balance, Created
    # Inventory
    "templates/inventory/items.html": [2, 3],  # Category, Type
    # Fixed Assets
    "templates/fixed_assets/assets.html": [2, 3],  # Category, Acquired
}

HIDDEN_CLASS = "hidden sm:table-cell"


def already_has_hiding(class_str: str) -> bool:
    """Check if a class string already contains responsive hiding."""
    return "hidden sm:table-cell" in class_str or "hidden md:table-cell" in class_str


def add_hidden_to_tag(tag: str) -> str:
    """Add 'hidden sm:table-cell' to a <th> or <td> tag's class attribute."""
    if already_has_hiding(tag):
        return tag  # Already has responsive hiding

    # Find existing class="..."
    class_match = re.search(r'class="([^"]*)"', tag)
    if class_match:
        existing = class_match.group(1)
        new_classes = f"{HIDDEN_CLASS} {existing}"
        return tag[: class_match.start(1)] + new_classes + tag[class_match.end(1) :]
    else:
        # No class attribute — add one after the tag name
        # Match <th or <td, possibly with scope= or other attrs
        insert_match = re.match(r"(<t[hd])", tag)
        if insert_match:
            insert_pos = insert_match.end()
            return tag[:insert_pos] + f' class="{HIDDEN_CLASS}"' + tag[insert_pos:]
    return tag


def process_file(filepath: Path, cols_to_hide: list[int], apply: bool) -> int:
    """Process a single template file, adding hidden classes to specified columns.

    Returns number of fixes applied.
    """
    content = filepath.read_text()
    lines = content.split("\n")
    fixes = 0

    # We need to track which table rows we're in and count columns.
    # Strategy: find <thead> <tr> and <tbody> <tr> sections, count <th>/<td> tags
    # and apply hiding to the configured column indices.

    in_thead = False
    in_tbody = False
    in_tr = False
    col_index = 0
    new_lines = []

    for line in lines:
        stripped = line.strip()

        # Track context
        if "<thead" in stripped:
            in_thead = True
        if "</thead" in stripped:
            in_thead = False
        if "<tbody" in stripped:
            in_tbody = True
        if "</tbody" in stripped:
            in_tbody = False

        if "<tr" in stripped and (in_thead or in_tbody):
            in_tr = True
            col_index = 0
        if "</tr" in stripped:
            in_tr = False

        # Process <th> and <td> tags
        if in_tr and (in_thead or in_tbody):
            # Check for <th or <td opening tags on this line
            tag_pattern = re.compile(r"<(th|td)\b[^>]*>")
            match = tag_pattern.search(line)
            if match:
                if col_index in cols_to_hide:
                    tag_str = match.group(0)
                    if not already_has_hiding(tag_str):
                        new_tag = add_hidden_to_tag(tag_str)
                        line = line[: match.start()] + new_tag + line[match.end() :]
                        fixes += 1
                col_index += 1

        new_lines.append(line)

    if fixes > 0 and apply:
        filepath.write_text("\n".join(new_lines))

    return fixes


def main() -> None:
    apply = "--apply" in sys.argv
    mode = "APPLYING" if apply else "DRY RUN"
    print(f"=== Table Column Hiding ({mode}) ===\n")

    total_fixes = 0
    files_changed = 0
    root = Path(".")

    for rel_path, cols in sorted(COLUMN_HIDING_CONFIG.items()):
        filepath = root / rel_path
        if not filepath.exists():
            print(f"  SKIP {rel_path} (not found)")
            continue

        fixes = process_file(filepath, cols, apply)
        if fixes > 0:
            print(f"  {fixes:3d} fixes  {rel_path}  (cols {cols})")
            total_fixes += fixes
            files_changed += 1
        else:
            print(f"    0 fixes  {rel_path}")

    print(f"\nTotal: {total_fixes} fixes across {files_changed} files")
    if not apply and total_fixes > 0:
        print("Run with --apply to apply changes.")


if __name__ == "__main__":
    main()
