"""ERP's staged adoption of the Governance external-connector ratchet.

The detector is NOT here. Governance ADR 0010 owns the six-category classifier
and the two-directional baseline, and ERP consumes it through the pinned
`standards-check` path. What these tests hold is the part that is genuinely
ERP's:

* the adoption stays BLOCKED while the Governance pin is a placeholder, and the
  block cannot be half-lifted;
* the staged schema-6 baselines are the measured inventory checked in beside
  them, not a number chosen to pass;
* the adjudicated `EventHandlerCheckpoint` -> `EventHandlerReceipt` rename moved
  the code concept only — the table it maps is untouched.

Each guard is proved in BOTH directions (ADR 0018): the check must fail when
the guarded thing breaks, not merely pass today.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "check_connector_adoption.py"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "engineering-standards.yml"
STAGED_PROFILE = REPO_ROOT / ".dotmac" / "standards-profile.v6.json"
INVENTORY = REPO_ROOT / "docs" / "inventories" / "external-connector-surface.json"
RECEIPT_MODEL = (
    REPO_ROOT / "app" / "models" / "finance" / "platform" / "event_handler_receipt.py"
)

ACCEPTED_SHA = "1" * 40
OTHER_SHA = "2" * 40


def _load_checker():
    spec = importlib.util.spec_from_file_location("check_connector_adoption", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


checker = _load_checker()


def _pinned_workflow(tmp_path: pathlib.Path, ref: str) -> pathlib.Path:
    text = WORKFLOW.read_text(encoding="utf-8").replace(
        f'GOVERNANCE_REF: "{checker.PLACEHOLDER_REF}"', f'GOVERNANCE_REF: "{ref}"'
    )
    path = tmp_path / "workflow.yml"
    path.write_text(text, encoding="utf-8")
    return path


def _pinned_profile(tmp_path: pathlib.Path, revision: str) -> pathlib.Path:
    data = json.loads(STAGED_PROFILE.read_text(encoding="utf-8"))
    data["governance_model"]["revision"] = revision
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


# --- The pin barrier -------------------------------------------------------


def test_the_adoption_is_blocked_on_governance_approval() -> None:
    """As staged, the job must be RED. Governance ADR 0010 is only Proposed."""
    problems = checker.check_pin(WORKFLOW, STAGED_PROFILE)
    assert problems, (
        "the connector-surface job would report a pass against an unapproved "
        "Governance revision"
    )
    assert any(checker.PLACEHOLDER_REF in problem for problem in problems)
    assert any("null OID" in problem for problem in problems)


@pytest.mark.parametrize(
    "workflow_ref, profile_revision",
    [
        (ACCEPTED_SHA, checker.PLACEHOLDER_REVISION),  # only the workflow moved
        (checker.PLACEHOLDER_REF, ACCEPTED_SHA),  # only the profile moved
        (ACCEPTED_SHA, OTHER_SHA),  # two different engines
        ("main", ACCEPTED_SHA),  # a mutable ref is not a pin
    ],
)
def test_a_half_lifted_or_mutable_pin_still_fails(
    tmp_path: pathlib.Path, workflow_ref: str, profile_revision: str
) -> None:
    assert checker.check_pin(
        _pinned_workflow(tmp_path, workflow_ref),
        _pinned_profile(tmp_path, profile_revision),
    )


def test_the_pin_check_passes_once_both_carry_the_same_accepted_sha(
    tmp_path: pathlib.Path,
) -> None:
    """The other direction: the barrier is a barrier, not an unconditional no."""
    assert (
        checker.check_pin(
            _pinned_workflow(tmp_path, ACCEPTED_SHA),
            _pinned_profile(tmp_path, ACCEPTED_SHA),
        )
        == []
    )


def test_the_connector_job_is_not_yet_a_required_check() -> None:
    """A blocked job must not be silently made non-blocking instead."""
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "--pin" in text, (
        "the connector-surface job must run the pin barrier before anything else"
    )
    assert "continue-on-error" not in text, (
        "a red job is the point; do not let it report success"
    )


# --- The staged profile ----------------------------------------------------


def test_staged_baselines_are_the_measured_inventory() -> None:
    assert checker.check_profile(STAGED_PROFILE, INVENTORY) == []


def test_a_baseline_edited_away_from_the_measurement_fails(
    tmp_path: pathlib.Path,
) -> None:
    data = json.loads(STAGED_PROFILE.read_text(encoding="utf-8"))
    data["external_connector_surface"]["baselines"]["http_client"] += 3
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert checker.check_profile(path, INVENTORY)


def test_widening_the_runtime_root_fails(tmp_path: pathlib.Path) -> None:
    data = json.loads(STAGED_PROFILE.read_text(encoding="utf-8"))
    data["external_connector_surface"]["runtime_roots"] = ["app/api"]
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert checker.check_profile(path, INVENTORY)


def test_an_inventory_whose_counts_contradict_its_file_list_fails(
    tmp_path: pathlib.Path,
) -> None:
    data = json.loads(INVENTORY.read_text(encoding="utf-8"))
    data["counts"]["sync_checkpoint"] += 1
    path = tmp_path / "inventory.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert checker.check_profile(STAGED_PROFILE, path)


def test_erp_declares_no_exclusions(tmp_path: pathlib.Path) -> None:
    """An exclusion is not the remedy for a mis-classification (ADR 0010)."""
    data = json.loads(STAGED_PROFILE.read_text(encoding="utf-8"))
    data["external_connector_surface"]["exclusions"] = [
        {
            "path": "app/models/finance/platform/event_handler_receipt.py",
            "category": "sync_checkpoint",
            "reason": "this is a local outbox receipt and not a feed position",
            "premise": {"kind": "generated", "marker": "@generated"},
        }
    ]
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert checker.check_profile(path, INVENTORY), (
        "ERP resolved the checkpoint question by renaming the concept, not by "
        "excluding the file"
    )


# --- The adjudicated rename ------------------------------------------------


def test_the_rename_left_persistence_identity_alone() -> None:
    assert checker.check_persistence(RECEIPT_MODEL) == []


@pytest.mark.parametrize(
    "find, replace",
    [
        # A changed table is a migration masquerading as a rename.
        ('__tablename__ = "event_handler_checkpoint"', '__tablename__ = "receipts"'),
        ("uq_checkpoint_event_handler", "uq_receipt_event_handler"),
        ('name="checkpoint_status"', 'name="handler_receipt_status"'),
        ('{"schema": "platform"}', '{"schema": "finance"}'),
        ("class EventHandlerReceipt(Base):", "class EventHandlerCheckpoint(Base):"),
    ],
)
def test_moving_persistence_identity_fails(
    tmp_path: pathlib.Path, find: str, replace: str
) -> None:
    source = RECEIPT_MODEL.read_text(encoding="utf-8")
    assert find in source, f"{find!r} is no longer in the model; update this proof"
    path = tmp_path / "event_handler_receipt.py"
    path.write_text(source.replace(find, replace), encoding="utf-8")
    assert checker.check_persistence(path)


# --- Inventory staleness ---------------------------------------------------
#
# While the pin is a placeholder NO Governance engine runs in CI, so the
# baselines are only as good as the inventory beside them. Without this guard a
# new connector module lands in `app/` and every local check still reports OK —
# demonstrated before the guard existed, and held here in both directions.


def test_the_inventory_still_describes_this_runtime_tree() -> None:
    assert checker.check_inventory(REPO_ROOT, INVENTORY) == []


def test_a_new_runtime_file_makes_the_inventory_stale(
    tmp_path: pathlib.Path,
) -> None:
    """The exact miss this guard was added for: a new connector module lands."""
    data = json.loads(INVENTORY.read_text(encoding="utf-8"))
    planted = "app/services/planted_connector.py"
    assert planted not in data["runtime_files"]
    data["runtime_files"].append(planted)
    data["runtime_files"] = sorted(data["runtime_files"])
    data["runtime_files_measured"] = len(data["runtime_files"])
    data["runtime_files_digest"] = checker.runtime_digest(data["runtime_files"])
    path = tmp_path / "inventory.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    problems = checker.check_inventory(REPO_ROOT, path)
    assert problems, "a runtime file set that no longer matches must be reported"
    assert planted in problems[0]


def test_a_removed_runtime_file_makes_the_inventory_stale(
    tmp_path: pathlib.Path,
) -> None:
    """Retirement is recorded, not inferred — the same rule as the ratchet."""
    data = json.loads(INVENTORY.read_text(encoding="utf-8"))
    dropped = data["runtime_files"].pop()
    data["runtime_files_measured"] = len(data["runtime_files"])
    data["runtime_files_digest"] = checker.runtime_digest(data["runtime_files"])
    path = tmp_path / "inventory.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    problems = checker.check_inventory(REPO_ROOT, path)
    assert problems
    assert dropped in problems[0]


def test_an_inventory_without_a_digest_cannot_claim_to_be_current(
    tmp_path: pathlib.Path,
) -> None:
    """Dropping the digest must not be a way to switch the guard off."""
    data = json.loads(INVENTORY.read_text(encoding="utf-8"))
    del data["runtime_files_digest"]
    path = tmp_path / "inventory.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert checker.check_inventory(REPO_ROOT, path)


def test_the_recorded_count_and_digest_must_agree(tmp_path: pathlib.Path) -> None:
    """A hand-edited count with an untouched digest is still caught."""
    data = json.loads(INVENTORY.read_text(encoding="utf-8"))
    data["runtime_files_measured"] += 1
    path = tmp_path / "inventory.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert checker.check_inventory(REPO_ROOT, path)


def test_the_inventory_does_not_claim_to_be_a_plain_committed_revision() -> None:
    """The measurement includes the uncommitted rename; it must say so.

    At `measured_at_revision` alone `sync_checkpoint` is 8. The inventory
    records 7. Without `measured_tree` saying why, the provenance would be a
    false claim that happens to justify the lowered baseline.
    """
    data = json.loads(INVENTORY.read_text(encoding="utf-8"))
    assert data["counts"]["sync_checkpoint"] == 7
    assert "measured_tree" in data, (
        "the inventory records a working-tree measurement and must disclose it"
    )
    assert "8" in data["measured_tree"], (
        "the disclosure must name the committed-revision count it differs from"
    )


def test_the_old_checkpoint_names_are_gone_from_the_runtime() -> None:
    """The rename is complete, so the baseline reduction is real."""
    offenders = [
        path
        for path in (REPO_ROOT / "app").rglob("*.py")
        if "EventHandlerCheckpoint(" in path.read_text(encoding="utf-8")
        or "class CheckpointStatus" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []
