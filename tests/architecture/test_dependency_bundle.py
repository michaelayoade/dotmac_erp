"""`scripts/dependency_bundle.py`'s refusals, each planted against.

Three groups of tests:

1. The plan digest: what changes it, what does not, and every "unrecognised
   dependency form is refused, never silently omitted" case.
2. The bundle mechanics: safe ZIP extraction, archive/member hash
   verification, run-metadata verification, and "no field defaults its way
   past a refusal" for each of them.
3. The NAMED, ENFORCED duplication between this module and `erp_lock.py` —
   see both modules' docstrings and
   `docs/architecture/dependency-bundle-duplication-inventory.json`. One
   shared table of adversarial vectors drives BOTH implementations of each
   duplicated behaviour; a divergence between the copies is a test failure
   here, not a discovery years later. A second pair of tests enforces the
   inventory itself: every entry's two symbols must exist in both modules,
   and the entry-id set may only shrink from the baseline below.
"""

from __future__ import annotations

import base64
import json
import sys
import urllib.parse
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import dependency_bundle as db  # noqa: E402
import erp_lock  # noqa: E402

INVENTORY_PATH = (
    ROOT / "docs" / "architecture" / "dependency-bundle-duplication-inventory.json"
)

#: The non-growing baseline. This inventory may only SHRINK from this exact
#: set — a duplicated behaviour appearing that is not in this set fails
#: `test_the_inventory_has_not_grown_past_the_baseline`. Shrinking it (as
#: entries are retired) means updating THIS constant in the same reviewed
#: change that shrinks the inventory file.
BASELINE_INVENTORY_IDS = frozenset(
    {"sha256-hex-digest", "approved-artifact-url", "credential-string-scan"}
)


def _project_root(tmp_path: Path, pyproject_text: str, lock_text: str) -> Path:
    (tmp_path / "pyproject.toml").write_text(pyproject_text, encoding="utf-8")
    (tmp_path / "poetry.lock").write_text(lock_text, encoding="utf-8")
    return tmp_path


BASE_PYPROJECT = """
[tool.poetry]
name = "sample"
version = "1.0.0"
package-mode = false

[tool.poetry.dependencies]
python = ">=3.11,<3.13"
requests = "^2.31.0"
dotmac-kernel = {version = "0.1.0a1", source = "forgejo"}

[[tool.poetry.source]]
name = "forgejo"
url = "https://registry.dotmac.io/api/packages/dotmac/pypi/simple/"
priority = "explicit"

[tool.poetry.group.dev.dependencies]
pytest = "8.2.2"
"""

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
"""


def _base_surface(tmp_path: Path) -> db.DependencySurface:
    root = _project_root(tmp_path, BASE_PYPROJECT, BASE_LOCK)
    return db.extract_dependency_surface(root)


# ── plan digest: baseline is stable and well-formed ───────────────────────


def test_the_baseline_surface_produces_a_stable_64_hex_digest(tmp_path: Path) -> None:
    surface = _base_surface(tmp_path)
    digest_a = db.compute_plan_digest(surface)
    digest_b = db.compute_plan_digest(surface)
    assert digest_a == digest_b
    assert len(digest_a) == 64
    int(digest_a, 16)  # raises ValueError if not hex


# ── plan digest: relevant changes MUST change it ──────────────────────────


@pytest.mark.parametrize(
    "mutated_lock",
    [
        pytest.param(
            BASE_LOCK.replace(
                'version = "0.1.0a1"\npython-versions = ">=3.11"\ngroups = ["main"]\nfiles = [\n    {file = "dotmac_kernel-0.1.0a1-py3-none-any.whl", hash = "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},\n]',
                'version = "0.1.0a1"\npython-versions = ">=3.11"\ngroups = ["main"]\nfiles = [\n    {file = "dotmac_kernel-0.1.0a1-py3-none-any.whl", hash = "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"},\n]',
            ),
            id="private-pin-hash-changes",
        ),
    ],
)
def test_a_private_hash_change_changes_the_digest(
    tmp_path: Path, mutated_lock: str
) -> None:
    base_surface = _base_surface(tmp_path)
    base_digest = db.compute_plan_digest(base_surface)

    other_root = tmp_path / "mutated"
    other_root.mkdir()
    mutated_surface = db.extract_dependency_surface(
        _project_root(other_root, BASE_PYPROJECT, mutated_lock)
    )
    assert db.compute_plan_digest(mutated_surface) != base_digest


def test_a_private_transitive_dependency_addition_changes_the_digest(
    tmp_path: Path,
) -> None:
    base_surface = _base_surface(tmp_path)
    base_digest = db.compute_plan_digest(base_surface)

    lock_with_transitive = (
        BASE_LOCK
        + """
[[package]]
name = "dotmac-extra"
version = "0.1.0a1"
python-versions = ">=3.11"
groups = ["main"]
files = []

[package.source]
type = "legacy"
url = "https://registry.dotmac.io/api/packages/dotmac/pypi/simple"
reference = "forgejo"
"""
    )
    other_root = tmp_path / "with_transitive"
    other_root.mkdir()
    mutated_surface = db.extract_dependency_surface(
        _project_root(other_root, BASE_PYPROJECT, lock_with_transitive)
    )
    assert db.compute_plan_digest(mutated_surface) != base_digest


def test_a_private_source_url_change_changes_the_digest() -> None:
    # Constructed directly as DependencySurface objects (not through
    # extraction) to isolate exactly the one field under test.
    import dataclasses

    surface = db.DependencySurface(
        schema_version=1,
        forgejo_source_url=db.FORGEJO_LOCK_URL,
        target_python=">=3.11,<3.13",
        target_platform=db.TARGET_PLATFORM,
        dependencies=(),
        lock_packages=(),
    )
    mutated = dataclasses.replace(
        surface,
        forgejo_source_url="https://registry.dotmac.io/api/packages/dotmac/pypi/simple/alt",
    )
    assert db.compute_plan_digest(surface) != db.compute_plan_digest(mutated)


def test_a_marker_change_changes_the_digest() -> None:
    import dataclasses

    dep = db.ForgejoDependency(
        name="dotmac-kernel",
        normalised_name="dotmac-kernel",
        group="main",
        version="0.1.0a1",
        markers=None,
        extras=(),
    )
    surface = db.DependencySurface(
        schema_version=1,
        forgejo_source_url=db.FORGEJO_LOCK_URL,
        target_python=">=3.11,<3.13",
        target_platform=db.TARGET_PLATFORM,
        dependencies=(dep,),
        lock_packages=(),
    )
    mutated_dep = dataclasses.replace(dep, markers="python_version < '3.13'")
    mutated = dataclasses.replace(surface, dependencies=(mutated_dep,))
    assert db.compute_plan_digest(surface) != db.compute_plan_digest(mutated)


def test_a_group_change_changes_the_digest() -> None:
    import dataclasses

    dep = db.ForgejoDependency(
        name="dotmac-kernel",
        normalised_name="dotmac-kernel",
        group="main",
        version="0.1.0a1",
        markers=None,
        extras=(),
    )
    surface = db.DependencySurface(
        schema_version=1,
        forgejo_source_url=db.FORGEJO_LOCK_URL,
        target_python=">=3.11,<3.13",
        target_platform=db.TARGET_PLATFORM,
        dependencies=(dep,),
        lock_packages=(),
    )
    mutated = dataclasses.replace(
        surface, dependencies=(dataclasses.replace(dep, group="dev"),)
    )
    assert db.compute_plan_digest(surface) != db.compute_plan_digest(mutated)


# ── plan digest: irrelevant changes MUST NOT change it ───────────────────


def test_a_toml_comment_and_reordering_do_not_change_the_digest(tmp_path: Path) -> None:
    base_surface = _base_surface(tmp_path)
    base_digest = db.compute_plan_digest(base_surface)

    reformatted = "# a harmless comment\n" + BASE_PYPROJECT.replace(
        'python = ">=3.11,<3.13"\nrequests = "^2.31.0"\ndotmac-kernel',
        'requests = "^2.31.0"\n\n# reordered, still equivalent\npython = ">=3.11,<3.13"\ndotmac-kernel',
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


# ── plan digest: unrecognised forms are REFUSED, never silently omitted ──


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
    # The dotmac-kernel [package.source] table declares type = "legacy"
    # exactly once, immediately preceded by dotmac-kernel's own [[package]]
    # header — rewriting THAT occurrence to type = "url" leaves every other
    # (public, "pypi"-sourced) package's own "legacy" type alone.
    lock = BASE_LOCK.replace(
        'name = "dotmac-kernel"\nversion = "0.1.0a1"\npython-versions = ">=3.11"\ngroups = ["main"]\nfiles = [\n'
        '    {file = "dotmac_kernel-0.1.0a1-py3-none-any.whl", hash = "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},\n'
        ']\n\n[package.source]\ntype = "legacy"',
        'name = "dotmac-kernel"\nversion = "0.1.0a1"\npython-versions = ">=3.11"\ngroups = ["main"]\nfiles = [\n'
        '    {file = "dotmac_kernel-0.1.0a1-py3-none-any.whl", hash = "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},\n'
        ']\n\n[package.source]\ntype = "url"',
    )
    assert lock != BASE_LOCK, (
        "the targeted replacement did not match; fix the test fixture"
    )
    root = _project_root(tmp_path, BASE_PYPROJECT, lock)
    with pytest.raises(db.ManifestError, match="malformed forgejo source"):
        db.extract_dependency_surface(root)


# ── bundle mechanics: archive digest ──────────────────────────────────────


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


# ── bundle mechanics: safe extraction ─────────────────────────────────────


def _make_zip(
    tmp_path: Path, members: dict[str, bytes], *, name: str = "bundle.zip"
) -> Path:
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as archive:
        for member_name, data in members.items():
            archive.writestr(member_name, data)
    return path


def test_safe_extraction_of_a_clean_archive(tmp_path: Path) -> None:
    archive = _make_zip(tmp_path, {"a.whl": b"AAAA", "b.whl": b"BBBBBB"})
    dest = tmp_path / "out"
    extracted = db.safe_extract_zip(archive, dest, {"a.whl": 4, "b.whl": 6})
    assert sorted(extracted) == ["a.whl", "b.whl"]
    assert (dest / "a.whl").read_bytes() == b"AAAA"


def test_a_duplicate_member_name_is_refused(tmp_path: Path) -> None:
    archive = tmp_path / "dup.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("a.whl", b"AAAA")
        zf.writestr("a.whl", b"BBBB")
    with pytest.raises(db.ExtractionError, match="duplicate"):
        db.safe_extract_zip(archive, tmp_path / "out", {"a.whl": 4})


def test_an_absolute_path_member_is_refused(tmp_path: Path) -> None:
    archive = _make_zip(tmp_path, {"/etc/passwd": b"x"})
    with pytest.raises(db.ExtractionError, match="absolute path"):
        db.safe_extract_zip(archive, tmp_path / "out", {"/etc/passwd": 1})


def test_a_traversal_member_is_refused(tmp_path: Path) -> None:
    archive = _make_zip(tmp_path, {"../evil": b"x"})
    with pytest.raises(db.ExtractionError, match="traversal"):
        db.safe_extract_zip(archive, tmp_path / "out", {"../evil": 1})


def test_a_symlink_member_is_refused(tmp_path: Path) -> None:
    archive_path = tmp_path / "link.zip"
    with zipfile.ZipFile(archive_path, "w") as zf:
        info = zipfile.ZipInfo("link")
        info.external_attr = 0o120777 << 16  # S_IFLNK
        zf.writestr(info, "target")
    with pytest.raises(db.ExtractionError, match="symlink"):
        db.safe_extract_zip(archive_path, tmp_path / "out", {"link": 6})


def test_case_colliding_members_are_refused(tmp_path: Path) -> None:
    archive = _make_zip(tmp_path, {"A.whl": b"A", "a.whl": b"a"})
    with pytest.raises(db.ExtractionError, match="collide case-insensitively"):
        db.safe_extract_zip(archive, tmp_path / "out", {"A.whl": 1, "a.whl": 1})


def test_an_oversized_member_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(db, "MAX_MEMBER_BYTES", 3)
    archive = _make_zip(tmp_path, {"big.whl": b"AAAAAA"})
    with pytest.raises(db.ExtractionError, match="cap"):
        db.safe_extract_zip(archive, tmp_path / "out", {"big.whl": 6})


def test_an_unlisted_member_is_refused(tmp_path: Path) -> None:
    archive = _make_zip(tmp_path, {"a.whl": b"AAAA", "sneaky.sh": b"#!/bin/sh\n"})
    with pytest.raises(db.ExtractionError, match="not named in the verified"):
        db.safe_extract_zip(archive, tmp_path / "out", {"a.whl": 4})


def test_a_manifest_expected_member_missing_from_the_archive_is_refused(
    tmp_path: Path,
) -> None:
    archive = _make_zip(tmp_path, {"a.whl": b"AAAA"})
    with pytest.raises(db.ExtractionError, match="does not contain"):
        db.safe_extract_zip(archive, tmp_path / "out", {"a.whl": 4, "missing.whl": 10})


# ── bundle mechanics: member hash verification ────────────────────────────


def test_member_hash_verification_passes_for_correct_bytes(tmp_path: Path) -> None:
    dest = tmp_path / "out"
    dest.mkdir()
    (dest / "a.whl").write_bytes(b"AAAA")
    db.verify_member_hashes(dest, {"a.whl": db.sha256_hex(b"AAAA")})


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


# ── run metadata verification: each missing field refuses independently ──


def _valid_policy() -> dict:
    return {
        "repository": {"full_name": "michaelayoade/dotmac_erp", "id": 123456},
        "producer_workflow_path": ".github/workflows/dependency-bundle-produce.yml",
        "binder_workflow_path": ".github/workflows/dependency-bundle-bind.yml",
    }


def _valid_run_metadata() -> dict:
    return {
        "repository_full_name": "michaelayoade/dotmac_erp",
        "repository_id": 123456,
        "workflow_path": ".github/workflows/dependency-bundle-produce.yml",
        "run_id": 111,
        "run_attempt": 1,
        "trusted_workflow_sha": "a" * 40,
        "artifact_id": 222,
        "artifact_name": "erp-dependency-bundle-deadbeef",
    }


def test_valid_run_metadata_verifies(tmp_path: Path) -> None:
    result = db.verify_run_metadata(_valid_run_metadata(), _valid_policy())
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
    ],
)
def test_each_missing_run_metadata_field_refuses_independently(
    missing_field: str,
) -> None:
    metadata = _valid_run_metadata()
    del metadata[missing_field]
    with pytest.raises(db.BundleVerificationError, match="missing required fields"):
        db.verify_run_metadata(metadata, _valid_policy())


def test_a_repository_id_mismatch_is_refused_even_if_the_name_matches() -> None:
    metadata = _valid_run_metadata()
    metadata["repository_id"] = 999999
    with pytest.raises(db.BundleVerificationError, match="repository_id"):
        db.verify_run_metadata(metadata, _valid_policy())


def test_a_non_hex_trusted_workflow_sha_is_refused() -> None:
    metadata = _valid_run_metadata()
    metadata["trusted_workflow_sha"] = "not-a-sha"
    with pytest.raises(db.BundleVerificationError, match="40-hex"):
        db.verify_run_metadata(metadata, _valid_policy())


# ── bundle manifest creation: each missing field refuses independently ───


def _valid_run() -> db.RunMetadata:
    return db.RunMetadata(**_valid_run_metadata())


def test_create_bundle_manifest_with_all_fields_succeeds() -> None:
    manifest = db.create_bundle_manifest(
        plan_digest="a" * 64,
        run=_valid_run(),
        archive_sha256="b" * 64,
        member_hashes={"a.whl": "c" * 64},
    )
    assert manifest["plan_digest"] == "a" * 64


def test_create_bundle_manifest_refuses_a_missing_plan_digest() -> None:
    with pytest.raises(db.BundleVerificationError, match="plan_digest"):
        db.create_bundle_manifest(
            plan_digest="",
            run=_valid_run(),
            archive_sha256="b" * 64,
            member_hashes={"a.whl": "c" * 64},
        )


def test_create_bundle_manifest_refuses_a_missing_archive_digest() -> None:
    with pytest.raises(db.BundleVerificationError, match="archive_sha256"):
        db.create_bundle_manifest(
            plan_digest="a" * 64,
            run=_valid_run(),
            archive_sha256="",
            member_hashes={"a.whl": "c" * 64},
        )


def test_create_bundle_manifest_refuses_no_member_hashes() -> None:
    with pytest.raises(db.BundleVerificationError, match="at least one"):
        db.create_bundle_manifest(
            plan_digest="a" * 64,
            run=_valid_run(),
            archive_sha256="b" * 64,
            member_hashes={},
        )


def test_create_bundle_manifest_refuses_a_malformed_member_hash() -> None:
    with pytest.raises(db.BundleVerificationError, match="member"):
        db.create_bundle_manifest(
            plan_digest="a" * 64,
            run=_valid_run(),
            archive_sha256="b" * 64,
            member_hashes={"a.whl": "not-a-hash"},
        )


# ── candidate binding ──────────────────────────────────────────────────


def test_binding_succeeds_when_digests_match() -> None:
    manifest = db.create_bundle_manifest(
        plan_digest="a" * 64,
        run=_valid_run(),
        archive_sha256="b" * 64,
        member_hashes={"a.whl": "c" * 64},
    )
    binding = db.bind_bundle_to_candidate(manifest, "d" * 40, "a" * 64)
    assert binding.bundle_run_id == 111


def test_binding_refuses_a_digest_mismatch() -> None:
    manifest = db.create_bundle_manifest(
        plan_digest="a" * 64,
        run=_valid_run(),
        archive_sha256="b" * 64,
        member_hashes={"a.whl": "c" * 64},
    )
    with pytest.raises(db.BundleVerificationError, match="does not match"):
        db.bind_bundle_to_candidate(manifest, "d" * 40, "f" * 64)


# ── no registry fallback: static proof over the module's own source ──────


def test_no_function_in_this_module_names_a_network_call() -> None:
    """A cheap but real static check: no HTTP client symbol appears
    anywhere in scripts/dependency_bundle.py's source. This module reasons
    about already-fetched bytes/JSON; it never fetches them itself."""

    source = (ROOT / "scripts" / "dependency_bundle.py").read_text(encoding="utf-8")
    for forbidden in ("urllib.request", "http.client", "requests.", "httpx."):
        assert forbidden not in source, (
            f"{forbidden!r} must not appear: no network calls"
        )


# ── policy: the shipped file is currently, correctly, unresolved ─────────


def test_the_shipped_policy_file_loads_and_binds_the_real_repository_id() -> None:
    """The shipped policy carries the RESOLVED immutable repository id.

    This inverted when the id was resolved from the GitHub API. The refusal
    behaviour it used to prove is not lost: it is proven independently, and
    better, by `test_a_policy_with_a_non_positive_repository_id_is_refused`,
    which plants a bad value rather than depending on the shipped file being
    broken. A guard that can only fire while the repository is misconfigured
    stops being a guard the moment someone fixes the configuration.

    Both identifiers are asserted deliberately. A `full_name` survives a
    rename or transfer while the numeric id never does, so checking only the
    name would let a different repository that briefly held this name satisfy
    the binding.
    """
    policy_path = ROOT / ".github" / "dependency-bundle-policy.json"
    policy = db.load_policy(policy_path)
    assert policy["repository"]["full_name"] == "michaelayoade/dotmac_erp"
    assert policy["repository"]["id"] == 1141216651


def test_a_policy_with_a_real_positive_repository_id_loads() -> None:
    policy_path = ROOT / ".github" / "dependency-bundle-policy.json"
    raw = json.loads(policy_path.read_text(encoding="utf-8"))
    raw["repository"]["id"] = 123456789

    import tempfile

    with tempfile.TemporaryDirectory() as d:
        candidate = Path(d) / "policy.json"
        candidate.write_text(json.dumps(raw), encoding="utf-8")
        loaded = db.load_policy(candidate)
        assert loaded["repository"]["id"] == 123456789


def test_a_policy_with_a_non_positive_repository_id_is_refused() -> None:
    policy_path = ROOT / ".github" / "dependency-bundle-policy.json"
    raw = json.loads(policy_path.read_text(encoding="utf-8"))
    raw["repository"]["id"] = 0
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        candidate = Path(d) / "policy.json"
        candidate.write_text(json.dumps(raw), encoding="utf-8")
        with pytest.raises(db.PolicyError, match="positive integer"):
            db.load_policy(candidate)


# ── shared adversarial vector table: approved_artifact_url ───────────────

_PAGE_URL = "https://registry.dotmac.io/api/packages/dotmac/pypi/simple/dotmac-kernel/"

# Each vector: (href, expect_refusal, reason). Both `db.approved_artifact_url`
# and `erp_lock.approved_artifact_url` are driven through every row; a
# divergence in accept/refuse between the two is a failure of
# `test_both_implementations_of_approved_artifact_url_agree_on_every_vector`.
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


# ── shared adversarial vector table: credential scanning ─────────────────

#: Deliberately contains `/`, a space, and `=` so the percent-encoded vector
#: below actually differs from the plain credential (a credential made only
#: of unreserved characters would percent-encode to itself, which proves
#: nothing about the percent-encoding branch).
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


# ── shared adversarial vector table: sha256_hex parity ────────────────────


@pytest.mark.parametrize(
    "data",
    [b"", b"a", b"the quick brown fox", bytes(range(256))],
    ids=["empty", "single-byte", "sentence", "all-256-byte-values"],
)
def test_both_sha256_hex_implementations_agree(data: bytes) -> None:
    assert db.sha256_hex(data) == erp_lock.sha256_hex(data)


# ── the duplication inventory itself: two-directional, non-growing ───────


def _load_inventory() -> dict:
    return json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))


def _resolve_symbol(dotted: str) -> object:
    """Resolve `scripts.dependency_bundle.sha256_hex` /
    `scripts.erp_lock.approved_artifact_url`-shaped dotted names against the
    two already-imported modules in THIS test file — deliberately not a
    generic importer, so a symbol that does not exist raises AttributeError
    rather than being silently treated as absent."""

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
