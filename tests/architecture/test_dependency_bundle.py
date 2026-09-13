"""`scripts/dependency_bundle.py`'s refusals, each planted against.

Four groups of tests:

1. The plan digest: total classification of every dependency form (public,
   approved Forgejo, approved off-index, or a named refusal), every
   selection-significant field's sensitivity, and every "unrecognised
   dependency form is refused, never silently omitted" case.
2. The bundle mechanics: plan-to-member closure, safe/private/atomic
   extraction, archive/member hash verification, run-metadata verification,
   and "no field defaults its way past a refusal" for each of them.
3. The NAMED, ENFORCED duplication between this module and `erp_lock.py` —
   see both modules' docstrings and
   `docs/architecture/dependency-bundle-duplication-inventory.json`. One
   shared table of adversarial vectors drives BOTH implementations of each
   LISTED duplicated helper; a second, independent scan detects any
   UNLISTED near-duplicate function body between the two modules.
4. `scripts/dependency_normalisation.py`: the one shared owner of PEP 503
   name normalisation, and the divergence it fixed.
"""

from __future__ import annotations

import ast
import base64
import dataclasses
import difflib
import json
import sys
import tempfile
import urllib.parse
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import dependency_bundle as db  # noqa: E402
import dependency_normalisation  # noqa: E402
import erp_lock  # noqa: E402

INVENTORY_PATH = (
    ROOT / "docs" / "architecture" / "dependency-bundle-duplication-inventory.json"
)

#: The non-growing baseline. This inventory may only SHRINK from this exact
#: set — a duplicated behaviour appearing that is not in this set fails
#: `test_the_inventory_has_not_grown_past_the_baseline`. Widening it (as a
#: NEW, deliberately-accepted orchestration duplicate is found and listed)
#: or shrinking it (as entries are retired) both mean updating THIS constant
#: in the same reviewed change.
BASELINE_INVENTORY_IDS = frozenset(
    {
        "sha256-hex-digest",
        "approved-artifact-url",
        "credential-string-scan",
        "credential-encodings",
    }
)

REAL_POLICY_PATH = ROOT / ".github" / "dependency-bundle-policy.json"


def _real_permitted_off_index() -> dict[str, db.OffIndexPin]:
    policy = json.loads(REAL_POLICY_PATH.read_text(encoding="utf-8"))
    return db.load_permitted_off_index_dependencies(policy)


def _project_root(tmp_path: Path, pyproject_text: str, lock_text: str) -> Path:
    (tmp_path / "pyproject.toml").write_text(pyproject_text, encoding="utf-8")
    (tmp_path / "poetry.lock").write_text(lock_text, encoding="utf-8")
    return tmp_path


def base_pyproject(extra_dep_line: str = "") -> str:
    return f"""
[tool.poetry]
name = "sample"
version = "1.0.0"
package-mode = false

[tool.poetry.dependencies]
python = ">=3.11,<3.13"
requests = "^2.31.0"
dotmac-kernel = {{version = "0.1.0a1", source = "forgejo"}}
{extra_dep_line}

[[tool.poetry.source]]
name = "forgejo"
url = "https://registry.dotmac.io/api/packages/dotmac/pypi/simple/"
priority = "explicit"

[tool.poetry.group.dev.dependencies]
pytest = "8.2.2"
"""


BASE_PYPROJECT = base_pyproject()

BASE_LOCK = """
[[package]]
name = "requests"
version = "2.31.0"
python-versions = ">=3.8"
groups = ["main"]
files = []

[package.source]
type = "legacy"
url = "https://pypi.org/simple"
reference = "pypi"

[[package]]
name = "dotmac-kernel"
version = "0.1.0a1"
python-versions = ">=3.11"
groups = ["main"]
optional = false
files = [
    {file = "dotmac_kernel-0.1.0a1-py3-none-any.whl", hash = "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
]

[package.source]
type = "legacy"
url = "https://registry.dotmac.io/api/packages/dotmac/pypi/simple"
reference = "forgejo"

[[package]]
name = "pytest"
version = "8.2.2"
python-versions = ">=3.8"
groups = ["dev"]
files = []

[package.source]
type = "legacy"
url = "https://pypi.org/simple"
reference = "pypi"

[metadata]
lock-version = "2.1"
python-versions = ">=3.11,<3.13"
content-hash = "0000000000000000000000000000000000000000000000000000000000000000"
"""


def _base_surface(tmp_path: Path) -> db.DependencySurface:
    root = _project_root(tmp_path, BASE_PYPROJECT, BASE_LOCK)
    return db.extract_dependency_surface(root)


# ═══════════════════════════════════════════════════════════════════════
# 1. Plan digest: total classification and selection-significant fields
# ═══════════════════════════════════════════════════════════════════════


def test_the_baseline_surface_produces_a_stable_64_hex_digest(tmp_path: Path) -> None:
    surface = _base_surface(tmp_path)
    digest_a = db.compute_plan_digest(surface)
    digest_b = db.compute_plan_digest(surface)
    assert digest_a == digest_b
    assert len(digest_a) == 64
    int(digest_a, 16)


def test_extraction_against_the_real_repository_succeeds() -> None:
    """The real pyproject.toml/poetry.lock, with the real off-index
    allowlist from the real policy file, extracts cleanly and produces the
    real approved off-index dependency."""

    surface = db.extract_dependency_surface(ROOT, _real_permitted_off_index())
    names = {d.normalised_name for d in surface.off_index_dependencies}
    assert "dotmac-integration-client" in names
    digest = db.compute_plan_digest(surface)
    assert len(digest) == 64


# ── item 1: total classification — nothing off-index vanishes silently ──


def test_an_unlisted_git_dependency_is_refused_not_silently_dropped(
    tmp_path: Path,
) -> None:
    manifest = base_pyproject(
        'evil-package = { git = "https://evil.example.com/evil.git", tag = "v1.0" }'
    )
    root = _project_root(tmp_path, manifest, BASE_LOCK)
    with pytest.raises(db.ManifestError, match="off-index dependency form"):
        db.extract_dependency_surface(root)


def test_an_unlisted_path_dependency_is_refused_not_silently_dropped(
    tmp_path: Path,
) -> None:
    manifest = base_pyproject('local-thing = { path = "../local-thing" }')
    root = _project_root(tmp_path, manifest, BASE_LOCK)
    with pytest.raises(db.ManifestError, match="off-index dependency form"):
        db.extract_dependency_surface(root)


def test_an_unlisted_file_dependency_is_refused_not_silently_dropped(
    tmp_path: Path,
) -> None:
    manifest = base_pyproject('local-wheel = { file = "../thing.whl" }')
    root = _project_root(tmp_path, manifest, BASE_LOCK)
    with pytest.raises(db.ManifestError, match="off-index dependency form"):
        db.extract_dependency_surface(root)


def test_requires_plugins_is_refused() -> None:
    manifest = BASE_PYPROJECT.replace(
        "[tool.poetry]\n",
        '[tool.poetry]\nrequires-plugins = { "poetry-plugin-export" = ">=1.0" }\n',
    )
    with tempfile.TemporaryDirectory() as d:
        root = _project_root(Path(d), manifest, BASE_LOCK)
        with pytest.raises(db.ManifestError, match="requires-plugins"):
            db.extract_dependency_surface(root)


def test_pep735_dependency_groups_is_refused_not_silently_dropped(
    tmp_path: Path,
) -> None:
    """Finding 2: [dependency-groups] (PEP 735) is a top-level table of
    plain PEP 508 requirement strings, so a direct-reference entry
    (`name @ https://...`) there could bypass total classification the
    same way an off-index Poetry table would -- and this module has no
    PEP 508 classifier to examine it with. erp_lock.py already traverses
    this surface for its own validation; this module must at least refuse
    it, not silently ignore it and leave the digest unmoved."""

    manifest = (
        BASE_PYPROJECT
        + '\n[dependency-groups]\ndev = ["evil-package @ https://evil.example.com/x.whl"]\n'
    )
    root = _project_root(tmp_path, manifest, BASE_LOCK)
    with pytest.raises(db.ManifestError, match="dependency-groups"):
        db.extract_dependency_surface(root)


_OFF_INDEX_MANIFEST = base_pyproject(
    "dotmac-integration-client = { git = "
    '"https://github.com/michaelayoade/dotmac-integration-client.git", tag = "v0.2.0" }'
)
_OFF_INDEX_LOCK = (
    BASE_LOCK
    + """
[[package]]
name = "dotmac-integration-client"
version = "0.2.0"
python-versions = ">=3.11"
groups = ["main"]
files = []

[package.source]
type = "git"
url = "https://github.com/michaelayoade/dotmac-integration-client.git"
reference = "v0.2.0"
resolved_reference = "a4fe55f4ed704c556c4d1e3cc728ec4ef0dd8042"
"""
)
_GOOD_PIN = {
    "dotmac-integration-client": db.OffIndexPin(
        url="https://github.com/michaelayoade/dotmac-integration-client.git",
        tag="v0.2.0",
        commit="a4fe55f4ed704c556c4d1e3cc728ec4ef0dd8042",
    )
}


def test_the_approved_off_index_pin_is_accepted_and_verified(tmp_path: Path) -> None:
    root = _project_root(tmp_path, _OFF_INDEX_MANIFEST, _OFF_INDEX_LOCK)
    surface = db.extract_dependency_surface(root, _GOOD_PIN)
    assert len(surface.off_index_dependencies) == 1
    assert surface.off_index_dependencies[0].resolved_commit == (
        "a4fe55f4ed704c556c4d1e3cc728ec4ef0dd8042"
    )


def test_off_index_lock_commit_disagreement_is_refused(tmp_path: Path) -> None:
    wrong_lock = _OFF_INDEX_LOCK.replace(
        'resolved_reference = "a4fe55f4ed704c556c4d1e3cc728ec4ef0dd8042"',
        f'resolved_reference = "{"c" * 40}"',
    )
    root = _project_root(tmp_path, _OFF_INDEX_MANIFEST, wrong_lock)
    with pytest.raises(db.ManifestError, match="does not match its pinned identity"):
        db.extract_dependency_surface(root, _GOOD_PIN)


def test_the_off_index_identity_moves_the_digest(tmp_path: Path) -> None:
    root = _project_root(tmp_path, _OFF_INDEX_MANIFEST, _OFF_INDEX_LOCK)
    good_digest = db.compute_plan_digest(db.extract_dependency_surface(root, _GOOD_PIN))

    bad_pin = {
        "dotmac-integration-client": db.OffIndexPin(
            url="https://github.com/michaelayoade/dotmac-integration-client.git",
            tag="v0.2.0",
            commit="b" * 40,
        )
    }
    other_root = tmp_path / "other"
    other_root.mkdir()
    other_lock = _OFF_INDEX_LOCK.replace(
        'resolved_reference = "a4fe55f4ed704c556c4d1e3cc728ec4ef0dd8042"',
        f'resolved_reference = "{"b" * 40}"',
    )
    bad_digest = db.compute_plan_digest(
        db.extract_dependency_surface(
            _project_root(other_root, _OFF_INDEX_MANIFEST, other_lock), bad_pin
        )
    )
    assert good_digest != bad_digest, (
        f"before (approved commit): {good_digest}\nafter (different commit): {bad_digest}"
    )


# ── item 2: every selection-significant field moves the digest ──────────


def _surface_with_dependency(dep: db.ForgejoDependency) -> db.DependencySurface:
    return db.DependencySurface(
        schema_version=db.PLAN_SCHEMA_VERSION,
        forgejo_source_url=db.FORGEJO_LOCK_URL,
        target_python=">=3.11,<3.13",
        target_platform=db.TARGET_PLATFORM,
        lock_format_version="2.1",
        lock_python_versions=">=3.11,<3.13",
        dependencies=(dep,),
        off_index_dependencies=(),
        lock_packages=(),
    )


def _surface_with_lock_package(pkg: db.LockPackage) -> db.DependencySurface:
    return db.DependencySurface(
        schema_version=db.PLAN_SCHEMA_VERSION,
        forgejo_source_url=db.FORGEJO_LOCK_URL,
        target_python=">=3.11,<3.13",
        target_platform=db.TARGET_PLATFORM,
        lock_format_version="2.1",
        lock_python_versions=">=3.11,<3.13",
        dependencies=(),
        off_index_dependencies=(),
        lock_packages=(pkg,),
    )


_BASE_DEP = db.ForgejoDependency(
    name="dotmac-kernel",
    normalised_name="dotmac-kernel",
    group="main",
    version="0.1.0a1",
    markers=None,
    extras=(),
    optional=False,
    python_constraint=None,
    group_optional=False,
)

_BASE_LOCK_PKG = db.LockPackage(
    name="dotmac-kernel",
    normalised_name="dotmac-kernel",
    version="0.1.0a1",
    groups=("main",),
    optional=False,
    python_versions=">=3.11",
    markers=None,
    extras={},
    dependencies={},
    source_type="legacy",
    source_url=db.FORGEJO_LOCK_URL,
    source_reference="forgejo",
    files=(
        {
            "file": "dotmac_kernel-0.1.0a1-py3-none-any.whl",
            "hash": "sha256:" + "a" * 64,
        },
    ),
)


@pytest.mark.parametrize(
    "label,mutate",
    [
        ("optional False->True", lambda dep: dataclasses.replace(dep, optional=True)),
        (
            "markers None->set",
            lambda dep: dataclasses.replace(dep, markers="python_version < '3.13'"),
        ),
        ("extras added", lambda dep: dataclasses.replace(dep, extras=("speedups",))),
        ("group main->dev", lambda dep: dataclasses.replace(dep, group="dev")),
        (
            "python constraint None->'>=3.12'",
            lambda dep: dataclasses.replace(dep, python_constraint=">=3.12"),
        ),
        (
            "group_optional False->True",
            lambda dep: dataclasses.replace(dep, group_optional=True),
        ),
    ],
)
def test_each_forgejo_dependency_field_moves_the_digest(label, mutate) -> None:
    before = _surface_with_dependency(_BASE_DEP)
    after = _surface_with_dependency(mutate(_BASE_DEP))
    before_digest = db.compute_plan_digest(before)
    after_digest = db.compute_plan_digest(after)
    assert before_digest != after_digest, (
        f"{label}: before={before_digest} after={after_digest}"
    )


@pytest.mark.parametrize(
    "label,mutate",
    [
        ("optional False->True", lambda pkg: dataclasses.replace(pkg, optional=True)),
        (
            "python_versions changed",
            lambda pkg: dataclasses.replace(pkg, python_versions=">=3.12"),
        ),
        ("groups changed", lambda pkg: dataclasses.replace(pkg, groups=("dev",))),
        (
            "wheel filename changed",
            lambda pkg: dataclasses.replace(
                pkg,
                files=(
                    {
                        "file": "dotmac_kernel-0.1.0a1-py3-none-manylinux1_x86_64.whl",
                        "hash": pkg.files[0]["hash"],
                    },
                ),
            ),
        ),
        (
            "hash changed",
            lambda pkg: dataclasses.replace(
                pkg,
                files=({"file": pkg.files[0]["file"], "hash": "sha256:" + "b" * 64},),
            ),
        ),
        (
            "markers None->set",
            lambda pkg: dataclasses.replace(pkg, markers="python_version < '3.13'"),
        ),
        (
            "extras added",
            lambda pkg: dataclasses.replace(pkg, extras={"speedups": ["orjson (>=3)"]}),
        ),
    ],
)
def test_each_lock_package_field_moves_the_digest(label, mutate) -> None:
    before = _surface_with_lock_package(_BASE_LOCK_PKG)
    after = _surface_with_lock_package(mutate(_BASE_LOCK_PKG))
    before_digest = db.compute_plan_digest(before)
    after_digest = db.compute_plan_digest(after)
    assert before_digest != after_digest, (
        f"{label}: before={before_digest} after={after_digest}"
    )


def test_forgejo_source_url_change_moves_the_digest() -> None:
    surface = db.DependencySurface(
        schema_version=db.PLAN_SCHEMA_VERSION,
        forgejo_source_url=db.FORGEJO_LOCK_URL,
        target_python=">=3.11,<3.13",
        target_platform=db.TARGET_PLATFORM,
        lock_format_version="2.1",
        lock_python_versions=">=3.11,<3.13",
        dependencies=(),
        off_index_dependencies=(),
        lock_packages=(),
    )
    mutated = dataclasses.replace(
        surface, forgejo_source_url=db.FORGEJO_LOCK_URL + "-alt"
    )
    before, after = db.compute_plan_digest(surface), db.compute_plan_digest(mutated)
    assert before != after, f"before={before} after={after}"


def test_lock_format_version_change_moves_the_digest(tmp_path: Path) -> None:
    base_surface = _base_surface(tmp_path)
    mutated = dataclasses.replace(base_surface, lock_format_version="1.1")
    before, after = (
        db.compute_plan_digest(base_surface),
        db.compute_plan_digest(mutated),
    )
    assert before != after, f"before={before} after={after}"


def test_lock_python_versions_change_moves_the_digest(tmp_path: Path) -> None:
    base_surface = _base_surface(tmp_path)
    mutated = dataclasses.replace(base_surface, lock_python_versions=">=3.9,<3.10")
    before, after = (
        db.compute_plan_digest(base_surface),
        db.compute_plan_digest(mutated),
    )
    assert before != after, f"before={before} after={after}"


# ── irrelevant changes must NOT change the digest ────────────────────────


def test_a_toml_comment_and_reordering_do_not_change_the_digest(tmp_path: Path) -> None:
    base_surface = _base_surface(tmp_path)
    base_digest = db.compute_plan_digest(base_surface)

    reformatted = "# a harmless comment\n" + BASE_PYPROJECT.replace(
        'python = ">=3.11,<3.13"\nrequests = "^2.31.0"',
        'requests = "^2.31.0"\n\n# reordered, still equivalent\npython = ">=3.11,<3.13"',
    )
    other_root = tmp_path / "reformatted"
    other_root.mkdir()
    mutated_surface = db.extract_dependency_surface(
        _project_root(other_root, reformatted, BASE_LOCK)
    )
    assert db.compute_plan_digest(mutated_surface) == base_digest


def test_the_applications_own_version_does_not_change_the_digest(
    tmp_path: Path,
) -> None:
    base_surface = _base_surface(tmp_path)
    base_digest = db.compute_plan_digest(base_surface)

    bumped = BASE_PYPROJECT.replace('version = "1.0.0"', 'version = "2.5.1"')
    other_root = tmp_path / "bumped"
    other_root.mkdir()
    mutated_surface = db.extract_dependency_surface(
        _project_root(other_root, bumped, BASE_LOCK)
    )
    assert db.compute_plan_digest(mutated_surface) == base_digest


def test_an_unrelated_public_package_change_does_not_change_the_digest(
    tmp_path: Path,
) -> None:
    base_surface = _base_surface(tmp_path)
    base_digest = db.compute_plan_digest(base_surface)

    bumped_lock = BASE_LOCK.replace('version = "2.31.0"', 'version = "2.32.5"')
    other_root = tmp_path / "public_bump"
    other_root.mkdir()
    mutated_surface = db.extract_dependency_surface(
        _project_root(other_root, BASE_PYPROJECT, bumped_lock)
    )
    assert db.compute_plan_digest(mutated_surface) == base_digest


# ── other unrecognised-form refusals (kept from the previous slice) ──────


def test_a_legacy_dev_dependencies_table_is_refused(tmp_path: Path) -> None:
    manifest = BASE_PYPROJECT + '\n[tool.poetry.dev-dependencies]\nfoo = "1.0"\n'
    root = _project_root(tmp_path, manifest, BASE_LOCK)
    with pytest.raises(db.ManifestError, match="dev-dependencies"):
        db.extract_dependency_surface(root)


def test_a_pep621_dependencies_table_is_refused(tmp_path: Path) -> None:
    manifest = '[project]\ndependencies = ["requests"]\n\n' + BASE_PYPROJECT
    root = _project_root(tmp_path, manifest, BASE_LOCK)
    with pytest.raises(db.ManifestError, match="PEP 621"):
        db.extract_dependency_surface(root)


def test_a_duplicate_forgejo_declaration_across_groups_is_refused(
    tmp_path: Path,
) -> None:
    manifest = BASE_PYPROJECT.replace(
        '[tool.poetry.group.dev.dependencies]\npytest = "8.2.2"',
        '[tool.poetry.group.dev.dependencies]\npytest = "8.2.2"\n'
        'dotmac-kernel = {version = "0.1.0a1", source = "forgejo"}',
    )
    root = _project_root(tmp_path, manifest, BASE_LOCK)
    with pytest.raises(db.ManifestError, match="declared more than once"):
        db.extract_dependency_surface(root)


def test_a_present_poetry_toml_is_refused(tmp_path: Path) -> None:
    root = _project_root(tmp_path, BASE_PYPROJECT, BASE_LOCK)
    (root / "poetry.toml").write_text(
        "[virtualenvs]\ncreate = false\n", encoding="utf-8"
    )
    with pytest.raises(db.ManifestError, match="poetry.toml"):
        db.extract_dependency_surface(root)


def test_an_alternate_forgejo_url_is_refused(tmp_path: Path) -> None:
    manifest = BASE_PYPROJECT.replace(
        "registry.dotmac.io/api/packages/dotmac/pypi/simple/",
        "registry.dotmac.io/api/packages/dotmac/pypi/simple-alt/",
    )
    root = _project_root(tmp_path, manifest, BASE_LOCK)
    with pytest.raises(db.ManifestError, match="approved index"):
        db.extract_dependency_surface(root)


def test_a_direct_registry_url_is_refused(tmp_path: Path) -> None:
    manifest = BASE_PYPROJECT.replace(
        'dotmac-kernel = {version = "0.1.0a1", source = "forgejo"}',
        'dotmac-kernel = {url = "https://registry.dotmac.io/api/packages/dotmac/pypi/files/dotmac_kernel-0.1.0a1-py3-none-any.whl"}',
    )
    root = _project_root(tmp_path, manifest, BASE_LOCK)
    with pytest.raises(db.ManifestError, match="direct registry URL"):
        db.extract_dependency_surface(root)


def test_a_version_range_on_a_private_package_is_refused(tmp_path: Path) -> None:
    manifest = BASE_PYPROJECT.replace(
        'dotmac-kernel = {version = "0.1.0a1", source = "forgejo"}',
        'dotmac-kernel = {version = "^0.1.0a1", source = "forgejo"}',
    )
    root = _project_root(tmp_path, manifest, BASE_LOCK)
    with pytest.raises(db.ManifestError, match="EXACT version"):
        db.extract_dependency_surface(root)


def test_a_manifest_lock_version_disagreement_is_refused(tmp_path: Path) -> None:
    manifest = BASE_PYPROJECT.replace(
        'dotmac-kernel = {version = "0.1.0a1", source = "forgejo"}',
        'dotmac-kernel = {version = "0.1.0a2", source = "forgejo"}',
    )
    root = _project_root(tmp_path, manifest, BASE_LOCK)
    with pytest.raises(db.ManifestError, match="disagree"):
        db.extract_dependency_surface(root)


def test_a_malformed_forgejo_lock_source_is_refused(tmp_path: Path) -> None:
    lock = BASE_LOCK.replace(
        'name = "dotmac-kernel"\nversion = "0.1.0a1"\npython-versions = ">=3.11"\n'
        'groups = ["main"]\noptional = false\nfiles = [\n'
        '    {file = "dotmac_kernel-0.1.0a1-py3-none-any.whl", hash = "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},\n'
        ']\n\n[package.source]\ntype = "legacy"',
        'name = "dotmac-kernel"\nversion = "0.1.0a1"\npython-versions = ">=3.11"\n'
        'groups = ["main"]\noptional = false\nfiles = [\n'
        '    {file = "dotmac_kernel-0.1.0a1-py3-none-any.whl", hash = "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},\n'
        ']\n\n[package.source]\ntype = "url"',
    )
    assert lock != BASE_LOCK, (
        "the targeted replacement did not match; fix the test fixture"
    )
    root = _project_root(tmp_path, BASE_PYPROJECT, lock)
    with pytest.raises(db.ManifestError, match="malformed forgejo source"):
        db.extract_dependency_surface(root)


def test_a_non_table_lock_package_entry_is_refused_not_a_raw_crash(
    tmp_path: Path,
) -> None:
    """Finding 7: a `[[package]]` entry that is not itself a table used to
    reach `pkg.get(...)` and raise a raw AttributeError."""

    lock = (
        'package = ["not-a-table"]\n\n'
        "[metadata]\n"
        'lock-version = "2.1"\n'
        'python-versions = ">=3.11,<3.13"\n'
        'content-hash = "0000000000000000000000000000000000000000000000000000000000000000"\n'
    )
    root = _project_root(tmp_path, BASE_PYPROJECT, lock)
    with pytest.raises(db.ManifestError, match="must be a table"):
        db.extract_dependency_surface(root)


def test_a_non_table_lock_source_does_not_crash_and_is_still_caught(
    tmp_path: Path,
) -> None:
    """A `[[package]].source` that is a scalar, not a table, used to reach
    `source.get(...)` and raise a raw AttributeError. It no longer crashes
    -- the package is treated as not-obviously-private (its source cannot
    be read at all) and SKIPPED, but since the manifest still declares
    dotmac-kernel as a forgejo dependency, the existing manifest/lock
    cross-check catches the resulting disagreement anyway: private state
    is not silently accepted, it surfaces one check later."""

    lock = (
        '[[package]]\nname = "dotmac-kernel"\nversion = "0.1.0a1"\n'
        'source = "not-a-table"\n\n'
        "[metadata]\n"
        'lock-version = "2.1"\n'
        'python-versions = ">=3.11,<3.13"\n'
        'content-hash = "0000000000000000000000000000000000000000000000000000000000000000"\n'
    )
    root = _project_root(tmp_path, BASE_PYPROJECT, lock)
    with pytest.raises(db.ManifestError, match="no corresponding"):
        db.extract_dependency_surface(root)


def test_a_non_table_lock_dependencies_is_refused_not_a_raw_crash(
    tmp_path: Path,
) -> None:
    """Finding 7: `dict(pkg.get("dependencies", {}) or {})` used to raise a
    raw ValueError when `dependencies` was a non-mapping truthy value
    (e.g. a list of strings)."""

    lock = BASE_LOCK.replace(
        'groups = ["main"]\noptional = false\nfiles = [\n'
        '    {file = "dotmac_kernel-0.1.0a1-py3-none-any.whl", hash = "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},\n'
        ']\n\n[package.source]\ntype = "legacy"\nurl = "https://registry.dotmac.io/api/packages/dotmac/pypi/simple"\nreference = "forgejo"',
        'groups = ["main"]\noptional = false\ndependencies = ["a", "b"]\nfiles = [\n'
        '    {file = "dotmac_kernel-0.1.0a1-py3-none-any.whl", hash = "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},\n'
        ']\n\n[package.source]\ntype = "legacy"\nurl = "https://registry.dotmac.io/api/packages/dotmac/pypi/simple"\nreference = "forgejo"',
    )
    assert lock != BASE_LOCK, "the targeted replacement did not match"
    root = _project_root(tmp_path, BASE_PYPROJECT, lock)
    with pytest.raises(db.ManifestError, match="non-table dependencies"):
        db.extract_dependency_surface(root)


def test_verify_run_metadata_refuses_a_non_dict_metadata(tmp_path: Path) -> None:
    candidate_root = _candidate_root(tmp_path)
    with pytest.raises(db.BundleVerificationError, match="must be a dict"):
        db.verify_run_metadata(
            ["not", "a", "dict"], _valid_policy(), candidate_root=candidate_root
        )


def test_verify_run_metadata_refuses_a_non_dict_policy(tmp_path: Path) -> None:
    candidate_root = _candidate_root(tmp_path)
    digest = _candidate_digest(candidate_root)
    with pytest.raises(db.BundleVerificationError, match="must be a dict"):
        db.verify_run_metadata(
            _valid_run_metadata(digest),
            ["not", "a", "dict"],
            candidate_root=candidate_root,
        )


# ═══════════════════════════════════════════════════════════════════════
# 2. Bundle mechanics
# ═══════════════════════════════════════════════════════════════════════

# ── item 4/5: plan-to-member closure, computed bindings, recorded sizes ──


def _run() -> db.RunMetadata:
    return db.RunMetadata(
        repository_full_name="michaelayoade/dotmac_erp",
        repository_id=1141216651,
        workflow_path=".github/workflows/dependency-bundle-produce.yml",
        run_id=111,
        run_attempt=1,
        trusted_workflow_sha="a" * 40,
        artifact_id=222,
        artifact_name="erp-dependency-bundle-x",
        artifact_run_id=111,
        environment_name="forgejo-registry-read-main",
    )


#: `create_bundle_manifest` now reads and hashes REAL files rather than
#: trusting a caller-reported summary, so these tests need a lock fixture
#: whose declared hash is the hash of bytes the test can actually write —
#: `_BASE_LOCK_PKG`'s fixed "aaaa...a" placeholder is not the hash of
#: anything, and no real file could ever match it.
_MANIFEST_TEST_WHEEL_NAME = "dotmac_kernel-0.1.0a1-py3-none-any.whl"
_MANIFEST_TEST_WHEEL_BYTES = b"pretend wheel content for bundle-manifest tests"
_MANIFEST_TEST_WHEEL_SHA256 = db.sha256_hex(_MANIFEST_TEST_WHEEL_BYTES)
_MANIFEST_TEST_LOCK_PKG = dataclasses.replace(
    _BASE_LOCK_PKG,
    files=(
        {
            "file": _MANIFEST_TEST_WHEEL_NAME,
            "hash": "sha256:" + _MANIFEST_TEST_WHEEL_SHA256,
        },
    ),
)

_SECOND_MANIFEST_TEST_WHEEL_NAME = "dotmac_ui-0.1.0a7-py3-none-any.whl"
_SECOND_MANIFEST_TEST_WHEEL_BYTES = b"pretend second wheel content"
_SECOND_MANIFEST_TEST_WHEEL_SHA256 = db.sha256_hex(_SECOND_MANIFEST_TEST_WHEEL_BYTES)
_SECOND_MANIFEST_TEST_LOCK_PKG = dataclasses.replace(
    _BASE_LOCK_PKG,
    name="dotmac-ui",
    normalised_name="dotmac-ui",
    files=(
        {
            "file": _SECOND_MANIFEST_TEST_WHEEL_NAME,
            "hash": "sha256:" + _SECOND_MANIFEST_TEST_WHEEL_SHA256,
        },
    ),
)


def _manifest_test_surface() -> db.DependencySurface:
    return _surface_with_lock_package(_MANIFEST_TEST_LOCK_PKG)


def _write_bytes(tmp_path: Path, name: str, content: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(content)
    return path


def _write_wheel(tmp_path: Path, content: bytes = _MANIFEST_TEST_WHEEL_BYTES) -> Path:
    return _write_bytes(tmp_path, _MANIFEST_TEST_WHEEL_NAME, content)


def _write_archive(tmp_path: Path, name: str = "bundle.zip") -> Path:
    return _write_bytes(tmp_path, name, b"pretend outer archive bytes")


def test_create_bundle_manifest_computes_plan_digest_and_sizes(tmp_path: Path) -> None:
    surface = _manifest_test_surface()
    wheel_path = _write_wheel(tmp_path)
    archive_path = _write_archive(tmp_path)
    manifest = db.create_bundle_manifest(
        surface=surface,
        acquired_files={_MANIFEST_TEST_WHEEL_NAME: wheel_path},
        archive_path=archive_path,
        run=_run(),
    )
    assert manifest["plan_digest"] == db.compute_plan_digest(surface)
    assert manifest["archive_sha256"] == db.sha256_hex(archive_path.read_bytes())
    record = manifest["members"][_MANIFEST_TEST_WHEEL_NAME]
    assert record["size"] == len(_MANIFEST_TEST_WHEEL_BYTES)
    assert record["sha256"] == _MANIFEST_TEST_WHEEL_SHA256


def test_create_bundle_manifest_refuses_when_a_planned_file_was_not_acquired(
    tmp_path: Path,
) -> None:
    surface = _manifest_test_surface()
    archive_path = _write_archive(tmp_path)
    with pytest.raises(db.BundleVerificationError, match="not acquired"):
        db.create_bundle_manifest(
            surface=surface, acquired_files={}, archive_path=archive_path, run=_run()
        )


def test_create_bundle_manifest_refuses_an_unaccounted_for_acquired_member(
    tmp_path: Path,
) -> None:
    surface = _manifest_test_surface()
    wheel_path = _write_wheel(tmp_path)
    archive_path = _write_archive(tmp_path)
    extra_path = _write_bytes(tmp_path, "extra-unplanned-file.whl", b"extra")
    acquired = {
        _MANIFEST_TEST_WHEEL_NAME: wheel_path,
        "extra-unplanned-file.whl": extra_path,
    }
    with pytest.raises(db.BundleVerificationError, match="does not name them"):
        db.create_bundle_manifest(
            surface=surface,
            acquired_files=acquired,
            archive_path=archive_path,
            run=_run(),
        )


def test_create_bundle_manifest_refuses_a_digest_mismatch_between_plan_and_acquisition(
    tmp_path: Path,
) -> None:
    surface = _manifest_test_surface()
    wrong_path = _write_wheel(
        tmp_path, content=b"the wrong bytes, not what the plan requires"
    )
    archive_path = _write_archive(tmp_path)
    with pytest.raises(db.BundleVerificationError, match="but the plan requires"):
        db.create_bundle_manifest(
            surface=surface,
            acquired_files={_MANIFEST_TEST_WHEEL_NAME: wrong_path},
            archive_path=archive_path,
            run=_run(),
        )


def test_mixing_one_plans_digest_with_a_different_plans_files_is_refused(
    tmp_path: Path,
) -> None:
    """The item-4 demonstration: plan A's identity may never be attached to
    a file set that does not match it. Plan B here has a strictly SMALLER
    lock-package set (a genuinely different plan); acquiring exactly plan
    A's files and asking for a manifest under plan B's surface must fail
    the closure check."""

    surface_a = dataclasses.replace(
        _manifest_test_surface(),
        lock_packages=(_MANIFEST_TEST_LOCK_PKG, _SECOND_MANIFEST_TEST_LOCK_PKG),
    )
    surface_b = dataclasses.replace(surface_a, lock_packages=(_MANIFEST_TEST_LOCK_PKG,))
    assert db.compute_plan_digest(surface_a) != db.compute_plan_digest(surface_b)

    wheel_a_path = _write_wheel(tmp_path)
    wheel_b_path = _write_bytes(
        tmp_path, _SECOND_MANIFEST_TEST_WHEEL_NAME, _SECOND_MANIFEST_TEST_WHEEL_BYTES
    )
    archive_path = _write_archive(tmp_path)
    acquired_a = {
        _MANIFEST_TEST_WHEEL_NAME: wheel_a_path,
        _SECOND_MANIFEST_TEST_WHEEL_NAME: wheel_b_path,
    }

    # correct pairing succeeds
    manifest = db.create_bundle_manifest(
        surface=surface_a,
        acquired_files=acquired_a,
        archive_path=archive_path,
        run=_run(),
    )
    assert manifest["plan_digest"] == db.compute_plan_digest(surface_a)

    # mismatched pairing (plan B's surface, plan A's acquired files) refused
    with pytest.raises(db.BundleVerificationError):
        db.create_bundle_manifest(
            surface=surface_b,
            acquired_files=acquired_a,
            archive_path=archive_path,
            run=_run(),
        )


def test_planned_artifacts_is_derived_only_from_the_surface() -> None:
    surface = _surface_with_lock_package(_BASE_LOCK_PKG)
    artifacts = db.planned_artifacts(surface)
    assert artifacts == (
        db.PlannedArtifact(
            package_normalised_name="dotmac-kernel",
            filename="dotmac_kernel-0.1.0a1-py3-none-any.whl",
            sha256="a" * 64,
        ),
    )


# ── archive digest ────────────────────────────────────────────────────


def test_archive_digest_matches(tmp_path: Path) -> None:
    archive = tmp_path / "bundle.zip"
    archive.write_bytes(b"hello world")
    expected = db.sha256_hex(b"hello world")
    assert db.verify_archive_digest(archive, expected) == expected


def test_archive_digest_mismatch_is_refused(tmp_path: Path) -> None:
    archive = tmp_path / "bundle.zip"
    archive.write_bytes(b"hello world")
    with pytest.raises(db.BundleVerificationError, match="digest mismatch"):
        db.verify_archive_digest(archive, "0" * 64)


# ── item 6: private, safe, atomic extraction ─────────────────────────


def _make_zip(
    tmp_path: Path, members: dict[str, bytes], *, name: str = "bundle.zip"
) -> Path:
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as archive:
        for member_name, data in members.items():
            archive.writestr(member_name, data)
    return path


def _manifest_for(members: dict[str, bytes]) -> dict:
    return {
        "schema_version": 2,
        "plan_digest": "a" * 64,
        "archive_sha256": "b" * 64,
        "members": {
            name: {"sha256": db.sha256_hex(data), "size": len(data)}
            for name, data in members.items()
        },
        "run": {},
    }


def test_extract_verified_bundle_is_the_only_public_entry_point() -> None:
    assert not hasattr(db, "safe_extract_zip")
    assert hasattr(db, "_extract_zip_members")
    assert hasattr(db, "extract_verified_bundle")


def test_a_clean_bundle_extracts_and_publishes_atomically(tmp_path: Path) -> None:
    members = {"a.whl": b"AAAA", "b.whl": b"BBBBBB"}
    archive = _make_zip(tmp_path, members)
    dest = tmp_path / "out"
    extracted = db.extract_verified_bundle(archive, dest, _manifest_for(members))
    assert sorted(extracted) == ["a.whl", "b.whl"]
    assert (dest / "a.whl").read_bytes() == b"AAAA"


def test_extraction_refuses_a_pre_existing_destination(tmp_path: Path) -> None:
    members = {"a.whl": b"AAAA"}
    archive = _make_zip(tmp_path, members)
    dest = tmp_path / "out"
    dest.mkdir()
    with pytest.raises(db.ExtractionError, match="already exists"):
        db.extract_verified_bundle(archive, dest, _manifest_for(members))


def test_extraction_is_atomic_on_failure_nothing_is_published(tmp_path: Path) -> None:
    archive = _make_zip(tmp_path, {"good.whl": b"GOOD", "bad.whl": b"BAD"})
    bad_manifest = _manifest_for({"good.whl": b"GOOD", "bad.whl": b"BAD"})
    bad_manifest["members"]["bad.whl"]["sha256"] = db.sha256_hex(b"WRONG")
    dest = tmp_path / "out"
    with pytest.raises(db.BundleVerificationError, match="hash mismatch"):
        db.extract_verified_bundle(archive, dest, bad_manifest)
    assert not dest.exists()
    # and no stray staging directory was left behind either
    assert list(tmp_path.iterdir()) == [
        p for p in tmp_path.iterdir() if p.name == archive.name
    ]


def test_a_duplicate_member_name_is_refused(tmp_path: Path) -> None:
    archive = tmp_path / "dup.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("a.whl", b"AAAA")
        zf.writestr("a.whl", b"BBBB")
    with pytest.raises(db.ExtractionError, match="duplicate"):
        db.extract_verified_bundle(
            archive, tmp_path / "out", _manifest_for({"a.whl": b"AAAA"})
        )


def test_an_absolute_path_member_is_refused(tmp_path: Path) -> None:
    archive = _make_zip(tmp_path, {"/etc/passwd": b"x"})
    with pytest.raises(db.ExtractionError, match="absolute path"):
        db.extract_verified_bundle(
            archive, tmp_path / "out", _manifest_for({"/etc/passwd": b"x"})
        )


def test_a_traversal_member_is_refused(tmp_path: Path) -> None:
    archive = _make_zip(tmp_path, {"../evil": b"x"})
    with pytest.raises(db.ExtractionError, match="traversal"):
        db.extract_verified_bundle(
            archive, tmp_path / "out", _manifest_for({"../evil": b"x"})
        )


def test_a_symlink_member_is_refused(tmp_path: Path) -> None:
    archive_path = tmp_path / "link.zip"
    with zipfile.ZipFile(archive_path, "w") as zf:
        info = zipfile.ZipInfo("link")
        info.external_attr = 0o120777 << 16
        zf.writestr(info, "target")
    with pytest.raises(db.ExtractionError, match="symlink"):
        db.extract_verified_bundle(
            archive_path, tmp_path / "out", _manifest_for({"link": b"target"})
        )


def test_case_colliding_members_are_refused(tmp_path: Path) -> None:
    archive = _make_zip(tmp_path, {"A.whl": b"A", "a.whl": b"a"})
    with pytest.raises(db.ExtractionError, match="collide case-insensitively"):
        db.extract_verified_bundle(
            archive, tmp_path / "out", _manifest_for({"A.whl": b"A", "a.whl": b"a"})
        )


def test_resolved_target_aliasing_is_refused(tmp_path: Path) -> None:
    """`a.whl` and `./a.whl` are different literal names but the SAME
    resolved filesystem target — item 6's aliasing fix."""

    archive_path = tmp_path / "alias.zip"
    with zipfile.ZipFile(archive_path, "w") as zf:
        zf.writestr("a.whl", "AAAA")
        zf.writestr("./a.whl", "BBBB")
    manifest = {
        "schema_version": 2,
        "plan_digest": "a" * 64,
        "archive_sha256": "b" * 64,
        "members": {
            "a.whl": {"sha256": db.sha256_hex(b"AAAA"), "size": 4},
            "./a.whl": {"sha256": db.sha256_hex(b"BBBB"), "size": 4},
        },
        "run": {},
    }
    with pytest.raises(db.ExtractionError, match="SAME target path"):
        db.extract_verified_bundle(archive_path, tmp_path / "out", manifest)


def test_an_oversized_member_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(db, "MAX_MEMBER_BYTES", 3)
    archive = _make_zip(tmp_path, {"big.whl": b"AAAAAA"})
    with pytest.raises(db.ExtractionError, match="cap"):
        db.extract_verified_bundle(
            archive, tmp_path / "out", _manifest_for({"big.whl": b"AAAAAA"})
        )


def test_an_unlisted_member_is_refused(tmp_path: Path) -> None:
    archive = _make_zip(tmp_path, {"a.whl": b"AAAA", "sneaky.sh": b"#!/bin/sh\n"})
    with pytest.raises(db.ExtractionError, match="not named in the verified"):
        db.extract_verified_bundle(
            archive, tmp_path / "out", _manifest_for({"a.whl": b"AAAA"})
        )


def test_a_manifest_expected_member_missing_from_the_archive_is_refused(
    tmp_path: Path,
) -> None:
    archive = _make_zip(tmp_path, {"a.whl": b"AAAA"})
    manifest = _manifest_for({"a.whl": b"AAAA"})
    manifest["members"]["missing.whl"] = {"sha256": "c" * 64, "size": 10}
    with pytest.raises(db.ExtractionError, match="does not contain"):
        db.extract_verified_bundle(archive, tmp_path / "out", manifest)


def test_a_corrupted_archive_is_refused_not_a_raw_badzipfile(tmp_path: Path) -> None:
    """Finding 7: `zipfile.BadZipFile` used to escape uncaught."""

    archive = tmp_path / "corrupt.zip"
    archive.write_bytes(b"this is not a zip file at all")
    manifest = _manifest_for({"a.whl": b"AAAA"})
    with pytest.raises(db.ExtractionError, match="cannot open"):
        db.extract_verified_bundle(archive, tmp_path / "out", manifest)


def test_the_aggregate_member_count_cap_is_enforced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(db, "MAX_MEMBER_COUNT", 3)
    members = {f"f{i}.whl": b"X" for i in range(5)}
    archive = _make_zip(tmp_path, members)
    with pytest.raises(db.ExtractionError, match="member cap"):
        db.extract_verified_bundle(archive, tmp_path / "out", _manifest_for(members))


def test_the_aggregate_total_size_cap_is_enforced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(db, "MAX_TOTAL_UNCOMPRESSED_BYTES", 5)
    members = {"a.whl": b"AAAA", "b.whl": b"BBBB"}
    archive = _make_zip(tmp_path, members)
    with pytest.raises(db.ExtractionError, match="aggregate"):
        db.extract_verified_bundle(archive, tmp_path / "out", _manifest_for(members))


def test_the_compression_ratio_cap_is_enforced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(db, "MAX_COMPRESSION_RATIO", 2)
    payload = b"A" * 100_000
    archive_path = tmp_path / "bomb.zip"
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("bomb.whl", payload)
    manifest = _manifest_for({"bomb.whl": payload})
    with pytest.raises(db.ExtractionError, match="compression ratio"):
        db.extract_verified_bundle(archive_path, tmp_path / "out", manifest)


def test_member_hash_verification_refuses_a_mismatch(tmp_path: Path) -> None:
    dest = tmp_path / "out"
    dest.mkdir()
    (dest / "a.whl").write_bytes(b"AAAA")
    with pytest.raises(db.BundleVerificationError, match="hash mismatch"):
        db.verify_member_hashes(dest, {"a.whl": db.sha256_hex(b"different")})


def test_member_hash_verification_refuses_a_missing_file(tmp_path: Path) -> None:
    dest = tmp_path / "out"
    dest.mkdir()
    with pytest.raises(db.BundleVerificationError, match="missing"):
        db.verify_member_hashes(dest, {"a.whl": db.sha256_hex(b"AAAA")})


# ── item 6: build_local_index is atomic, like extract_verified_bundle ────


def test_build_local_index_publishes_a_clean_index_atomically(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    wheel_path = source_dir / "dotmac_kernel-0.1.0a1-py3-none-any.whl"
    wheel_path.write_bytes(b"wheel bytes")
    packages = {
        "dotmac-kernel": [
            (
                "dotmac_kernel-0.1.0a1-py3-none-any.whl",
                db.sha256_hex(b"wheel bytes"),
                wheel_path,
            )
        ]
    }
    index_root = tmp_path / "index"
    db.build_local_index(index_root, packages)
    assert (index_root / "simple" / "index.html").is_file()
    assert (
        index_root
        / "simple"
        / "dotmac-kernel"
        / "dotmac_kernel-0.1.0a1-py3-none-any.whl"
    ).read_bytes() == b"wheel bytes"


def test_build_local_index_refuses_a_pre_existing_destination(tmp_path: Path) -> None:
    index_root = tmp_path / "index"
    index_root.mkdir()
    with pytest.raises(db.BundleVerificationError, match="already exists"):
        db.build_local_index(index_root, {})


def test_build_local_index_is_atomic_on_failure_nothing_is_published(
    tmp_path: Path,
) -> None:
    """The item-6 demonstration: package A comes first (sorted before B),
    package B's source file is missing. Before this fix, A would already be
    published under `index_root` when the failure on B is raised. Now
    NOTHING is published, and no stray staging directory is left behind."""

    source_dir = tmp_path / "source"
    source_dir.mkdir()
    wheel_a = source_dir / "a_pkg-1.0-py3-none-any.whl"
    wheel_a.write_bytes(b"A bytes")
    missing_wheel_b = source_dir / "b_pkg-1.0-py3-none-any.whl"  # never written
    packages = {
        "a-pkg": [("a_pkg-1.0-py3-none-any.whl", db.sha256_hex(b"A bytes"), wheel_a)],
        "b-pkg": [("b_pkg-1.0-py3-none-any.whl", "b" * 64, missing_wheel_b)],
    }
    index_root = tmp_path / "index"
    with pytest.raises(db.BundleVerificationError, match="source file missing"):
        db.build_local_index(index_root, packages)
    assert not index_root.exists()
    assert [p.name for p in source_dir.parent.iterdir() if p.name != "source"] == []


def test_build_local_index_refuses_an_unnormalised_package_key(tmp_path: Path) -> None:
    index_root = tmp_path / "index"
    with pytest.raises(db.BundleVerificationError, match="not PEP-503-normalised"):
        db.build_local_index(index_root, {"Dotmac_Kernel": []})
    assert not index_root.exists()


def test_build_local_index_refuses_an_invalid_pep503_key_rather_than_crashing(
    tmp_path: Path,
) -> None:
    """A key that only NORMALISES to something invalid (edge separator)
    must be refused with BundleVerificationError, not the raw ValueError
    normalise_name raises (finding 8's owner change, applied here too)."""

    index_root = tmp_path / "index"
    with pytest.raises(db.BundleVerificationError, match="not a valid PEP 503 name"):
        db.build_local_index(index_root, {"-dotmac-kernel-": []})


def test_build_local_index_refuses_a_package_key_that_is_a_filesystem_path(
    tmp_path: Path,
) -> None:
    """Finding 3: a key like `/escaped/bundle-path` used to pass the
    "already normalised" check (normalise_name did not validate the input
    charset, so `/` passed straight through and the key compared equal to
    itself), then escape the staging directory entirely when joined via
    `Path.__truediv__` -- an absolute right-hand operand replaces the left
    side."""

    index_root = tmp_path / "index"
    escape_target = tmp_path / "escaped"
    with pytest.raises(
        db.BundleVerificationError, match="not a valid distribution name"
    ):
        db.build_local_index(index_root, {str(escape_target): []})
    assert not escape_target.exists()
    assert not index_root.exists()


def test_build_local_index_refuses_a_filename_containing_a_path_separator(
    tmp_path: Path,
) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    evil_target = tmp_path / "evil-escaped-file"
    wheel_path = source_dir / "wheel.whl"
    wheel_path.write_bytes(b"wheel bytes")
    index_root = tmp_path / "index"
    packages = {
        "dotmac-kernel": [
            (str(evil_target), db.sha256_hex(b"wheel bytes"), wheel_path),
        ]
    }
    with pytest.raises(db.BundleVerificationError, match="not a safe bare filename"):
        db.build_local_index(index_root, packages)
    assert not evil_target.exists()
    assert not index_root.exists()


def test_build_local_index_refuses_a_dotdot_filename(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    wheel_path = source_dir / "wheel.whl"
    wheel_path.write_bytes(b"wheel bytes")
    index_root = tmp_path / "index"
    packages = {"dotmac-kernel": [("..", db.sha256_hex(b"wheel bytes"), wheel_path)]}
    with pytest.raises(db.BundleVerificationError, match="not a safe bare filename"):
        db.build_local_index(index_root, packages)


def test_build_local_index_escapes_html_metacharacters_in_anchors(
    tmp_path: Path,
) -> None:
    """Finding 3: a caller-controlled filename reaches a resolver-facing
    HTML page; without escaping, `<`/`>`/`&`/`"` in a filename would inject
    markup into that page."""

    source_dir = tmp_path / "source"
    source_dir.mkdir()
    filename = 'inject"><script>alert(1)</script>.whl'
    wheel_path = source_dir / "wheel.whl"
    wheel_path.write_bytes(b"wheel bytes")
    index_root = tmp_path / "index"
    packages = {
        "dotmac-kernel": [(filename, db.sha256_hex(b"wheel bytes"), wheel_path)]
    }
    db.build_local_index(index_root, packages)
    html_text = (index_root / "simple" / "dotmac-kernel" / "index.html").read_text(
        encoding="utf-8"
    )
    assert "<script>" not in html_text
    assert "&lt;script&gt;" in html_text


# ── item 7: strong local run-metadata validation ─────────────────────


def _valid_policy() -> dict:
    return {
        "repository": {"full_name": "michaelayoade/dotmac_erp", "id": 1141216651},
        "producer_workflow_path": ".github/workflows/dependency-bundle-produce.yml",
        "binder_workflow_path": ".github/workflows/dependency-bundle-bind.yml",
        "environment_name": "forgejo-registry-read-main",
        "artifact_name_pattern": "erp-dependency-bundle-{plan_digest}",
    }


#: The SHA `_write_git_head` bakes into every synthetic candidate checkout
#: below — a fixed, known value so a test can assert
#: `bind_bundle_to_candidate` returned exactly the SHA the candidate's own
#: `.git/HEAD` names, not something asserted by the caller.
_CANDIDATE_HEAD_SHA = "c" * 40


def _write_git_head(root: Path, sha: str = _CANDIDATE_HEAD_SHA) -> None:
    """A minimal, detached-HEAD-shaped `.git` directory: `.git/HEAD`
    containing a raw 40-hex commit SHA directly, which is exactly what a
    real detached-HEAD checkout (e.g. `actions/checkout`'s default) looks
    like on disk. `_read_git_head_sha` reads this instead of accepting a
    caller-asserted SHA."""

    git_dir = root / ".git"
    git_dir.mkdir(exist_ok=True)
    (git_dir / "HEAD").write_text(sha + "\n", encoding="utf-8")


def _candidate_root(tmp_path: Path) -> Path:
    """A real, on-disk candidate tree — `verify_run_metadata` and
    `bind_bundle_to_candidate` now derive the candidate's plan digest (and,
    for the latter, its commit SHA) themselves by reading this, rather
    than accepting either as a bare caller-supplied value."""

    root = tmp_path / "candidate"
    root.mkdir(exist_ok=True)
    project_root = _project_root(root, BASE_PYPROJECT, BASE_LOCK)
    _write_git_head(project_root)
    return project_root


def _candidate_digest(candidate_root: Path) -> str:
    return db.compute_plan_digest(db.extract_dependency_surface(candidate_root))


def _valid_run_metadata(candidate_digest: str) -> dict:
    return {
        "repository_full_name": "michaelayoade/dotmac_erp",
        "repository_id": 1141216651,
        "workflow_path": ".github/workflows/dependency-bundle-produce.yml",
        "run_id": 111,
        "run_attempt": 1,
        "trusted_workflow_sha": "a" * 40,
        "artifact_id": 222,
        "artifact_run_id": 111,
        "artifact_name": f"erp-dependency-bundle-{candidate_digest}",
        "environment_name": "forgejo-registry-read-main",
    }


def test_valid_run_metadata_verifies(tmp_path: Path) -> None:
    candidate_root = _candidate_root(tmp_path)
    digest = _candidate_digest(candidate_root)
    result = db.verify_run_metadata(
        _valid_run_metadata(digest), _valid_policy(), candidate_root=candidate_root
    )
    assert result.run_id == 111


@pytest.mark.parametrize(
    "missing_field",
    [
        "repository_full_name",
        "repository_id",
        "workflow_path",
        "run_id",
        "run_attempt",
        "trusted_workflow_sha",
        "artifact_id",
        "artifact_name",
        "artifact_run_id",
        "environment_name",
    ],
)
def test_each_missing_run_metadata_field_refuses_independently(
    missing_field: str, tmp_path: Path
) -> None:
    candidate_root = _candidate_root(tmp_path)
    metadata = _valid_run_metadata(_candidate_digest(candidate_root))
    del metadata[missing_field]
    with pytest.raises(db.BundleVerificationError, match="missing required fields"):
        db.verify_run_metadata(metadata, _valid_policy(), candidate_root=candidate_root)


def test_the_all_zero_null_sha_is_refused(tmp_path: Path) -> None:
    """Previously PASSED: `_COMMIT_SHA.match` alone accepts 40 zero
    characters as valid hex."""

    candidate_root = _candidate_root(tmp_path)
    metadata = _valid_run_metadata(_candidate_digest(candidate_root))
    metadata["trusted_workflow_sha"] = "0" * 40
    with pytest.raises(db.BundleVerificationError, match="null SHA"):
        db.verify_run_metadata(metadata, _valid_policy(), candidate_root=candidate_root)


@pytest.mark.parametrize(
    "field",
    ["run_id", "run_attempt", "artifact_id", "repository_id", "artifact_run_id"],
)
def test_a_negative_coordinate_is_refused(field: str, tmp_path: Path) -> None:
    """Previously PASSED: only presence was checked, not sign."""

    candidate_root = _candidate_root(tmp_path)
    metadata = _valid_run_metadata(_candidate_digest(candidate_root))
    metadata[field] = -1
    with pytest.raises(db.BundleVerificationError, match="positive integer"):
        db.verify_run_metadata(metadata, _valid_policy(), candidate_root=candidate_root)


@pytest.mark.parametrize(
    "field",
    ["run_id", "run_attempt", "artifact_id", "repository_id", "artifact_run_id"],
)
def test_a_float_coordinate_is_refused(field: str, tmp_path: Path) -> None:
    """Previously PASSED: `int(1.9) == 1` truncates a float silently
    instead of refusing it."""

    candidate_root = _candidate_root(tmp_path)
    metadata = _valid_run_metadata(_candidate_digest(candidate_root))
    metadata[field] = 111.9
    with pytest.raises(db.BundleVerificationError, match="positive integer"):
        db.verify_run_metadata(metadata, _valid_policy(), candidate_root=candidate_root)


def test_the_binder_workflow_path_is_refused_a_producer_is_required(
    tmp_path: Path,
) -> None:
    """Previously PASSED: either path was accepted."""

    candidate_root = _candidate_root(tmp_path)
    metadata = _valid_run_metadata(_candidate_digest(candidate_root))
    metadata["workflow_path"] = ".github/workflows/dependency-bundle-bind.yml"
    with pytest.raises(db.BundleVerificationError, match="PRODUCER"):
        db.verify_run_metadata(metadata, _valid_policy(), candidate_root=candidate_root)


def test_an_artifact_name_unrelated_to_the_plan_digest_is_refused(
    tmp_path: Path,
) -> None:
    """Previously PASSED: artifact_name was never checked at all."""

    candidate_root = _candidate_root(tmp_path)
    metadata = _valid_run_metadata(_candidate_digest(candidate_root))
    metadata["artifact_name"] = "unrelated-artifact"
    with pytest.raises(db.BundleVerificationError, match="does not match the expected"):
        db.verify_run_metadata(metadata, _valid_policy(), candidate_root=candidate_root)


def test_wrong_environment_is_refused(tmp_path: Path) -> None:
    """Previously PASSED: environment was never checked at all."""

    candidate_root = _candidate_root(tmp_path)
    metadata = _valid_run_metadata(_candidate_digest(candidate_root))
    metadata["environment_name"] = "some-other-env"
    with pytest.raises(db.BundleVerificationError, match="environment_name"):
        db.verify_run_metadata(metadata, _valid_policy(), candidate_root=candidate_root)


def test_artifact_belonging_to_a_different_run_is_refused(tmp_path: Path) -> None:
    """Previously PASSED: artifact ownership was never checked at all."""

    candidate_root = _candidate_root(tmp_path)
    metadata = _valid_run_metadata(_candidate_digest(candidate_root))
    metadata["artifact_run_id"] = 999
    with pytest.raises(db.BundleVerificationError, match="does not belong"):
        db.verify_run_metadata(metadata, _valid_policy(), candidate_root=candidate_root)


def test_verify_run_metadata_docstring_states_it_is_local_only() -> None:
    doc = db.verify_run_metadata.__doc__ or ""
    assert "LOCAL" in doc or "local" in doc


# ── candidate binding ──────────────────────────────────────────────────


def test_binding_refuses_a_digest_mismatch(tmp_path: Path) -> None:
    surface = _manifest_test_surface()
    wheel_path = _write_wheel(tmp_path)
    archive_path = _write_archive(tmp_path)
    manifest = db.create_bundle_manifest(
        surface=surface,
        acquired_files={_MANIFEST_TEST_WHEEL_NAME: wheel_path},
        archive_path=archive_path,
        run=_run(),
    )
    # `_candidate_root` extracts to a DIFFERENT surface than `surface`
    # (a direct dotmac-kernel dependency with a different pinned wheel
    # hash) -- binding against it must be refused.
    mismatched_root = _candidate_root(tmp_path)
    with pytest.raises(db.BundleVerificationError, match="does not match"):
        db.bind_bundle_to_candidate(manifest, _run(), mismatched_root)


#: A real, on-disk manifest+lock pair whose extracted `DependencySurface`
#: is EXACTLY `_manifest_test_surface()`'s: no direct forgejo dependency
#: (the manifest declares none), one transitive forgejo lock package
#: (`dotmac-kernel`) whose wheel filename/hash matches
#: `_MANIFEST_TEST_LOCK_PKG` exactly. `extract_dependency_surface` does not
#: require a forgejo lock package to have a matching manifest dependency —
#: only the reverse — so this is a legitimate, real shape, not a fabricated
#: one.
_MATCHING_CANDIDATE_PYPROJECT = base_pyproject().replace(
    'dotmac-kernel = {version = "0.1.0a1", source = "forgejo"}\n', ""
)
_MATCHING_CANDIDATE_LOCK = BASE_LOCK.replace(
    'hash = "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"',
    f'hash = "sha256:{_MANIFEST_TEST_WHEEL_SHA256}"',
)


def _matching_candidate_root(tmp_path: Path, subdir: str) -> Path:
    """A candidate tree whose extracted surface digest is guaranteed equal
    to `_manifest_test_surface()`'s -- shared by every test below that
    needs binding's DIGEST check to already have passed, so it can isolate
    a different refusal (SHA/RunMetadata) instead."""

    directory = tmp_path / subdir
    directory.mkdir()
    candidate_root = _project_root(
        directory, _MATCHING_CANDIDATE_PYPROJECT, _MATCHING_CANDIDATE_LOCK
    )
    _write_git_head(candidate_root)
    return candidate_root


def test_binding_succeeds_when_digests_match(tmp_path: Path) -> None:
    surface = _manifest_test_surface()
    wheel_path = _write_wheel(tmp_path)
    archive_path = _write_archive(tmp_path)
    manifest = db.create_bundle_manifest(
        surface=surface,
        acquired_files={_MANIFEST_TEST_WHEEL_NAME: wheel_path},
        archive_path=archive_path,
        run=_run(),
    )
    candidate_root = _matching_candidate_root(tmp_path, "matching-candidate")
    assert _candidate_digest(candidate_root) == manifest["plan_digest"], (
        "test fixture bug: the matching candidate must extract to the exact "
        "same surface as _manifest_test_surface()"
    )
    binding = db.bind_bundle_to_candidate(manifest, _run(), candidate_root)
    assert binding.bundle_run_id == 111
    assert binding.candidate_sha == _CANDIDATE_HEAD_SHA, (
        "the binding must report the SHA read from the candidate's own "
        ".git/HEAD, not an asserted value"
    )


def test_binding_refuses_a_candidate_with_no_git_checkout(tmp_path: Path) -> None:
    """Finding 4: candidate_sha used to be a bare parameter the caller
    asserted; it is now derived from candidate_root's own .git, and a
    candidate that is not a git checkout at all must be refused rather
    than silently accepted with no SHA to report."""

    surface = _manifest_test_surface()
    wheel_path = _write_wheel(tmp_path)
    archive_path = _write_archive(tmp_path)
    manifest = db.create_bundle_manifest(
        surface=surface,
        acquired_files={_MANIFEST_TEST_WHEEL_NAME: wheel_path},
        archive_path=archive_path,
        run=_run(),
    )
    no_git_root = tmp_path / "no-git-candidate"
    no_git_root.mkdir()
    _project_root(no_git_root, BASE_PYPROJECT, BASE_LOCK)  # no .git written
    with pytest.raises(db.BundleVerificationError, match="not a git checkout"):
        db.bind_bundle_to_candidate(manifest, _run(), no_git_root)


def test_binding_refuses_a_null_sha_head(tmp_path: Path) -> None:
    surface = _manifest_test_surface()
    wheel_path = _write_wheel(tmp_path)
    archive_path = _write_archive(tmp_path)
    manifest = db.create_bundle_manifest(
        surface=surface,
        acquired_files={_MANIFEST_TEST_WHEEL_NAME: wheel_path},
        archive_path=archive_path,
        run=_run(),
    )
    null_sha_root = tmp_path / "null-sha-candidate"
    null_sha_root.mkdir()
    _project_root(null_sha_root, BASE_PYPROJECT, BASE_LOCK)
    _write_git_head(null_sha_root, sha="0" * 40)
    with pytest.raises(db.BundleVerificationError, match="null SHA"):
        db.bind_bundle_to_candidate(manifest, _run(), null_sha_root)


def test_binding_refuses_a_raw_dict_in_place_of_runmetadata(tmp_path: Path) -> None:
    """Finding 4: bind_bundle_to_candidate used to re-derive run_id/
    artifact_id from bundle_manifest["run"] itself with a loose int()
    coercion (accepting True, truncating 1.9). It now requires the
    ALREADY-VERIFIED RunMetadata object verify_run_metadata returns, and
    refuses a raw dict outright rather than re-deriving trust from
    unvalidated fields."""

    surface = _manifest_test_surface()
    wheel_path = _write_wheel(tmp_path)
    archive_path = _write_archive(tmp_path)
    manifest = db.create_bundle_manifest(
        surface=surface,
        acquired_files={_MANIFEST_TEST_WHEEL_NAME: wheel_path},
        archive_path=archive_path,
        run=_run(),
    )
    candidate_root = _matching_candidate_root(tmp_path, "matching-candidate")
    with pytest.raises(
        db.BundleVerificationError, match="already-verified RunMetadata"
    ):
        db.bind_bundle_to_candidate(manifest, {"run_id": 111}, candidate_root)


def test_binding_refuses_a_runmetadata_for_an_unrelated_run(tmp_path: Path) -> None:
    """Finding 4: a validly-shaped RunMetadata for a DIFFERENT run must not
    be mixable with this bundle_manifest -- the two are cross-checked, not
    merely type-checked."""

    surface = _manifest_test_surface()
    wheel_path = _write_wheel(tmp_path)
    archive_path = _write_archive(tmp_path)
    manifest = db.create_bundle_manifest(
        surface=surface,
        acquired_files={_MANIFEST_TEST_WHEEL_NAME: wheel_path},
        archive_path=archive_path,
        run=_run(),
    )
    unrelated_run = dataclasses.replace(_run(), run_id=999, artifact_run_id=999)
    candidate_root = _matching_candidate_root(tmp_path, "matching-candidate")
    with pytest.raises(db.BundleVerificationError, match="unrelated run"):
        db.bind_bundle_to_candidate(manifest, unrelated_run, candidate_root)


# ── no registry fallback: static proof over the module's own source ──────


def test_no_function_in_this_module_names_a_network_call() -> None:
    source = (ROOT / "scripts" / "dependency_bundle.py").read_text(encoding="utf-8")
    for forbidden in ("urllib.request", "http.client", "requests.", "httpx."):
        assert forbidden not in source, (
            f"{forbidden!r} must not appear: no network calls"
        )


# ── policy: repository id is now resolved ─────────────────────────────


def test_the_shipped_policy_file_loads_with_its_resolved_repository_id() -> None:
    policy = db.load_policy(REAL_POLICY_PATH)
    assert policy["repository"]["id"] == 1141216651
    assert policy["repository"]["full_name"] == "michaelayoade/dotmac_erp"


@pytest.mark.parametrize("bad_id", [0, -1, None, "1141216651", 1141216651.0])
def test_a_non_positive_or_wrongly_typed_repository_id_is_still_refused(bad_id) -> None:
    raw = json.loads(REAL_POLICY_PATH.read_text(encoding="utf-8"))
    raw["repository"]["id"] = bad_id
    with tempfile.TemporaryDirectory() as d:
        candidate = Path(d) / "policy.json"
        candidate.write_text(json.dumps(raw), encoding="utf-8")
        with pytest.raises(db.PolicyError, match="positive integer"):
            db.load_policy(candidate)


# ── policy: schema/target/retention/workflow-path/pattern validation ─────
# (finding 5) -- previously ANY schema_version, target shape, retention
# value, workflow-path type, or artifact_name_pattern was accepted.


def _load_policy_with(mutate) -> None:
    raw = json.loads(REAL_POLICY_PATH.read_text(encoding="utf-8"))
    mutate(raw)
    with tempfile.TemporaryDirectory() as d:
        candidate = Path(d) / "policy.json"
        candidate.write_text(json.dumps(raw), encoding="utf-8")
        db.load_policy(candidate)


def test_the_shipped_policy_still_passes_every_new_validation() -> None:
    db.load_policy(REAL_POLICY_PATH)


@pytest.mark.parametrize("bad_schema_version", [0, 2, "1", 1.0, None])
def test_a_wrong_schema_version_is_refused(bad_schema_version) -> None:
    with pytest.raises(db.PolicyError, match="schema_version"):
        _load_policy_with(
            lambda raw: raw.__setitem__("schema_version", bad_schema_version)
        )


def test_a_target_missing_python_is_refused() -> None:
    with pytest.raises(db.PolicyError, match="target"):
        _load_policy_with(lambda raw: raw["target"].pop("python"))


def test_a_target_with_the_wrong_platform_is_refused() -> None:
    with pytest.raises(db.PolicyError, match="target"):
        _load_policy_with(
            lambda raw: raw["target"].__setitem__("platform", "win_amd64")
        )


@pytest.mark.parametrize("bad_retention", [0, -1, 14.5, "14", None])
def test_a_non_positive_or_wrongly_typed_retention_is_refused(bad_retention) -> None:
    with pytest.raises(db.PolicyError, match="artifact_retention_days"):
        _load_policy_with(
            lambda raw: raw.__setitem__("artifact_retention_days", bad_retention)
        )


@pytest.mark.parametrize(
    "bad_path", ["not-under-workflows.yml", ".github/workflows/no-extension", 123, None]
)
def test_a_malformed_workflow_path_is_refused(bad_path) -> None:
    with pytest.raises(db.PolicyError, match="producer_workflow_path"):
        _load_policy_with(
            lambda raw: raw.__setitem__("producer_workflow_path", bad_path)
        )


def test_identical_producer_and_binder_paths_are_refused() -> None:
    with pytest.raises(db.PolicyError, match="two different workflow files"):
        _load_policy_with(
            lambda raw: raw.__setitem__(
                "binder_workflow_path", raw["producer_workflow_path"]
            )
        )


@pytest.mark.parametrize(
    "bad_pattern", ["erp-dependency-bundle-no-placeholder", "", 123, None]
)
def test_an_artifact_name_pattern_without_the_placeholder_is_refused(
    bad_pattern,
) -> None:
    with pytest.raises(db.PolicyError, match="artifact_name_pattern"):
        _load_policy_with(
            lambda raw: raw.__setitem__("artifact_name_pattern", bad_pattern)
        )


@pytest.mark.parametrize(
    "malformed_pattern",
    [
        # Each of these contains the literal '{plan_digest}' substring (so
        # it passes the first, substring-only check) but still fails to
        # `.format(plan_digest=...)`.
        "erp-dependency-bundle-{plan_digest}-{unexpected_field}",
        "erp-dependency-bundle-{plan_digest}-{bad!q}",
        "erp-dependency-bundle-{plan_digest}-{unbalanced",
    ],
)
def test_an_artifact_name_pattern_that_looks_valid_but_does_not_format_is_refused(
    malformed_pattern,
) -> None:
    """Finding 6: a substring check on '{plan_digest}' alone does not prove
    `.format(plan_digest=...)` succeeds -- an extra field, a bad
    conversion, or unbalanced braces all still contain the literal
    substring and would previously reach verify_run_metadata's
    `.format(...)` call as a raw KeyError/ValueError/IndexError."""

    with pytest.raises(db.PolicyError, match="not a valid format string"):
        _load_policy_with(
            lambda raw: raw.__setitem__("artifact_name_pattern", malformed_pattern)
        )


@pytest.mark.parametrize("field,bad_value", [("url", 123), ("tag", None), ("url", "")])
def test_load_permitted_off_index_dependencies_refuses_non_string_fields(
    field, bad_value
) -> None:
    """Finding 6: a non-string url/tag used to pass straight through into
    an OffIndexPin and only fail later, deep inside URL normalisation or a
    raw string comparison, with a confusing error far from the real cause."""

    policy = json.loads(REAL_POLICY_PATH.read_text(encoding="utf-8"))
    policy["permitted_off_index_dependencies"]["dotmac-integration-client"][field] = (
        bad_value
    )
    with pytest.raises(db.PolicyError, match=f"\\.{field} must be"):
        db.load_permitted_off_index_dependencies(policy)


# ═══════════════════════════════════════════════════════════════════════
# 3. Duplication: shared-vector table + unlisted-duplicate detector
# ═══════════════════════════════════════════════════════════════════════

_PAGE_URL = "https://registry.dotmac.io/api/packages/dotmac/pypi/simple/dotmac-kernel/"

URL_VECTORS: list[tuple[str, bool, str]] = [
    (
        "dotmac_kernel-0.1.0a1-py3-none-any.whl#sha256=" + "a" * 64,
        False,
        "a normal, in-prefix artifact link with a fragment is accepted",
    ),
    ("", True, "an empty href is refused"),
    (
        "http://registry.dotmac.io/api/packages/dotmac/pypi/files/x.whl",
        True,
        "non-https is refused",
    ),
    (
        "https://attacker:pw@registry.dotmac.io/api/packages/dotmac/pypi/files/x.whl",
        True,
        "embedded userinfo is refused",
    ),
    (
        "https://evil.example.com/api/packages/dotmac/pypi/files/x.whl",
        True,
        "a different host is refused",
    ),
    (
        "https://registry.dotmac.io/api/packages/dotmac/pypi/files/../../../etc/passwd",
        True,
        "a literal .. traversal segment is refused",
    ),
    (
        "https://registry.dotmac.io/api/packages/dotmac/pypi/files/%2e%2e/%2e%2e/etc/passwd",
        True,
        "a once-percent-encoded .. traversal segment is refused",
    ),
    (
        "https://registry.dotmac.io/api/packages/dotmac/pypi/files/%252e%252e/etc/passwd",
        True,
        "a twice-percent-encoded .. traversal segment is refused",
    ),
    (
        "https://registry.dotmac.io/api/packages/dotmac/pypi/files/x\\y.whl",
        True,
        "a literal backslash is refused",
    ),
    (
        "https://registry.dotmac.io/other/path/x.whl",
        True,
        "a path outside the approved prefix is refused",
    ),
]


def _refuses_db(href: str) -> bool:
    try:
        db.approved_artifact_url(href, _PAGE_URL)
    except db.DependencyBundleError:
        return True
    return False


def _refuses_erp_lock(href: str) -> bool:
    try:
        erp_lock.approved_artifact_url(href, _PAGE_URL)
    except erp_lock.Refusal:
        return True
    return False


@pytest.mark.parametrize(
    "href,expect_refusal,reason", URL_VECTORS, ids=[v[2] for v in URL_VECTORS]
)
def test_both_implementations_of_approved_artifact_url_agree_on_every_vector(
    href: str, expect_refusal: bool, reason: str
) -> None:
    db_refused = _refuses_db(href)
    erp_lock_refused = _refuses_erp_lock(href)
    assert db_refused == expect_refusal, f"dependency_bundle: {reason}"
    assert erp_lock_refused == expect_refusal, f"erp_lock: {reason}"
    assert db_refused == erp_lock_refused, (
        f"the two implementations DISAGREED on {href!r} ({reason}): "
        f"dependency_bundle refused={db_refused}, erp_lock refused={erp_lock_refused}"
    )


# ── off-index repository-URL identity: erp_lock.off_index_pin_problems vs
#    dependency_bundle._classify_off_index_spec, over the SAME pin ─────────

_OFF_INDEX_PIN_NAME = "dotmac-integration-client"
_OFF_INDEX_PIN_URL = "https://github.com/michaelayoade/dotmac-integration-client.git"
_OFF_INDEX_PIN_TAG = "v0.2.0"
_OFF_INDEX_PIN_COMMIT = "a4fe55f4ed704c556c4d1e3cc728ec4ef0dd8042"

# Each vector: (declared_git_url, expect_refusal, reason). Both
# `erp_lock.off_index_pin_problems` and
# `dependency_bundle._classify_off_index_spec` are driven through every
# row, against the identical pinned identity, and must agree on
# accept/refuse. This is the vector table for the SEMANTIC divergence a
# body-similarity detector cannot see: one path normalised both sides of
# the comparison, the other compared raw strings.
OFF_INDEX_URL_VECTORS: list[tuple[str, bool, str]] = [
    (
        _OFF_INDEX_PIN_URL,
        False,
        "the exact pinned spelling is accepted",
    ),
    (
        "https://github.com/michaelayoade/dotmac-integration-client",
        False,
        "a missing .git suffix, present only on the pin's side, normalises equal",
    ),
    (
        _OFF_INDEX_PIN_URL + "/",
        False,
        "a trailing slash normalises equal",
    ),
    (
        "HTTPS://GitHub.com/michaelayoade/dotmac-integration-client.git",
        False,
        "a mixed-case scheme and host normalises equal",
    ),
    (
        "http://github.com/michaelayoade/dotmac-integration-client.git",
        True,
        "a non-https scheme is a spelling the normaliser does not recognise, "
        "and is refused rather than guessed at",
    ),
    (
        "git@github.com:michaelayoade/dotmac-integration-client.git",
        True,
        "an ssh-form URL is a spelling the normaliser does not recognise, "
        "and is refused rather than guessed at",
    ),
    (
        "https:///dotmac-integration-client.git",
        True,
        "an empty netloc is a spelling the normaliser does not recognise, "
        "and is refused rather than guessed at",
    ),
]


def _refuses_erp_lock_off_index(declared_url: str) -> bool:
    pin = erp_lock.OffIndexPin(
        url=_OFF_INDEX_PIN_URL, tag=_OFF_INDEX_PIN_TAG, commit=_OFF_INDEX_PIN_COMMIT
    )
    spec = {"git": declared_url, "tag": _OFF_INDEX_PIN_TAG}
    problems = erp_lock.off_index_pin_problems(f"main.{_OFF_INDEX_PIN_NAME}", spec, pin)
    return bool(problems)


def _refuses_db_off_index(declared_url: str) -> bool:
    pin = db.OffIndexPin(
        url=_OFF_INDEX_PIN_URL, tag=_OFF_INDEX_PIN_TAG, commit=_OFF_INDEX_PIN_COMMIT
    )
    spec = {"git": declared_url, "tag": _OFF_INDEX_PIN_TAG}
    try:
        db._classify_off_index_spec(
            _OFF_INDEX_PIN_NAME, spec, "main", False, {_OFF_INDEX_PIN_NAME: pin}
        )
    except db.DependencyBundleError:
        return True
    return False


@pytest.mark.parametrize(
    "declared_url,expect_refusal,reason",
    OFF_INDEX_URL_VECTORS,
    ids=[v[2] for v in OFF_INDEX_URL_VECTORS],
)
def test_both_implementations_of_off_index_url_identity_agree_on_every_vector(
    declared_url: str, expect_refusal: bool, reason: str
) -> None:
    db_refused = _refuses_db_off_index(declared_url)
    erp_lock_refused = _refuses_erp_lock_off_index(declared_url)
    assert db_refused == expect_refusal, f"dependency_bundle: {reason}"
    assert erp_lock_refused == expect_refusal, f"erp_lock: {reason}"
    assert db_refused == erp_lock_refused, (
        f"the two implementations DISAGREED on {declared_url!r} ({reason}): "
        f"dependency_bundle refused={db_refused}, erp_lock refused={erp_lock_refused}"
    )


# ── the LOCK-level off-index URL pair: erp_lock.off_index_lock_problems vs
#    dependency_bundle._verify_off_index_lock_entry ─────────────────────────
#
# This is the pair the original convergence did NOT touch: the review found
# it independently, at a body-similarity ratio of 0.185 (both accept the
# same lock-URL spellings via the shared normaliser now, but erp_lock
# accumulates a problem list against its own global ALLOWED_OFF_INDEX_
# DEPENDENCIES while dependency_bundle raises against a caller-supplied
# ApprovedOffIndexDependency). Reuses OFF_INDEX_URL_VECTORS -- it is the
# same question, "does this declared repository URL identify the pinned
# one", asked one layer down at the LOCK instead of the manifest.


def _lock_with_off_index_url(declared_url: str) -> dict:
    return {
        "package": [
            {
                "name": _OFF_INDEX_PIN_NAME,
                "source": {
                    "type": "git",
                    "url": declared_url,
                    "reference": _OFF_INDEX_PIN_TAG,
                    "resolved_reference": _OFF_INDEX_PIN_COMMIT,
                },
            }
        ]
    }


def _refuses_erp_lock_off_index_lock(declared_url: str) -> bool:
    return bool(
        erp_lock.off_index_lock_problems(_lock_with_off_index_url(declared_url))
    )


def _refuses_db_off_index_lock(declared_url: str) -> bool:
    dep = db.ApprovedOffIndexDependency(
        name=_OFF_INDEX_PIN_NAME,
        normalised_name=db.normalise_name(_OFF_INDEX_PIN_NAME),
        group="main",
        url=_OFF_INDEX_PIN_URL,
        tag=_OFF_INDEX_PIN_TAG,
        resolved_commit=_OFF_INDEX_PIN_COMMIT,
        group_optional=False,
    )
    try:
        db._verify_off_index_lock_entry(dep, _lock_with_off_index_url(declared_url))
    except db.DependencyBundleError:
        return True
    return False


@pytest.mark.parametrize(
    "declared_url,expect_refusal,reason",
    OFF_INDEX_URL_VECTORS,
    ids=[v[2] for v in OFF_INDEX_URL_VECTORS],
)
def test_both_implementations_of_off_index_lock_url_identity_agree_on_every_vector(
    declared_url: str, expect_refusal: bool, reason: str
) -> None:
    db_refused = _refuses_db_off_index_lock(declared_url)
    erp_lock_refused = _refuses_erp_lock_off_index_lock(declared_url)
    assert db_refused == expect_refusal, f"dependency_bundle (lock): {reason}"
    assert erp_lock_refused == expect_refusal, f"erp_lock (lock): {reason}"
    assert db_refused == erp_lock_refused, (
        f"the two implementations DISAGREED on lock url {declared_url!r} "
        f"({reason}): dependency_bundle refused={db_refused}, "
        f"erp_lock refused={erp_lock_refused}"
    )


# ── the LOCK-level off-index NAME pair (finding 5): erp_lock normalises
#    both sides; dependency_bundle used to compare raw strings ───────────

OFF_INDEX_NAME_VECTORS: list[tuple[str, bool, str]] = [
    (_OFF_INDEX_PIN_NAME, False, "the exact pinned name is accepted"),
    (
        "Dotmac_Integration_Client",
        False,
        "mixed case and underscores normalise equal",
    ),
    (
        "dotmac.integration.client",
        False,
        "dots normalise equal to the pinned hyphenated name",
    ),
    (
        "DOTMAC-INTEGRATION-CLIENT",
        False,
        "uppercase normalises equal",
    ),
    (
        "dotmac-integration-client-other",
        True,
        "a genuinely different name does not match",
    ),
    (
        "dotmac-integration",
        True,
        "a name that is merely a prefix does not match",
    ),
]


def _lock_with_off_index_name(declared_name: str) -> dict:
    return {
        "package": [
            {
                "name": declared_name,
                "source": {
                    "type": "git",
                    "url": _OFF_INDEX_PIN_URL,
                    "reference": _OFF_INDEX_PIN_TAG,
                    "resolved_reference": _OFF_INDEX_PIN_COMMIT,
                },
            }
        ]
    }


def _refuses_erp_lock_off_index_lock_name(declared_name: str) -> bool:
    return bool(
        erp_lock.off_index_lock_problems(_lock_with_off_index_name(declared_name))
    )


def _refuses_db_off_index_lock_name(declared_name: str) -> bool:
    dep = db.ApprovedOffIndexDependency(
        name=_OFF_INDEX_PIN_NAME,
        normalised_name=db.normalise_name(_OFF_INDEX_PIN_NAME),
        group="main",
        url=_OFF_INDEX_PIN_URL,
        tag=_OFF_INDEX_PIN_TAG,
        resolved_commit=_OFF_INDEX_PIN_COMMIT,
        group_optional=False,
    )
    try:
        db._verify_off_index_lock_entry(dep, _lock_with_off_index_name(declared_name))
    except db.DependencyBundleError:
        return True
    return False


@pytest.mark.parametrize(
    "declared_name,expect_refusal,reason",
    OFF_INDEX_NAME_VECTORS,
    ids=[v[2] for v in OFF_INDEX_NAME_VECTORS],
)
def test_both_implementations_of_off_index_lock_name_identity_agree_on_every_vector(
    declared_name: str, expect_refusal: bool, reason: str
) -> None:
    db_refused = _refuses_db_off_index_lock_name(declared_name)
    erp_lock_refused = _refuses_erp_lock_off_index_lock_name(declared_name)
    assert db_refused == expect_refusal, f"dependency_bundle (lock name): {reason}"
    assert erp_lock_refused == expect_refusal, f"erp_lock (lock name): {reason}"
    assert db_refused == erp_lock_refused, (
        f"the two implementations DISAGREED on lock name {declared_name!r} "
        f"({reason}): dependency_bundle refused={db_refused}, "
        f"erp_lock refused={erp_lock_refused}"
    )


def test_normalise_repository_url_does_not_erase_a_query_string() -> None:
    """Finding 4's second half: a query string or fragment used to be
    discarded during normalisation, so `repo.git` and `repo.git?x=1`
    compared equal even though the query string is part of what actually
    reaches the resolver. It must now make the two compare UNEQUAL."""

    plain = dependency_normalisation.normalise_repository_url(_OFF_INDEX_PIN_URL)
    with_query = dependency_normalisation.normalise_repository_url(
        _OFF_INDEX_PIN_URL + "?different-input"
    )
    assert plain != with_query


def test_normalise_repository_url_does_not_raise_on_a_malformed_authority() -> None:
    """Finding 6: a malformed IPv6-shaped authority (unbalanced brackets)
    makes `urllib.parse.urlsplit` itself raise ValueError. This pure
    comparison helper must never raise -- an unrecognised spelling is
    returned unchanged, exactly like a non-https scheme or empty netloc."""

    malformed = "https://[::1/not-a-valid-authority"
    result = dependency_normalisation.normalise_repository_url(malformed)
    assert result == malformed


def test_a_query_string_on_the_off_index_pin_is_refused_by_both() -> None:
    tampered = _OFF_INDEX_PIN_URL + "?different-input"
    assert _refuses_db_off_index(tampered) is True
    assert _refuses_erp_lock_off_index(tampered) is True
    assert _refuses_db_off_index_lock(tampered) is True
    assert _refuses_erp_lock_off_index_lock(tampered) is True


def test_dependency_bundle_imports_the_shared_repository_url_normaliser() -> None:
    assert (
        db.normalise_repository_url is dependency_normalisation.normalise_repository_url
    )


def test_erp_lock_imports_the_shared_repository_url_normaliser() -> None:
    assert (
        erp_lock._normalised_repository_url
        is dependency_normalisation.normalise_repository_url
    )


def test_classify_off_index_spec_refuses_a_non_dict_spec_rather_than_crashing() -> None:
    """`erp_lock.off_index_pin_problems` names a non-dict spec as a problem
    rather than crashing. `_classify_off_index_spec` must do the same — a
    raw TypeError/AttributeError is not a refusal."""

    pin = db.OffIndexPin(
        url=_OFF_INDEX_PIN_URL, tag=_OFF_INDEX_PIN_TAG, commit=_OFF_INDEX_PIN_COMMIT
    )
    with pytest.raises(db.ManifestError, match="must be a table"):
        db._classify_off_index_spec(
            _OFF_INDEX_PIN_NAME, 123, "main", False, {_OFF_INDEX_PIN_NAME: pin}
        )
    # erp_lock's own equivalent guard, for comparison: a named problem, not a raise.
    erp_pin = erp_lock.OffIndexPin(
        url=_OFF_INDEX_PIN_URL, tag=_OFF_INDEX_PIN_TAG, commit=_OFF_INDEX_PIN_COMMIT
    )
    problems = erp_lock.off_index_pin_problems(
        f"main.{_OFF_INDEX_PIN_NAME}", 123, erp_pin
    )
    assert problems, "erp_lock's own guard should also name this a problem"


CREDENTIAL = "s3cr3t/token value=42"

CREDENTIAL_VECTORS: list[tuple[str, bool, str]] = [
    (
        "nothing interesting here",
        False,
        "a file with no trace of the credential is clean",
    ),
    (f"Authorization: Bearer {CREDENTIAL}", True, "the plain credential is found"),
    (
        "encoded=" + urllib.parse.quote(CREDENTIAL, safe=""),
        True,
        "the percent-encoded credential is found",
    ),
    (
        "b64=" + base64.b64encode(CREDENTIAL.encode()).decode("ascii"),
        True,
        "the base64-encoded credential is found",
    ),
    (
        "authz=" + base64.b64encode(f"ci-reader:{CREDENTIAL}".encode()).decode("ascii"),
        True,
        "the base64 basic-auth encoding of the credential is found",
    ),
]


@pytest.mark.parametrize(
    "haystack,expect_found,reason",
    CREDENTIAL_VECTORS,
    ids=[v[2] for v in CREDENTIAL_VECTORS],
)
def test_both_implementations_of_credential_scanning_agree_on_every_vector(
    tmp_path: Path, haystack: str, expect_found: bool, reason: str
) -> None:
    target = tmp_path / "evidence.txt"
    target.write_text(haystack, encoding="utf-8")

    db_found = bool(db.scan_for_credential([target], CREDENTIAL))
    erp_lock_found = bool(erp_lock.credential_sightings([target], CREDENTIAL))

    assert db_found == expect_found, f"dependency_bundle: {reason}"
    assert erp_lock_found == expect_found, f"erp_lock: {reason}"
    assert db_found == erp_lock_found, (
        f"the two implementations DISAGREED on {reason!r}: "
        f"dependency_bundle found={db_found}, erp_lock found={erp_lock_found}"
    )


def test_both_implementations_refuse_an_empty_credential_rather_than_scan_vacuously(
    tmp_path: Path,
) -> None:
    target = tmp_path / "evidence.txt"
    target.write_text("anything", encoding="utf-8")
    with pytest.raises(db.DependencyBundleError):
        db.scan_for_credential([target], "")
    with pytest.raises(erp_lock.Refusal):
        erp_lock.credential_sightings([target], "")


@pytest.mark.parametrize(
    "data",
    [b"", b"a", b"the quick brown fox", bytes(range(256))],
    ids=["empty", "single-byte", "sentence", "all-256-byte-values"],
)
def test_both_sha256_hex_implementations_agree(data: bytes) -> None:
    assert db.sha256_hex(data) == erp_lock.sha256_hex(data)


def test_both_credential_encodings_implementations_agree() -> None:
    assert db.credential_encodings(CREDENTIAL) == erp_lock.credential_encodings(
        CREDENTIAL
    )


# ── the listed inventory itself: two-directional, non-growing ────────────


def _load_inventory() -> dict:
    return json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))


def _resolve_symbol(dotted: str) -> object:
    parts = dotted.split(".")
    assert parts[0] == "scripts", dotted
    module_name = parts[1]
    module = {"dependency_bundle": db, "erp_lock": erp_lock}[module_name]
    obj = module
    for attr in parts[2:]:
        obj = getattr(obj, attr)
    return obj


def test_every_inventory_entry_resolves_in_both_modules() -> None:
    inventory = _load_inventory()
    for entry in inventory["entries"]:
        db_symbol = _resolve_symbol(entry["dependency_bundle_symbol"])
        erp_symbol = _resolve_symbol(entry["erp_lock_symbol"])
        assert callable(db_symbol), entry["dependency_bundle_symbol"]
        assert callable(erp_symbol), entry["erp_lock_symbol"]


def test_the_inventory_has_not_grown_past_the_baseline() -> None:
    inventory = _load_inventory()
    actual_ids = {entry["id"] for entry in inventory["entries"]}
    assert actual_ids <= BASELINE_INVENTORY_IDS, (
        f"the duplication inventory grew: {actual_ids - BASELINE_INVENTORY_IDS} "
        "is not in the baseline. A NEW duplicated behaviour must not be "
        "added without a deliberate, reviewed widening of "
        "BASELINE_INVENTORY_IDS in this same test file."
    )


def test_the_inventory_has_no_duplicate_ids() -> None:
    inventory = _load_inventory()
    ids = [entry["id"] for entry in inventory["entries"]]
    assert len(ids) == len(set(ids))


# ── the unlisted-duplicate DETECTOR: catches what the list forgot ────────

_DETECTOR_MIN_BODY_LENGTH = 30
_DETECTOR_SIMILARITY_THRESHOLD = 0.8


def _function_bodies(path: Path) -> dict[str, str]:
    """Every module-level-or-nested function's body, source-unparsed with
    its docstring (if any) stripped, keyed by function name. Intended for
    ratio-based near-duplicate detection, not exact matching."""

    tree = ast.parse(path.read_text(encoding="utf-8"))
    bodies: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(getattr(body[0], "value", None), ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                body = body[1:]
            bodies[node.name] = "\n".join(ast.unparse(n) for n in body)
    return bodies


def find_unlisted_duplicate_helpers(
    listed_pairs: set[tuple[str, str]],
    *,
    db_path: Path = ROOT / "scripts" / "dependency_bundle.py",
    erp_lock_path: Path = ROOT / "scripts" / "erp_lock.py",
    threshold: float = _DETECTOR_SIMILARITY_THRESHOLD,
) -> list[tuple[str, str, float]]:
    """Every (dependency_bundle function, erp_lock function) pair whose
    bodies are near-identical (SequenceMatcher ratio >= `threshold`) and
    which is NOT in `listed_pairs`. An empty result means every
    sufficiently-similar pair is accounted for in the inventory.
    """

    db_bodies = _function_bodies(db_path)
    erp_bodies = _function_bodies(erp_lock_path)
    findings: list[tuple[str, str, float]] = []
    for db_name, db_src in sorted(db_bodies.items()):
        if len(db_src) < _DETECTOR_MIN_BODY_LENGTH:
            continue
        for erp_name, erp_src in sorted(erp_bodies.items()):
            if len(erp_src) < _DETECTOR_MIN_BODY_LENGTH:
                continue
            if (db_name, erp_name) in listed_pairs:
                continue
            ratio = difflib.SequenceMatcher(None, db_src, erp_src).ratio()
            if ratio >= threshold:
                findings.append((db_name, erp_name, round(ratio, 3)))
    return findings


def _inventory_symbol_pairs(inventory: dict) -> set[tuple[str, str]]:
    return {
        (
            entry["dependency_bundle_symbol"].rsplit(".", 1)[-1],
            entry["erp_lock_symbol"].rsplit(".", 1)[-1],
        )
        for entry in inventory["entries"]
    }


def test_no_unlisted_duplicate_security_helpers_between_the_two_modules() -> None:
    inventory = _load_inventory()
    findings = find_unlisted_duplicate_helpers(_inventory_symbol_pairs(inventory))
    assert findings == [], (
        f"unlisted near-duplicate helper(s) found between dependency_bundle.py "
        f"and erp_lock.py: {findings}. Either this is a genuine new "
        "orchestration duplicate that belongs in "
        "docs/architecture/dependency-bundle-duplication-inventory.json (and "
        "BASELINE_INVENTORY_IDS above), or it is pure shared semantics that "
        "belongs in scripts/dependency_normalisation.py (or a sibling shared "
        "module) instead."
    )


def test_the_detector_catches_a_planted_unlisted_duplicate(tmp_path: Path) -> None:
    """Sensitivity proof: copy a real dependency_bundle function under a
    new name into a scratch copy of the module — an exact-duplicate body
    the inventory does NOT mention — and show the detector reports it."""

    db_source = (ROOT / "scripts" / "dependency_bundle.py").read_text(encoding="utf-8")
    planted_source = db_source + (
        "\n\ndef _planted_unlisted_duplicate_of_sha256_hex(data: bytes) -> str:\n"
        "    return hashlib.sha256(data).hexdigest()\n"
    )
    scratch_db = tmp_path / "dependency_bundle_planted.py"
    scratch_db.write_text(planted_source, encoding="utf-8")

    inventory = _load_inventory()
    findings = find_unlisted_duplicate_helpers(
        _inventory_symbol_pairs(inventory), db_path=scratch_db
    )
    planted_hits = [
        f for f in findings if f[0] == "_planted_unlisted_duplicate_of_sha256_hex"
    ]
    assert planted_hits, f"the detector did not catch the planted duplicate: {findings}"
    assert planted_hits[0][1] == "sha256_hex"


def test_the_detector_does_not_flag_generic_cli_boilerplate_as_a_near_miss() -> None:
    """Near-miss: both modules' `main()` wrappers share generic
    argparse/try-except shape but are not a security-relevant duplicate —
    the threshold must not fire on this."""

    inventory = _load_inventory()
    findings = find_unlisted_duplicate_helpers(_inventory_symbol_pairs(inventory))
    assert not any(name == "main" for name, _, _ in findings)


# ═══════════════════════════════════════════════════════════════════════
# 3b. Finding 9: sub-threshold pairs the body-similarity detector missed
# ═══════════════════════════════════════════════════════════════════════
#
# Each pair below was found by the adversarial review at a similarity ratio
# far under the detector's 0.8 threshold, because the divergence is
# semantic (different validation depth, different accepted grammar) rather
# than textual. For each: either converged onto the demonstrably correct
# behaviour (the manifest-source-url trailing slash), or documented and
# PLANTED as a genuine, tracked, intentionally-not-converged difference.


def test_a_manifest_source_url_missing_its_trailing_slash_is_refused_by_both() -> None:
    """CONVERGED (was a bug, not a legitimate difference): dependency_bundle
    used to accept the forgejo source url with OR without a trailing slash
    (`.rstrip("/")` before comparing); erp_lock.manifest_problems always
    required the exact spelling. erp_lock's stricter, already-reviewed
    behaviour won."""

    poetry = {
        "dependencies": {"python": ">=3.11,<3.13"},
        "source": [
            {"name": "forgejo", "url": db.FORGEJO_LOCK_URL, "priority": "explicit"}
        ],
    }
    with pytest.raises(db.ManifestError, match="approved index"):
        db._forgejo_source_url(poetry)

    manifest = {"tool": {"poetry": poetry}}
    problems = erp_lock.manifest_problems(manifest, {})
    assert any(erp_lock.MANIFEST_INDEX_URL in p for p in problems), (
        "erp_lock should already refuse this spelling too"
    )


def test_an_unrelated_second_source_is_a_named_divergence_not_a_bug() -> None:
    """NOT converged: erp_lock.manifest_problems refuses ANY second
    `[[tool.poetry.source]]` because it validates a manifest for a LIVE,
    credentialed Poetry resolution, where an unrelated extra index could
    still change what that resolution does. dependency_bundle never runs
    Poetry and holds no credential; it only needs to know whether a second
    source could be MISTAKEN for the private one, so an unrelated second
    source is accepted here. This is a deliberately narrower threat model,
    not an oversight — planted here so a future change to either side must
    consciously decide whether to keep disagreeing."""

    poetry = {
        "dependencies": {"python": ">=3.11,<3.13"},
        "source": [
            {"name": "forgejo", "url": db.FORGEJO_MANIFEST_URL, "priority": "explicit"},
            {"name": "other-public-mirror", "url": "https://pypi.example.org/simple/"},
        ],
    }
    db._forgejo_source_url(poetry)  # accepted here

    manifest = {"tool": {"poetry": poetry}}
    problems = erp_lock.manifest_problems(manifest, {})
    assert any("unexpected" in p and "other-public-mirror" in p for p in problems), (
        "erp_lock should refuse the unrelated second source"
    )


def test_the_version_regex_divergence_is_named_not_a_bug() -> None:
    """NOT converged: erp_lock._EXACT_VERSION exists only to validate the
    two specific, already-known ALLOWED_MOVEMENTS version strings in a
    closed, reviewed workflow, and allows exactly ONE of a pre/post/dev
    suffix (non-stackable). dependency_bundle._EXACT_VERSION must recognise
    the full space of exact PEP 440 versions for ANY future forgejo pin,
    including a real, stackable PEP 440 spelling erp_lock's narrower regex
    refuses."""

    stacked_suffix_version = "1.0a1.post1"
    assert db._is_exact_version(stacked_suffix_version) is True
    assert erp_lock._EXACT_VERSION.fullmatch(stacked_suffix_version) is None


def test_lock_packages_is_stricter_than_erp_locks_acquisition_plan() -> None:
    """NOT converged (tracked as a real, security-relevant gap in
    erp_lock.acquisition_plan, out of scope for this branch to fix):
    erp_lock's lock-side loop includes a package by checking ONLY
    `source.reference == "forgejo"`, never `type` or `url`. This module's
    _lock_packages requires all three to agree, refusing a mismatched
    combination. A lock entry with the right `reference` but a WRONG url
    is refused here and silently trusted there."""

    lock = {
        "package": [
            {
                "name": "dotmac-files",
                "version": "0.1.0a4",
                "source": {
                    "type": "legacy",
                    "reference": "forgejo",
                    "url": erp_lock.LOCK_INDEX_URL,
                },
            },
            {
                "name": "dotmac-tax",
                "version": "0.1.0a4",
                "source": {
                    "type": "legacy",
                    "reference": "forgejo",
                    "url": erp_lock.LOCK_INDEX_URL,
                },
            },
            {
                "name": "dotmac-kernel",
                "version": "0.1.0a1",
                "source": {
                    "type": "sdist",
                    "reference": "forgejo",
                    "url": "https://evil.example.com/not-the-real-index",
                },
            },
        ]
    }
    with pytest.raises(db.ManifestError, match="malformed forgejo source"):
        db._lock_packages(lock)

    manifest = {"tool": {"poetry": {"dependencies": {}}}}
    plan = erp_lock.acquisition_plan(
        manifest, lock, {"dotmac-files": "0.1.0a4", "dotmac-tax": "0.1.0a4"}
    )
    assert plan.get("dotmac-kernel") == "0.1.0a1", (
        "erp_lock's acquisition_plan silently trusts the mismatched entry "
        "-- this is the tracked gap, not an assertion that it should"
    )


# ── the live off-index policy, read from both real sources, must agree ───


def test_the_live_off_index_policy_agrees_with_erp_locks_hardcoded_allowlist() -> None:
    """The body-similarity detector cannot see duplicated CONSTANTS at all,
    and the earlier off-index vector tables recreate the pin's values as
    test-local constants rather than reading the two LIVE sources. This
    reads both: `.github/dependency-bundle-policy.json`'s
    `permitted_off_index_dependencies` and
    `erp_lock.ALLOWED_OFF_INDEX_DEPENDENCIES` directly, and fails if a
    human ever edits one without the other."""

    policy = json.loads(REAL_POLICY_PATH.read_text(encoding="utf-8"))
    policy_pins = policy["permitted_off_index_dependencies"]
    erp_pins = erp_lock.ALLOWED_OFF_INDEX_DEPENDENCIES

    assert set(policy_pins) == set(erp_pins), (
        f"policy names {sorted(policy_pins)}, erp_lock names "
        f"{sorted(erp_pins)} -- the two off-index allowlists have drifted"
    )
    for name, policy_pin in policy_pins.items():
        erp_pin = erp_pins[name]
        assert policy_pin["url"] == erp_pin.url, name
        assert policy_pin["tag"] == erp_pin.tag, name
        assert policy_pin["commit"] == erp_pin.commit, name


# ═══════════════════════════════════════════════════════════════════════
# 4. dependency_normalisation: the one shared owner
# ═══════════════════════════════════════════════════════════════════════


def test_dependency_bundle_imports_the_shared_normaliser() -> None:
    assert db.normalise_name is dependency_normalisation.normalise_name


def test_erp_lock_imports_the_shared_TOTAL_normaliser() -> None:
    """A regression this branch introduced and then fixed: erp_lock's
    identity comparison must consume the TOTAL, never-raising form
    (`normalise_name_for_identity`), not the validating `normalise_name` —
    see `dependency_normalisation`'s "Two forms, for two genuinely
    different contracts"."""

    assert erp_lock._normalised is dependency_normalisation.normalise_name_for_identity


@pytest.mark.parametrize(
    "raw",
    [
        "dotmac.thing",
        "Dotmac_Thing",
        "dotmac--thing",
        "DOTMAC-KERNEL",
    ],
)
def test_both_forms_agree_on_every_clean_normalisation_vector(raw: str) -> None:
    """For a name that carries no edge separator and no invalid character,
    the STRICT form (`normalise_name`) and the TOTAL form
    (`normalise_name_for_identity`, which `erp_lock` consumes) compute the
    IDENTICAL result — validation only ever ADDS a refusal on top of the
    same collapse-and-lower-case logic, never changes it for input that
    was always going to pass. This is what makes `erp_lock`'s behaviour
    unchanged from before this branch touched it, for every name its real
    call sites ever see."""

    assert db.normalise_name(raw) == erp_lock._normalised(raw)


@pytest.mark.parametrize(
    "raw",
    ["-dotmac-thing-", "dotmac-thing-", "-dotmac-thing", "Dotmac_Thing."],
)
def test_the_strict_form_refuses_an_edge_separator_name(raw: str) -> None:
    """PEP 503 normalisation does not strip an edge separator, and no valid
    distribution name can carry one — refused outright by the strict,
    validating form used for manifest/lock parsing and filesystem-key
    derivation."""

    with pytest.raises(ValueError, match="starts or ends with a separator"):
        db.normalise_name(raw)


@pytest.mark.parametrize(
    "raw",
    ["-dotmac-thing-", "dotmac-thing-", "-dotmac-thing", "Dotmac_Thing."],
)
def test_the_total_form_never_raises_and_simply_fails_to_match(raw: str) -> None:
    """The SAME edge-separator names the strict form refuses must NOT raise
    through `erp_lock._normalised` (the total form) — that is the exact
    regression this branch introduced and then fixed: a candidate-supplied
    `poetry.lock` package name reaching this comparison inside the
    credentialed `erp-lock.yml` resolve workflow must never crash it. The
    total form instead returns a value that simply does not equal the
    clean, stripped identity — non-equality achieved by NOT normalising
    away the very thing that makes the name invalid, never by raising."""

    result = erp_lock._normalised(raw)
    assert isinstance(result, str)
    clean_identity = "dotmac-thing"
    assert result != clean_identity, (
        f"{raw!r} must not spuriously compare equal to {clean_identity!r}"
    )


def test_the_strict_normaliser_refuses_an_edge_separator_name_rather_than_stripping_it() -> (
    None
):
    with pytest.raises(ValueError, match="starts or ends with a separator"):
        dependency_normalisation.normalise_name("-dotmac-thing-")


def test_the_total_normaliser_never_raises_for_an_invalid_filesystem_path() -> None:
    """The other half of the regression: a caller-controlled string that is
    a filesystem path (not a distribution name at all) must not crash the
    TOTAL form either -- it is not this form's job to validate a charset,
    only to answer an identity comparison without raising."""

    result = dependency_normalisation.normalise_name_for_identity(
        "/escaped/bundle-path"
    )
    assert result == "/escaped/bundle-path"


def test_the_strict_normaliser_refuses_the_same_filesystem_path() -> None:
    with pytest.raises(ValueError, match="not a valid distribution name"):
        dependency_normalisation.normalise_name("/escaped/bundle-path")
