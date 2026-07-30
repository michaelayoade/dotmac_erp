"""The version-impact gate must actually gate.

A required check that silently passes is worse than no check: it reads as
enforcement while enforcing nothing. These tests pin the classification rules
so a future edit to RELEASE_PATTERNS cannot quietly stop requiring labels.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/ci/check_version_impact.py"
WORKFLOW = ROOT / ".github/workflows/version-impact.yml"


def _module():
    spec = importlib.util.spec_from_file_location("check_version_impact", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_deployable_paths_are_release_relevant() -> None:
    check = _module()
    deployable = [
        "app/services/inventory/notifications.py",
        "app/tasks/inventory.py",
        "src/main.ts",
        "templates/base.html",
        "static/css/app.css",
        "locales/en.json",
        "alembic/versions/001_initial.py",
        "deploy/docker-compose.prod.yml",
        "Dockerfile",
        "Dockerfile.hardened",
        "docker-compose.yml",
        "gunicorn.conf.py",
        "pyproject.toml",
        "poetry.lock",
        "uv.lock",
        "package.json",
        "package-lock.json",
    ]
    assert check._release_relevant(deployable) == deployable


def test_non_deployable_paths_never_require_a_label() -> None:
    check = _module()
    ignored = [
        ".github/workflows/ci.yml",
        "docs/ARCHITECTURE.md",
        "proposals/idea.md",
        "reports/audit.md",
        "tests/architecture/test_version_impact_gate.py",
        "scripts/ci/check_version_impact.py",
        "README.md",
        "app/services/README.md",
    ]
    assert check._release_relevant(ignored) == []


def test_scripts_are_release_relevant_but_ci_scripts_are_not() -> None:
    check = _module()
    # scripts/** ships with the image; scripts/ci/** only runs in Actions.
    assert check._release_relevant(["scripts/bump_version.py"]) == [
        "scripts/bump_version.py"
    ]
    assert check._release_relevant(["scripts/ci/check_version_impact.py"]) == []


def test_version_labels_are_extracted_and_others_ignored() -> None:
    check = _module()
    pr = {
        "labels": [
            {"name": "bug"},
            {"name": "version:minor"},
            {"name": "needs-review"},
        ]
    }
    assert check._version_labels(pr) == ["version:minor"]
    assert check._version_labels({"labels": []}) == []
    assert check._version_labels({}) == []


def test_none_justification_requires_a_reason() -> None:
    check = _module()
    assert check._has_none_justification(
        {"body": "Version impact: none because this only edits CI docs."}
    )
    assert not check._has_none_justification({"body": "Version impact: none"})
    assert not check._has_none_justification({"body": ""})
    assert not check._has_none_justification({})


def _triggers(workflow: dict) -> dict:
    # YAML 1.1 parses a bare `on:` key as the boolean True, not the string
    # "on". Accept either so the test does not depend on the loader's flavour.
    return workflow.get("on", workflow.get(True, {}))


def test_workflow_reports_the_required_context_name() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text())
    # Branch protection requires this exact context string. A required check
    # that never reports deadlocks every PR, so the name must not drift.
    assert workflow["jobs"]["check"]["name"] == "Check version impact label"
    # It must run on label changes, or adding the label would never re-report.
    triggers = _triggers(workflow)["pull_request"]["types"]
    assert "labeled" in triggers and "unlabeled" in triggers


def test_workflow_checks_out_full_history() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text())
    checkout = workflow["jobs"]["check"]["steps"][0]
    # fetch-depth: 0 — the three-dot diff needs a reachable merge-base.
    assert checkout["with"]["fetch-depth"] == 0
