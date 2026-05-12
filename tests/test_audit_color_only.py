"""Unit tests for the color-only status dot scanner.

Guards against false positives (large decorative circles, paired-with-text
dots that already have aria-hidden) and false negatives (subtle status dot
shapes we want to keep catching).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from textwrap import dedent

_SCRIPT_PATH = Path(__file__).parent.parent / "scripts" / "audit_color_only.py"
spec = importlib.util.spec_from_file_location("audit_color_only", _SCRIPT_PATH)
audit = importlib.util.module_from_spec(spec)
sys.modules["audit_color_only"] = audit
spec.loader.exec_module(audit)


def _scan(html: str, tmp_path: Path) -> list[dict]:
    p = tmp_path / "sample.html"
    p.write_text(html, encoding="utf-8")
    return audit.scan_file(p)


# --- looks_like_status_dot --------------------------------------------------


def test_small_rounded_emerald_dot_is_status_dot() -> None:
    assert audit.looks_like_status_dot('class="h-2 w-2 rounded-full bg-emerald-500"')


def test_small_rounded_with_size_first_is_status_dot() -> None:
    assert audit.looks_like_status_dot('class="w-3 h-3 rounded-full bg-rose-500"')


def test_large_circle_is_not_status_dot() -> None:
    assert not audit.looks_like_status_dot(
        'class="h-72 w-72 rounded-full bg-cyan-500/10 blur-3xl"'
    )


def test_avatar_circle_is_not_status_dot() -> None:
    assert not audit.looks_like_status_dot(
        'class="flex h-10 w-10 items-center justify-center rounded-full bg-green-100"'
    )


def test_pill_shape_without_size_is_not_status_dot() -> None:
    assert not audit.looks_like_status_dot(
        'class="inline-flex items-center gap-2 rounded-full bg-slate-100 px-3 py-1"'
    )


def test_slate_grey_is_not_flagged_as_status_color() -> None:
    # slate is intentionally excluded from STATUS_COLORS (used for neutral chrome).
    assert not audit.looks_like_status_dot('class="h-2 w-2 rounded-full bg-slate-500"')


def test_pastel_shade_below_300_is_not_status_dot() -> None:
    # bg-emerald-100 is a soft pill background, not a saturated status dot.
    assert not audit.looks_like_status_dot(
        'class="h-2 w-2 rounded-full bg-emerald-100"'
    )


# --- has_accessible_name ---------------------------------------------------


def test_aria_label_exempts() -> None:
    assert audit.has_accessible_name('aria-label="Urgent priority"')


def test_aria_hidden_exempts() -> None:
    assert audit.has_accessible_name('aria-hidden="true"')


def test_title_attr_exempts() -> None:
    assert audit.has_accessible_name('title="Urgent"')


def test_role_img_exempts() -> None:
    assert audit.has_accessible_name('role="img"')


def test_plain_class_does_not_exempt() -> None:
    assert not audit.has_accessible_name('class="something"')


# --- scan_file end-to-end --------------------------------------------------


def test_empty_status_dot_is_flagged(tmp_path: Path) -> None:
    html = '<span class="h-2 w-2 rounded-full bg-emerald-500"></span>'
    violations = _scan(html, tmp_path)
    assert len(violations) == 1
    assert violations[0]["line"] == 1


def test_status_dot_with_aria_hidden_is_not_flagged(tmp_path: Path) -> None:
    html = (
        '<span class="h-2 w-2 rounded-full bg-emerald-500" aria-hidden="true"></span>'
    )
    assert _scan(html, tmp_path) == []


def test_status_dot_with_aria_label_is_not_flagged(tmp_path: Path) -> None:
    html = '<span class="h-2 w-2 rounded-full bg-rose-500" aria-label="Urgent"></span>'
    assert _scan(html, tmp_path) == []


def test_status_dot_with_inner_text_is_not_flagged(tmp_path: Path) -> None:
    # An "indicator" with text inside is no longer color-only.
    html = '<span class="h-2 w-2 rounded-full bg-emerald-500">!</span>'
    assert _scan(html, tmp_path) == []


def test_decorative_large_circle_is_not_flagged(tmp_path: Path) -> None:
    html = '<div class="absolute h-72 w-72 rounded-full bg-cyan-500/10 blur-3xl"></div>'
    assert _scan(html, tmp_path) == []


def test_avatar_initial_circle_is_not_flagged(tmp_path: Path) -> None:
    html = (
        '<div class="flex h-10 w-10 items-center justify-center '
        'rounded-full bg-blue-100"><span>AB</span></div>'
    )
    assert _scan(html, tmp_path) == []


def test_chart_legend_pattern_is_flagged_when_dot_lacks_aria(
    tmp_path: Path,
) -> None:
    html = dedent("""
        <span class="flex items-center gap-1.5">
          <span class="w-2 h-2 rounded-full bg-emerald-500"></span>
          <span>Active</span>
        </span>
    """).strip()
    violations = _scan(html, tmp_path)
    assert len(violations) == 1


def test_multiple_dots_each_get_own_violation(tmp_path: Path) -> None:
    html = dedent("""
        <span class="h-2 w-2 rounded-full bg-emerald-500"></span>
        <span class="h-2 w-2 rounded-full bg-rose-500"></span>
    """).strip()
    violations = _scan(html, tmp_path)
    assert len(violations) == 2
