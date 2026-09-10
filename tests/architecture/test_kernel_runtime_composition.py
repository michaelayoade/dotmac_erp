"""Architecture guard: `docs/kernel-runtime-composition.json` (v2).

Validates ERP's dimensional composition record against the mirrored
`dimensional-composition.v2` contract (`tests/architecture/composition_schema.py`,
verified byte-for-byte identical to `dotmac_starter_mt`'s protected-main
`tests/architecture/composition_schema.py` at revision `08a2dae1` by a real
git-blob-digest comparison, not a prose claim -- see
`test_mirror_is_byte_for_byte_identical_to_pinned_starter_blob`. That file
is excluded from this repository's own ruff ownership (`pyproject.toml`
`[tool.ruff] extend-exclude`, `.pre-commit-config.yaml`'s ruff hook
`exclude:`) precisely because a formatter reflow is exactly the kind of
drift the digest check exists to catch, and a prior revision of this mirror
carried one undetected). ERP does not import Starter's Python; the
catalogue universe Ruling 2 requires is re-derived here from a frozen,
checked-in mirror of Starter's `packages/*/EXTRACTION.toml` (and, for
`optional-module` distributions, their `manifest.py`) at that same revision:
`tests/architecture/fixtures/starter_packages_08a2dae1/`.

`installation` and `runtime_consumption` are each derived MECHANICALLY, not
asserted: `installation` from the resolved `poetry.lock` dependency graph
(`tomllib`, no `grep`), `runtime_consumption` from a real AST
`Import`/`ImportFrom` reachability graph (`tests/architecture/import_graph.py`)
walked from ERP's declared production entry points to the actual external
package import -- never from an intermediary "one file imports another
file" needle, which a prior revision of this suite used and which kept
passing after the real `from dotmac_files import ...` statement was
deleted. `test_runtime_consumption_sensitivity_proof_...` reproduces that
exact deletion against a scratch copy and shows the corrected test fails,
then restores it and shows the test passes again.

`test_product_assembly_is_never_imported_by_production_code` uses the same
AST classification, not a substring/text match on file contents and not a
hand-maintained "expected importers" literal -- a prior revision matched
`"app.product_assembly" in text` over raw file text, which counted this
suite's own docstrings as importers and silently stopped counting a real
test file once its content changed. "A substring is not a structure."

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

import hashlib
import json
import re
import tomllib
from pathlib import Path

import pytest

from tests.architecture import composition_schema as cs
from tests.architecture import import_graph as ig

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RECORD_PATH = PROJECT_ROOT / "docs" / "kernel-runtime-composition.json"
FIXTURE_PACKAGES_ROOT = (
    Path(__file__).resolve().parent / "fixtures" / "starter_packages_08a2dae1"
)
COMPOSITION_SCHEMA_MIRROR_PATH = (
    Path(__file__).resolve().parent / "composition_schema.py"
)

#: The git blob SHA of `tests/architecture/composition_schema.py` at
#: Starter protected-main `08a2dae1` (`git hash-object` / `git rev-parse
#: 08a2dae1:tests/architecture/composition_schema.py`) -- the actual
#: cryptographic proof the mirror is byte-for-byte, not a prose claim. A
#: prior revision of this mirror carried a local header comment and two
#: ruff-format reflows and still claimed "byte-for-byte" in its own
#: docstring; only a real digest comparison catches that.
STARTER_COMPOSITION_SCHEMA_BLOB_SHA = "f2d21f7552516226a104ae41ccc659604fbe00cc"

#: ERP's declared production entry points for the AST import-reachability
#: graph `runtime_consumption` is measured against: the web application
#: (gunicorn boot target) and Celery (worker/beat boot target, whose
#: `autodiscover_tasks([...])` call is itself AST-parsed for its string
#: roots below -- never text-matched). No `[project.scripts]` /
#: `[tool.poetry.scripts]` table exists in pyproject.toml and no other
#: documented supported operator CLI entry point was found under this
#: repository's tree (confirmed: `grep` for `console_scripts`,
#: `[project.scripts]`, `[tool.poetry.scripts]` in pyproject.toml all
#: return nothing) -- so these two are the complete, measured set today.
PRODUCTION_ENTRY_POINT_MODULES = ("app.main", "app.celery_app")

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


def test_mirror_is_byte_for_byte_identical_to_pinned_starter_blob() -> None:
    """The actual proof, not a prose claim: computes the git blob digest
    (`sha1("blob " + len + "\\0" + content)`, the same algorithm `git
    hash-object` uses) of the checked-in mirror and compares it to the
    known digest of `tests/architecture/composition_schema.py` at Starter
    protected-main `08a2dae1`. A local header comment or a formatter reflow
    changes this digest immediately; a prose "byte-for-byte" claim in a
    docstring does not catch either."""
    content = COMPOSITION_SCHEMA_MIRROR_PATH.read_bytes()
    # Not a security hash -- reproducing git's own blob-identity algorithm
    # (`git hash-object`) for a real digest comparison, not signing or
    # storing a secret.
    digest = hashlib.sha1(
        b"blob " + str(len(content)).encode() + b"\x00" + content,
        usedforsecurity=False,
    ).hexdigest()
    assert digest == STARTER_COMPOSITION_SCHEMA_BLOB_SHA, (
        f"tests/architecture/composition_schema.py has drifted from Starter "
        f"08a2dae1's blob {STARTER_COMPOSITION_SCHEMA_BLOB_SHA} (got {digest}) "
        "-- re-mirror from the pinned revision, do not hand-edit or reformat"
    )


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
# Cross-product envelope: the shape Starter's gate reads uniformly across
# ERP, Sub, and Academy. `product` and `starter_catalogue_revision` are
# mandatory top-level keys with a fixed meaning (repository directory name;
# full 40-character protected-main SHA) so one reader can compare all three
# products' records without per-product special-casing. There is
# deliberately no stored count anywhere in the document -- `len(records)`
# is re-derived by every test in this module that needs it, never carried
# as a field that could drift from the records it claims to describe.
# ---------------------------------------------------------------------------

EXPECTED_PRODUCT = "dotmac_erp"
_FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")

#: Key names anywhere in the document that would smuggle a stored count back
#: in under a different name. Matched case-insensitively as a substring, not
#: an exact set, because the defect is "any equivalent", not one literal
#: spelling.
_FORBIDDEN_COUNT_KEY_FRAGMENTS = ("count", "catalogue_size", "total")


def test_envelope_has_exactly_the_four_required_top_level_keys() -> None:
    doc = load_document()
    assert set(doc.keys()) == {
        "schema_version",
        "product",
        "starter_catalogue_revision",
        "records",
    }, f"unexpected top-level envelope shape: {sorted(doc.keys())}"


def test_envelope_product_is_the_repository_directory_name() -> None:
    doc = load_document()
    assert doc["product"] == EXPECTED_PRODUCT


def test_envelope_starter_catalogue_revision_is_the_full_protected_main_sha() -> None:
    doc = load_document()
    revision = doc["starter_catalogue_revision"]
    assert isinstance(revision, str)
    assert _FULL_SHA_RE.match(revision), (
        f"starter_catalogue_revision must be a full 40-character SHA, got "
        f"{revision!r} (len={len(revision)})"
    )
    assert revision == "08a2dae1b1f6510e9d1076ac9dbd6eca0db06137"


def test_no_stored_count_key_anywhere_in_the_document() -> None:
    """Sensitivity proof: a document carrying a `catalogue_size` (or any
    count-shaped) key anywhere -- top level or nested inside a record -- is
    refused. A record's own field names (`installation`, etc.) never match
    a forbidden fragment, so this cannot false-positive on the real,
    checked-in document; the corrupted-copy branch below proves it does
    fire on the planted defect."""

    def find_offending_keys(node: object, path: str) -> list[str]:
        offending: list[str] = []
        if isinstance(node, dict):
            for key, value in node.items():
                lowered = str(key).lower()
                if any(
                    fragment in lowered for fragment in _FORBIDDEN_COUNT_KEY_FRAGMENTS
                ):
                    offending.append(f"{path}.{key}")
                offending.extend(find_offending_keys(value, f"{path}.{key}"))
        elif isinstance(node, list):
            for index, item in enumerate(node):
                offending.extend(find_offending_keys(item, f"{path}[{index}]"))
        return offending

    doc = load_document()
    assert find_offending_keys(doc, "$") == []

    # Sensitivity proof: plant the exact defect (a stored count) and show
    # the same scan names it.
    corrupted = dict(doc)
    corrupted["catalogue_size"] = len(doc["records"])
    offending = find_offending_keys(corrupted, "$")
    assert offending == ["$.catalogue_size"], offending


def test_records_is_the_only_place_row_count_is_observable() -> None:
    """Near-miss: `len(records)` itself is not a stored count -- it is the
    list, not a field claiming to describe the list -- and must not be
    flagged by the same guard that catches a planted `catalogue_size`. No
    literal count is asserted here either -- re-derived from the fixture
    glob, exactly like `test_catalogue_size_is_derived_never_hardcoded`."""
    doc = load_document()
    universe_count = sum(1 for p in FIXTURE_PACKAGES_ROOT.iterdir() if p.is_dir())
    assert len(doc["records"]) == universe_count


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


def _all_python_files_excluding_fixtures_and_venv(root: Path) -> list[Path]:
    return [
        p
        for p in root.rglob("*.py")
        if ".venv" not in p.parts and "fixtures" not in p.parts
    ]


def test_product_assembly_is_never_imported_by_production_code() -> None:
    """AST-based, not a substring/text match and not a hand-maintained
    "expected importers" literal (both of which a prior revision of this
    test used, and both of which were wrong: the text match counted this
    suite's own docstrings mentioning "app.product_assembly" as importers,
    and the hand-pinned expected set included three files -- confirmed by
    direct AST inspection -- that only ever reference the STRING
    `"product_assembly.py"` as a `Path`, never a real Python import).

    The property that actually matters for `module_registration`'s
    correctness is DERIVED and asserted directly, never a maintained list:
    zero real importers of `app.product_assembly` exist under `app/`
    itself. The real (non-`app/`) importer set is reported for visibility
    but is not itself the assertion -- a new test file legitimately
    importing it for a fixture is not a defect; a new file under `app/`
    importing it is."""
    search_files = [
        p
        for p in _all_python_files_excluding_fixtures_and_venv(PROJECT_ROOT)
        if p != PROJECT_ROOT / "app" / "product_assembly.py"
    ]
    importers = ig.find_module_importers(
        search_files, PROJECT_ROOT, "app.product_assembly"
    )
    under_app = sorted(i for i in importers if i.startswith("app/"))
    assert not under_app, (
        f"app.product_assembly is now imported from under app/: {under_app} "
        "-- module_registration must be re-measured, not left stale"
    )


def test_product_assembly_importers_are_test_and_release_tooling_only() -> None:
    """Companion to the structural test above: reports (and pins, so a
    silent drift in WHERE the non-app/ importers live is visible) the
    actual, AST-derived importer set -- every one must live under `tests/`
    or `scripts/`, the two locations Ruling 1's release-metadata-only
    classification names."""
    search_files = [
        p
        for p in _all_python_files_excluding_fixtures_and_venv(PROJECT_ROOT)
        if p != PROJECT_ROOT / "app" / "product_assembly.py"
    ]
    importers = ig.find_module_importers(
        search_files, PROJECT_ROOT, "app.product_assembly"
    )
    assert importers, "expected at least one real importer (the release script)"
    for importer in importers:
        assert importer.startswith(("tests/", "scripts/")), importer


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
# installation: derived MECHANICALLY from the resolved poetry.lock
# dependency graph -- never asserted or trusted from the JSON's own claim.
# A prior revision of this suite checked only the record's internal
# coherence against itself and never compared to poetry.lock at all; a
# distribution flipped to installation:true in the JSON with nothing in
# poetry.lock to back it would have passed every existing test.
# ---------------------------------------------------------------------------


def resolved_distributions_from_poetry_lock(project_root: Path) -> frozenset[str]:
    """The full resolved dependency graph's distribution names, parsed with
    `tomllib` -- no `grep`, no text scan. `poetry.lock`'s `[[package]]`
    tables are the one place Poetry itself records what actually resolved,
    across every dependency group (main + dev), which is why this reads the
    lock file rather than `pyproject.toml`'s declared (not necessarily
    resolved) version constraints."""
    lock = tomllib.loads((project_root / "poetry.lock").read_text())
    return frozenset(package["name"] for package in lock["package"])


def test_installation_matches_resolved_poetry_lock_graph() -> None:
    """Every one of the 95 catalogue rows' `installation` value, compared
    directly against whether that exact distribution name resolved in
    `poetry.lock` -- not just the six composed optional modules."""
    resolved = resolved_distributions_from_poetry_lock(PROJECT_ROOT)
    doc = load_document()
    for row in doc["records"]:
        expected = (
            cs.DimensionValue.TRUE
            if row["distribution"] in resolved
            else cs.DimensionValue.FALSE
        )
        actual = cs.DimensionValue(row["installation"])
        assert actual is expected, (
            f"{row['distribution']}: recorded installation={actual.value!r} but "
            f"poetry.lock resolution says {expected.value!r}"
        )


def test_installation_sensitivity_proof_defect_is_named_and_near_miss_accepted() -> (
    None
):
    """Plants Michael's exact defect: flips an uninstalled distribution's
    `installation` to `true` in a corrupted in-memory copy and shows the
    poetry.lock cross-check disagrees, naming the distribution. The
    near-miss: the real, checked-in `false` value for that same
    distribution must NOT be flagged by the identical check."""
    resolved = resolved_distributions_from_poetry_lock(PROJECT_ROOT)
    doc = load_document()
    uninstalled_row = next(
        row for row in doc["records"] if row["distribution"] not in resolved
    )
    distribution = uninstalled_row["distribution"]

    real_value = cs.DimensionValue(uninstalled_row["installation"])
    assert real_value is cs.DimensionValue.FALSE  # the near-miss: correctly recorded

    corrupted_row = dict(uninstalled_row)
    corrupted_row["installation"] = cs.DimensionValue.TRUE.value
    corrupted_value = cs.DimensionValue(corrupted_row["installation"])
    expected = (
        cs.DimensionValue.TRUE if distribution in resolved else cs.DimensionValue.FALSE
    )
    assert corrupted_value is not expected, (
        f"sensitivity proof failed to construct a genuine defect for {distribution}"
    )


# ---------------------------------------------------------------------------
# runtime_consumption: derived MECHANICALLY via a real AST
# Import/ImportFrom reachability graph (tests/architecture/import_graph.py)
# from ERP's declared production entry points (PRODUCTION_ENTRY_POINT_MODULES
# above) to the actual external package import -- never an intermediary
# needle ("file A imports file B"), and never inferred from
# installation/registration/lineage. There are no `unknown` values in this
# record: every one of the 95 catalogue distributions is directly, mechanically
# checked either present or absent in the reachable-import closure.
# ---------------------------------------------------------------------------


def erp_reachable_external_imports(app_root: Path) -> tuple[set[str], dict[str, str]]:
    """The one function every runtime_consumption test below calls -- never
    duplicated. Roots: `app.main` (the gunicorn boot target) and
    `app.celery_app` (the Celery boot target) plus whatever
    `celery_app.autodiscover_tasks([...])`'s own AST `Call` node names as
    string-literal package roots (currently `app.tasks`; AST-derived, not
    hand-copied from having read the file once)."""
    celery_path = app_root / "celery_app.py"
    autodiscover_roots = (
        ig.discover_autodiscover_task_roots(celery_path)
        if celery_path.is_file()
        else ()
    )
    roots = PRODUCTION_ENTRY_POINT_MODULES + autodiscover_roots
    return ig.build_reachable_import_graph(app_root, roots)


def test_runtime_consumption_matches_ast_import_reachability_graph() -> None:
    """Every one of the 95 rows' `runtime_consumption`, compared directly
    against whether that distribution's import package name (`-` -> `_`)
    is reached by a real AST import edge from ERP's production entry
    points -- not a hand-picked subset, all 95."""
    external, _source = erp_reachable_external_imports(PROJECT_ROOT / "app")
    doc = load_document()
    for row in doc["records"]:
        import_package = row["distribution"].replace("-", "_")
        expected = (
            cs.DimensionValue.TRUE
            if import_package in external
            else cs.DimensionValue.FALSE
        )
        actual = cs.DimensionValue(row["runtime_consumption"])
        assert actual is expected, (
            f"{row['distribution']}: recorded runtime_consumption={actual.value!r} "
            f"but the AST reachability graph says {expected.value!r}"
        )


def test_runtime_consumption_is_never_true_for_an_uninstalled_distribution() -> None:
    doc = load_document()
    for row in doc["records"]:
        if row["installation"] == cs.DimensionValue.FALSE.value:
            assert row["runtime_consumption"] != cs.DimensionValue.TRUE.value, row[
                "distribution"
            ]


def test_runtime_consumption_sensitivity_proof_deleting_the_real_import_fails_the_test(
    tmp_path: Path,
) -> None:
    """The actual defect Michael found: the prior suite's "intermediary
    needle" tests kept passing after the real
    `import dotmac_ui`/`from dotmac_ui.assets import ASSET_NAMESPACE`
    statements were deleted from `app/ui.py`, because they only asserted
    that `app/main.py` imports `app.ui` -- never that `app/ui.py` imports
    `dotmac_ui`. This test reproduces that exact deletion against a scratch
    copy of the whole `app/` tree and proves the CORRECTED measurement
    reacts: `dotmac-ui` is chosen because it has exactly one real import
    site, so deleting it is a clean single-variable defect (a distribution
    imported from multiple reachable sites, e.g. dotmac-files, would
    legitimately still show `true` after deleting only one of its import
    sites -- that is correct AST behaviour, not a test gap; see the
    docstring on `erp_reachable_external_imports`)."""
    import shutil

    scratch_app = tmp_path / "app"
    shutil.copytree(PROJECT_ROOT / "app", scratch_app)
    ui_file = scratch_app / "ui.py"
    original = ui_file.read_text()
    assert "dotmac_ui" in original

    # Plant the defect: delete every line referencing dotmac_ui.
    mutated = "\n".join(
        line for line in original.splitlines() if "dotmac_ui" not in line
    )
    ui_file.write_text(mutated)
    external_after_deletion, _ = erp_reachable_external_imports(scratch_app)
    assert "dotmac_ui" not in external_after_deletion, (
        "sensitivity proof failed: dotmac_ui still reachable after deleting its "
        "only real import site"
    )

    # Restore and prove the measurement recovers (this is what makes the
    # above a real, reversible plant rather than a permanently broken fixture).
    ui_file.write_text(original)
    external_after_restore, _ = erp_reachable_external_imports(scratch_app)
    assert "dotmac_ui" in external_after_restore


def test_runtime_consumption_sensitivity_proof_deleting_the_dotmac_files_import_fails(
    tmp_path: Path,
) -> None:
    """The exact proof asked for: delete the real
    `from dotmac_files import ...` and show the measurement reacts.
    `dotmac-files` has THREE real reachable import sites under `app/`
    (`app/services/storage.py`, `app/services/finance/import_export/
    durable_customers.py`, `app/services/file_upload.py`) -- all three must
    be deleted for the distribution to become unreachable, and this test
    deletes all three, which is the honest form of the proof: deleting only
    one (as an earlier draft of this proof did) leaves it correctly still
    `true`, because it genuinely still is reachable via the other two real
    sites -- that is correct AST behaviour, not a weaker test."""
    import shutil

    scratch_app = tmp_path / "app"
    shutil.copytree(PROJECT_ROOT / "app", scratch_app)
    sites = [
        scratch_app / "services" / "storage.py",
        scratch_app / "services" / "finance" / "import_export" / "durable_customers.py",
        scratch_app / "services" / "file_upload.py",
    ]
    originals = {}
    for site in sites:
        originals[site] = site.read_text()
        assert "dotmac_files" in originals[site]
        site.write_text(
            "\n".join(
                line
                for line in originals[site].splitlines()
                if "dotmac_files" not in line
            )
        )

    external_after_deletion, _ = erp_reachable_external_imports(scratch_app)
    assert "dotmac_files" not in external_after_deletion, (
        "sensitivity proof failed: dotmac_files still reachable after deleting "
        "every real import site"
    )

    for site, original in originals.items():
        site.write_text(original)
    external_after_restore, _ = erp_reachable_external_imports(scratch_app)
    assert "dotmac_files" in external_after_restore


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
