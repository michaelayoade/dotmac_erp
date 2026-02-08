#!/usr/bin/env python3
"""
Form Design System Audit Script

Checks all templates for form design system compliance.
"""

from collections import defaultdict
from pathlib import Path

violations = defaultdict(list)


def check_file(filepath):
    """Check a single template file for violations."""
    content = filepath.read_text()
    lines = content.split("\n")

    # Check if file has POST forms
    has_post_form = 'method="post"' in content.lower()
    if not has_post_form:
        return

    # 1. Check for CSRF token
    has_csrf = "request.state.csrf_form" in content
    if not has_csrf:
        violations["missing_csrf"].append(
            {
                "file": str(filepath.relative_to("/root/dotmac")),
                "line": None,
                "issue": "POST form without CSRF token",
            }
        )

    # 2. Check for alert() usage (should use toast)
    for i, line in enumerate(lines, 1):
        if "alert(" in line and not line.strip().startswith("//"):
            violations["uses_alert"].append(
                {
                    "file": str(filepath.relative_to("/root/dotmac")),
                    "line": i,
                    "issue": f"Uses alert(): {line.strip()[:80]}",
                }
            )

    # 3. Check for required fields without asterisk
    for i, line in enumerate(lines, 1):
        if "required" in line and "<label" in line:
            # Look for asterisk in same line or previous line
            prev_line = lines[i - 2] if i > 1 else ""
            if (
                'text-rose-500">*</span>' not in line
                and 'text-rose-500">*</span>' not in prev_line
            ):
                violations["required_no_asterisk"].append(
                    {
                        "file": str(filepath.relative_to("/root/dotmac")),
                        "line": i,
                        "issue": f"Required field without asterisk: {line.strip()[:80]}",
                    }
                )

    # 4. Check for currency/financial inputs without font-mono
    for i, line in enumerate(lines, 1):
        if (
            'name="amount"' in line
            or 'name="price"' in line
            or 'name="cost"' in line
            or 'name="rate"' in line
            or 'name="total"' in line
            or 'name="balance"' in line
        ):
            if "font-mono" not in line and "form-input-currency" not in line:
                # Check next few lines for font-mono class
                has_mono = any(
                    "font-mono" in lines[j] for j in range(i, min(i + 3, len(lines)))
                )
                if not has_mono:
                    violations["financial_no_mono"].append(
                        {
                            "file": str(filepath.relative_to("/root/dotmac")),
                            "line": i,
                            "issue": f"Financial input without font-mono: {line.strip()[:80]}",
                        }
                    )

    # 5. Check for improper form action layout
    for i, line in enumerate(lines, 1):
        if "<button" in line and 'type="submit"' in line:
            # Look for proper flex layout in surrounding context
            context = "\n".join(lines[max(0, i - 5) : min(len(lines), i + 5)])
            if "Cancel" in context or "cancel" in context:
                if "justify-end" not in context and "ml-auto" not in context:
                    violations["improper_button_layout"].append(
                        {
                            "file": str(filepath.relative_to("/root/dotmac")),
                            "line": i,
                            "issue": "Form buttons without justify-end or ml-auto layout",
                        }
                    )

    # 6. Check form spacing
    for i, line in enumerate(lines, 1):
        if "<form" in line.lower() and 'method="post"' in line.lower():
            if "space-y-" not in line:
                # Check next few lines
                has_spacing = any(
                    "space-y-" in lines[j] for j in range(i, min(i + 3, len(lines)))
                )
                if not has_spacing:
                    violations["form_no_spacing"].append(
                        {
                            "file": str(filepath.relative_to("/root/dotmac")),
                            "line": i,
                            "issue": "Form without space-y-* class",
                        }
                    )


def main():
    templates_dir = Path("/root/dotmac/templates")

    # Find all HTML files
    html_files = list(templates_dir.rglob("*.html"))

    print(f"Checking {len(html_files)} template files...")

    for filepath in html_files:
        try:
            check_file(filepath)
        except Exception as e:
            print(f"Error checking {filepath}: {e}")

    # Print results
    print("\n" + "=" * 80)
    print("FORM DESIGN SYSTEM AUDIT RESULTS")
    print("=" * 80 + "\n")

    total_violations = sum(len(v) for v in violations.values())

    if total_violations == 0:
        print("✓ No violations found!")
        return

    print(f"Found {total_violations} total violations\n")

    # Sort by severity
    severity_order = [
        "missing_csrf",
        "uses_alert",
        "financial_no_mono",
        "required_no_asterisk",
        "improper_button_layout",
        "form_no_spacing",
    ]

    for violation_type in severity_order:
        if violation_type not in violations:
            continue

        items = violations[violation_type]
        count = len(items)

        type_names = {
            "missing_csrf": "Missing CSRF Token (P0)",
            "uses_alert": "Uses alert() instead of toast (P1)",
            "financial_no_mono": "Financial input without font-mono (P1)",
            "required_no_asterisk": "Required field without asterisk (P2)",
            "improper_button_layout": "Improper form button layout (P2)",
            "form_no_spacing": "Form without space-y spacing (P2)",
        }

        print(f"\n{type_names[violation_type]}: {count} violations")
        print("-" * 80)

        # Group by file
        by_file = defaultdict(list)
        for item in items:
            by_file[item["file"]].append(item)

        for file in sorted(by_file.keys()):
            file_items = by_file[file]
            print(f"\n  {file}:")
            for item in file_items[:5]:  # Limit to 5 per file
                if item["line"]:
                    print(f"    Line {item['line']}: {item['issue']}")
                else:
                    print(f"    {item['issue']}")
            if len(file_items) > 5:
                print(f"    ... and {len(file_items) - 5} more")

    print("\n" + "=" * 80)
    print(
        f"SUMMARY: {total_violations} total violations across {len(violations)} categories"
    )
    print("=" * 80)


if __name__ == "__main__":
    main()
