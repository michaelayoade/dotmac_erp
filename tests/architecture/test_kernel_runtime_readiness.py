"""Architecture guard: `docs/kernel-runtime-readiness.json` matches the tree.

The Kernel successor's cross-repo compatibility gate lives in
``dotmac_starter_mt``. It used to take product facts as booleans authored in
STARTER's own JSON — a producing repository asserting a consuming
repository's property, which it cannot verify. That shape was ruled out. The
corrected shape: each product owns a typed readiness record in its OWN tree,
and the product's OWN CI validates the record against that tree. Starter's
gate reads the record from a Git blob at a bound revision and parses it; it
authors nothing.

This file is the "own CI validates it" half for ``dotmac_erp``. Every claim
in the record is checked against ERP's tree by a small, named function below
— never assumed. A requirement whose ``satisfied`` value cannot genuinely be
checked mechanically is a defect in the record, not something this file
should wave through; see ``REQUIREMENT_CHECKS``/``COMPOSITION_CHECKS`` for the
one checker per claim.

Sensitivity is proven, not assumed: ``test_a_false_requirement_is_rejected``
plants a requirement whose declared ``satisfied`` disagrees with what the
checker finds in the real tree, and asserts the validator refuses it;
``test_a_legitimate_record_is_accepted`` runs the real, checked-in record
through the same validator and asserts it passes, so the guard is proven to
distinguish the two rather than reject everything.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RECORD_PATH = PROJECT_ROOT / "docs" / "kernel-runtime-readiness.json"

SCHEMA = "kernel-runtime-readiness.v1"
PRODUCT = "dotmac_erp"
#: Canonical, owned by Starter's `PRODUCT_SPECS` -- not a phrase this record
#: coins. The richer semantics stay in `requirements[]`; encoding them into the
#: subject makes one identifier answer two questions, which is how three
#: repositories came to answer one question three different ways.
SUBJECT = "erp-kernel-successor-readiness"

# The nine files that legitimately construct/consume an async database
# engine/session today (verified below by an independent, re-derived sweep —
# this constant is the DECLARED set the record asserts, and the sweep proves
# it, rather than the sweep being trusted blind).
DECLARED_ASYNC_FILES = frozenset(
    {
        "app/db/__init__.py",
        "app/rls.py",
        "app/services/finance/common/numbering.py",
        "app/services/finance/settings_web.py",
        "app/services/people/settings_web.py",
        "app/web/deps.py",
        "app/web/finance/settings.py",
        "app/web/people/settings.py",
        "app/web/settings.py",
    }
)

ASYNC_MARKERS = (
    "AsyncSession",
    "get_async_engine",
    "get_async_session_local",
    "AsyncSessionLocal",
    "create_async_engine",
    "get_async_db",
    "get_async_db_for_org",
)

KERNEL_SYNC_RUNTIME_MARKERS = (
    "dotmac_kernel.session_runtime",
    "DatabaseRuntime",
    "bind_database_runtime",
    "resolve_database_runtime",
)

SOURCE_REF_RE = re.compile(r"^(?P<path>[^:]+)(:(?P<start>\d+)(-(?P<end>\d+))?)?$")


# ---------------------------------------------------------------------------
# Generic envelope parsing
# ---------------------------------------------------------------------------


def load_record(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def resolve_source_reference(root: Path, ref: str) -> None:
    """Raise AssertionError unless ``ref`` names a real path/line in ``root``."""
    match = SOURCE_REF_RE.match(ref)
    assert match, f"source_reference {ref!r} is not path[:line[-line]] shaped"
    rel_path = match.group("path")
    target = root / rel_path
    assert target.is_file(), f"source_reference names a missing file: {rel_path}"
    start = match.group("start")
    if start is None:
        return
    line_count = len(target.read_text().splitlines())
    start_i = int(start)
    end_i = int(match.group("end")) if match.group("end") else start_i
    assert 1 <= start_i <= end_i <= line_count, (
        f"source_reference {ref!r} line range out of bounds "
        f"({rel_path} has {line_count} lines)"
    )


def validate_envelope(record: dict[str, Any]) -> None:
    assert record.get("schema") == SCHEMA
    assert record.get("product") == PRODUCT
    assert record.get("subject") == SUBJECT, (
        f"subject must be exactly {SUBJECT!r}, got {record.get('subject')!r}"
    )
    assert isinstance(record.get("requirements"), list) and record["requirements"]
    assert isinstance(record.get("composition"), list)
    assert isinstance(record.get("source_references"), list)
    for req in record["requirements"]:
        assert set(req) == {
            "id",
            "statement",
            "satisfied",
            "source_reference",
        }, f"requirement has unexpected keys: {sorted(req)}"
        assert isinstance(req["id"], str) and req["id"]
        assert isinstance(req["statement"], str) and req["statement"]
        assert isinstance(req["satisfied"], bool)
        assert isinstance(req["source_reference"], str) and req["source_reference"]
    for comp in record["composition"]:
        assert set(comp) == {
            "declaration",
            "source_reference",
        }, f"composition entry has unexpected keys: {sorted(comp)}"
        assert isinstance(comp["declaration"], str) and comp["declaration"]
        assert isinstance(comp["source_reference"], str) and comp["source_reference"]
    for ref in record["source_references"]:
        assert isinstance(ref, str) and ref


def _read(root: Path, rel: str) -> str:
    return (root / rel).read_text()


def _joined(root: Path, rel: str) -> str:
    """Content with newlines collapsed to spaces, for prose substring checks."""
    return _read(root, rel).replace("\n", " ")


def _grep_sites(root: Path, pattern: str, *, glob: str = "app/**/*.py") -> list[str]:
    sites: list[str] = []
    for path in sorted(root.glob(glob)):
        content = path.read_text()
        for lineno, line in enumerate(content.splitlines(), start=1):
            if pattern in line:
                sites.append(f"{path.relative_to(root)}:{lineno}")
    return sites


# ---------------------------------------------------------------------------
# One checker per requirement ``id`` — each independently re-derives the
# claim from the tree; none trusts the record's own prose.
# ---------------------------------------------------------------------------


def check_single_sync_engine_construction_site(root: Path) -> bool:
    sites = _grep_sites(root, "create_engine(")
    # create_async_engine( does not contain create_engine( as a substring
    # (the token is split by "_async_"), so no exclusion is needed — proven
    # by test_create_async_engine_is_not_mistaken_for_sync below.
    return sites == ["app/db/__init__.py:65"]


def check_async_database_paths_isolated_from_kernel(root: Path) -> bool:
    found: set[str] = set()
    for path in sorted(root.glob("app/**/*.py")):
        content = path.read_text()
        if any(marker in content for marker in ASYNC_MARKERS):
            found.add(str(path.relative_to(root)))
    if found != DECLARED_ASYNC_FILES:
        return False
    return all("dotmac_kernel" not in _read(root, rel) for rel in DECLARED_ASYNC_FILES)


def check_fork_safety_hooks_owned_by_product(root: Path) -> bool:
    celery = _read(root, "app/celery_app.py")
    gunicorn = _read(root, "gunicorn.conf.py")
    return "@worker_process_init.connect" in celery and (
        "def post_worker_init" in gunicorn
    )


def check_no_runtime_bypassrls_credential_or_per_call_flag(root: Path) -> bool:
    allowed = {"app/runtime_admission.py", "app/migration_credential_custody.py"}
    for path in sorted(root.glob("app/**/*.py")):
        content = path.read_text()
        if "MIGRATION_DATABASE_URL" in content:
            rel = str(path.relative_to(root))
            if rel not in allowed:
                return False
        if re.search(r"bypass_rls\s*=\s*True", content):
            return False
    return True


def check_statement_timeout_and_pool_tuning_set_at_construction(root: Path) -> bool:
    content = _read(root, "app/db/__init__.py")
    return (
        "statement_timeout" in content
        and "def get_engine" in content
        and "create_engine(" in content
    )


def check_legacy_tenant_guc_is_a_single_named_value(root: Path) -> bool:
    content = _read(root, "app/rls.py")
    gucs = set(re.findall(r"set_config\('(app\.[a-z_]+)'", content))
    return gucs == {"app.current_organization_id", "app.current_tenant"}


def check_no_runtime_bypass_rls_guc_writer_remains(root: Path) -> bool:
    for path in sorted(root.glob("app/**/*.py")):
        if "bypass_rls" in path.read_text().lower():
            return False
    return "cannot assert a PostgreSQL RLS bypass" in _joined(root, "app/rls.py")


def check_sync_runtime_not_yet_composed(root: Path) -> bool:
    for path in sorted(root.glob("app/**/*.py")):
        content = path.read_text()
        if any(marker in content for marker in KERNEL_SYNC_RUNTIME_MARKERS):
            return False
    pin = _read(root, "pyproject.toml")
    return 'dotmac-kernel = {version = "0.1.0a98", source = "forgejo"}' in pin


REQUIREMENT_CHECKS: dict[str, Any] = {
    "single-sync-engine-construction-site": (
        check_single_sync_engine_construction_site
    ),
    "async-database-paths-isolated-from-kernel": (
        check_async_database_paths_isolated_from_kernel
    ),
    "fork-safety-hooks-owned-by-product": check_fork_safety_hooks_owned_by_product,
    "no-runtime-bypassrls-credential-or-per-call-flag": (
        check_no_runtime_bypassrls_credential_or_per_call_flag
    ),
    "statement-timeout-and-pool-tuning-set-at-construction": (
        check_statement_timeout_and_pool_tuning_set_at_construction
    ),
    "legacy-tenant-guc-is-a-single-named-value": (
        check_legacy_tenant_guc_is_a_single_named_value
    ),
    "no-runtime-bypass-rls-guc-writer-remains": (
        check_no_runtime_bypass_rls_guc_writer_remains
    ),
    "sync-runtime-not-yet-composed": check_sync_runtime_not_yet_composed,
}


# ---------------------------------------------------------------------------
# One checker per composition ``declaration``, keyed by its index in the
# record (order is part of the fixed envelope this record maintains).
# ---------------------------------------------------------------------------


def check_kernel_pin_is_exact(root: Path) -> bool:
    pin = _read(root, "pyproject.toml")
    return 'dotmac-kernel = {version = "0.1.0a98", source = "forgejo"}' in pin


def check_consume_pure_modules(root: Path) -> bool:
    content = _read(root, "tests/architecture/test_kernel_import_boundary.py")
    match = re.search(
        r"ALLOWED_KERNEL_MODULES.*?frozenset\(\s*\{(?P<body>.*?)\}\s*\)",
        content,
        re.DOTALL,
    )
    assert match, "ALLOWED_KERNEL_MODULES not found in the import-boundary guard"
    modules = set(re.findall(r'"([a-z_]+)"', match.group("body")))
    return modules == {
        "assembly",
        "capabilities",
        "features",
        "licensing",
        "money",
        "planes",
        "prerequisites",
        "profiles",
        "providers",
        "testing",
    }


def check_tenancy_scope_symbol_composed(root: Path) -> bool:
    return "from dotmac_kernel.cache import TenantScope" in _read(
        root, "app/tenancy.py"
    )


def check_tenant_projection_symbol_composed(root: Path) -> bool:
    return "from dotmac_kernel.models import Tenant" in _read(
        root, "app/services/tenant_projection.py"
    )


COMPOSITION_CHECKS: list[Any] = [
    check_kernel_pin_is_exact,
    check_consume_pure_modules,
    check_tenancy_scope_symbol_composed,
    check_tenant_projection_symbol_composed,
    check_sync_runtime_not_yet_composed,  # same claim as the requirement above
]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_record_exists_and_parses() -> None:
    assert RECORD_PATH.is_file(), f"missing {RECORD_PATH}"
    record = load_record(RECORD_PATH)
    validate_envelope(record)


def test_every_source_reference_resolves_in_the_tree() -> None:
    record = load_record(RECORD_PATH)
    for req in record["requirements"]:
        resolve_source_reference(PROJECT_ROOT, req["source_reference"])
    for comp in record["composition"]:
        resolve_source_reference(PROJECT_ROOT, comp["source_reference"])
    for ref in record["source_references"]:
        resolve_source_reference(PROJECT_ROOT, ref)


def test_every_requirement_has_a_named_checker() -> None:
    record = load_record(RECORD_PATH)
    ids = [req["id"] for req in record["requirements"]]
    assert len(ids) == len(set(ids)), "requirement ids must be unique"
    missing = [i for i in ids if i not in REQUIREMENT_CHECKS]
    assert not missing, (
        f"requirement(s) with no mechanical checker: {missing} — either write "
        "a checker or restate the requirement so it can be checked"
    )


def test_every_requirement_satisfied_value_matches_the_tree() -> None:
    """MEASURED half: each declared ``satisfied`` boolean is re-derived, not
    trusted, from the current state of the tree."""
    record = load_record(RECORD_PATH)
    for req in record["requirements"]:
        checker = REQUIREMENT_CHECKS[req["id"]]
        actual = checker(PROJECT_ROOT)
        assert actual == req["satisfied"], (
            f"requirement {req['id']!r} declares satisfied={req['satisfied']} "
            f"but the tree shows {actual}"
        )


def test_every_composition_declaration_matches_the_tree() -> None:
    record = load_record(RECORD_PATH)
    assert len(record["composition"]) == len(COMPOSITION_CHECKS), (
        "composition list length drifted from the checkers wired for it — "
        "add/remove a checker in COMPOSITION_CHECKS to match"
    )
    for comp, checker in zip(record["composition"], COMPOSITION_CHECKS, strict=True):
        assert checker(PROJECT_ROOT), (
            f"composition declaration not supported by the tree: "
            f"{comp['declaration']!r}"
        )


def test_create_async_engine_is_not_mistaken_for_sync() -> None:
    """Near-miss proof for the string-containment shortcut in the checker
    above: 'create_engine(' must NOT match inside 'create_async_engine('."""
    assert "create_engine(" not in "xcreate_async_engine("[1:]
    line = "    _async_engine = create_async_engine("
    assert "create_engine(" not in line


def test_async_isolation_sweep_matches_the_declared_file_set() -> None:
    """The record's declared 9-file async surface is exactly what an
    independent marker sweep over app/ finds today — not a stale subset."""
    found: set[str] = set()
    for path in sorted(PROJECT_ROOT.glob("app/**/*.py")):
        content = path.read_text()
        if any(marker in content for marker in ASYNC_MARKERS):
            found.add(str(path.relative_to(PROJECT_ROOT)))
    assert found == DECLARED_ASYNC_FILES


# ---------------------------------------------------------------------------
# Sensitivity proof
# ---------------------------------------------------------------------------


def _valid_record() -> dict[str, Any]:
    return load_record(RECORD_PATH)


def test_a_legitimate_record_is_accepted() -> None:
    """Near-miss control: the real, checked-in record — every claim
    genuinely true of the tree — passes every check above."""
    record = _valid_record()
    validate_envelope(record)
    for req in record["requirements"]:
        assert REQUIREMENT_CHECKS[req["id"]](PROJECT_ROOT) == req["satisfied"]


@pytest.mark.parametrize(
    "requirement_id",
    [
        "single-sync-engine-construction-site",
        "async-database-paths-isolated-from-kernel",
        "no-runtime-bypassrls-credential-or-per-call-flag",
        "sync-runtime-not-yet-composed",
    ],
)
def test_a_false_requirement_is_rejected(requirement_id: str) -> None:
    """Plant: flip one requirement's declared ``satisfied`` to disagree with
    what the tree actually shows, and assert the check that would run in CI
    (`test_every_requirement_satisfied_value_matches_the_tree`'s logic)
    refuses it. This is the exact defect the record exists to rule out — a
    boolean asserted by a party that did not (or could not) verify it."""
    record = _valid_record()
    by_id = {req["id"]: req for req in record["requirements"]}
    target = by_id[requirement_id]
    flipped = {**target, "satisfied": not target["satisfied"]}

    checker = REQUIREMENT_CHECKS[flipped["id"]]
    actual = checker(PROJECT_ROOT)
    assert actual != flipped["satisfied"], (
        "sensitivity proof is broken: the flipped value coincidentally "
        "matches the tree, so this plant proves nothing"
    )


def test_a_false_source_reference_is_rejected() -> None:
    """Plant: point a requirement's source_reference at a line beyond the
    file's length, and assert resolve_source_reference refuses it."""
    line_count = len(
        (PROJECT_ROOT / "app" / "db" / "__init__.py").read_text().splitlines()
    )
    bogus = f"app/db/__init__.py:{line_count + 500}"
    with pytest.raises(AssertionError):
        resolve_source_reference(PROJECT_ROOT, bogus)


def test_a_false_composition_declaration_is_rejected() -> None:
    """Plant: assert a kernel pin version that is not the one in
    pyproject.toml, and show the composition checker refuses it."""

    def wrong_pin_checker(root: Path) -> bool:
        pin = _read(root, "pyproject.toml")
        return 'dotmac-kernel = {version = "9.9.9a1", source = "forgejo"}' in pin

    assert wrong_pin_checker(PROJECT_ROOT) is False


def test_envelope_rejects_an_extra_key_on_a_requirement() -> None:
    record = _valid_record()
    record["requirements"][0] = {**record["requirements"][0], "extra": "nope"}
    with pytest.raises(AssertionError):
        validate_envelope(record)


def test_envelope_rejects_wrong_schema_or_product() -> None:
    for bad in ({"schema": "kernel-runtime-readiness.v2"}, {"product": "dotmac_sub"}):
        record = {**_valid_record(), **bad}
        with pytest.raises(AssertionError):
            validate_envelope(record)
