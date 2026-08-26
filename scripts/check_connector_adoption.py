#!/usr/bin/env python3
"""Guard ERP's staged adoption of the external-connector-surface ratchet.

ERP does NOT own the detector. The six-category classifier and its
two-directional baseline live in the Dotmac Governance engine (Governance ADR
0010, `standards_control/engine.py`), and ERP consumes it through the pinned
`standards-check` action exactly as it already consumes the rest of the
engineering-standards profile. A product-local copy of the classifier is the
drift class ADR 0006 exists to prevent, so nothing here re-implements it.

What IS ERP's job, and what this script checks:

1. `--pin`   the adoption is BLOCKED until a real accepted Governance revision
             exists. Both the workflow ref and the staged profile carry an
             obvious placeholder, and this refuses to go green on either of
             them — including the half-migrated state where one is replaced
             and the other is not.
2. `--profile`  the staged schema-6 profile's declared baselines are exactly
             the measured inventory checked in beside it, so a baseline cannot
             be hand-edited away from what was measured.
3. `--persistence`  the adjudicated rename of the local-outbox receipt model
             (Governance ADR 0010 adjudication, recorded in
             `docs/inventories/external-connector-surface.md`) changed the CODE
             concept only. Table, schema, columns, constraint and PostgreSQL
             enum-type names are frozen here: a changed table would be a
             migration masquerading as a rename.
4. `--inventory`  the checked-in measurement still describes THIS runtime tree.
             While the pin is a placeholder no Governance engine runs in CI, so
             the baselines are only as good as the inventory beside them, and a
             point-in-time measurement silently rots as `app/` changes. This
             compares the runtime FILE SET — the structural rules only (excluded
             directories, `test_` prefix), never the classifier — against the
             digest recorded at measurement time.

             What it catches: a runtime file added, deleted or renamed since the
             measurement, which is how a new connector module lands.
             What it does NOT catch, stated because an undisclosed blind spot is
             worse than a known one: a connector added to a file that was
             already measured. Only the Governance classifier sees that, and it
             is exactly what stays blocked on approval. This guard bounds the
             staleness window; it does not close it.

Run all three (the default) and the exit status is the adoption's real state:
non-zero while blocked on Governance approval.

    python3 scripts/check_connector_adoption.py
    python3 scripts/check_connector_adoption.py --pin
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import pathlib
import re
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

#: Structural rules ONLY, mirrored from Governance ADR 0010's measured scope so
#: the same files are enumerated. These are directory/filename conventions, not
#: connector detection: nothing here decides whether a file IS a connector, so
#: this is not a second copy of the classifier.
RUNTIME_EXCLUDED_PARTS = frozenset(
    {"tests", "test", "__pycache__", "migrations", "alembic", "node_modules", ".venv"}
)

WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "engineering-standards.yml"
STAGED_PROFILE_PATH = REPO_ROOT / ".dotmac" / "standards-profile.v6.json"
INVENTORY_PATH = REPO_ROOT / "docs" / "inventories" / "external-connector-surface.json"
RECEIPT_MODEL_PATH = (
    REPO_ROOT / "app" / "models" / "finance" / "platform" / "event_handler_receipt.py"
)

#: The workflow placeholder. Replaced, in the same change, by the 40-character
#: SHA of the Governance commit that ACCEPTS ADR 0010 on canonical `main`.
PLACEHOLDER_REF = "PENDING-APPROVAL"
#: The profile placeholder. `governance_model.revision` is schema-constrained to
#: 40 lower-case hex, so the placeholder is the Git null OID: structurally
#: parseable, obviously not a commit, and refused here.
PLACEHOLDER_REVISION = "0" * 40
GIT_SHA = re.compile(r"^[0-9a-f]{40}$")

GOVERNANCE_REF_LINE = re.compile(r"^\s*GOVERNANCE_REF:\s*['\"]?([^'\"\s#]+)", re.M)

CONNECTOR_CATEGORIES = (
    "http_client",
    "webhook_surface",
    "provider_credential",
    "connector_task",
    "sync_checkpoint",
    "delivery_retry",
)

#: Persistence identity of the local-outbox handler receipt, frozen at the
#: rename. Every name here is what PostgreSQL already holds; the migration
#: lineage (`alembic/versions/create_ifrs_schemas.py`) is untouched by the
#: rename and must stay that way.
FROZEN_PERSISTENCE = {
    "model_class": "EventHandlerReceipt",
    "status_enum_class": "HandlerReceiptStatus",
    "tablename": "event_handler_checkpoint",
    "schema": "platform",
    "unique_constraint": "uq_checkpoint_event_handler",
    "pg_enum_type": "checkpoint_status",
    "columns": (
        "checkpoint_id",
        "event_id",
        "handler_name",
        "processed_at",
        "status",
        "attempts",
        "last_error",
        "created_at",
    ),
}

#: Name fragments the Governance classifier reads as "a position in an external
#: feed". This is NOT a copy of the detector — it is the one assertion that
#: keeps the `sync_checkpoint` baseline reduction honest: if the receipt model
#: reacquires a feed-checkpoint name, the lowered baseline is wrong.
FEED_CHECKPOINT_NAME_FRAGMENTS = ("checkpoint", "syncstate", "synccursor")


def _fail(problems: list[str], message: str) -> None:
    problems.append(message)


def check_pin(workflow_path: pathlib.Path, profile_path: pathlib.Path) -> list[str]:
    """The adoption must not go green against a placeholder Governance pin."""
    problems: list[str] = []
    try:
        workflow = workflow_path.read_text(encoding="utf-8")
    except OSError as error:
        return [f"cannot read {workflow_path}: {error}"]
    matches = GOVERNANCE_REF_LINE.findall(workflow)
    if len(matches) != 1:
        return [
            f"{workflow_path} must declare exactly one GOVERNANCE_REF; "
            f"found {len(matches)}"
        ]
    workflow_ref = matches[0]

    try:
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return [f"cannot read {profile_path}: {error}"]
    profile_ref = str(profile.get("governance_model", {}).get("revision", ""))

    if workflow_ref == PLACEHOLDER_REF:
        _fail(
            problems,
            f"GOVERNANCE_REF is still {PLACEHOLDER_REF!r}. The external-connector"
            " ratchet is BLOCKED: Governance ADR 0010 is Proposed, no human has"
            " accepted it, and there is no revision to pin. Replace this with the"
            " 40-character SHA of the Governance commit that accepts ADR 0010 on"
            " canonical main.",
        )
    elif not GIT_SHA.fullmatch(workflow_ref):
        _fail(
            problems,
            f"GOVERNANCE_REF {workflow_ref!r} is not a 40-character lower-case"
            " Git SHA. A tag or branch is mutable and is not a pin.",
        )

    if profile_ref == PLACEHOLDER_REVISION:
        _fail(
            problems,
            f"{profile_path.name}: governance_model.revision is the Git null OID"
            " placeholder. Replace it with the same accepted Governance SHA in the"
            " same change that replaces GOVERNANCE_REF.",
        )
    elif not GIT_SHA.fullmatch(profile_ref):
        _fail(
            problems,
            f"{profile_path.name}: governance_model.revision {profile_ref!r} is not"
            " a 40-character lower-case Git SHA.",
        )

    if not problems and workflow_ref != profile_ref:
        _fail(
            problems,
            f"GOVERNANCE_REF {workflow_ref} and {profile_path.name}"
            f" governance_model.revision {profile_ref} disagree; the workflow would"
            " run an engine other than the one the profile was written against.",
        )
    return problems


def check_profile(
    profile_path: pathlib.Path, inventory_path: pathlib.Path
) -> list[str]:
    """The staged baselines must be exactly the measured inventory."""
    problems: list[str] = []
    try:
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return [f"cannot read staged adoption inputs: {error}"]

    if profile.get("schema_version") != 6:
        _fail(
            problems,
            "staged profile must declare schema_version 6 (the version that adds"
            " external_connector_surface)",
        )
    surface = profile.get("external_connector_surface")
    if not isinstance(surface, dict):
        return problems + ["staged profile has no external_connector_surface object"]

    roots = surface.get("runtime_roots")
    if roots != ["app"]:
        _fail(
            problems,
            f"runtime_roots {roots!r}: ERP's application runtime is `app`."
            " Widening or narrowing it moves counts without retiring anything.",
        )

    baselines = surface.get("baselines")
    if not isinstance(baselines, dict) or set(baselines) != set(CONNECTOR_CATEGORIES):
        return problems + [
            "baselines must declare exactly the six closed categories: "
            + ", ".join(CONNECTOR_CATEGORIES)
        ]

    counts = inventory.get("counts", {})
    files = inventory.get("files", {})
    for category in CONNECTOR_CATEGORIES:
        measured = counts.get(category)
        listed = len(files.get(category, []))
        declared = baselines[category]
        if measured != listed:
            _fail(
                problems,
                f"inventory {category}: count {measured} does not match its own"
                f" {listed} listed files; the inventory was hand-edited.",
            )
        if declared != measured:
            _fail(
                problems,
                f"{category}: staged baseline {declared} is not the measured"
                f" inventory {measured}. A baseline is a record of a measurement,"
                " not a number chosen to pass.",
            )

    exclusions = surface.get("exclusions")
    if exclusions != []:
        _fail(
            problems,
            "ERP declares no external-connector exclusions; an exclusion is not the"
            " remedy for a mis-classification (Governance ADR 0010).",
        )
    return problems


def runtime_files(repo_root: pathlib.Path, roots: list[str]) -> list[str]:
    """Enumerate the measured runtime file set by the structural rules alone."""
    found: list[str] = []
    for root in roots:
        base = repo_root / root
        if not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            relative = path.relative_to(repo_root)
            if RUNTIME_EXCLUDED_PARTS & set(relative.parts):
                continue
            if path.name.startswith("test_"):
                continue
            found.append(relative.as_posix())
    return sorted(found)


def runtime_digest(paths: list[str]) -> str:
    return hashlib.sha256("\n".join(paths).encode("utf-8")).hexdigest()


def check_inventory(repo_root: pathlib.Path, inventory_path: pathlib.Path) -> list[str]:
    """The checked-in measurement must still describe this runtime tree."""
    problems: list[str] = []
    try:
        inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return [f"cannot read {inventory_path}: {error}"]

    roots = inventory.get("runtime_roots")
    if not isinstance(roots, list) or not roots:
        return ["inventory declares no runtime_roots"]

    observed = runtime_files(repo_root, roots)
    recorded_count = inventory.get("runtime_files_measured")
    recorded_digest = inventory.get("runtime_files_digest")

    if recorded_digest is None:
        return [
            f"{inventory_path.name} records no runtime_files_digest, so the"
            " measurement cannot be shown to describe the current tree."
        ]

    if len(observed) != recorded_count or runtime_digest(observed) != recorded_digest:
        recorded_set = set(inventory.get("runtime_files", []) or [])
        added = sorted(set(observed) - recorded_set)
        removed = sorted(recorded_set - set(observed))
        detail = ""
        if recorded_set:
            if added:
                detail += f" added: {', '.join(added[:5])}"
            if removed:
                detail += f" removed: {', '.join(removed[:5])}"
        _fail(
            problems,
            f"the runtime file set changed since the measurement"
            f" ({recorded_count} files recorded, {len(observed)} found).{detail}"
            " The checked-in connector inventory and the baselines derived from"
            " it are STALE: re-measure with the Governance engine and update"
            " both in the same change.",
        )
    return problems


def _model_module(path: pathlib.Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _string_constants(node: ast.AST) -> list[str]:
    return [
        child.value
        for child in ast.walk(node)
        if isinstance(child, ast.Constant) and isinstance(child.value, str)
    ]


def check_persistence(model_path: pathlib.Path) -> list[str]:
    """The rename changed the code concept only; the table is untouched."""
    problems: list[str] = []
    try:
        module = _model_module(model_path)
    except (OSError, SyntaxError) as error:
        return [f"cannot read the receipt model at {model_path}: {error}"]

    classes = {
        node.name: node for node in module.body if isinstance(node, ast.ClassDef)
    }
    for expected in (
        FROZEN_PERSISTENCE["model_class"],
        FROZEN_PERSISTENCE["status_enum_class"],
    ):
        if expected not in classes:
            _fail(problems, f"{model_path.name} must declare class {expected}")

    for name in classes:
        lowered = name.lower()
        if any(part in lowered for part in FEED_CHECKPOINT_NAME_FRAGMENTS):
            _fail(
                problems,
                f"class {name} reads as a position in an external feed. This model"
                " owns local-outbox at-most-once receipts; a feed-checkpoint name"
                " here makes the lowered sync_checkpoint baseline wrong.",
            )

    model = classes.get(FROZEN_PERSISTENCE["model_class"])
    if model is None:
        return problems

    tablename: str | None = None
    columns: list[str] = []
    for node in model.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__tablename__"
            for target in node.targets
        ):
            if isinstance(node.value, ast.Constant) and isinstance(
                node.value.value, str
            ):
                tablename = node.value.value
        # Only mapped_column() attributes are persistence; a relationship() is
        # an ORM convenience with no column of its own.
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id == "mapped_column"
        ):
            columns.append(node.target.id)

    if tablename != FROZEN_PERSISTENCE["tablename"]:
        _fail(
            problems,
            f"__tablename__ is {tablename!r}, frozen value is"
            f" {FROZEN_PERSISTENCE['tablename']!r}. Changing it is a migration, not"
            " a rename.",
        )
    if tuple(columns) != FROZEN_PERSISTENCE["columns"]:
        _fail(
            problems,
            f"mapped columns {tuple(columns)!r} differ from the frozen"
            f" {FROZEN_PERSISTENCE['columns']!r}.",
        )

    literals = set(_string_constants(model))
    for key in ("schema", "unique_constraint", "pg_enum_type"):
        expected = FROZEN_PERSISTENCE[key]
        if expected not in literals:
            _fail(
                problems,
                f"the frozen {key} {expected!r} no longer appears in"
                f" {FROZEN_PERSISTENCE['model_class']}; persistence identity moved.",
            )
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pin", action="store_true")
    parser.add_argument("--profile", action="store_true")
    parser.add_argument("--persistence", action="store_true")
    parser.add_argument("--inventory", action="store_true")
    parser.add_argument("--repo-root", type=pathlib.Path, default=REPO_ROOT)
    parser.add_argument("--workflow-path", type=pathlib.Path, default=WORKFLOW_PATH)
    parser.add_argument(
        "--staged-profile-path", type=pathlib.Path, default=STAGED_PROFILE_PATH
    )
    parser.add_argument("--inventory-path", type=pathlib.Path, default=INVENTORY_PATH)
    parser.add_argument("--model-path", type=pathlib.Path, default=RECEIPT_MODEL_PATH)
    args = parser.parse_args(argv)

    selected = args.pin or args.profile or args.persistence or args.inventory
    problems: list[str] = []
    if args.pin or not selected:
        problems += check_pin(args.workflow_path, args.staged_profile_path)
    if args.profile or not selected:
        problems += check_profile(args.staged_profile_path, args.inventory_path)
    if args.persistence or not selected:
        problems += check_persistence(args.model_path)
    if args.inventory or not selected:
        problems += check_inventory(args.repo_root, args.inventory_path)

    if problems:
        for problem in problems:
            print(f"FAIL: {problem}")
        return 1
    print("connector-surface adoption checks: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
