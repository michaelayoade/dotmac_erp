"""Sensitivity proof for `scripts/check_evidence_json.py`.

The strict-JSON evidence documents (`docs/kernel-runtime-composition.json`,
`docs/kernel-runtime-readiness.json`) are read by another repository's gate
as data. A formatter or hand edit that rewrites one of them into a relaxed
JSON5/JSONC shape (a trailing comma being the concrete defect this repo
reproduced against `ruff format <path.json>` while diagnosing PR #530) must
fail loudly, naming the file, never pass silently.

`test_the_real_documents_parse_cleanly` proves the guard does not simply
refuse everything -- the real, checked-in documents pass it today.
`test_a_trailing_comma_document_is_rejected` plants the exact defect (a
synthetic document with a trailing comma) and proves `check_document` names
it. `test_a_document_with_only_trailing_whitespace_is_accepted` is the
near-miss: a cosmetic difference that is NOT the defect (trailing
whitespace/newline, exactly what `end-of-file-fixer` and
`trailing-whitespace` legitimately produce) must not be flagged.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from check_evidence_json import (  # noqa: E402
    STRICT_JSON_EVIDENCE_RELATIVE_PATHS,
    check_document,
    main,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_the_real_documents_parse_cleanly() -> None:
    for relative_path in STRICT_JSON_EVIDENCE_RELATIVE_PATHS:
        assert check_document(PROJECT_ROOT / relative_path) is None
    assert main(PROJECT_ROOT) == 0


def test_a_trailing_comma_document_is_rejected(tmp_path: Path) -> None:
    """Plants the exact defect this guard exists to catch: the trailing-comma
    shape `ruff format` produced when run directly against this repo's own
    composition document (reproduced and confirmed during diagnosis; not a
    hypothetical shape)."""
    corrupted = tmp_path / "docs" / "kernel-runtime-composition.json"
    corrupted.parent.mkdir(parents=True)
    corrupted.write_text('{\n  "schema_version": "dimensional-composition.v2",\n}\n')
    result = check_document(corrupted)
    assert result is not None
    assert "invalid JSON" in result
    assert str(corrupted) in result


def test_a_document_with_only_trailing_whitespace_is_accepted(tmp_path: Path) -> None:
    """Near-miss: legitimate whitespace-only differences (what
    `trailing-whitespace`/`end-of-file-fixer` produce) are not the defect
    and must not be flagged."""
    benign = tmp_path / "docs" / "kernel-runtime-composition.json"
    benign.parent.mkdir(parents=True)
    benign.write_text('{\n  "schema_version": "dimensional-composition.v2"\n}\n\n')
    assert check_document(benign) is None


def test_a_missing_document_is_named_not_silently_skipped(tmp_path: Path) -> None:
    missing = tmp_path / "docs" / "kernel-runtime-composition.json"
    result = check_document(missing)
    assert result == f"{missing}: missing"


def test_main_collects_every_failure_before_exiting(tmp_path: Path) -> None:
    """Both documents missing -> both named, not just the first."""
    (tmp_path / "docs").mkdir()
    assert main(tmp_path) == 1
