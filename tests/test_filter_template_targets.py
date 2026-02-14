from __future__ import annotations

import re
from pathlib import Path

TEMPLATES_DIR = Path("templates")
COMPACT_FILTERS_TOKEN = "compact_filters("
TARGET_ID_RE = re.compile(r'target_id\s*=\s*["\']([^"\']+)["\']')


def _iter_compact_filters_calls(source: str) -> list[tuple[str, int, int]]:
    calls: list[tuple[str, int, int]] = []
    cursor = 0
    while True:
        start = source.find(COMPACT_FILTERS_TOKEN, cursor)
        if start == -1:
            return calls

        depth = 1
        i = start + len(COMPACT_FILTERS_TOKEN)
        while i < len(source) and depth > 0:
            char = source[i]
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            i += 1

        calls.append((source[start:i], start, i))
        cursor = i


def _expected_target_ids(source: str) -> list[tuple[str, int]]:
    target_ids: list[tuple[str, int]] = []
    for call, _start, end in _iter_compact_filters_calls(source):
        target_match = TARGET_ID_RE.search(call)
        if target_match:
            target_ids.append((target_match.group(1).lstrip("#"), end))
        else:
            target_ids.append(("results-container", end))
    return target_ids


def test_templates_using_compact_filters_have_expected_htmx_target_regions():
    missing_targets: list[tuple[str, str]] = []

    for template in TEMPLATES_DIR.rglob("*.html"):
        if template.as_posix().startswith("templates/components/"):
            continue
        source = template.read_text(encoding="utf-8")
        if COMPACT_FILTERS_TOKEN not in source:
            continue

        expected_ids = _expected_target_ids(source)
        for target_id, call_end in expected_ids:
            target_match = re.search(rf'id=["\']{re.escape(target_id)}["\']', source)
            if not target_match:
                missing_targets.append((str(template), target_id))
                continue
            if target_match.start() < call_end:
                missing_targets.append(
                    (str(template), f"{target_id} (placed before filter call)")
                )

    assert not missing_targets, (
        "Templates with compact_filters are missing expected HTMX target regions: "
        + ", ".join(f"{path} -> #{target}" for path, target in missing_targets)
    )
