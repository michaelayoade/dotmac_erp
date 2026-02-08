#!/usr/bin/env python3
"""
Replace inline module switcher and sidebar footer with shared partial includes.

Targets: inventory, people, procurement, expense, modules (operations), admin.
Finance already uses the partials.
"""

from pathlib import Path

# Configuration for each sidebar template
SIDEBAR_CONFIGS = {
    "templates/inventory/base_inventory.html": {
        "current_module": "inventory",
        "module_accent": "amber",
    },
    "templates/procurement/base_procurement.html": {
        "current_module": "procurement",
        "module_accent": "blue",
    },
    "templates/modules/base_modules.html": {
        "current_module": "operations",
        "module_accent": "indigo",
    },
    "templates/expense/base_expense.html": {
        "current_module": "expense",
        "module_accent": "amber",
    },
    "templates/admin/base_admin.html": {
        "current_module": "admin",
        "module_accent": "slate",
        "dark_sidebar": True,
    },
}

# People is special — has TWO switch module sections (HR vs self-service)
# We'll handle it separately


def find_switch_module_block(lines: list[str]) -> tuple[int, int] | None:
    """Find the start and end of the inline Switch Module block.

    Returns (start_line_idx, end_line_idx) where end is the last line of the block.
    The block starts at the "Switch Module" divider comment/section
    and ends at the closing </nav>.
    """
    start = None
    for i, line in enumerate(lines):
        # Look for the Switch Module section start
        if "Switch Module" in line and start is None:
            # Go back to find the comment or div that starts this section
            j = i
            while j > 0 and lines[j - 1].strip().startswith("<!--"):
                j -= 1
            start = j
            break

    if start is None:
        return None

    # Find the </nav> that closes the navigation section
    end = None
    for i in range(start, len(lines)):
        stripped = lines[i].strip()
        if stripped == "</nav>":
            end = i  # The line BEFORE </nav> is the last line of the block
            break

    if end is None:
        return None

    return (start, end)


def find_footer_block(lines: list[str]) -> tuple[int, int] | None:
    """Find the inline footer block.

    Returns (start_line_idx, end_line_idx).
    The footer starts after </nav> (usually a div with border-t)
    and ends just before </aside>.
    """
    nav_end = None
    for i, line in enumerate(lines):
        if line.strip() == "</nav>":
            nav_end = i
            break

    if nav_end is None:
        return None

    # Footer starts right after </nav> — skip blank lines
    start = nav_end + 1
    while start < len(lines) and lines[start].strip() == "":
        start += 1

    # Look for the comment that marks the footer, or the footer div itself
    # Check if there's a comment before the footer div
    if "Footer" in lines[start] or "User Menu" in lines[start]:
        pass  # Comment line is part of the block
    elif "border-t" in lines[start]:
        pass  # The footer div itself
    else:
        # Check next line
        if start + 1 < len(lines) and (
            "border-t" in lines[start + 1] or "Footer" in lines[start + 1]
        ):
            pass

    # Find </aside>
    end = None
    for i in range(start, len(lines)):
        if lines[i].strip() == "</aside>":
            end = i
            break

    if end is None:
        return None

    return (start, end)


def process_file(filepath: Path, config: dict) -> int:
    """Replace inline module switcher and footer with partial includes."""
    content = filepath.read_text()
    lines = content.split("\n")

    # Find the switch module block
    switch_block = find_switch_module_block(lines)
    if switch_block is None:
        print(f"  WARNING: Could not find Switch Module block in {filepath}")
        return 0

    # Find the footer block
    footer_block = find_footer_block(lines)
    if footer_block is None:
        print(f"  WARNING: Could not find footer block in {filepath}")
        return 0

    switch_start, switch_end = switch_block
    footer_start, footer_end = footer_block

    print(f"  Switch Module: lines {switch_start + 1}-{switch_end + 1}")
    print(f"  Footer: lines {footer_start + 1}-{footer_end + 1}")

    # Build replacement for switch module
    current_module = config["current_module"]
    module_accent = config["module_accent"]
    is_dark = config.get("dark_sidebar", False)

    # Determine indentation from the original lines
    indent = "            "  # 12 spaces (3 levels)

    switcher_replacement = []
    switcher_replacement.append("")
    if is_dark:
        switcher_replacement.append(
            f'{indent}{{% with current_module="{current_module}", dark_sidebar=true %}}'
        )
    else:
        switcher_replacement.append(
            f'{indent}{{% with current_module="{current_module}" %}}'
        )
    switcher_replacement.append(
        f'{indent}    {{% include "partials/_module_switcher.html" %}}'
    )
    switcher_replacement.append(f"{indent}{{% endwith %}}")

    # Build replacement for footer
    footer_replacement = []
    footer_replacement.append("")
    if is_dark:
        footer_replacement.append(
            f'        {{% with module_accent="{module_accent}", dark_sidebar=true %}}'
        )
    else:
        footer_replacement.append(
            f'        {{% with module_accent="{module_accent}" %}}'
        )
    footer_replacement.append(
        '            {% include "partials/_sidebar_footer.html" %}'
    )
    footer_replacement.append("        {% endwith %}")

    # Apply replacements (footer first since it's later in the file)
    new_lines = (
        lines[:switch_start]
        + switcher_replacement
        + lines[switch_end:footer_start]  # Keep </nav> and blank lines
        + footer_replacement
        + lines[footer_end:]  # Keep </aside> and everything after
    )

    new_content = "\n".join(new_lines)
    filepath.write_text(new_content)

    removed_lines = (switch_end - switch_start) + (footer_end - footer_start)
    added_lines = len(switcher_replacement) + len(footer_replacement)
    print(f"  Removed ~{removed_lines} inline lines, added {added_lines} include lines")
    return removed_lines - added_lines


def main() -> None:
    root = Path("/root/dotmac")
    total_saved = 0

    for rel_path, config in SIDEBAR_CONFIGS.items():
        filepath = root / rel_path
        if not filepath.exists():
            print(f"SKIP: {rel_path} not found")
            continue

        # Check if already using partial
        content = filepath.read_text()
        if "_module_switcher.html" in content:
            print(f"SKIP: {rel_path} already uses _module_switcher.html")
            continue

        print(f"\nProcessing: {rel_path}")
        saved = process_file(filepath, config)
        total_saved += saved

    print(f"\n{'=' * 50}")
    print(f"Total lines saved: {total_saved}")


if __name__ == "__main__":
    main()
