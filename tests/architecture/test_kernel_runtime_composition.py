"""Architecture guard: `docs/kernel-runtime-composition.json` (v2).

Validates ERP's dimensional composition record against the mirrored
`dimensional-composition.v2` contract (`tests/architecture/composition_schema.py`,
verified byte-for-byte identical to `dotmac_starter_mt`'s protected-main
`tests/architecture/composition_schema.py` at revision `a9dc45ec` by a real
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
`tests/architecture/fixtures/starter_packages_a9dc45ec/` (measured
byte-for-byte identical in content to the prior `..._08a2dae1` mirror --
Starter made no commit touching any `packages/*/EXTRACTION.toml` or
`manifest.py` between the two revisions -- so this is a rename for
provenance honesty, not a re-derivation).

Every product's envelope (this document's top-level
`{schema_version, product, starter_catalogue_revision, records}` shape) is
now read through the ONE shared reader Starter's contract exports,
`cs.composition_records_from_envelope` -- this module keeps no local
envelope-shape logic of its own (no hand-rolled "exactly these four keys",
no hand-rolled duplicate-distribution or product-mismatch check). That
reader's own closed-shape refusal, duplicate-distribution refusal, and
row-product-mismatch refusal are exercised directly by
`test_shared_envelope_reader_ingests_the_real_document_without_refusal` and
the sensitivity proofs immediately below it -- not re-implemented here.

`installation` is derived MECHANICALLY from the contract's own lock-group
and install-recipe primitives -- `cs.derive_lock_group_membership` (read
from `poetry.lock`), `cs.derive_group_optionality` (read from
`pyproject.toml`), and `cs.parse_install_command` applied to the COMPLETE
logical `RUN` instruction of every `poetry install`/`poetry sync` recipe
found in ERP's own checked-in Dockerfiles (`Dockerfile`,
`Dockerfile.hardened` -- the latter carries two independent build stages,
each with its own recipe) -- never asserted, never grep'd, and never a
hand-built flag tuple (`InstallRecipe` has no public constructor; the only
way to get one is `cs.parse_install_command`). `runtime_consumption` is
derived from a real AST `Import`/`ImportFrom` reachability graph
(`tests/architecture/import_graph.py`) walked from ERP's declared production
entry points to the actual external package import -- never from an
intermediary "one file imports another file" needle, which a prior
revision of this suite used and which kept passing after the real
`from dotmac_files import ...` statement was deleted.
`test_runtime_consumption_sensitivity_proof_...` reproduces that exact
deletion against a scratch copy and shows the corrected test fails, then
restores it and shows the test passes again.

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

Sensitivity is proven, not assumed, and proven by calling the REAL
offender-finding function (`find_registration_mismatches`,
`find_installation_mismatches`) over a corrupted in-memory copy of the
record list -- never by re-deriving what that function "would" say and
asserting the re-derivation against itself, which is vacuous. The
`installation` sensitivity proof additionally mutates a REAL Dockerfile
(copied to a scratch path, never the checked-in file) to select a different
dependency profile and proves `cs.derive_installation_dimension` -- the
exact function the real check calls -- disagrees with the unchanged,
checked-in record; the companion near-miss proves the unmutated tree still
derives `false` for `dotmac-deployment-foundation` and `true` for
`dotmac-kernel`, so the check can say yes as well as refuse.
"""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from pathlib import Path

import pytest
from packaging.utils import canonicalize_name

from tests.architecture import composition_schema as cs
from tests.architecture import import_graph as ig

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RECORD_PATH = PROJECT_ROOT / "docs" / "kernel-runtime-composition.json"
FIXTURE_PACKAGES_ROOT = (
    Path(__file__).resolve().parent / "fixtures" / "starter_packages_a9dc45ec"
)
COMPOSITION_SCHEMA_MIRROR_PATH = (
    Path(__file__).resolve().parent / "composition_schema.py"
)

#: The git blob SHA of `tests/architecture/composition_schema.py` at
#: Starter protected-main `a9dc45ec` (`git hash-object` / `git rev-parse
#: a9dc45ec:tests/architecture/composition_schema.py`) -- the actual
#: cryptographic proof the mirror is byte-for-byte, not a prose claim. A
#: prior revision of this mirror carried a local header comment and two
#: ruff-format reflows and still claimed "byte-for-byte" in its own
#: docstring; only a real digest comparison catches that.
#:
#: PROVENANCE -- checked in here so both this constant and
#: `EXPECTED_STARTER_CATALOGUE_REVISION` below are RE-DERIVABLE from a
#: `dotmac_starter_mt` checkout, not merely asserted. Commands run directly
#: against Starter's own git history (never against this mirror computing
#: its own digest and comparing it to itself -- that would pass identically
#: whether the mirror was current or silently drifted):
#:
#:     $ git -C <starter-checkout> rev-parse a9dc45ec
#:     a9dc45ecd00d5a0163b6544278888220082e2e75
#:     $ git -C <starter-checkout> log --oneline -1 a9dc45ec
#:     a9dc45ec installation means the production profile, derived from
#:     lock groups and the recipe that selects them (#687)
#:     $ git -C <starter-checkout> merge-base --is-ancestor a9dc45ec origin/main
#:     $ echo $?
#:     0   # a9dc45ec is an ancestor of (at the time of this mirror, IS)
#:         # Starter's protected main
#:     $ git -C <starter-checkout> rev-parse \
#:         a9dc45ec:tests/architecture/composition_schema.py
#:     5e253827deea454bf9870f5900043010d43a5f71
#:
#: If this mirror is ever re-pinned to a newer Starter revision, update this
#: comment block, `STARTER_COMPOSITION_SCHEMA_BLOB_SHA`, and
#: `EXPECTED_STARTER_CATALOGUE_REVISION` together -- the three must always
#: name the same Starter commit.
STARTER_COMPOSITION_SCHEMA_BLOB_SHA = "5e253827deea454bf9870f5900043010d43a5f71"

#: ERP's declared production entry points for the AST import-reachability
#: graph `runtime_consumption` is measured against: the web application
#: (`app.main`, the gunicorn boot target), Celery's actual worker process
#: entry point (`app.celery_worker_entrypoint` -- `docker-compose.yml`'s
#: `worker` service runs `python -m app.celery_worker_entrypoint`, which
#: `os.execvp`s `celery -A app.celery_app`, not a bare `celery -A
#: app.celery_app` invocation directly), and `app.celery_app` itself (the
#: `-A` target that entry point execs into, and also Beat's declared
#: scheduler target) -- plus whatever `autodiscover_tasks([...])`'s own AST
#: `Call` node names as string roots (resolved below, never text-matched).
#:
#: This is NOT a claim of completeness over every way ERP code can run.
#: `gunicorn.conf.py` (imports `app.prometheus_multiprocess` at boot) lives
#: at the repository root, outside this module index's `app/` walk, and is
#: not included as a root; its closure is a subset of `app.main`'s own
#: reachable set regardless. `app/tools/` (nine one-off operator scripts,
#: e.g. `fix_stuck_paid_expense_claims.py`) and `alembic/` (migration
#: revisions) are REAL production-adjacent entry points this graph does not
#: walk from and are left explicitly UNMONITORED for `runtime_consumption`
#: purposes, per ADR-0018's framing (a stated gap, not a silently exempted
#: one) -- `pyproject.toml` per-file-ignores already treats both as a
#: different code-quality tier from `app/`'s own surface. No
#: `[project.scripts]`/`[tool.poetry.scripts]` table exists in
#: `pyproject.toml` (confirmed: `grep` returns nothing), so there is no
#: additional installed-console-script entry point to add.
PRODUCTION_ENTRY_POINT_MODULES = (
    "app.main",
    "app.celery_worker_entrypoint",
    "app.celery_app",
)


def composed_optional_modules() -> frozenset[str]:
    """The distributions `app/product_assembly.py` actually passes as real
    `ModuleManifest` values (`COMPOSED_MODULE_MANIFESTS`) -- the only place
    in this repository a `ModuleManifest` value is passed into an
    assembly-shaped object at all. DERIVED from that module's own
    `COMPOSED_MODULE_DISTRIBUTIONS` mapping, never a hand-maintained literal:
    a prior revision of this constant hard-coded the six distribution names,
    which meant a seventh composed module would be silently unmonitored on
    both `module_registration` and `migration_lineage` until someone
    remembered to update the set by hand.

    Imported LOCALLY, not at module scope, matching this repository's own
    convention (`test_accounting_composition.py`,
    `test_files_composition.py`) for reading `app.*` modules whose import
    graph reaches every composed distribution's real package -- doing that
    at module scope would make every test in THIS file collection-time
    dependent on all six being installed, rather than only the tests that
    actually need the derived set."""
    from app.product_assembly import COMPOSED_MODULE_DISTRIBUTIONS

    return frozenset(COMPOSED_MODULE_DISTRIBUTIONS.values())


#: ERP's checked-in build recipes, read directly -- never a hand-copied
#: install-line literal. `Dockerfile.hardened` carries two independent
#: `poetry install` stages (the Nuitka compiler stage and the production
#: runtime stage); both are real, deployed recipes and both are read.
DOCKERFILES = (
    PROJECT_ROOT / "Dockerfile",
    PROJECT_ROOT / "Dockerfile.hardened",
)

#: A RUN instruction invoking Poetry's installer, anywhere in the logical
#: (continuation-joined) instruction text -- used only to SELECT which of a
#: Dockerfile's many `RUN` instructions are handed to
#: `cs.parse_install_command` at all; every other `RUN` (`apt-get update`,
#: `npm ci`, ...) is not a recognised install-recipe shape and would be
#: refused outright by that parser's own grammar, so it is never offered to
#: it in the first place.
_POETRY_INSTALL_RUN_RE = re.compile(r"\bpoetry\s+(install|sync)\b")


# ---------------------------------------------------------------------------
# Envelope / schema-version loading
# ---------------------------------------------------------------------------


def load_document() -> dict[str, object]:
    return json.loads(RECORD_PATH.read_text())


def load_envelope_records() -> tuple[cs.CompositionRecord, ...]:
    """The one ingestion path this module uses: the shared contract reader,
    never a local envelope check. Exercises the closed envelope shape, the
    duplicate-distribution refusal, and the row/envelope product-agreement
    refusal on every real test run, because those are exactly what
    `cs.composition_records_from_envelope` itself enforces before returning
    a single record."""
    return cs.composition_records_from_envelope(load_document(), FIXTURE_PACKAGES_ROOT)


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
    protected-main `a9dc45ec`. A local header comment or a formatter reflow
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
        f"a9dc45ec's blob {STARTER_COMPOSITION_SCHEMA_BLOB_SHA} (got {digest}) "
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
# Cross-product envelope: ingested ENTIRELY through the shared contract
# reader, `cs.composition_records_from_envelope`. No local envelope-shape
# logic lives in this module -- the closed-shape check, the duplicate-
# distribution refusal, and the row/envelope product-agreement check are
# all that reader's own, exercised here by calling it over the real
# document and over deliberately corrupted copies.
# ---------------------------------------------------------------------------

EXPECTED_PRODUCT = "dotmac_erp"

#: Same Starter commit as `STARTER_COMPOSITION_SCHEMA_BLOB_SHA` above --
#: see that constant's PROVENANCE comment for the re-derivable `git`
#: commands (`rev-parse`, `log`, `merge-base --is-ancestor`) that produced
#: both this value and the blob digest from Starter's own history.
EXPECTED_STARTER_CATALOGUE_REVISION = "a9dc45ecd00d5a0163b6544278888220082e2e75"


def test_shared_envelope_reader_ingests_the_real_document_without_refusal() -> None:
    """The real, checked-in document parses cleanly through the one shared
    reader every product routes through -- the near-miss half of the
    sensitivity proofs below."""
    records = load_envelope_records()
    assert len(records) == 95
    by_distribution = {r.distribution: r for r in records}
    assert by_distribution["dotmac-deployment-foundation"].product == EXPECTED_PRODUCT
    assert by_distribution["dotmac-kernel"].product == EXPECTED_PRODUCT


def test_envelope_product_and_revision_match_the_expected_values() -> None:
    doc = load_document()
    assert doc["product"] == EXPECTED_PRODUCT
    assert doc["starter_catalogue_revision"] == EXPECTED_STARTER_CATALOGUE_REVISION
    assert re.match(r"^[0-9a-f]{40}$", doc["starter_catalogue_revision"])


def test_envelope_reader_sensitivity_proof_unknown_top_level_key_is_refused() -> None:
    """Plants the exact defect `cs.composition_records_from_envelope`'s own
    closed-shape check exists to catch: a stored count smuggled in under an
    unrecognized top-level key. Calls the REAL reader, not a re-derivation
    of what it would do."""
    doc = load_document()
    corrupted = dict(doc)
    corrupted["catalogue_size"] = len(doc["records"])
    with pytest.raises(cs.IncompatibleSchemaVersion):
        cs.composition_records_from_envelope(corrupted, FIXTURE_PACKAGES_ROOT)


def test_envelope_reader_sensitivity_proof_duplicate_distribution_is_refused() -> None:
    doc = load_document()
    corrupted = dict(doc)
    corrupted["records"] = [*doc["records"], doc["records"][0]]
    with pytest.raises(cs.EnvelopeIncoherence):
        cs.composition_records_from_envelope(corrupted, FIXTURE_PACKAGES_ROOT)


def test_envelope_reader_sensitivity_proof_row_product_mismatch_is_refused() -> None:
    """Near-miss companion to
    `test_shared_envelope_reader_ingests_the_real_document_without_refusal`:
    the real document's rows all agree with the envelope's `product` (this
    was NOT always true -- see the module's history of the
    `dotmac-erp`/`dotmac_erp` product-field defect, fixed directly in
    `docs/kernel-runtime-composition.json`); a planted disagreement on one
    row is refused by the real reader."""
    doc = load_document()
    records = [dict(r) for r in doc["records"]]
    records[0] = {**records[0], "product": "some-other-product"}
    corrupted = {**doc, "records": records}
    with pytest.raises(cs.EnvelopeIncoherence):
        cs.composition_records_from_envelope(corrupted, FIXTURE_PACKAGES_ROOT)


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
# Every record must parse and cohere via the shared envelope reader
# ---------------------------------------------------------------------------


def test_every_record_parses_and_coheres_via_the_shared_envelope_reader() -> None:
    """Exercises `cs.composition_records_from_envelope` end to end
    (envelope closed shape, every row's classification-match check, the
    classification/NOT_APPLICABLE coherence invariants, and Ruling 1's
    manifest-derived lineage applicability) and `derive_composition_state`
    for all 95 rows. A structurally incoherent record raises here rather
    than being written to the JSON at all."""
    records = load_envelope_records()
    for record in records:
        cs.derive_composition_state(record)  # never raises for a coherent record


def test_no_record_carries_a_derived_only_field() -> None:
    doc = load_document()
    for row in doc["records"]:
        for forbidden in cs._DERIVED_ONLY_FIELDS:
            assert forbidden not in row, f"{row['distribution']} authors {forbidden!r}"


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

    `imported_by_boot_entry_point` is derived by `ig.find_module_importers`
    (real `Import`/`ImportFrom` AST nodes), never a substring match -- a
    prior revision used `"product_assembly" in main_source`, which is
    exactly the defect class this whole suite exists to retire ("a
    substring is not a structure"). When `app/main.py` genuinely does not
    import it, `consumed_by_a_real_effect` is also a definite `False` -- a
    real effect cannot consume an object the boot entry point never
    reaches. When it DOES import it, this function does not attempt to
    prove whether the import is fed to a real effect (that would require
    tracing the call graph, not just the import graph) and reports
    `consumed_by_a_real_effect=None` -- an honest `INDETERMINATE`, never a
    guessed `True`. This means `AssemblyConsumptionKind.BOOT_PATH_CONSUMED`
    is NOT in this function's range today (it needs both facts `True`, and
    this function can only ever prove the first) -- see
    `test_measure_erp_boot_assembly_consumption_can_reach_indeterminate`,
    which proves the `imported=True` branch is real and reachable, not dead
    code, by pointing this function at a scratch tree that does import the
    assembly.
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
    imported = bool(
        ig.find_module_importers([main_path], repo_root, "app.product_assembly")
    )
    consumed = False if not imported else None
    return cs.AssemblyConsumptionTrace(
        boot_entry_point=boot_entry_point,
        imported_by_boot_entry_point=imported,
        consumed_by_a_real_effect=consumed,
    )


def test_measure_erp_boot_assembly_consumption_can_reach_indeterminate(
    tmp_path: Path,
) -> None:
    """A checker with no reachable "yes" is the same defect class as a
    checker that refuses everything. `measure_erp_boot_assembly_consumption`
    can never return `BOOT_PATH_CONSUMED` (see its docstring); this proves
    its other real branch -- `imported=True` -- is genuinely reachable and
    correctly reports `INDETERMINATE` (not a guessed `RELEASE_METADATA_ONLY`
    or a crash) rather than being dead code, by building a scratch tree
    whose `app/main.py` does contain a real `import app.product_assembly`."""
    scratch_app = tmp_path / "app"
    scratch_app.mkdir()
    (scratch_app / "product_assembly.py").write_text(
        "ERP_PRODUCT_ASSEMBLY = object()\n"
    )
    (scratch_app / "main.py").write_text("import app.product_assembly\n")

    trace = measure_erp_boot_assembly_consumption(tmp_path)
    assert trace.imported_by_boot_entry_point is True
    assert trace.consumed_by_a_real_effect is None
    assert trace.classify() is cs.AssemblyConsumptionKind.INDETERMINATE


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


def measured_registration_value() -> cs.DimensionValue:
    trace = measure_erp_boot_assembly_consumption(PROJECT_ROOT)
    site = cs.RegistrationCallSite(
        callee="ProductAssemblySpec",
        argument_kind="ModuleManifest_tuple",
        assembly_consumption=trace,
    )
    kind = cs.classify_registration_call_site(site)
    return cs.RegistrationEvidence(kind=kind, measured=True).as_dimension_value()


def find_registration_mismatches(
    records: list[dict[str, object]],
    measured_value: cs.DimensionValue,
    composed_modules: frozenset[str],
) -> list[str]:
    """The one function both the real check and its sensitivity proof call
    -- see `find_installation_mismatches`'s docstring for why that matters."""
    offenders = []
    for row in records:
        if row["distribution"] not in composed_modules:
            continue
        recorded = cs.DimensionValue(row["module_registration"])
        if recorded is not measured_value:
            offenders.append(row["distribution"])
    return offenders


def test_registration_boundary_matches_measured_erp_boot_path() -> None:
    """The core Ruling-1 cross-check: for every one of the six distributions
    `app/product_assembly.py` actually passes as real `ModuleManifest`
    values, independently re-derive `RegistrationEvidence` from ERP's
    measured boot-path trace and assert it equals the recorded JSON value."""
    measured_value = measured_registration_value()
    doc = load_document()
    offenders = find_registration_mismatches(
        doc["records"], measured_value, composed_optional_modules()
    )
    assert offenders == [], offenders


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


def test_registration_boundary_sensitivity_proof_defect_named_and_near_miss_accepted() -> (
    None
):
    """Plants v1's exact defect: flips `dotmac-accounting`'s
    `module_registration` to `true` in a corrupted in-memory copy of the
    RECORD LIST (whose only real call site, `app/product_assembly.py`, is
    release metadata never reached from `app/main.py`) and runs it through
    the real offender-finder -- not a re-derivation of what that function
    does, the function itself (a prior revision asserted `TRUE is not
    FALSE` by construction and never called it at all). The near-miss: the
    real, checked-in records produce zero offenders."""
    measured_value = measured_registration_value()
    doc = load_document()

    assert (
        find_registration_mismatches(
            doc["records"], measured_value, composed_optional_modules()
        )
        == []
    )  # near-miss

    corrupted_records = [
        {**row, "module_registration": cs.DimensionValue.TRUE.value}
        if row["distribution"] == "dotmac-accounting"
        else row
        for row in doc["records"]
    ]
    offenders = find_registration_mismatches(
        corrupted_records, measured_value, composed_optional_modules()
    )
    assert offenders == ["dotmac-accounting"], offenders


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
    for distribution in composed_optional_modules():
        import_pkg = distribution.replace("-", "_")
        present = f"{import_pkg}.migrations:versions" in version_locations_line
        recorded = cs.DimensionValue(by_name[distribution]["migration_lineage"])
        assert (recorded is cs.DimensionValue.TRUE) == present, distribution


# ---------------------------------------------------------------------------
# installation: derived MECHANICALLY through the shared contract's own
# lock-group, group-optionality and install-recipe primitives -- never
# asserted, never grep'd, and never a hand-built flag tuple. `installation`
# now means the PRODUCTION PROFILE: a dependency resolved only into a dev or
# tooling group and excluded from the deployed artifact is `false` even
# though it appears in `poetry.lock` (Michael's ruling -- see module
# docstring). A prior revision of this suite checked only whether a
# distribution resolved ANYWHERE in `poetry.lock`, main or dev alike, which
# is exactly the defect this derivation corrects.
# ---------------------------------------------------------------------------


def _extract_run_instructions(dockerfile_text: str) -> list[str]:
    """Split a Dockerfile's raw text into complete logical `RUN`
    instructions -- each returned string is the FULL multi-line instruction,
    backslash continuations and all, exactly as `cs.parse_install_command`
    itself expects (it joins continuations internally; this function's only
    job is finding where one instruction ends and the next begins, never
    pre-joining or pre-trimming the text it returns)."""
    lines = dockerfile_text.splitlines(keepends=True)
    instructions: list[str] = []
    current: list[str] | None = None
    for line in lines:
        stripped = line.rstrip("\n")
        if current is None:
            if stripped.lstrip().startswith("RUN"):
                current = [line]
                if not stripped.rstrip().endswith("\\"):
                    instructions.append("".join(current))
                    current = None
            continue
        current.append(line)
        if not stripped.rstrip().endswith("\\"):
            instructions.append("".join(current))
            current = None
    return instructions


def find_poetry_install_recipes(dockerfile_path: Path) -> tuple[cs.InstallRecipe, ...]:
    """Every `poetry install`/`poetry sync` recipe in one Dockerfile, as
    COMPLETE logical `RUN` instructions handed to `cs.parse_install_command`
    -- never a single hand-picked line out of a longer instruction.
    `_POETRY_INSTALL_RUN_RE` selects WHICH `RUN` instructions are offered to
    the parser at all; every other `RUN` (`apt-get update`, `npm ci`, ...)
    would be refused by the parser's own grammar and is never offered to
    it."""
    text = dockerfile_path.read_text()
    recipes = []
    for index, instruction in enumerate(_extract_run_instructions(text)):
        if not _POETRY_INSTALL_RUN_RE.search(instruction):
            continue
        recipes.append(
            cs.parse_install_command(
                instruction, source=f"{dockerfile_path.name}#{index}"
            )
        )
    return tuple(recipes)


#: The exact number of `poetry install`/`poetry sync` recipes checked-in
#: across every Dockerfile this validator reads -- one from `Dockerfile`'s
#: dependency-builder stage, and two from `Dockerfile.hardened`'s
#: independent nuitka-compiler and production stages. Pinned TWO-
#: DIRECTIONALLY (`==`, never `>=`) because `all_deployed_install_recipes`
#: previously only asserted the union was non-empty: since all three real
#: recipes happen to select the identical group set (`{"main"}`), silently
#: dropping `Dockerfile.hardened` from `DOCKERFILES` entirely -- or losing
#: one of its two stages -- left every test in this module green. This
#: constant, plus the per-Dockerfile assertion below, is what makes that an
#: observed fact rather than an asserted one: a dropped file/stage lowers
#: the total, a silently added one raises it, and either fails here before
#: any derivation runs.
EXPECTED_INSTALL_RECIPE_COUNT = 3


def all_deployed_install_recipes(
    dockerfiles: tuple[Path, ...] = DOCKERFILES,
) -> tuple[cs.InstallRecipe, ...]:
    """Every real, checked-in install recipe across every one of ERP's
    Dockerfiles -- `cs.derive_installation_group_universe` unions their
    selected groups; a dependency reaching only one of several deployed
    profiles is still installed (Michael's ruling). Asserts PER-DOCKERFILE
    that each named file yields at least one recipe (a file present in
    `dockerfiles` but contributing zero recipes is named, not silently
    absorbed into the union), and pins the TOTAL at
    `EXPECTED_INSTALL_RECIPE_COUNT` so a dropped file/stage or a silently
    added one is caught even when every recipe happens to select the same
    groups."""
    recipes: list[cs.InstallRecipe] = []
    for dockerfile in dockerfiles:
        found = find_poetry_install_recipes(dockerfile)
        assert found, f"no poetry install/sync recipe found in {dockerfile}"
        recipes.extend(found)
    assert len(recipes) == EXPECTED_INSTALL_RECIPE_COUNT, (
        f"expected exactly {EXPECTED_INSTALL_RECIPE_COUNT} checked-in poetry "
        f"install/sync recipes across {dockerfiles}, found {len(recipes)} -- "
        "a dropped Dockerfile/stage changes which production profile "
        "installation is derived against, even when the surviving recipes "
        "happen to select the same groups as before"
    )
    return tuple(recipes)


def load_lock_group_membership(project_root: Path) -> cs.LockGroupMembership | None:
    lock_document = tomllib.loads((project_root / "poetry.lock").read_text())
    return cs.derive_lock_group_membership(lock_document)


def load_group_optionality(project_root: Path) -> dict[str, bool] | None:
    pyproject_document = tomllib.loads((project_root / "pyproject.toml").read_text())
    return cs.derive_group_optionality(pyproject_document)


def find_installation_mismatches(
    records: list[dict[str, object]],
    *,
    lock_membership: cs.LockGroupMembership | None,
    recipes: tuple[cs.InstallRecipe, ...],
    group_optionality: dict[str, bool] | None,
) -> list[str]:
    """The one function both `test_installation_matches_derived_production_
    profile` and its sensitivity proof call -- never duplicated logic
    between "the real check" and "the thing the sensitivity proof asserts
    about"; that duplication is exactly how a prior revision's proof went
    vacuous (it asserted `TRUE is not FALSE` by construction and never
    called this function at all)."""
    offenders = []
    for row in records:
        expected = cs.derive_installation_dimension(
            distribution=str(row["distribution"]),
            lock_membership=lock_membership,
            recipes=recipes,
            group_optionality=group_optionality,
        )
        actual = cs.DimensionValue(row["installation"])
        if actual is not expected:
            offenders.append(row["distribution"])
    return offenders


def test_installation_matches_derived_production_profile() -> None:
    """Every one of the 95 catalogue rows' `installation` value, compared
    directly against `cs.derive_installation_dimension`'s PRODUCTION-PROFILE
    derivation -- the lock's group membership, `pyproject.toml`'s group
    optionality, and the union of every deployed Dockerfile's install
    recipe -- not whether the distribution merely resolved anywhere in
    `poetry.lock`."""
    lock_membership = load_lock_group_membership(PROJECT_ROOT)
    group_optionality = load_group_optionality(PROJECT_ROOT)
    recipes = all_deployed_install_recipes()
    doc = load_document()
    offenders = find_installation_mismatches(
        doc["records"],
        lock_membership=lock_membership,
        recipes=recipes,
        group_optionality=group_optionality,
    )
    assert offenders == [], offenders


def test_installation_names_are_already_pep503_normalized() -> None:
    """Pins the invariant the derivation relies on: every distribution name
    in the catalogue and every resolved `poetry.lock` name is ALREADY its
    own canonical form (Dotmac's naming convention never needed the
    underscore/dot/case folding PEP 503 exists to handle). If that ever
    stops being true, canonicalization is silently doing real work this
    test would otherwise never catch."""
    lock_membership = load_lock_group_membership(PROJECT_ROOT)
    assert lock_membership is not None
    for name in lock_membership.groups_by_distribution:
        assert canonicalize_name(name) == name, name
    doc = load_document()
    for row in doc["records"]:
        distribution = str(row["distribution"])
        assert canonicalize_name(distribution) == distribution, distribution


def test_installation_row_shape_carries_exactly_the_declared_fields() -> None:
    """D8: a planted extra key (e.g. `api_key`) on a record row is refused
    here even though the mirrored Starter contract currently accepts and
    silently drops unknown payload keys (Starter #686, merged as `b081ff73`,
    closes that half of the contract; this closes ERP's half in the
    meantime, and stays correct after the re-mirror since a stricter local
    check is never invalidated by a stricter upstream one) -- and is, in
    fact, now REDUNDANT with the mirrored contract's own closed-shape
    refusal in `cs.composition_record_from_payload` (exercised end to end by
    `load_envelope_records` above), which is exactly the intended outcome:
    this check was a temporary local patch pending the upstream fix, not a
    permanent second authority."""
    expected_keys = frozenset(cs.REQUIRED_PAYLOAD_FIELDS) | {"schema_version"}
    doc = load_document()
    for row in doc["records"]:
        assert set(row) == expected_keys, (
            f"{row['distribution']}: row keys {sorted(row)} != "
            f"expected {sorted(expected_keys)}"
        )


def test_installation_row_shape_sensitivity_proof_extra_key_is_refused() -> None:
    expected_keys = frozenset(cs.REQUIRED_PAYLOAD_FIELDS) | {"schema_version"}
    doc = load_document()
    planted = dict(doc["records"][0])
    planted["api_key"] = "sk_live_not_a_real_secret_but_would_be_refused"
    assert set(planted) != expected_keys

    # The mirrored contract's OWN closed-shape refusal independently catches
    # the identical plant -- not just this module's local check.
    with pytest.raises(cs.IncompatibleSchemaVersion):
        cs.composition_record_from_payload(planted, FIXTURE_PACKAGES_ROOT)


def test_installation_near_miss_unmutated_tree_derives_expected_foundation_and_kernel() -> (
    None
):
    """The near-miss half of the sensitivity proof below: over the REAL,
    unmutated Dockerfiles, `dotmac-deployment-foundation` (resolves only
    into the `dev` group in `poetry.lock`) derives `false`, and
    `dotmac-kernel` (resolves into `main`) derives `true` -- proving the
    derivation can say yes as well as refuse."""
    lock_membership = load_lock_group_membership(PROJECT_ROOT)
    group_optionality = load_group_optionality(PROJECT_ROOT)
    recipes = all_deployed_install_recipes()

    foundation = cs.derive_installation_dimension(
        distribution="dotmac-deployment-foundation",
        lock_membership=lock_membership,
        recipes=recipes,
        group_optionality=group_optionality,
    )
    kernel = cs.derive_installation_dimension(
        distribution="dotmac-kernel",
        lock_membership=lock_membership,
        recipes=recipes,
        group_optionality=group_optionality,
    )
    assert foundation is cs.DimensionValue.FALSE
    assert kernel is cs.DimensionValue.TRUE

    doc = load_document()
    by_name = {row["distribution"]: row for row in doc["records"]}
    assert by_name["dotmac-deployment-foundation"]["installation"] == "false"
    assert by_name["dotmac-kernel"]["installation"] == "true"


def test_installation_sensitivity_proof_a_mutated_recipe_disagrees_with_the_unchanged_record(
    tmp_path: Path,
) -> None:
    """The provenance plant: copy the REAL `Dockerfile` to a scratch path,
    rewrite its install line to select a different profile (`--with dev`
    instead of `--only main` -- now installing `dotmac-deployment-
    foundation`'s `dev` group too), and show the REAL function the real
    check calls (`cs.derive_installation_dimension`) disagrees with the
    checked-in, UNCHANGED record. This is the honest form of the proof: the
    record on disk is never touched, only the Dockerfile copy is -- proving
    the validator would catch stale evidence, not merely that two different
    inputs produce two different outputs.

    This mutates `Dockerfile` ONLY -- see
    `test_installation_sensitivity_proof_a_mutated_hardened_recipe_disagrees_
    with_the_unchanged_record` immediately below for the equivalent proof
    against `Dockerfile.hardened`, the file `EXPECTED_INSTALL_RECIPE_COUNT`
    exists to keep from being silently dropped."""
    real_dockerfile = PROJECT_ROOT / "Dockerfile"
    original_text = real_dockerfile.read_text()
    assert "poetry install --only main --no-root --no-ansi" in original_text

    mutated_text = original_text.replace(
        "poetry install --only main --no-root --no-ansi",
        "poetry install --with dev --no-root --no-ansi",
    )
    assert mutated_text != original_text

    scratch_dockerfile = tmp_path / "Dockerfile"
    scratch_dockerfile.write_text(mutated_text)

    lock_membership = load_lock_group_membership(PROJECT_ROOT)
    group_optionality = load_group_optionality(PROJECT_ROOT)
    mutated_recipes = find_poetry_install_recipes(scratch_dockerfile)
    assert mutated_recipes, "the mutated recipe must still parse"

    mutated_value = cs.derive_installation_dimension(
        distribution="dotmac-deployment-foundation",
        lock_membership=lock_membership,
        recipes=mutated_recipes,
        group_optionality=group_optionality,
    )
    assert mutated_value is cs.DimensionValue.TRUE, (
        "sensitivity proof failed: --with dev must select dotmac-deployment-"
        "foundation's dev group"
    )

    doc = load_document()
    recorded_row = next(
        row
        for row in doc["records"]
        if row["distribution"] == "dotmac-deployment-foundation"
    )
    recorded_value = cs.DimensionValue(recorded_row["installation"])
    assert recorded_value is cs.DimensionValue.FALSE  # the checked-in record, untouched

    # The actual proof: the real offender-finder, called over the mutated
    # recipe, names the checked-in record as a mismatch.
    offenders = find_installation_mismatches(
        [recorded_row],
        lock_membership=lock_membership,
        recipes=mutated_recipes,
        group_optionality=group_optionality,
    )
    assert offenders == ["dotmac-deployment-foundation"], offenders


def test_installation_sensitivity_proof_a_mutated_hardened_recipe_disagrees_with_the_unchanged_record(
    tmp_path: Path,
) -> None:
    """Companion to the `Dockerfile` provenance plant above, exercised
    against `Dockerfile.hardened` -- the file whose "two independent build
    stages, each with its own recipe" this module's constants claim as
    covered fact and `EXPECTED_INSTALL_RECIPE_COUNT` now enforces. Copies
    the REAL file, rewrites its FIRST (`nuitka-compiler` stage) `poetry
    install --only main --no-interaction --no-ansi` occurrence to select
    `--with dev` too, and shows the same disagreement the `Dockerfile` proof
    shows -- proving this file's coverage is exercised, not merely asserted
    in a docstring."""
    real_dockerfile = PROJECT_ROOT / "Dockerfile.hardened"
    original_text = real_dockerfile.read_text()
    target = "poetry install --only main --no-interaction --no-ansi"
    assert original_text.count(target) == 2, (
        "Dockerfile.hardened's two build-stage recipes have drifted from "
        "the exact shape this proof mutates -- update the target string"
    )

    mutated_text = original_text.replace(
        target, "poetry install --with dev --no-interaction --no-ansi", 1
    )
    assert mutated_text != original_text
    assert mutated_text.count(target) == 1, (
        "exactly one of the two occurrences must remain unmutated"
    )

    scratch_dockerfile = tmp_path / "Dockerfile.hardened"
    scratch_dockerfile.write_text(mutated_text)

    lock_membership = load_lock_group_membership(PROJECT_ROOT)
    group_optionality = load_group_optionality(PROJECT_ROOT)
    mutated_recipes = find_poetry_install_recipes(scratch_dockerfile)
    assert len(mutated_recipes) == 2, "both build-stage recipes must still parse"

    mutated_value = cs.derive_installation_dimension(
        distribution="dotmac-deployment-foundation",
        lock_membership=lock_membership,
        recipes=mutated_recipes,
        group_optionality=group_optionality,
    )
    assert mutated_value is cs.DimensionValue.TRUE, (
        "sensitivity proof failed: --with dev on one Dockerfile.hardened "
        "stage must select dotmac-deployment-foundation's dev group"
    )

    doc = load_document()
    recorded_row = next(
        row
        for row in doc["records"]
        if row["distribution"] == "dotmac-deployment-foundation"
    )
    recorded_value = cs.DimensionValue(recorded_row["installation"])
    assert recorded_value is cs.DimensionValue.FALSE  # the checked-in record, untouched

    offenders = find_installation_mismatches(
        [recorded_row],
        lock_membership=lock_membership,
        recipes=mutated_recipes,
        group_optionality=group_optionality,
    )
    assert offenders == ["dotmac-deployment-foundation"], offenders


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
    duplicated. Roots: `PRODUCTION_ENTRY_POINT_MODULES` (see that constant's
    docstring for exactly which entry points and why) plus whatever
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
    """Three independent, STRUCTURED facts, each measured by the function
    that actually owns it -- never a raw-substring position check. A prior
    revision tested "dev-only" by asserting the distribution's name did not
    appear in `pyproject.toml`'s text BEFORE the `[tool.poetry.group.dev.
    dependencies]` header string -- which proves only "not declared above
    that header" (a second group declared AFTER `dev` would pass the same
    check while genuinely not being dev-only) and reads no Dockerfile and no
    lock at all, so it could not have proven "never ships in the runtime
    image" either, despite the test's own name."""
    doc = load_document()
    by_name = {row["distribution"]: row for row in doc["records"]}
    assert (
        by_name["dotmac-deployment-foundation"]["runtime_consumption"]
        == cs.DimensionValue.FALSE.value
    )
    assert (
        by_name["dotmac-deployment-foundation"]["installation"]
        == cs.DimensionValue.FALSE.value
    )

    # "dev-only": the structured fact, straight from poetry.lock's own
    # group membership -- exactly, never a superset or a subset.
    lock_membership = load_lock_group_membership(PROJECT_ROOT)
    assert lock_membership is not None
    assert lock_membership.groups_by_distribution[
        "dotmac-deployment-foundation"
    ] == frozenset({"dev"})
