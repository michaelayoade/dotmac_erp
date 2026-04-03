from __future__ import annotations

from pathlib import Path


def _read(rel_path: str) -> str:
    root = Path(__file__).resolve().parents[1]
    return (root / rel_path).read_text(encoding="utf-8")


def test_sidebar_template_has_mode_gated_performance_entries() -> None:
    src = _read("templates/people/base_people.html")
    assert "{% if performance_private_enabled %}" in src
    assert "{% if performance_government_enabled %}" in src
    assert 'href="/people/perf"' in src
    assert 'href="/people/perf/pms/dashboard"' in src
    assert "{{ performance_nav_label }}" in src
    assert "{{ pms_nav_label }}" in src


def test_landing_templates_use_mode_specific_labels() -> None:
    private_src = _read("templates/people/perf/index.html")
    pms_src = _read("templates/people/perf/pms/dashboard.html")

    assert "{{ performance_nav_label }}" in private_src
    assert "{{ pms_nav_label }} Dashboard" in pms_src
