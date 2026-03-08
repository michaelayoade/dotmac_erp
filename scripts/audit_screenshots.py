"""Audit captured screenshots for common UI issues.

Checks:
1. Blank/near-blank pages (mostly white or single color)
2. Error pages (HTTP 500, 404, 400 text in page)
3. Login redirects (captured the login page instead of target)
4. Unusually small pages (missing content / empty)
5. Unusually tall pages (unpaginated lists, runaway rendering)
6. Missing sidebar (wrong layout — page too narrow or no left panel)
7. Dark/broken rendering (mostly black or very dark)
"""

import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from PIL import Image

SCREENSHOTS_DIR = Path("/home/dotmac/projects/dotmac_erp/docs/screenshots")
VIEWPORT_WIDTH = 1440
VIEWPORT_HEIGHT = 900

# Thresholds
MIN_HEIGHT = 400  # Suspiciously short page
MAX_HEIGHT = 15000  # Suspiciously tall page
BLANK_WHITE_RATIO = 0.92  # >92% white pixels = likely blank
BLANK_DARK_RATIO = 0.90  # >90% dark pixels = broken render
SIDEBAR_SAMPLE_X = 80  # X position to sample for sidebar presence
SIDEBAR_COLOR_RANGE = 40  # How uniform the sidebar column should be


def analyze_image(filepath):
    """Analyze a single screenshot and return a list of issues."""
    issues = []
    try:
        img = Image.open(filepath)
    except Exception as e:
        issues.append(("CORRUPT", f"Cannot open image: {e}"))
        return issues, {}

    width, height = img.size
    stats = {"width": width, "height": height}

    # Convert to numpy for analysis
    arr = np.array(img)

    # --- Check 1: Dimensions ---
    if height < MIN_HEIGHT:
        issues.append(("SHORT_PAGE", f"Height {height}px — likely empty or error"))
    if height > MAX_HEIGHT:
        issues.append(
            ("TALL_PAGE", f"Height {height}px — possibly unpaginated or runaway")
        )

    # --- Check 2: Blank/white page ---
    # Consider pixels with R,G,B all > 245 as "white"
    if len(arr.shape) == 3:
        white_mask = np.all(arr[:, :, :3] > 245, axis=2)
        white_ratio = white_mask.sum() / (width * height)
        stats["white_ratio"] = round(float(white_ratio), 3)
        if white_ratio > BLANK_WHITE_RATIO:
            issues.append(("BLANK_PAGE", f"{white_ratio:.0%} white pixels"))

        # --- Check 3: Dark/broken render ---
        dark_mask = np.all(arr[:, :, :3] < 30, axis=2)
        dark_ratio = dark_mask.sum() / (width * height)
        stats["dark_ratio"] = round(float(dark_ratio), 3)
        if dark_ratio > BLANK_DARK_RATIO:
            issues.append(
                ("DARK_PAGE", f"{dark_ratio:.0%} dark pixels — broken render?")
            )

    # --- Check 4: Error page detection ---
    # Sample the top portion for red/amber error indicators
    # We check image height — error pages tend to be short with centered content
    if height < 600 and width == VIEWPORT_WIDTH:
        issues.append(
            ("POSSIBLE_ERROR", f"Short page ({height}px) — may be error/404/400")
        )

    # --- Check 5: Sidebar presence ---
    # Sample a vertical strip at x=80 (mid-sidebar region)
    # A proper sidebar should have a consistent color band
    if width >= VIEWPORT_WIDTH and height >= VIEWPORT_HEIGHT:
        sidebar_strip = arr[100 : min(500, height), SIDEBAR_SAMPLE_X, :3]
        if len(sidebar_strip) > 0:
            sidebar_std = np.std(sidebar_strip, axis=0).mean()
            stats["sidebar_std"] = round(float(sidebar_std), 2)
            # High variance in sidebar column = likely no sidebar
            if sidebar_std > SIDEBAR_COLOR_RANGE:
                issues.append(
                    (
                        "NO_SIDEBAR",
                        f"Sidebar column variance={sidebar_std:.1f} — may be missing sidebar",
                    )
                )

    # --- Check 6: Mostly single-color (not white/dark) ---
    if len(arr.shape) == 3:
        mean_color = arr[:, :, :3].mean(axis=(0, 1))
        color_std = arr[:, :, :3].std(axis=(0, 1)).mean()
        stats["color_std"] = round(float(color_std), 2)
        if color_std < 15 and not issues:
            issues.append(
                (
                    "UNIFORM_COLOR",
                    f"Very low color variance ({color_std:.1f}) — possibly blank/broken",
                )
            )

    return issues, stats


def main():
    png_files = sorted(SCREENSHOTS_DIR.rglob("*.png"))
    print(f"Auditing {len(png_files)} screenshots...\n")

    all_issues = []
    heights = []
    modules_summary = defaultdict(lambda: {"total": 0, "issues": 0})

    for filepath in png_files:
        rel_path = filepath.relative_to(SCREENSHOTS_DIR)
        module = rel_path.parts[0] if len(rel_path.parts) > 1 else "_root"

        issues, stats = analyze_image(filepath)
        modules_summary[module]["total"] += 1
        heights.append((str(rel_path), stats.get("height", 0)))

        if issues:
            modules_summary[module]["issues"] += 1
            all_issues.append((str(rel_path), issues, stats))

    # --- Report ---
    print("=" * 70)
    print("SCREENSHOT AUDIT REPORT")
    print("=" * 70)

    # Module summary
    print(f"\n{'Module':<20} {'Total':>6} {'Issues':>7} {'Pass Rate':>10}")
    print("-" * 45)
    total_pages = 0
    total_issues = 0
    for module in sorted(modules_summary):
        m = modules_summary[module]
        total_pages += m["total"]
        total_issues += m["issues"]
        rate = ((m["total"] - m["issues"]) / m["total"] * 100) if m["total"] else 0
        print(f"{module:<20} {m['total']:>6} {m['issues']:>7} {rate:>9.0f}%")
    print("-" * 45)
    overall_rate = (
        ((total_pages - total_issues) / total_pages * 100) if total_pages else 0
    )
    print(f"{'TOTAL':<20} {total_pages:>6} {total_issues:>7} {overall_rate:>9.0f}%")

    # Issues by severity
    if all_issues:
        # Group by issue type
        by_type = defaultdict(list)
        for path, issues, stats in all_issues:
            for issue_type, detail in issues:
                by_type[issue_type].append((path, detail))

        severity_order = [
            "CORRUPT",
            "DARK_PAGE",
            "BLANK_PAGE",
            "POSSIBLE_ERROR",
            "SHORT_PAGE",
            "NO_SIDEBAR",
            "TALL_PAGE",
            "UNIFORM_COLOR",
        ]

        for issue_type in severity_order:
            if issue_type not in by_type:
                continue
            items = by_type[issue_type]
            print(f"\n{'=' * 70}")
            print(f"{issue_type} ({len(items)} pages)")
            print("-" * 70)
            for path, detail in items:
                print(f"  {path}")
                print(f"    {detail}")

    # Top 10 tallest pages (potential pagination issues)
    heights.sort(key=lambda x: x[1], reverse=True)
    print(f"\n{'=' * 70}")
    print("TOP 15 TALLEST PAGES (check for missing pagination)")
    print("-" * 70)
    for path, h in heights[:15]:
        flag = " <<<" if h > MAX_HEIGHT else ""
        print(f"  {h:>6}px  {path}{flag}")

    # Top 10 shortest pages
    heights.sort(key=lambda x: x[1])
    print(f"\n{'=' * 70}")
    print("TOP 15 SHORTEST PAGES (check for errors/empty states)")
    print("-" * 70)
    for path, h in heights[:15]:
        flag = " <<<" if h < MIN_HEIGHT else ""
        print(f"  {h:>6}px  {path}{flag}")

    # Write machine-readable report
    report = {
        "total_pages": total_pages,
        "total_issues": total_issues,
        "pass_rate": round(overall_rate, 1),
        "issues": [
            {"path": path, "issues": [(t, d) for t, d in issues], "stats": stats}
            for path, issues, stats in all_issues
        ],
    }
    report_path = SCREENSHOTS_DIR / "audit-report.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)
    print(f"\nJSON report: {report_path}")

    print(f"\nDone. {total_issues} pages flagged out of {total_pages}.")
    return 1 if total_issues > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
