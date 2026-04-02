"""Compliance guard for PMS 2022 summary-to-feature traceability.

This test suite validates that each summary clause has mapped feature files and
mapped executable test evidence.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
MATRIX_PATH = ROOT / "docs/compliance/pms_2022_traceability_matrix.json"


def _load_matrix() -> dict:
    return json.loads(MATRIX_PATH.read_text(encoding="utf-8"))


def test_traceability_matrix_file_exists() -> None:
    assert MATRIX_PATH.exists(), "Traceability matrix JSON is missing"


def test_traceability_matrix_has_all_16_clauses() -> None:
    matrix = _load_matrix()
    clauses = matrix.get("clauses", [])
    assert len(clauses) == 16

    ids = [c["id"] for c in clauses]
    assert len(ids) == len(set(ids)), "Clause IDs must be unique"

    expected_ids = {f"C{i:02d}" for i in range(1, 17)}
    assert set(ids) == expected_ids


@pytest.mark.parametrize(
    "clause", _load_matrix().get("clauses", []), ids=lambda c: c["id"]
)
def test_each_clause_has_feature_and_test_refs(clause: dict) -> None:
    assert clause.get("feature_refs"), f"{clause['id']} missing feature refs"
    assert clause.get("test_refs"), f"{clause['id']} missing test refs"


@pytest.mark.parametrize(
    "clause", _load_matrix().get("clauses", []), ids=lambda c: c["id"]
)
def test_feature_refs_exist(clause: dict) -> None:
    for rel_path in clause["feature_refs"]:
        abs_path = ROOT / rel_path
        assert abs_path.exists(), (
            f"{clause['id']} references missing feature path: {rel_path}"
        )


@pytest.mark.parametrize(
    "clause", _load_matrix().get("clauses", []), ids=lambda c: c["id"]
)
def test_test_refs_exist_and_case_names_are_present(clause: dict) -> None:
    for test_ref in clause["test_refs"]:
        rel_path = test_ref["path"]
        abs_path = ROOT / rel_path
        assert abs_path.exists(), (
            f"{clause['id']} references missing test file: {rel_path}"
        )

        contents = abs_path.read_text(encoding="utf-8")
        for case_name in test_ref.get("cases", []):
            assert f"def {case_name}" in contents, (
                f"{clause['id']} missing mapped test case {case_name} in {rel_path}"
            )


def test_key_insights_have_evidence_tests() -> None:
    matrix = _load_matrix()
    insights = matrix.get("key_insights", [])
    assert len(insights) == 8

    for insight in insights:
        evidence = insight.get("evidence_tests", [])
        assert evidence, f"{insight['id']} missing evidence_tests"
        for rel_path in evidence:
            assert (ROOT / rel_path).exists(), (
                f"{insight['id']} references missing test file: {rel_path}"
            )
