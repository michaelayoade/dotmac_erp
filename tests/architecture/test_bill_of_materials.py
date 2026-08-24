"""Enforce ADR-0004's frozen bill of materials.

Programme step 1 freezes WHICH modules the composed ERP installs and who owns
everything else. These checks make that freeze enforceable rather than stated:
a module cannot be pinned without a row, a row cannot claim to be composed
without a pin, and neither census can be edited into silence.

Each detector carries a sensitivity proof. A closure check over a set that
happens to be closed passes for the wrong reason, and the day it stops biting
is the day nobody notices.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from app import bill_of_materials as bom

REPO_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = REPO_ROOT / "pyproject.toml"

#: The private index every Dotmac module resolves from. A dependency that does
#: not name it is an ordinary PyPI package and is not part of this product's
#: module composition.
MODULE_INDEX = "forgejo"


def _module_pins(document: dict[str, object]) -> dict[str, str]:
    """Every distribution pinned from the Dotmac index, with its exact version."""
    poetry = document.get("tool", {})
    assert isinstance(poetry, dict)
    tool_poetry = poetry.get("poetry", {})
    assert isinstance(tool_poetry, dict)
    dependencies = tool_poetry.get("dependencies", {})
    assert isinstance(dependencies, dict)

    pins: dict[str, str] = {}
    for name, requirement in dependencies.items():
        if not isinstance(requirement, dict):
            continue
        if requirement.get("source") != MODULE_INDEX:
            continue
        version = requirement.get("version")
        assert isinstance(version, str), f"{name} pins no version"
        pins[name] = version
    return pins


def _pyproject() -> dict[str, object]:
    with PYPROJECT.open("rb") as handle:
        return tomllib.load(handle)


# --------------------------------------------------------------------------
# Module closure: every Starter distribution is selected or excluded.
# --------------------------------------------------------------------------


def test_every_starter_distribution_has_exactly_one_disposition() -> None:
    selected = bom.SELECTED_DISTRIBUTIONS
    excluded = bom.EXCLUDED_DISTRIBUTIONS

    assert not selected & excluded, (
        "a distribution is selected or excluded, never both: "
        f"{sorted(selected & excluded)}"
    )
    assert not (selected | excluded) - bom.STARTER_PACKAGE_CENSUS, (
        "disposed a distribution that does not exist at the census revision: "
        f"{sorted((selected | excluded) - bom.STARTER_PACKAGE_CENSUS)}"
    )
    assert not bom.STARTER_PACKAGE_CENSUS - selected - excluded, (
        "undisposed Starter distribution — silence is not an answer: "
        f"{sorted(bom.STARTER_PACKAGE_CENSUS - selected - excluded)}"
    )


def test_the_module_closure_detector_is_sensitive() -> None:
    census = bom.STARTER_PACKAGE_CENSUS | {"dotmac-invented"}
    assert census - bom.SELECTED_DISTRIBUTIONS - bom.EXCLUDED_DISTRIBUTIONS == {
        "dotmac-invented"
    }


def test_no_distribution_is_listed_twice() -> None:
    selected = [module.distribution for module in bom.SELECTED]
    excluded = [module.distribution for module in bom.EXCLUDED]
    assert len(selected) == len(set(selected))
    assert len(excluded) == len(set(excluded))


# --------------------------------------------------------------------------
# Capability closure: every capability has exactly one named owner.
# --------------------------------------------------------------------------


def test_every_capability_has_exactly_one_owner() -> None:
    carried = [
        capability for module in bom.SELECTED for capability in module.capabilities
    ]
    retained = [entry.capability for entry in bom.RETAINED]

    assert len(carried) == len(set(carried)), (
        "two modules claim the same capability: "
        f"{sorted({c for c in carried if carried.count(c) > 1})}"
    )
    assert len(retained) == len(set(retained))
    assert not set(carried) & set(retained), (
        "a capability is module-owned or retained, never both: "
        f"{sorted(set(carried) & set(retained))}"
    )
    assert bom.ERP_CAPABILITY_CENSUS == set(carried) | set(retained)


def test_every_selected_module_carries_at_least_one_capability() -> None:
    """A module installed for no capability is a dependency, not a decision."""
    empty = [module.distribution for module in bom.SELECTED if not module.capabilities]
    assert not empty, empty


def test_every_disposition_states_a_reason() -> None:
    for module in bom.SELECTED:
        assert module.rationale.strip(), module.distribution
    for module in bom.EXCLUDED:
        assert module.rationale.strip(), module.distribution
        assert module.owner.strip(), module.distribution
    for entry in bom.RETAINED:
        assert entry.rationale.strip(), entry.capability


# --------------------------------------------------------------------------
# The pins and the bill of materials describe the same product.
# --------------------------------------------------------------------------


def test_composed_modules_are_exactly_the_pinned_modules() -> None:
    pins = _module_pins(_pyproject())
    assert set(pins) == bom.COMPOSED_DISTRIBUTIONS, (
        "pinned but not recorded as composed: "
        f"{sorted(set(pins) - bom.COMPOSED_DISTRIBUTIONS)}; "
        "recorded as composed but not pinned: "
        f"{sorted(bom.COMPOSED_DISTRIBUTIONS - set(pins))}"
    )


def test_no_module_is_pinned_without_a_bill_of_materials_row() -> None:
    pins = _module_pins(_pyproject())
    assert not set(pins) - bom.SELECTED_DISTRIBUTIONS, (
        "composing a module the frozen bill of materials does not select: "
        f"{sorted(set(pins) - bom.SELECTED_DISTRIBUTIONS)}"
    )


def test_the_pin_detector_reads_the_real_pins() -> None:
    """Sensitivity: an added Dotmac pin must be seen, an added PyPI one must not."""
    document = _pyproject()
    dependencies = document["tool"]["poetry"]["dependencies"]  # type: ignore[index]
    assert isinstance(dependencies, dict)

    dependencies["dotmac-smuggled"] = {"version": "0.1.0a1", "source": MODULE_INDEX}
    dependencies["ordinary-pypi-package"] = {"version": "1.2.3"}
    pins = _module_pins(document)

    assert "dotmac-smuggled" in pins
    assert "ordinary-pypi-package" not in pins
    assert pins["dotmac-smuggled"] == "0.1.0a1"


def test_the_pins_are_exact() -> None:
    """A frozen product cannot float. No caret, tilde, range or wildcard."""
    for distribution, version in _module_pins(_pyproject()).items():
        assert not any(
            character in version for character in "^~><*,"
        ), f"{distribution} pins a range: {version!r}"


# --------------------------------------------------------------------------
# States are declared, and an unavailable module cannot claim to be installed.
# --------------------------------------------------------------------------


def test_states_come_from_the_closed_vocabularies() -> None:
    for module in bom.SELECTED:
        assert module.state in bom.COMPOSITION_STATES, module.distribution
        assert module.release_state in bom.RELEASE_STATES, module.distribution


def test_a_module_that_does_not_exist_cannot_be_composed() -> None:
    impossible = [
        module.distribution
        for module in bom.SELECTED
        if module.state == "composed" and module.release_state != "released"
    ]
    assert not impossible, (
        "recorded as composed while unreleased or unbuilt — a pin that cannot "
        f"resolve: {impossible}"
    )


def test_unreleased_selections_are_visible() -> None:
    """Step 2 cannot compose these. The list is the work, so it must be readable."""
    unreleased = {
        module.distribution
        for module in bom.SELECTED
        if module.release_state != "released"
    }
    assert unreleased == {
        "dotmac-app-sync",
        "dotmac-fx-policy",
        "dotmac-template-studio",
        "dotmac-workforce",
    }


# --------------------------------------------------------------------------
# The census is a claim about another repository, so it is pinned to a tree.
# --------------------------------------------------------------------------


def test_the_census_is_pinned_to_an_immutable_revision() -> None:
    revision = bom.STARTER_PACKAGE_CENSUS_REVISION
    assert len(revision) == 40
    assert set(revision) <= set("0123456789abcdef")
    assert bom.STARTER_PACKAGE_CENSUS_REPOSITORY.startswith("https://github.com/")


def test_exclusions_are_sorted_by_distribution() -> None:
    """Sorted rows keep a review diff to the line that actually changed.

    `SELECTED` is grouped by role instead — foundation, then finance, then the
    rest — because reading it in that order is what makes an omission visible.
    """
    names = [module.distribution for module in bom.EXCLUDED]
    assert names == sorted(names)


# --------------------------------------------------------------------------
# Step 2: composing a module is a four-file atomic change.
#
# The pin, the `version_locations` entry, the expected head and the plan row
# describe the same act. Any one of them alone is a half-composed module: a
# pin with no lineage installs dead code, a lineage with no expected head
# grows an unreviewed Alembic head, and an expected head with no pin names a
# revision that cannot be found.
# --------------------------------------------------------------------------

ALEMBIC_INI = REPO_ROOT / "alembic.ini"


def _version_locations() -> list[str]:
    """The raw `version_locations` entries, without %(here)s interpolation."""
    for line in ALEMBIC_INI.read_text(encoding="utf-8").splitlines():
        if line.startswith("version_locations"):
            return line.split("=", 1)[1].split()
    raise AssertionError("alembic.ini declares no version_locations")


def _composed_lineage_locations() -> set[str]:
    """Just the module lineages: `package.migrations:versions` entries."""
    return {entry for entry in _version_locations() if ".migrations:versions" in entry}


def _plan() -> dict[str, bom.CompositionStep]:
    return {step.distribution: step for step in bom.COMPOSITION_PLAN}


def test_the_plan_covers_exactly_the_selected_modules() -> None:
    assert set(_plan()) == bom.SELECTED_DISTRIBUTIONS, (
        "planned but not selected: "
        f"{sorted(set(_plan()) - bom.SELECTED_DISTRIBUTIONS)}; "
        "selected but unplanned: "
        f"{sorted(bom.SELECTED_DISTRIBUTIONS - set(_plan()))}"
    )


def test_tranche_zero_is_exactly_what_is_composed() -> None:
    tranche_zero = {
        step.distribution for step in bom.COMPOSITION_PLAN if step.tranche == 0
    }
    assert tranche_zero == bom.COMPOSED_DISTRIBUTIONS


def test_every_tranche_is_a_declared_tranche() -> None:
    for step in bom.COMPOSITION_PLAN:
        assert step.tranche in bom.TRANCHE_NAMES, step.distribution


def test_composed_lineages_agree_with_the_migration_bindings() -> None:
    """The plan and `app/migration_bindings.py` name the same heads."""
    from app.migration_bindings import COMPOSED_MODULE_LINEAGES

    planned = {
        step.lineage_branch: step.lineage_head
        for step in bom.COMPOSITION_PLAN
        if step.tranche == 0 and step.lineage_branch is not None
    }
    assert planned == COMPOSED_MODULE_LINEAGES


def test_composed_lineages_agree_with_alembic_version_locations() -> None:
    """Every composed stateful module contributes exactly one lineage entry."""
    expected = {
        f"{step.distribution.replace('-', '_')}.migrations:versions"
        for step in bom.COMPOSITION_PLAN
        if step.tranche == 0 and step.lineage_branch is not None
    }
    assert _composed_lineage_locations() == expected, (
        "alembic.ini and the composition plan disagree: "
        f"{sorted(_composed_lineage_locations() ^ expected)}"
    )


def test_the_lineage_location_detector_reads_the_real_file() -> None:
    """Sensitivity: the ERP lineage is not a module lineage, and is not counted."""
    locations = _version_locations()
    assert any(entry.endswith("/alembic/versions") for entry in locations)
    assert all(
        "/alembic/versions" not in entry for entry in _composed_lineage_locations()
    )
    assert len(locations) == len(_composed_lineage_locations()) + 1


def test_no_lineage_is_composed_twice() -> None:
    branches = [
        step.lineage_branch
        for step in bom.COMPOSITION_PLAN
        if step.lineage_branch is not None
    ]
    heads = [
        step.lineage_head
        for step in bom.COMPOSITION_PLAN
        if step.lineage_head is not None
    ]
    schemas = [step.schema for step in bom.COMPOSITION_PLAN if step.schema is not None]
    assert len(branches) == len(set(branches))
    assert len(heads) == len(set(heads))
    assert len(schemas) == len(set(schemas)), (
        "two modules claim one schema — a namespace collision: "
        f"{sorted({s for s in schemas if schemas.count(s) > 1})}"
    )


def test_a_stateful_module_declares_branch_head_and_schema_together() -> None:
    """Half a lineage declaration is worse than none: it reads as complete."""
    for step in bom.COMPOSITION_PLAN:
        declared = (
            step.lineage_branch is not None,
            step.lineage_head is not None,
            step.schema is not None,
        )
        assert len(set(declared)) == 1, (
            f"{step.distribution} declares only part of a lineage: "
            f"branch={step.lineage_branch} head={step.lineage_head} "
            f"schema={step.schema}"
        )


# --------------------------------------------------------------------------
# Prerequisites: a module is composed only where its effects are supplied.
# --------------------------------------------------------------------------


def test_every_composed_module_has_its_prerequisites_supplied() -> None:
    for step in bom.COMPOSITION_PLAN:
        if step.tranche != 0:
            continue
        missing = set(step.requires_effects) - bom.ASSEMBLY_SUPPLIED_EFFECTS
        assert not missing, f"{step.distribution} composed without {sorted(missing)}"


def test_the_supplied_effects_are_exactly_the_assembly_bindings() -> None:
    from app.migration_bindings import ASSEMBLY_PREREQUISITE_BINDINGS

    bound = {binding.prerequisite for binding in ASSEMBLY_PREREQUISITE_BINDINGS}
    assert bound == bom.ASSEMBLY_SUPPLIED_EFFECTS


def test_modules_blocked_on_a_missing_effect_are_declared() -> None:
    """Derive the blocked set, and compare it to the declared one.

    Supplying an effect must therefore delete its row here in the same change
    that adds the migration — the edit most likely to be forgotten.
    """
    derived: dict[str, list[str]] = {}
    for step in bom.COMPOSITION_PLAN:
        for effect in step.requires_effects:
            if effect in bom.ASSEMBLY_SUPPLIED_EFFECTS:
                continue
            derived.setdefault(effect, []).append(step.distribution)

    assert {
        effect: tuple(sorted(names)) for effect, names in derived.items()
    } == bom.MISSING_EFFECTS


def test_a_module_needing_a_missing_effect_is_not_in_an_unblocked_tranche() -> None:
    for step in bom.COMPOSITION_PLAN:
        missing = set(step.requires_effects) - bom.ASSEMBLY_SUPPLIED_EFFECTS
        if missing:
            assert step.tranche in {2, 3}, (
                f"{step.distribution} needs {sorted(missing)} but sits in "
                f"tranche {step.tranche}"
            )


# --------------------------------------------------------------------------
# The kernel floor the selection demands.
# --------------------------------------------------------------------------


def _alpha(version: str) -> int:
    assert "a" in version, version
    return int(version.rsplit("a", 1)[1])


def test_the_kernel_pin_satisfies_every_composed_module_floor() -> None:
    pinned = _module_pins(_pyproject())["dotmac-kernel"]
    for step in bom.COMPOSITION_PLAN:
        if step.tranche != 0 or step.kernel_floor is None:
            continue
        assert _alpha(pinned) >= _alpha(step.kernel_floor), (
            f"{step.distribution} floors at {step.kernel_floor}, kernel is {pinned}"
        )


def test_the_outstanding_kernel_repin_is_visible() -> None:
    """The selection demands more kernel than the assembly pins.

    This is a real obligation of step 2, not a defect: composing tranche 1
    starts with the repin. When the repin lands, both halves move here in the
    same change.
    """
    demanded = bom.KERNEL_FLOOR_DEMANDED_BY_SELECTION
    floors = [
        step.kernel_floor
        for step in bom.COMPOSITION_PLAN
        if step.kernel_floor is not None
    ]
    assert _alpha(demanded) == max(_alpha(floor) for floor in floors)

    pinned = _module_pins(_pyproject())["dotmac-kernel"]
    assert _alpha(pinned) < _alpha(demanded), (
        "the kernel now satisfies the whole selection — delete this check and "
        "let test_the_kernel_pin_satisfies_every_composed_module_floor carry it"
    )
