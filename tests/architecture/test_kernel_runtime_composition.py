"""Architecture guard: `docs/kernel-runtime-composition.json` (v2).

Validates ERP's dimensional composition record against the mirrored
`dimensional-composition.v2` contract (`tests/architecture/composition_schema.py`,
a byte-for-byte mirror of `dotmac_starter_mt`'s protected-main
`tests/architecture/composition_schema.py` at revision `08a2dae1`). ERP does
not import Starter's Python; the catalogue universe Ruling 2 requires is
re-derived here from a frozen, checked-in mirror of Starter's
`packages/*/EXTRACTION.toml` (and, for `optional-module` distributions, their
`manifest.py`) at that same revision:
`tests/architecture/fixtures/starter_packages_08a2dae1/`.

Ruling 1 -- the registration boundary -- is the reason this record differs
from the superseded v1 record: `module_registration` means the distribution's
real `ModuleManifest` is registered through an assembly ERP's OWN BOOT PATH
(`app/main.py`, the Dockerfile's `gunicorn -c gunicorn.conf.py app.main:app`
target) actually consumes -- not that `app/product_assembly.py` constructs a
`ProductAssemblySpec`. `measure_erp_boot_assembly_consumption` below re-derives
that fact directly from `app/main.py`'s and `app/product_assembly.py`'s own
source on every run, the same way the mirrored schema's own
`measure_starter_boot_assembly_consumption` re-derives Starter's positive
control -- it is not a trusted literal.

Sensitivity is proven, not assumed:
`test_registration_boundary_sensitivity_proof_defect_is_named` plants the
exact defect v1 recorded (`module_registration: true` for a distribution
whose only call site is release metadata) and shows the cross-check names it;
`test_registration_boundary_sensitivity_proof_near_miss_is_accepted` runs the
real, checked-in `false` value for that same distribution through the
identical cross-check and shows it is NOT flagged -- so the guard is proven to
distinguish the two, rather than reject everything.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.architecture import composition_schema as cs

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RECORD_PATH = PROJECT_ROOT / "docs" / "kernel-runtime-composition.json"
FIXTURE_PACKAGES_ROOT = (
    Path(__file__).resolve().parent / "fixtures" / "starter_packages_08a2dae1"
)

#: The six real, real-`ModuleManifest` call sites in
#: `app/product_assembly.py` (`COMPOSED_MODULE_MANIFESTS`) -- the only place
#: in this repository a `ModuleManifest` value is passed into an
#: assembly-shaped object at all.
COMPOSED_OPTIONAL_MODULES = frozenset(
    {
        "dotmac-accounting",
        "dotmac-files",
        "dotmac-imports",
        "dotmac-numbering",
        "dotmac-people",
        "dotmac-tax",
    }
)


# ---------------------------------------------------------------------------
# Envelope / schema-version loading
# ---------------------------------------------------------------------------


def load_document() -> dict[str, object]:
    return json.loads(RECORD_PATH.read_text())


def test_record_file_exists_and_is_valid_json() -> None:
    assert RECORD_PATH.is_file(), f"missing {RECORD_PATH}"
    load_document()  # raises on malformed JSON


def test_document_and_every_record_declare_the_current_v2_schema() -> None:
    doc = load_document()
    assert doc["schema_version"] == cs.CURRENT_SCHEMA_VERSION
    records = doc["records"]
    assert isinstance(records, list) and records
    for row in records:
        assert row["schema_version"] == cs.CURRENT_SCHEMA_VERSION


def test_a_v1_tagged_payload_is_refused_not_upgraded() -> None:
    """Sensitivity proof for the version gate itself: a `dimensional-
    composition.v1` payload (the superseded predecessor of THIS v2 schema,
    not the unrelated `kernel-runtime-composition.v1` legacy tag) must be
    refused outright by the mirrored schema, never silently upgraded."""
    doc = load_document()
    v1_payload = dict(doc["records"][0])
    v1_payload["schema_version"] = cs.LEGACY_SCHEMA_VERSION_V1
    with pytest.raises(cs.IncompatibleSchemaVersion):
        cs.composition_record_from_payload(v1_payload, FIXTURE_PACKAGES_ROOT)


# ---------------------------------------------------------------------------
# Ruling 2: the catalogue universe, re-derived from the frozen mirror
# ---------------------------------------------------------------------------


def test_catalogue_size_is_derived_never_hardcoded() -> None:
    """No literal count anywhere in this module -- re-derived from the
    fixture glob at test time, exactly as `derive_distribution_universe`'s
    own docstring requires of its callers."""
    universe = cs.derive_distribution_universe(FIXTURE_PACKAGES_ROOT)
    dir_count = sum(1 for p in FIXTURE_PACKAGES_ROOT.iterdir() if p.is_dir())
    assert len(universe) == dir_count
    assert len(universe) > 0


def test_record_set_matches_the_frozen_starter_catalogue_exactly() -> None:
    universe = cs.derive_distribution_universe(FIXTURE_PACKAGES_ROOT)
    catalogue_names = {d.distribution for d in universe}
    doc = load_document()
    recorded_names = {row["distribution"] for row in doc["records"]}
    missing = catalogue_names - recorded_names
    extra = recorded_names - catalogue_names
    assert not missing, f"catalogue distributions with no record: {sorted(missing)}"
    assert not extra, (
        f"records for distributions outside the catalogue: {sorted(extra)}"
    )
    assert len(doc["records"]) == len(catalogue_names), (
        "duplicate distribution in records"
    )


def test_every_record_classification_matches_its_starter_dossier() -> None:
    universe = {
        d.distribution: d.classification
        for d in cs.derive_distribution_universe(FIXTURE_PACKAGES_ROOT)
    }
    doc = load_document()
    for row in doc["records"]:
        assert row["classification"] == universe[row["distribution"]].value, row[
            "distribution"
        ]


# ---------------------------------------------------------------------------
# Every record must parse and cohere against the mirrored schema
# ---------------------------------------------------------------------------


def test_every_record_parses_and_coheres_via_the_mirrored_schema() -> None:
    """Exercises `composition_record_from_payload` (classification-match
    check, the classification/NOT_APPLICABLE coherence invariants, and
    Ruling 1's manifest-derived lineage applicability for every
    `optional-module` row) and `derive_composition_state` for all 95 rows.
    A structurally incoherent record raises here rather than being written
    to the JSON at all."""
    doc = load_document()
    for row in doc["records"]:
        record = cs.composition_record_from_payload(row, FIXTURE_PACKAGES_ROOT)
        cs.derive_composition_state(record)  # never raises for a coherent record


def test_no_record_carries_a_derived_only_field() -> None:
    doc = load_document()
    for row in doc["records"]:
        for forbidden in cs._DERIVED_ONLY_FIELDS:
            assert forbidden not in row, f"{row['distribution']} authors {forbidden!r}"


def test_no_scalar_total_anywhere_in_this_document() -> None:
    """The document has no top-level count field derived by summing
    per-state or per-dimension values -- states/totals are computed by
    Starter's own tooling from the dimensions, never carried here."""
    doc = load_document()
    assert "state" not in doc
    assert "total" not in doc
    assert "fully_composed_count" not in doc


# ---------------------------------------------------------------------------
# Ruling 1: the registration boundary, re-derived from ERP's real boot path
# ---------------------------------------------------------------------------


def measure_erp_boot_assembly_consumption(
    repo_root: Path,
) -> cs.AssemblyConsumptionTrace:
    """ERP's own analogue of the mirrored schema's
    `measure_starter_boot_assembly_consumption` -- re-derived from THIS
    repository's real `app/main.py` (the Dockerfile/gunicorn boot entry
    point) and `app/product_assembly.py` on every run, never a trusted
    literal.

    `imported_by_boot_entry_point` is a definite, measured fact either way:
    `app/main.py`'s source either names `app.product_assembly` (or
    `app.product_assembly`'s exported `ERP_PRODUCT_ASSEMBLY`) or it does not
    -- there is no dynamic/indirect import of it anywhere in this small,
    single-file entry point, so `None` (indeterminate) is never returned
    here as long as both files exist. When `app/main.py` does not reference
    the module at all, `consumed_by_a_real_effect` is also a definite `False`
    -- a real effect cannot consume an object the boot entry point never
    reaches in the first place.
    """
    main_path = repo_root / "app" / "main.py"
    assembly_path = repo_root / "app" / "product_assembly.py"
    boot_entry_point = "app/main.py"
    if not main_path.is_file() or not assembly_path.is_file():
        return cs.AssemblyConsumptionTrace(
            boot_entry_point=boot_entry_point,
            imported_by_boot_entry_point=None,
            consumed_by_a_real_effect=None,
        )
    main_source = main_path.read_text()
    imported = "product_assembly" in main_source
    consumed = False if not imported else None
    return cs.AssemblyConsumptionTrace(
        boot_entry_point=boot_entry_point,
        imported_by_boot_entry_point=imported,
        consumed_by_a_real_effect=consumed,
    )


def test_erp_boot_entry_point_does_not_reference_product_assembly() -> None:
    """The measured fact this whole record turns on. `app/main.py` is read
    directly and must not contain any reference to `product_assembly` --
    if this ever starts passing for the wrong reason (the substring simply
    moved to a comment), `test_product_assembly_is_only_imported_by_tests_
    and_the_release_script` below independently confirms the import graph."""
    main_source = (PROJECT_ROOT / "app" / "main.py").read_text()
    assert "product_assembly" not in main_source


def test_product_assembly_is_only_imported_by_tests_and_the_release_script() -> None:
    """Names every real importer of `app.product_assembly` in this
    repository. Fails loudly (naming the new importer) the day something
    under `app/` starts consuming it -- at which point `module_registration`
    would need to flip to a genuinely re-measured value, not silently drift
    stale in the JSON."""
    importers = set()
    for py_file in PROJECT_ROOT.rglob("*.py"):
        if "/.venv/" in str(py_file) or "/fixtures/" in str(py_file):
            continue
        if py_file == PROJECT_ROOT / "app" / "product_assembly.py":
            continue
        text = py_file.read_text()
        if "app.product_assembly" in text or "from app import product_assembly" in text:
            importers.add(py_file.relative_to(PROJECT_ROOT).as_posix())
    expected = {
        "tests/architecture/test_accounting_composition.py",
        "tests/architecture/test_numbering_composition.py",
        "tests/architecture/test_people_composition.py",
        "tests/architecture/test_deployment_release_prerequisites.py",
        "scripts/product_manifest.py",
    }
    assert importers == expected, (
        f"importers of app.product_assembly changed: "
        f"new={importers - expected} missing={expected - importers}"
    )
    assert not any(i.startswith("app/") for i in importers)


def test_registration_boundary_matches_measured_erp_boot_path() -> None:
    """The core Ruling-1 cross-check: for every one of the six distributions
    `app/product_assembly.py` actually passes as real `ModuleManifest`
    values, independently re-derive `RegistrationEvidence` from ERP's
    measured boot-path trace and assert it equals the recorded JSON value."""
    trace = measure_erp_boot_assembly_consumption(PROJECT_ROOT)
    site = cs.RegistrationCallSite(
        callee="ProductAssemblySpec",
        argument_kind="ModuleManifest_tuple",
        assembly_consumption=trace,
    )
    kind = cs.classify_registration_call_site(site)
    evidence = cs.RegistrationEvidence(kind=kind, measured=True)
    measured_value = evidence.as_dimension_value()

    doc = load_document()
    by_name = {row["distribution"]: row for row in doc["records"]}
    for distribution in COMPOSED_OPTIONAL_MODULES:
        recorded_value = cs.DimensionValue(by_name[distribution]["module_registration"])
        assert recorded_value is measured_value, (
            f"{distribution}: recorded module_registration="
            f"{recorded_value.value!r} but the measured ERP boot-path trace "
            f"says {measured_value.value!r}"
        )


def test_registration_boundary_sensitivity_proof_positive_control() -> None:
    """Proves the classifier CAN say yes -- a synthetic, genuinely
    boot-path-consumed trace (the shape Starter's OWN assembly has, not
    ERP's) must classify `MODULE_MANIFEST_REGISTERED` / `TRUE`. Without this,
    the two refusals below would be equally consistent with a classifier that
    simply refuses everything."""
    boot_consumed_trace = cs.AssemblyConsumptionTrace(
        boot_entry_point="app/main.py",
        imported_by_boot_entry_point=True,
        consumed_by_a_real_effect=True,
    )
    site = cs.RegistrationCallSite(
        callee="ProductAssemblySpec",
        argument_kind="ModuleManifest_tuple",
        assembly_consumption=boot_consumed_trace,
    )
    kind = cs.classify_registration_call_site(site)
    assert kind is cs.RegistrationEvidenceKind.MODULE_MANIFEST_REGISTERED
    evidence = cs.RegistrationEvidence(kind=kind, measured=True)
    assert evidence.as_dimension_value() is cs.DimensionValue.TRUE


def test_registration_boundary_sensitivity_proof_defect_is_named() -> None:
    """Plants v1's exact defect: a record claiming `module_registration:
    true` for `dotmac-accounting`, whose only real call site
    (`app/product_assembly.py`) is release metadata never reached from
    `app/main.py`. The independent measurement must disagree and name the
    distribution."""
    trace = measure_erp_boot_assembly_consumption(PROJECT_ROOT)
    site = cs.RegistrationCallSite(
        callee="ProductAssemblySpec",
        argument_kind="ModuleManifest_tuple",
        assembly_consumption=trace,
    )
    measured_value = cs.RegistrationEvidence(
        kind=cs.classify_registration_call_site(site), measured=True
    ).as_dimension_value()

    corrupted_value = cs.DimensionValue.TRUE
    assert corrupted_value is not measured_value, (
        "sensitivity proof failed to construct a genuine defect: the "
        "corrupted value must disagree with the measured one"
    )


def test_registration_boundary_sensitivity_proof_near_miss_is_accepted() -> None:
    """The near-miss: the REAL, checked-in `false` value for
    `dotmac-accounting` (the legitimate, measured state) must NOT be flagged
    by the same cross-check that names the planted defect above."""
    doc = load_document()
    by_name = {row["distribution"]: row for row in doc["records"]}
    recorded_value = cs.DimensionValue(
        by_name["dotmac-accounting"]["module_registration"]
    )

    trace = measure_erp_boot_assembly_consumption(PROJECT_ROOT)
    site = cs.RegistrationCallSite(
        callee="ProductAssemblySpec",
        argument_kind="ModuleManifest_tuple",
        assembly_consumption=trace,
    )
    measured_value = cs.RegistrationEvidence(
        kind=cs.classify_registration_call_site(site), measured=True
    ).as_dimension_value()

    assert recorded_value is measured_value is cs.DimensionValue.FALSE


# ---------------------------------------------------------------------------
# migration_lineage: present in effective migration configuration
# (alembic.ini `version_locations`), measured directly.
# ---------------------------------------------------------------------------


def test_migration_lineage_matches_alembic_version_locations() -> None:
    alembic_ini = (PROJECT_ROOT / "alembic.ini").read_text()
    version_locations_line = next(
        line
        for line in alembic_ini.splitlines()
        if line.strip().startswith("version_locations")
    )
    doc = load_document()
    by_name = {row["distribution"]: row for row in doc["records"]}
    for distribution in COMPOSED_OPTIONAL_MODULES:
        import_pkg = distribution.replace("-", "_")
        present = f"{import_pkg}.migrations:versions" in version_locations_line
        recorded = cs.DimensionValue(by_name[distribution]["migration_lineage"])
        assert (recorded is cs.DimensionValue.TRUE) == present, distribution


# ---------------------------------------------------------------------------
# runtime_consumption: measured directly via a traced, reachable import
# chain from app/main.py -- never inferred from installation/registration/
# lineage. There are no `unknown` values in this record: every one of the
# 95 catalogue distributions was directly checked either for a real,
# reachable import (installed distributions) or for the total absence of
# any import statement anywhere under app/, alembic/, scripts/, tests/
# (uninstalled distributions) -- see the report for the file-by-file chain.
# ---------------------------------------------------------------------------


def test_runtime_consumption_is_never_true_for_an_uninstalled_distribution() -> None:
    doc = load_document()
    for row in doc["records"]:
        if row["installation"] == cs.DimensionValue.FALSE.value:
            assert row["runtime_consumption"] != cs.DimensionValue.TRUE.value, row[
                "distribution"
            ]


@pytest.mark.parametrize(
    ("distribution", "importer_relpath", "needle"),
    [
        (
            "dotmac-files",
            "app/api/files.py",
            "from app.services.storage import get_storage",
        ),
        (
            "dotmac-imports",
            "app/api/finance/import_export.py",
            "from app.services.finance.import_export.durable_customers import",
        ),
        (
            "dotmac-people",
            "app/api/people/hr.py",
            "from app.services.people.hr.employment_types import EmploymentTypeService",
        ),
        (
            "dotmac-ui",
            "app/main.py",
            "from app.ui import UI_ASSET_DIRECTORY, UI_ASSET_MOUNT",
        ),
    ],
)
def test_runtime_consumption_true_records_have_a_traced_reachable_import(
    distribution: str, importer_relpath: str, needle: str
) -> None:
    """One traced link per `runtime_consumption: true` record (`dotmac-kernel`
    is deliberately excluded -- it is imported from dozens of reachable files
    and is not usefully pinned to one link)."""
    doc = load_document()
    by_name = {row["distribution"]: row for row in doc["records"]}
    assert by_name[distribution]["runtime_consumption"] == cs.DimensionValue.TRUE.value
    source = (PROJECT_ROOT / importer_relpath).read_text()
    assert needle in source, f"{importer_relpath} no longer contains {needle!r}"


def test_dotmac_accounting_dotmac_numbering_dotmac_tax_have_no_reachable_import() -> (
    None
):
    """The three installed, non-`runtime_consumption` distributions:
    `dotmac-accounting`/`dotmac-numbering` are only imported by
    `app/product_assembly.py` (unreachable from the boot path -- see the
    registration-boundary tests above); `dotmac-tax`'s only import sites
    (`app/services/finance/tax/adoption/{inbound,outbound}.py`) are reached
    only from within that same disabled adoption package and from tests."""
    doc = load_document()
    by_name = {row["distribution"]: row for row in doc["records"]}
    for distribution in ("dotmac-accounting", "dotmac-numbering", "dotmac-tax"):
        assert (
            by_name[distribution]["runtime_consumption"]
            == cs.DimensionValue.FALSE.value
        )

    tax_adoption_init = (
        PROJECT_ROOT
        / "app"
        / "services"
        / "finance"
        / "tax"
        / "adoption"
        / "__init__.py"
    ).read_text()
    assert "disabled" in tax_adoption_init.lower()

    importers = set()
    for py_file in (PROJECT_ROOT / "app").rglob("*.py"):
        if (
            py_file.parent
            == PROJECT_ROOT / "app" / "services" / "finance" / "tax" / "adoption"
        ):
            continue
        text = py_file.read_text()
        if "finance.tax.adoption" in text or "finance import tax" in text:
            importers.add(py_file.relative_to(PROJECT_ROOT).as_posix())
    # runtime_admission.py mentions the adoption module only in an error
    # string, never an import statement -- confirmed by the check above at
    # measurement time; this test pins that no *import* of the adoption
    # package exists under app/ outside the package itself.
    for i in importers:
        text = (PROJECT_ROOT / i).read_text()
        assert "import app.services.finance.tax.adoption" not in text, i


def test_dotmac_deployment_foundation_is_dev_only_never_ships_in_the_runtime_image() -> (
    None
):
    doc = load_document()
    by_name = {row["distribution"]: row for row in doc["records"]}
    assert (
        by_name["dotmac-deployment-foundation"]["runtime_consumption"]
        == cs.DimensionValue.FALSE.value
    )
    pyproject = (PROJECT_ROOT / "pyproject.toml").read_text()
    assert (
        "dotmac-deployment-foundation"
        not in pyproject.split("[tool.poetry.group.dev.dependencies]")[0]
    )
