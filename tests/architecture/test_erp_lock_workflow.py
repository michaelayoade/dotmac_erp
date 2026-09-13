"""`.github/workflows/erp-lock.yml`'s refusals, each planted against.

Adapted from `dotmac_vendor_control_plane`'s `kernel-lock.yml` /
`scripts/kernel_lock.py` — the same problem (a private-index lock entry cannot
be produced without a credential a workstation cannot hold), the same
structural repair (the credential and the resolver never share a job; the
resolver is never handed something it must build). Two things are genuinely
different here, not merely renamed, and each has its own tests below:

1. ERP moves TWO packages (`dotmac-files` 0.1.0a2 -> 0.1.0a4, `dotmac-tax`
   0.1.0a3 -> 0.1.0a4) against a CLOSED allowlist of movements — a caller
   cannot ask for a third package or a different version, and the check says
   so by name.
2. ERP's own `pyproject.toml` already declares a legitimate, unrelated git
   dependency (`dotmac-integration-client`). The guard this is adapted from
   refuses every off-index dependency form anywhere in the manifest; applied
   unmodified it would refuse ERP's OWN real, shipped manifest. That is
   accommodated by name (`ALLOWED_OFF_INDEX_DEPENDENCIES`), not by weakening
   the refusal for anything else.

Every other check here — the wheel-only gate, the drift comparison, the
credential scan, the workflow's job shape — is the same property Platform's
review already found necessary, re-proven against ERP's own script and
workflow.
"""

from __future__ import annotations

import ast
import base64
import json
import re
import sys
import tomllib
import urllib.parse
import zipfile
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "erp-lock.yml"
sys.path.insert(0, str(ROOT / "scripts"))

import erp_lock  # noqa: E402
from erp_lock import (  # noqa: E402
    ALLOWED_MOVEMENTS,
    ARTIFACT_ORIGIN,
    INDEX_SOURCE_NAME,
    INDEX_USERNAME,
    LOCK_INDEX_URL,
    MANIFEST_INDEX_URL,
    OFF_INDEX_DEPENDENCY_KEYS,
    Refusal,
    acquire,
    acquisition_plan,
    approved_artifact_url,
    artifact_belongs_to,
    artifact_names,
    build_evidence,
    checkout_problems,
    credential_encodings,
    credential_sightings,
    curl_argv,
    declarations_of,
    drift_problems,
    hash_problems,
    lock_wheel_problems,
    manifest_problems,
    movement_problems,
    pair_binding,
    point_at_mirror,
    protected_main_problems,
    replace_version,
    restore_index_url,
    set_content_hash,
    sha256_hex,
    sha256sums,
    write_credential_attestation,
    transfer_problems,
    unconditional_requirement_names,
    wheel_dependency_problems,
    wheel_requires_dist,
)

TARGETS = {name: pair[1] for name, pair in ALLOWED_MOVEMENTS.items()}
OLDS = {name: pair[0] for name, pair in ALLOWED_MOVEMENTS.items()}
FILES, TAX = "dotmac-files", "dotmac-tax"

# ── fixtures for the lock comparison ────────────────────────────────────────

_INDEX_SOURCE = {
    "type": "legacy",
    "url": LOCK_INDEX_URL,
    "reference": INDEX_SOURCE_NAME,
}
ATTRS, CATALOGUE, FILES_ENTRY, TAX_ENTRY = 0, 1, 2, 3


def _package(name: str, version: str, **extra: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "name": name,
        "version": version,
        "description": f"{name}, for the comparison",
        "optional": False,
        "python-versions": ">=3.11,<3.14",
        "groups": ["main"],
        "files": [
            {
                "file": f"{name.replace('-', '_')}-{version}-py3-none-any.whl",
                "hash": f"sha256:{name}-whl",
            },
            {
                "file": f"{name.replace('-', '_')}-{version}.tar.gz",
                "hash": f"sha256:{name}-sdist",
            },
        ],
    }
    entry.update(extra)
    return entry


def _lock(packages: list[dict[str, Any]], content_hash: str) -> dict[str, Any]:
    return {
        "package": packages,
        "metadata": {
            "lock-version": "2.1",
            "python-versions": ">=3.11,<3.13",
            "content-hash": content_hash,
        },
    }


def _before() -> dict[str, Any]:
    return _lock(
        [
            _package("attrs", "24.2.0"),
            _package("dotmac-kernel", "0.1.0a98", source=_INDEX_SOURCE),
            _package(FILES, OLDS[FILES], source=_INDEX_SOURCE),
            _package(TAX, OLDS[TAX], source=_INDEX_SOURCE),
        ],
        "content-hash-before",
    )


def _after() -> dict[str, Any]:
    """The only clean shape: both target entries moved, the content-hash moved."""

    resolved = _before()
    resolved["package"][FILES_ENTRY] = _package(
        FILES, TARGETS[FILES], source=_INDEX_SOURCE
    )
    resolved["package"][TAX_ENTRY] = _package(TAX, TARGETS[TAX], source=_INDEX_SOURCE)
    resolved["metadata"]["content-hash"] = "content-hash-after"
    return resolved


def _plant(index: int, field: str, value: Any) -> dict[str, Any]:
    resolved = _after()
    resolved["package"][index][field] = value
    return resolved


# ── the near-miss: only the two named packages moved ────────────────────────


def test_a_resolution_that_moved_only_the_two_named_packages_is_accepted() -> None:
    """SENSITIVITY. A gate that refuses everything proves nothing."""

    assert drift_problems(_before(), _after()) == []


_ELSEWHERE = {
    "type": "legacy",
    "url": "https://elsewhere.example/simple",
    "reference": "forgejo",
}


def test_a_repointed_source_on_an_unrelated_package_is_named() -> None:
    # attrs carries no `source` in `_before`, so plant it on the kernel entry
    # instead, which does.
    resolved = _plant(1, "source", _ELSEWHERE)
    problems = drift_problems(_before(), resolved)
    assert any("dotmac-kernel" in p and "source" in p for p in problems), problems


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("dependencies", {"anything": "*"}),
        ("extras", {"testing": ["pytest"]}),
        ("python-versions", ">=3.13"),
        ("optional", True),
        ("groups", ["main", "dev"]),
        ("description", "quietly rewritten"),
    ],
)
def test_every_other_field_of_an_unrelated_entry_is_compared(
    field: str, value: Any
) -> None:
    resolved = _plant(ATTRS, field, value)
    named = [p for p in drift_problems(_before(), resolved) if "attrs" in p]
    assert named, f"a changed `{field}` on an unrelated package was not named"
    assert field in named[0], named


@pytest.mark.parametrize("index", [FILES_ENTRY, TAX_ENTRY])
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source", _ELSEWHERE),
        ("extras", {"surprise": ["anything"]}),
        ("optional", True),
        ("groups", ["main", "dev"]),
        ("python-versions", ">=3.13"),
        ("description", "quietly rewritten"),
    ],
)
def test_metadata_drift_on_either_moved_entry_itself_is_refused(
    index: int, field: str, value: Any
) -> None:
    """Neither moved entry is a blank cheque, unlike a gate that exempted the
    whole entry — see kernel_lock.py's amendment 5 for the defect this
    guards. `dependencies` is deliberately NOT in this list — see the test
    below for why it is permitted, and separately bounded."""

    resolved = _plant(index, field, value)
    package = resolved["package"][index]["name"]
    named = [p for p in drift_problems(_before(), resolved) if package in p]
    assert named, f"a changed `{field}` on {package} was not named"
    assert any(field in p for p in named), named


@pytest.mark.parametrize("index", [FILES_ENTRY, TAX_ENTRY])
def test_either_moved_entry_may_still_move_its_version_and_files(index: int) -> None:
    """SENSITIVITY the other way. Refusing a moved `files` list would refuse
    every real run."""

    resolved = _after()
    package = resolved["package"][index]["name"]
    resolved["package"][index]["files"] = [
        {
            "file": f"{package.replace('-', '_')}-{TARGETS[package]}-py3-none-any.whl",
            "hash": "sha256:new",
        }
    ]
    assert drift_problems(_before(), resolved) == []


@pytest.mark.parametrize("index", [FILES_ENTRY, TAX_ENTRY])
def test_a_moved_entry_may_also_change_its_dependencies_table(index: int) -> None:
    """Unlike `kernel_lock.py`'s KERNEL_MUTABLE_FIELDS (which holds even the
    kernel's own `dependencies` immutable), point 7 of this workflow's brief
    explicitly permits `dependencies` to differ on the TWO moved packages — a
    version move can legitimately raise the moved package's own floor on a
    transitive dependency. `drift_problems` alone does not bound WHAT it may
    say; `wheel_dependency_problems` does, in the resolver job, against the
    acquired wheel's own Requires-Dist (proven separately above)."""

    resolved = _plant(index, "dependencies", {"dotmac-kernel": ">=0.1.0a99"})
    package = resolved["package"][index]["name"]
    assert not any(
        package in p and "dependencies" in p
        for p in drift_problems(_before(), resolved)
    )


def test_an_added_or_removed_package_is_named() -> None:
    added = _after()
    added["package"].append(_package("left-pad", "1.0.0"))
    assert any("left-pad" in p for p in drift_problems(_before(), added))

    removed = _after()
    del removed["package"][0]
    assert any("attrs" in p for p in drift_problems(_before(), removed))


def test_an_unchanged_content_hash_is_a_refusal() -> None:
    resolved = _after()
    resolved["metadata"]["content-hash"] = _before()["metadata"]["content-hash"]
    problems = drift_problems(_before(), resolved)
    assert any("content-hash" in p for p in problems), problems


def test_only_one_of_the_two_moving_is_still_named_as_a_refusal() -> None:
    """A resolution that moves `dotmac-files` but leaves `dotmac-tax` behind
    is not "half done and fine" — the workflow asked for both, and a lock
    describing only one movement is not the lock the dispatch requested."""

    resolved = _after()
    resolved["package"][TAX_ENTRY] = _package(TAX, OLDS[TAX], source=_INDEX_SOURCE)
    problems = drift_problems(_before(), resolved)
    assert any("dotmac-tax" in p and "did not move" in p for p in problems), problems


def test_two_entries_for_one_name_and_version_refuse() -> None:
    resolved = _after()
    resolved["package"].append(_package("attrs", "24.2.0"))
    with pytest.raises(Refusal):
        drift_problems(_before(), resolved)


# ── the closed allowlist of movements ────────────────────────────────────────


def test_the_allowlist_names_exactly_the_two_decided_movements() -> None:
    assert ALLOWED_MOVEMENTS == {
        "dotmac-files": ("0.1.0a2", "0.1.0a4"),
        "dotmac-tax": ("0.1.0a3", "0.1.0a4"),
    }


def test_a_third_package_is_refused_by_name() -> None:
    with pytest.raises(Refusal, match="dotmac-kernel"):
        erp_lock._parse_movement_args(  # noqa: SLF001
            [
                f"dotmac-files={TARGETS[FILES]}",
                f"dotmac-tax={TARGETS[TAX]}",
                "dotmac-kernel=0.1.0a99",
            ]
        )


def test_an_unlisted_version_of_a_listed_package_is_refused_by_name() -> None:
    with pytest.raises(Refusal, match="0.1.0a5"):
        erp_lock._parse_movement_args(  # noqa: SLF001
            ["dotmac-files=0.1.0a5", f"dotmac-tax={TARGETS[TAX]}"]
        )


def test_a_missing_movement_is_refused() -> None:
    with pytest.raises(Refusal, match="expected a --movement for each"):
        erp_lock._parse_movement_args([f"dotmac-files={TARGETS[FILES]}"])  # noqa: SLF001


def test_the_one_allowed_pair_is_accepted() -> None:
    """SENSITIVITY. The real dispatch shape must not be refused."""

    result = erp_lock._parse_movement_args(  # noqa: SLF001
        [f"dotmac-files={TARGETS[FILES]}", f"dotmac-tax={TARGETS[TAX]}"]
    )
    assert result == TARGETS


# ── declarations_of / movement_problems: every surface, exactly once ───────


def _manifest(**overrides: Any) -> dict[str, Any]:
    poetry: dict[str, Any] = {
        "dependencies": {
            "python": ">=3.11,<3.13",
            FILES: {"version": OLDS[FILES], "source": INDEX_SOURCE_NAME},
            TAX: {"version": OLDS[TAX], "source": INDEX_SOURCE_NAME},
            "dotmac-integration-client": {
                "git": "https://github.com/michaelayoade/dotmac-integration-client.git",
                "tag": "v0.2.0",
            },
            "fastapi": "0.111.0",
        },
        "group": {"dev": {"dependencies": {"pytest": "8.2.2"}}},
        "source": [
            {
                "name": INDEX_SOURCE_NAME,
                "url": MANIFEST_INDEX_URL,
                "priority": "explicit",
            }
        ],
    }
    poetry.update(overrides)
    return {"tool": {"poetry": poetry}}


def test_the_repositorys_own_manifest_is_declared_exactly_once_each() -> None:
    """NON-VACUITY, against the two real files."""

    with (ROOT / "pyproject.toml").open("rb") as handle:
        manifest = tomllib.load(handle)
    assert movement_problems(manifest, OLDS) == []


def test_a_second_declaration_of_a_moved_package_is_refused() -> None:
    """THE PLANT. Two declarations mean guessing which one is the pin."""

    manifest = _manifest()
    manifest["tool"]["poetry"]["group"]["dev"]["dependencies"][FILES] = OLDS[FILES]
    problems = movement_problems(manifest, OLDS)
    assert any(FILES in p and "expected exactly one" in p for p in problems), problems


def test_a_wrong_declared_version_is_refused() -> None:
    manifest = _manifest()
    manifest["tool"]["poetry"]["dependencies"][FILES]["version"] = "0.1.0a3"
    problems = movement_problems(manifest, OLDS)
    assert any(FILES in p and "0.1.0a3" in p for p in problems), problems


def test_declarations_of_finds_a_pep508_form_too() -> None:
    manifest = {"project": {"dependencies": [f"{FILES} (=={OLDS[FILES]})"]}}
    assert declarations_of(manifest, FILES) == ["project.dependencies[0]"]


# ── the edit: before-check, rewrite, after-check ────────────────────────────


def test_the_edit_moves_exactly_the_pin_in_the_real_manifest() -> None:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    for package, version in TARGETS.items():
        text = replace_version(text, package, version)
    manifest = tomllib.loads(text)
    assert (
        manifest["tool"]["poetry"]["dependencies"][FILES]["version"] == TARGETS[FILES]
    )
    assert manifest["tool"]["poetry"]["dependencies"][TAX]["version"] == TARGETS[TAX]
    assert manifest_problems(manifest, TARGETS) == []


@pytest.mark.parametrize(
    "text",
    [
        'dotmac-files = { version = "1" }\ndotmac-files = { version = "2" }\n',
        "nothing here declares it\n",
    ],
)
def test_the_edit_refuses_anything_but_one_declaration(text: str) -> None:
    with pytest.raises(Refusal):
        replace_version(text, FILES, "0.1.0a4")


# ── manifest-guard: ERP's own legitimate git dependency is accommodated ─────


def test_the_repositorys_own_manifest_passes_the_guard_after_the_edit() -> None:
    """NON-VACUITY. The real manifest carries a git dependency
    (`dotmac-integration-client`) that the upstream guard would refuse
    unmodified — this must still pass, by name, not by weakening the rule."""

    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    for package, version in TARGETS.items():
        text = replace_version(text, package, version)
    manifest = tomllib.loads(text)
    assert manifest_problems(manifest, TARGETS) == []


def test_a_synthetic_clean_manifest_passes() -> None:
    manifest = _manifest(
        dependencies={
            **_manifest()["tool"]["poetry"]["dependencies"],
            FILES: {"version": TARGETS[FILES], "source": INDEX_SOURCE_NAME},
            TAX: {"version": TARGETS[TAX], "source": INDEX_SOURCE_NAME},
        }
    )
    assert manifest_problems(manifest, TARGETS) == []


def test_an_off_index_dependency_NOT_on_the_allowlist_is_still_refused() -> None:
    """THE PLANT for the accommodation. `dotmac-integration-client` is
    exempted BY NAME; nothing else is."""

    manifest = _manifest()
    manifest["tool"]["poetry"]["dependencies"]["sneaky"] = {
        "git": "https://elsewhere.example/x.git"
    }
    problems = manifest_problems(manifest, TARGETS)
    assert any("`git`" in p and "sneaky" in p for p in problems), problems


def test_the_allowlisted_dependency_is_not_refused_for_its_git_key() -> None:
    manifest = _manifest()
    problems = manifest_problems(manifest, TARGETS)
    assert not any("dotmac-integration-client" in p for p in problems), problems


@pytest.mark.parametrize("key", sorted(OFF_INDEX_DEPENDENCY_KEYS))
def test_an_off_index_dependency_is_refused(key: str) -> None:
    manifest = _manifest()
    manifest["tool"]["poetry"]["dependencies"]["sneaky"] = {key: "../elsewhere"}
    problems = manifest_problems(manifest, TARGETS)
    assert any(f"`{key}`" in p for p in problems), (key, problems)


def test_a_moved_index_url_under_the_same_source_name_is_refused() -> None:
    manifest = _manifest(
        source=[
            {
                "name": INDEX_SOURCE_NAME,
                "url": "https://attacker.example/simple",
                "priority": "explicit",
            }
        ]
    )
    problems = manifest_problems(manifest, TARGETS)
    assert any("attacker.example" in p for p in problems), problems


def test_a_second_source_is_refused() -> None:
    manifest = _manifest(
        source=[
            {
                "name": INDEX_SOURCE_NAME,
                "url": MANIFEST_INDEX_URL,
                "priority": "explicit",
            },
            {"name": "other", "url": "https://other.example/simple"},
        ]
    )
    problems = manifest_problems(manifest, TARGETS)
    assert any("'other'" in p for p in problems), problems


def test_a_candidate_poetry_toml_is_refused(tmp_path: Path) -> None:
    (tmp_path / "poetry.toml").write_text("[certificates]\n")
    problems = checkout_problems(tmp_path)
    assert any("poetry.toml" in p for p in problems), problems


def test_a_checkout_without_one_is_clean() -> None:
    assert checkout_problems(ROOT) == []


def test_a_branch_only_candidate_sha_is_refused() -> None:
    main = "a" * 40
    branch_only = "b" * 40
    assert protected_main_problems(branch_only, main, "refs/heads/main", main)


def test_a_stale_main_candidate_sha_is_refused() -> None:
    old_main = "a" * 40
    current_main = "b" * 40
    assert protected_main_problems(old_main, current_main, "refs/heads/main", old_main)


def test_a_branch_dispatch_source_is_refused() -> None:
    main = "a" * 40
    assert protected_main_problems(main, main, "refs/heads/feature", main)


def test_exact_protected_main_candidate_is_accepted() -> None:
    main = "a" * 40
    assert protected_main_problems(main, main, "refs/heads/main", main) == []


def test_a_candidate_mismatching_workflow_sha_is_refused() -> None:
    main = "a" * 40
    assert protected_main_problems("b" * 40, main, "refs/heads/main", main)


# ── the index is data: every link it supplies is validated ─────────────────

_PAGE = f"{LOCK_INDEX_URL}/dotmac-files/"


def test_an_ordinary_relative_index_link_is_accepted() -> None:
    url = approved_artifact_url("../../files/dotmac_files-1.whl#sha256=ab", _PAGE)
    assert url.startswith(f"{ARTIFACT_ORIGIN}/api/packages/dotmac/pypi/files/")


@pytest.mark.parametrize(
    ("href", "why"),
    [
        ("https://elsewhere.example/x.whl", "off-origin"),
        ("http://registry.dotmac.io/api/packages/dotmac/pypi/files/x.whl", "http"),
        ("../../../../../../etc/passwd", "path traversal"),
        (
            "https://ci-reader:leaked@registry.dotmac.io/api/packages/dotmac/pypi/files/x.whl",
            "userinfo",
        ),
        ("https://registry.dotmac.io/other/x.whl", "outside the path prefix"),
        ("", "empty"),
    ],
)
def test_an_index_controlled_link_that_is_not_approved_refuses(
    href: str, why: str
) -> None:
    with pytest.raises(Refusal):
        approved_artifact_url(href, _PAGE)


@pytest.mark.parametrize(
    "href",
    [
        # Each of these resolves to a raw path that still starts with
        # ARTIFACT_PATH_PREFIX (so the existing prefix check alone would
        # accept it) and carries no LITERAL `..` segment (so the existing
        # literal check alone would accept it too) — only decoding the path
        # reveals the traversal, which is exactly what the server does.
        "%2e%2e/%2e%2e/dotmac_files-1.whl",
        "%2E%2E/%2E%2E/dotmac_files-1.whl",
        "%252e%252e/%252e%252e/dotmac_files-1.whl",
        ".%2e/.%2e/dotmac_files-1.whl",
        "..%2f..%2f..%2fetc/passwd",
    ],
    ids=[
        "lower-encoded",
        "upper-encoded",
        "double-encoded",
        "mixed-encoded",
        "encoded-slash-literal-dots",
    ],
)
def test_an_encoded_traversal_segment_is_refused(href: str) -> None:
    with pytest.raises(Refusal):
        approved_artifact_url(href, _PAGE)


@pytest.mark.parametrize(
    "href",
    [
        "../../files/dotmac_files-1.0%2Bbuild.5.whl#sha256=ab",
        "../../files/dotmac%7Efiles-1.whl#sha256=ab",
    ],
    ids=["encoded-plus-in-version", "encoded-tilde"],
)
def test_a_percent_encoded_href_that_is_not_traversal_is_still_accepted(
    href: str,
) -> None:
    url = approved_artifact_url(href, _PAGE)
    assert url.startswith(f"{ARTIFACT_ORIGIN}/api/packages/dotmac/pypi/files/")


def test_a_literal_backslash_in_the_resolved_path_is_refused() -> None:
    """`..\\..\\etc\\passwd` contains no `/` at all, so it is one opaque
    segment that survives every `..`-in-`split("/")` check and still starts
    with ARTIFACT_PATH_PREFIX. Whether the origin treats `\\` as a separator
    is not ours to assume either way, so a literal backslash is refused
    outright rather than reasoned about."""

    with pytest.raises(Refusal):
        approved_artifact_url("..\\..\\etc\\passwd", _PAGE)


def test_an_ordinary_path_with_no_backslash_still_passes() -> None:
    """POSITIVE CONTROL for the backslash refusal above."""

    url = approved_artifact_url("../../files/dotmac_files-1.whl#sha256=ab", _PAGE)
    assert url.startswith(f"{ARTIFACT_ORIGIN}/api/packages/dotmac/pypi/files/")


@pytest.mark.parametrize(
    "href",
    ["%c0%ae%c0%ae/etc/passwd", "%c0%af"],
    ids=["overlong-dot", "overlong-slash"],
)
def test_a_percent_escape_that_is_not_valid_utf8_is_refused(href: str) -> None:
    """`unquote` defaults to `errors='replace'`, so an overlong sequence like
    `%c0%ae` becomes U+FFFD on our side and can never reveal `..` here --
    while a differently-behaved decoder at the origin might read it as `.`
    or `/`. Our decode semantics are not proven to match the origin's, so
    what cannot be read unambiguously is refused rather than guessed at."""

    with pytest.raises(Refusal):
        approved_artifact_url(href, _PAGE)


def test_an_ordinary_percent_escape_still_decodes_and_passes() -> None:
    """POSITIVE CONTROL for the strict-UTF-8 refusal above -- an ordinary,
    valid percent-escape must still be accepted."""

    for href in (
        "../../files/dotmac_files-1.0%2Bbuild.5.whl#sha256=ab",
        "../../files/dotmac%7Efiles-1.whl#sha256=ab",
    ):
        url = approved_artifact_url(href, _PAGE)
        assert url.startswith(f"{ARTIFACT_ORIGIN}/api/packages/dotmac/pypi/files/")


def test_a_redirect_is_refused_rather_than_followed() -> None:
    argv = curl_argv("https://registry.dotmac.io/x", Path("artifact.whl"))
    assert "-L" not in argv and "--location" not in argv
    problems = transfer_problems("https://registry.dotmac.io/x", "302")
    assert any("redirect" in p.lower() for p in problems)
    assert transfer_problems("https://registry.dotmac.io/x", "200") == []


# ── acquisition_plan: the bundle is closed, and a gap refuses ──────────────


def test_the_bundle_closes_over_both_moved_packages() -> None:
    plan = acquisition_plan(_manifest(), _before(), TARGETS)
    assert plan[FILES] == TARGETS[FILES]
    assert plan[TAX] == TARGETS[TAX]


def test_a_private_dependency_that_is_not_an_exact_pin_refuses() -> None:
    manifest = _manifest()
    manifest["tool"]["poetry"]["dependencies"]["dotmac-approvals"] = {
        "version": "^0.1",
        "source": INDEX_SOURCE_NAME,
    }
    with pytest.raises(Refusal):
        acquisition_plan(manifest, _before(), TARGETS)


def test_the_repositorys_real_manifest_and_lock_produce_a_closed_plan() -> None:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        manifest = tomllib.load(handle)
    with (ROOT / "poetry.lock").open("rb") as handle:
        lock = tomllib.load(handle)
    plan = acquisition_plan(manifest, lock, TARGETS)
    assert plan[FILES] == TARGETS[FILES]
    assert plan[TAX] == TARGETS[TAX]


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("dotmac_files-0.1.0a4-py3-none-any.whl", True),
        ("dotmac_files-0.1.0a4.tar.gz", True),
        ("dotmac_files-0.1.0a40.tar.gz", False),
        ("dotmac_files_extra-0.1.0a4.tar.gz", False),
        ("dotmac_files-0.1.0a2.tar.gz", False),
    ],
)
def test_an_artifact_is_matched_to_its_exact_version(
    filename: str, expected: bool
) -> None:
    assert artifact_belongs_to(filename, FILES, "0.1.0a4") is expected


def test_the_lock_must_name_the_bytes_that_were_downloaded() -> None:
    version = TARGETS[FILES]
    names = sorted(artifact_names(FILES, version))
    digests = {names[0]: "a" * 64, names[1]: "b" * 64}
    lock = _lock([_package(FILES, version, source=_INDEX_SOURCE)], "hash")
    for item in lock["package"][0]["files"]:
        item["hash"] = f"sha256:{digests[item['file']]}"
    assert hash_problems(lock, digests, {FILES: version}) == []

    swapped = json.loads(json.dumps(lock))
    swapped["package"][0]["files"][0]["hash"] = "sha256:not-the-bytes"
    assert hash_problems(swapped, digests, {FILES: version})


# ── wheel-only: unchanged mechanism, reproven against ERP's own lock ───────


def test_a_dependency_available_only_as_an_sdist_is_refused_by_name() -> None:
    lock = {
        "package": [
            {
                "name": "anyio",
                "version": "4.14.2",
                "files": [{"file": "anyio-4.14.2-py3-none-any.whl"}],
            },
            {
                "name": "waitress",
                "version": "3.0.2",
                "files": [{"file": "waitress-3.0.2.tar.gz"}],
            },
        ]
    }
    problems = lock_wheel_problems(lock)
    assert len(problems) == 1
    assert problems[0].startswith("waitress 3.0.2 ")
    assert "no usable wheel" in problems[0]


def test_a_fully_wheel_available_resolution_is_admitted() -> None:
    lock = {
        "package": [
            {
                "name": "anyio",
                "version": "4.14.2",
                "files": [{"file": "anyio-4.14.2-py3-none-any.whl"}],
            },
            {
                "name": FILES,
                "version": TARGETS[FILES],
                "files": [
                    {"file": f} for f in sorted(artifact_names(FILES, TARGETS[FILES]))
                ],
            },
        ]
    }
    assert lock_wheel_problems(lock) == []


def test_a_tooling_only_sdist_is_outside_the_runtime_resolution() -> None:
    lock = {
        "package": [
            {
                "name": "compiler",
                "version": "1.0",
                "groups": ["dev"],
                "files": [{"file": "compiler-1.0.tar.gz"}],
            }
        ]
    }
    assert lock_wheel_problems(lock) == []


def test_an_off_index_pin_is_not_misclassified_as_an_empty_index_release() -> None:
    lock = {
        "package": [
            {
                "name": "client",
                "version": "1.0",
                "groups": ["main"],
                "files": [],
                "source": {
                    "type": "git",
                    "url": "https://example.test/client.git",
                    "reference": "v1.0",
                    "resolved_reference": "a" * 40,
                },
            }
        ]
    }
    assert lock_wheel_problems(lock) == []


def test_a_main_index_sdist_is_still_refused() -> None:
    lock = {
        "package": [
            {
                "name": "runtime-package",
                "version": "1.0",
                "groups": ["main", "dev"],
                "files": [{"file": "runtime_package-1.0.tar.gz"}],
            }
        ]
    }
    problems = lock_wheel_problems(lock)
    assert len(problems) == 1
    assert problems[0].startswith("runtime-package 1.0 ")


def test_this_repositorys_runtime_index_lock_is_already_wheel_only() -> None:
    with (ROOT / "poetry.lock").open("rb") as handle:
        lock = tomllib.load(handle)
    assert len(lock["package"]) > 20
    assert lock_wheel_problems(lock) == []


# ── bounding [package.dependencies] to the wheel's own Requires-Dist ───────


def _wheel_with_metadata(tmp_path: Path, requires: list[str]) -> Path:
    path = tmp_path / "thing-1.0-py3-none-any.whl"
    metadata = "Metadata-Version: 2.1\nName: thing\nVersion: 1.0\n" + "".join(
        f"Requires-Dist: {r}\n" for r in requires
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("thing-1.0.dist-info/METADATA", metadata)
    return path


def test_wheel_requires_dist_reads_the_wheels_own_metadata(tmp_path: Path) -> None:
    path = _wheel_with_metadata(
        tmp_path, ["sqlalchemy (>=2.0,<3.0)", "pytest (>=8) ; extra == 'test'"]
    )
    assert wheel_requires_dist(path) == [
        "sqlalchemy (>=2.0,<3.0)",
        "pytest (>=8) ; extra == 'test'",
    ]


def test_unconditional_requirement_names_drops_extras() -> None:
    names = unconditional_requirement_names(
        [
            "sqlalchemy (>=2.0,<3.0)",
            "pytest (>=8) ; extra == 'test'",
            "dotmac-kernel (>=0.1.0a56)",
        ]
    )
    assert names == {"sqlalchemy", "dotmac-kernel"}


def test_wheel_dependency_problems_is_clean_when_names_match() -> None:
    problems = wheel_dependency_problems(
        FILES,
        {"dotmac-kernel": ">=0.1.0a56", "sqlalchemy": ">=2.0,<3.0"},
        ["dotmac-kernel (>=0.1.0a56)", "sqlalchemy (>=2.0,<3.0)"],
    )
    assert problems == []


def test_wheel_dependency_problems_names_a_lock_only_dependency() -> None:
    """THE PLANT: the lock claims a dependency the wheel's own metadata never
    declares — exactly the shape a hand-edited or mis-resolved lock could
    take."""

    problems = wheel_dependency_problems(
        FILES,
        {"dotmac-kernel": ">=0.1.0a56", "requests": "*"},
        ["dotmac-kernel (>=0.1.0a56)"],
    )
    assert any("requests" in p and "never declares" in p for p in problems), problems


def test_wheel_dependency_problems_names_a_wheel_only_dependency() -> None:
    """THE PLANT the other way: the wheel unconditionally requires something
    the lock omits."""

    problems = wheel_dependency_problems(
        FILES,
        {"dotmac-kernel": ">=0.1.0a56"},
        ["dotmac-kernel (>=0.1.0a56)", "sqlalchemy (>=2.0)"],
    )
    assert any("sqlalchemy" in p and "absent from the lock" in p for p in problems), (
        problems
    )


# ── the mirror swap, and putting the real URL back ──────────────────────────


def test_the_mirror_swap_is_reversible() -> None:
    mirror = "http://127.0.0.1:8899/simple"
    text = f'[[tool.poetry.source]]\nname = "forgejo"\nurl = "{MANIFEST_INDEX_URL}"\n'
    aimed = point_at_mirror(text, mirror)
    assert mirror in aimed and MANIFEST_INDEX_URL not in aimed
    assert restore_index_url(aimed, mirror, MANIFEST_INDEX_URL) == text
    with pytest.raises(Refusal):
        point_at_mirror(text + text, mirror)
    with pytest.raises(Refusal):
        restore_index_url(text, mirror, MANIFEST_INDEX_URL)


def test_the_content_hash_line_is_replaced_exactly_once() -> None:
    lock = '[metadata]\nlock-version = "2.1"\ncontent-hash = "old"\n'
    assert 'content-hash = "new"' in set_content_hash(lock, "new")
    with pytest.raises(Refusal):
        set_content_hash(lock + 'content-hash = "second"\n', "new")


# ── defect-class 3: the credential scan can refuse ──────────────────────────

CREDENTIAL = "gho_A/b+c=d e9"


def test_an_absent_credential_is_a_refusal_in_its_own_right() -> None:
    with pytest.raises(Refusal):
        credential_encodings("")


@pytest.mark.parametrize(
    "render",
    [
        lambda c: c,
        lambda c: urllib.parse.quote(c, safe=""),
        lambda c: urllib.parse.quote_plus(c),
        lambda c: base64.b64encode(c.encode()).decode(),
        lambda c: base64.b64encode(f"{INDEX_USERNAME}:{c}".encode()).decode(),
    ],
)
def test_each_covered_encoding_is_found(tmp_path: Path, render: Any) -> None:
    subject = tmp_path / "pyproject.toml"
    subject.write_text(f'[tool.poetry]\nname = "{render(CREDENTIAL)}"\n')
    assert credential_sightings([subject], CREDENTIAL)


@pytest.mark.parametrize("root_name", ["candidate", "bundle"])
def test_credential_in_either_attestation_root_is_refused(
    tmp_path: Path, root_name: str
) -> None:
    candidate = tmp_path / "candidate"
    bundle = tmp_path / "bundle"
    candidate.mkdir()
    bundle.mkdir()
    (candidate if root_name == "candidate" else bundle).joinpath("payload").write_text(
        CREDENTIAL
    )
    with pytest.raises(Refusal, match="candidate or acquired bundle"):
        write_credential_attestation(
            tmp_path / "out",
            [
                path
                for path in (candidate / "payload", bundle / "payload")
                if path.exists()
            ],
            CREDENTIAL,
        )


def test_clean_credential_attestation_writes_fixed_marker(tmp_path: Path) -> None:
    out = tmp_path / "out"
    payload = tmp_path / "payload"
    payload.write_text("safe")
    write_credential_attestation(out, [payload], CREDENTIAL)
    assert (out / "credential-scan.ok").read_bytes() == b"credential scan clean\n"


def test_a_near_miss_is_not_reported(tmp_path: Path) -> None:
    subject = tmp_path / "pyproject.toml"
    subject.write_text(f"{CREDENTIAL[:-1]}\n{CREDENTIAL[1:]}\nghp_unrelated\n")
    assert credential_sightings([subject], CREDENTIAL) == []


# ── the pair leaves together, bound ──────────────────────────────────────────

_LOCK_TOML = """\
[[package]]
name = "dotmac-files"
version = "0.1.0a4"
files = [{file = "dotmac_files-0.1.0a4-py3-none-any.whl", hash = "sha256:a"}]

[metadata]
lock-version = "2.1"
python-versions = ">=3.11,<3.13"
content-hash = "the-content-hash"
"""


def _generated(tmp_path: Path) -> Path:
    manifest = tmp_path / "pyproject.toml"
    manifest.write_text('[tool.poetry]\nname = "x"\n')
    lock = tmp_path / "poetry.lock"
    lock.write_text(_LOCK_TOML)
    proof = tmp_path / "credential-scan.ok"
    proof.write_text("credential scan clean\n")
    out = tmp_path / "evidence"
    sightings = build_evidence(
        out,
        manifest,
        lock,
        {"ref": "a" * 40, "dotmac_files_version": "0.1.0a4", "workflow_run": "123"},
        proof,
    )
    assert sightings == []
    return out


def test_the_manifest_travels_with_the_lock(tmp_path: Path) -> None:
    out = _generated(tmp_path)
    assert {p.name for p in out.iterdir()} == {
        "pyproject.toml",
        "poetry.lock",
        "coordinates.txt",
        "credential-scan.ok",
        "SHA256SUMS",
    }
    assert (out / "credential-scan.ok").read_bytes() == b"credential scan clean\n"


def test_the_pair_is_verifiable_from_the_artifact_alone(tmp_path: Path) -> None:
    out = _generated(tmp_path)
    digests = {
        name: sha256_hex((out / name).read_bytes())
        for name in ("pyproject.toml", "poetry.lock")
    }
    coordinates = (out / "coordinates.txt").read_text()
    assert pair_binding(digests) in coordinates
    assert "123" in coordinates


def test_applying_one_half_of_the_pair_changes_the_binding(tmp_path: Path) -> None:
    out = _generated(tmp_path)
    digests = {
        name: sha256_hex((out / name).read_bytes())
        for name in ("pyproject.toml", "poetry.lock")
    }
    baseline = pair_binding(digests)
    for name in digests:
        other = dict(digests)
        other[name] = sha256_hex(b"a different file")
        assert pair_binding(other) != baseline


def test_a_credential_in_the_pair_is_not_scrubbed(tmp_path: Path) -> None:
    manifest = tmp_path / "pyproject.toml"
    manifest.write_text(f'[tool.poetry]\nname = "{CREDENTIAL}"\n')
    lock = tmp_path / "poetry.lock"
    lock.write_text(_LOCK_TOML)
    proof = tmp_path / "credential-scan.ok"
    proof.write_text("credential scan clean\n")
    out = tmp_path / "evidence"
    build_evidence(out, manifest, lock, {"ref": "a" * 40}, proof)
    assert CREDENTIAL in (out / "pyproject.toml").read_text()


def test_sha256sums_is_the_format_sha256sum_c_reads() -> None:
    assert (
        sha256sums({"b.txt": "beef", "a.txt": "cafe"}) == "cafe  a.txt\nbeef  b.txt\n"
    )


def test_no_release_or_deployment_language_in_the_pair_prose() -> None:
    """Point 9's own claim, checked in the artifact's own words."""

    from erp_lock import _PAIR_PROSE  # noqa: SLF001

    assert "NO release and NO deployment" in _PAIR_PROSE
    assert "committed nothing" in _PAIR_PROSE


# ── acquire, driven against a fake index (no network, no credential) ───────


def _fake_index(monkeypatch: pytest.MonkeyPatch, offered: dict[str, list[str]]) -> None:
    def _fetch(url: str, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        package = url.rstrip("/").rsplit("/", 1)[-1]
        if url.endswith("/"):
            rows = "\n".join(
                f'<a href="{name}">{name}</a><br>' for name in offered[package]
            )
            target.write_text(f"<html><body>{rows}</body></html>", encoding="utf-8")
        elif package.endswith(".whl"):
            with zipfile.ZipFile(target, "w") as archive:
                archive.writestr(
                    "dotmac_files-0.1.0a4.dist-info/METADATA",
                    "Metadata-Version: 2.1\nName: dotmac-files\nVersion: 0.1.0a4\n"
                    "Requires-Dist: dotmac-kernel (>=0.1.0a56)\n",
                )
        else:
            target.write_bytes(f"bytes of {target.name}".encode())

    monkeypatch.setattr(erp_lock, "fetch", _fetch)


def test_the_bundle_refuses_a_private_release_that_offers_only_an_sdist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_index(monkeypatch, {"dotmac-approvals": ["dotmac_approvals-0.1.0a5.tar.gz"]})
    with pytest.raises(Refusal) as refusal:
        acquire({"dotmac-approvals": "0.1.0a5"}, tmp_path / "bundle")
    message = str(refusal.value)
    assert message.startswith("dotmac-approvals 0.1.0a5 ")
    assert "no usable wheel" in message


def test_the_bundle_records_requires_dist_for_a_moved_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The acquisition step that makes `wheel-dependencies` possible without a
    second credentialed fetch: the wheel's own metadata is read once, here,
    while the credential is still in hand, and recorded for the credential-
    free job to compare against."""

    _fake_index(
        monkeypatch,
        {FILES: sorted(artifact_names(FILES, TARGETS[FILES]))},
    )
    out = tmp_path / "bundle"
    acquire({FILES: TARGETS[FILES]}, out)
    requires = json.loads((out / "requires" / f"{FILES}.json").read_text())
    assert requires == ["dotmac-kernel (>=0.1.0a56)"]


# ── the workflow's own shape ─────────────────────────────────────────────────


def _jobs() -> dict[str, list[str]]:
    lines = WORKFLOW.read_text().splitlines()
    start = lines.index("jobs:")
    jobs: dict[str, list[list[str]]] = {}
    current: str | None = None
    in_steps = False
    for line in lines[start + 1 :]:
        header = re.match(r"^  ([A-Za-z][\w-]*):\s*$", line)
        if header:
            current = header.group(1)
            jobs[current] = []
            in_steps = False
            continue
        if current is None:
            continue
        if line == "    steps:":
            in_steps = True
            continue
        if not in_steps:
            continue
        if line.startswith("      - "):
            jobs[current].append([line])
        elif jobs[current]:
            jobs[current][-1].append(line)
    return {
        name: ["\n".join(block) for block in blocks] for name, blocks in jobs.items()
    }


def _needs(job: str) -> set[str]:
    text = WORKFLOW.read_text()
    direct: dict[str, str] = {}
    current: str | None = None
    for line in text.splitlines():
        header = re.match(r"^  ([A-Za-z][\w-]*):\s*$", line)
        if header:
            current = header.group(1)
        elif current and line.startswith("    needs: "):
            direct[current] = line.split("needs: ", 1)[1].strip()
    closure: set[str] = set()
    frontier = [job]
    while frontier:
        name = frontier.pop()
        parent = direct.get(name)
        if parent and parent not in closure:
            closure.add(parent)
            frontier.append(parent)
    return closure


def _steps() -> list[str]:
    return [step for steps in _jobs().values() for step in steps]


def _commands(step: str) -> str:
    return "\n".join(
        line for line in step.splitlines() if not line.strip().startswith("#")
    )


def _run_scripts() -> list[str]:
    lines = WORKFLOW.read_text().splitlines()
    bodies: list[list[str]] = []
    inside: int | None = None
    for line in lines:
        match = re.match(r"^(\s*)-?\s*run: \|", line)
        if match:
            inside = len(match.group(1)) + 2
            bodies.append([])
            continue
        if inside is None:
            continue
        if line.strip() and not line.startswith(" " * inside):
            inside = None
            continue
        bodies.append(bodies.pop() + [line])
    return ["\n".join(body) for body in bodies]


def test_the_workflow_parser_found_the_jobs() -> None:
    jobs = _jobs()
    assert set(jobs) == {"acquire", "resolve", "attest"}, sorted(jobs)
    for name, steps in jobs.items():
        assert len(steps) >= 4, (name, len(steps))
    assert _needs("attest") == {"resolve", "acquire"}
    assert _needs("acquire") == set()


def test_the_job_that_runs_poetry_references_no_secret() -> None:
    jobs = _jobs()
    resolver = [
        name
        for name, steps in jobs.items()
        if any("poetry lock" in _commands(s) for s in steps)
    ]
    assert resolver == ["resolve"], resolver
    for step in jobs["resolve"]:
        assert "secrets." not in step, step
    assert "FORGEJO_READ_TOKEN" not in "\n".join(jobs["resolve"])


def test_the_resolver_job_asserts_its_own_emptiness_before_resolving() -> None:
    steps = _jobs()["resolve"]
    assertion = next(i for i, s in enumerate(steps) if "no credential is present" in s)
    resolution = next(i for i, s in enumerate(steps) if "poetry lock" in _commands(s))
    assert assertion < resolution
    assert "POETRY_HTTP_BASIC" in steps[assertion] and ".netrc" in steps[assertion]


def test_the_acquiring_and_attesting_jobs_run_no_poetry_and_no_package_code() -> None:
    jobs = _jobs()
    for name in ("acquire", "attest"):
        commands = "\n".join(_commands(s) for s in jobs[name])
        assert "install-poetry" not in commands, name
        assert not re.search(r"(^|\s)poetry\s", commands), name
        assert "pip install" not in commands, name


def test_there_is_no_authenticated_online_fallback() -> None:
    commands = "\n".join(_commands(s) for s in _jobs()["resolve"])
    assert "mirror-manifest" in commands
    assert "machine %s login %s password %s" not in commands


def _wheel_only_gate_problems(steps: list[str]) -> list[str]:
    commands = [_commands(step) for step in steps]
    gates = [i for i, c in enumerate(commands) if "erp_lock.py wheel-only" in c]
    resolutions = [
        i for i, c in enumerate(commands) if re.search(r"^\s*poetry lock\b", c, re.M)
    ]
    problems: list[str] = []
    if not gates:
        problems.append("no step runs the wheel-only gate at all")
    if not resolutions:
        problems.append("no step runs `poetry lock`")
    if not gates or not resolutions:
        return problems
    if not any(g < min(resolutions) for g in gates):
        problems.append(
            "every wheel-only check runs AFTER `poetry lock`, so none of them "
            "prevents a build backend running — they only report one that did"
        )
    if not any(g > max(resolutions) for g in gates):
        problems.append(
            "nothing re-checks the lock that leaves, so the artifact cannot "
            "say on its own that no entry in it needs a build"
        )
    return problems


def test_the_wheel_only_gate_runs_before_poetry_lock() -> None:
    assert _wheel_only_gate_problems(_jobs()["resolve"]) == []


def test_removing_the_wheel_only_gate_entirely_is_named() -> None:
    steps = [
        s for s in _jobs()["resolve"] if "erp_lock.py wheel-only" not in _commands(s)
    ]
    assert len(steps) == len(_jobs()["resolve"]) - 2, "the plant removed nothing"
    assert _wheel_only_gate_problems(steps) == [
        "no step runs the wheel-only gate at all"
    ]


def test_demoting_the_gate_to_an_after_the_fact_report_is_named() -> None:
    steps = _jobs()["resolve"]
    pre = next(
        i for i, s in enumerate(steps) if "erp_lock.py wheel-only" in _commands(s)
    )
    demoted = steps[:pre] + steps[pre + 1 :]
    problems = _wheel_only_gate_problems(demoted)
    assert problems == [
        "every wheel-only check runs AFTER `poetry lock`, so none of them "
        "prevents a build backend running — they only report one that did"
    ], problems


def _off_index_lock_gate_problems(steps: list[str]) -> list[str]:
    commands = [_commands(step) for step in steps]
    gates = [i for i, c in enumerate(commands) if "erp_lock.py off-index-lock" in c]
    resolutions = [
        i for i, c in enumerate(commands) if re.search(r"^\s*poetry lock\b", c, re.M)
    ]
    problems: list[str] = []
    if not gates:
        problems.append("no step runs the off-index-lock gate at all")
    if not resolutions:
        problems.append("no step runs `poetry lock`")
    if not gates or not resolutions:
        return problems
    if not any(g < min(resolutions) for g in gates):
        problems.append(
            "every off-index-lock check runs AFTER `poetry lock`, so an "
            "already-wrong pin is never caught before resolution"
        )
    if not any(g > max(resolutions) for g in gates):
        problems.append(
            "nothing re-checks the resolved lock, so resolution could have "
            "changed the off-index pin's identity unnoticed"
        )
    return problems


def test_the_off_index_lock_gate_runs_before_and_after_poetry_lock() -> None:
    assert _off_index_lock_gate_problems(_jobs()["resolve"]) == []


def test_removing_the_off_index_lock_gate_entirely_is_named() -> None:
    steps = [
        s
        for s in _jobs()["resolve"]
        if "erp_lock.py off-index-lock" not in _commands(s)
    ]
    assert len(steps) == len(_jobs()["resolve"]) - 2, "the plant removed nothing"
    assert _off_index_lock_gate_problems(steps) == [
        "no step runs the off-index-lock gate at all"
    ]


def test_every_checkout_refuses_to_persist_the_token() -> None:
    checkouts = [s for s in _steps() if "actions/checkout@" in s]
    assert len(checkouts) == 5, len(checkouts)
    for step in checkouts:
        assert "persist-credentials: false" in step, step


def test_the_trusted_checkout_comes_first_and_owns_the_workspace_root() -> None:
    for name, steps in _jobs().items():
        checkouts = [s for s in steps if "actions/checkout@" in s]
        assert checkouts, name
        assert "ref: ${{ github.sha }}" in checkouts[0], name
        assert "path:" not in checkouts[0], f"{name}: the trusted checkout owns /"
        for step in checkouts[1:]:
            assert "ref: ${{ inputs.ref }}" in step and "path: work" in step, name


def test_no_job_holds_the_credential_before_the_tree_is_judged() -> None:
    jobs = _jobs()
    guarded = {
        n for n, s in jobs.items() if any("manifest-guard" in _commands(st) for st in s)
    }
    assert guarded
    holders = {
        n
        for n, s in jobs.items()
        if any("secrets.FORGEJO_READ_TOKEN" in st for st in s)
    }
    assert holders
    for name in holders:
        steps = jobs[name]
        if name in guarded:
            guard = max(
                i for i, s in enumerate(steps) if "manifest-guard" in _commands(s)
            )
            held = [i for i, s in enumerate(steps) if "secrets.FORGEJO_READ_TOKEN" in s]
            assert all(i > guard for i in held), (name, guard, held)
        else:
            assert _needs(name) & guarded, name


def test_protected_main_is_decided_by_the_script_before_credentials() -> None:
    steps = _jobs()["acquire"]
    decision = next(
        i for i, s in enumerate(steps) if "erp_lock.py protected-main" in _commands(s)
    )
    holders = [i for i, s in enumerate(steps) if "secrets.FORGEJO_READ_TOKEN" in s]
    assert holders and decision < min(holders)


def test_only_acquire_references_the_forgejo_credential() -> None:
    text = WORKFLOW.read_text()
    sections = {
        name: re.search(
            rf"\n  {name}:\n(.*?)(?=\n  (?:acquire|resolve|attest):|\Z)",
            text,
            re.S,
        ).group(1)
        for name in ("acquire", "resolve", "attest")
    }
    assert "secrets.FORGEJO_READ_TOKEN" in sections["acquire"]
    assert all(
        "secrets.FORGEJO_READ_TOKEN" not in sections[name]
        for name in ("resolve", "attest")
    )


def test_the_ref_under_resolution_is_only_ever_read_from_work() -> None:
    scripts = "\n".join(_run_scripts())
    assert "python scripts/erp_lock.py" in scripts
    assert "python work/scripts/erp_lock.py" not in scripts
    for addressed in ("work/pyproject.toml", "work/poetry.lock"):
        assert addressed in scripts


def _interpolated_inputs(bodies: list[str]) -> list[str]:
    return [
        line
        for body in bodies
        for line in body.splitlines()
        if "${{" in line and "inputs." in line
    ]


def test_no_workflow_input_is_interpolated_into_a_shell_script() -> None:
    """The CRITICAL constraint: `${{ }}` templates BEFORE bash sees the
    script; an interpolated input is a script-injection sink even when the
    value happens to be regex-validated elsewhere. Every input crosses as
    `env:` and is referenced as a quoted shell variable instead."""

    assert _interpolated_inputs(_run_scripts()) == []


def test_the_interpolation_check_would_catch_one() -> None:
    """SENSITIVITY. The assertion above passes over a clean file, which
    proves nothing about the assertion."""

    planted = 'echo "ref ${{ inputs.ref }}"\necho safe\n'
    assert _interpolated_inputs([planted]) == ['echo "ref ${{ inputs.ref }}"']
    assert _interpolated_inputs(["echo safe\n"]) == []


def test_the_run_block_parser_actually_found_the_scripts() -> None:
    bodies = _run_scripts()
    assert len(bodies) >= 10, len(bodies)
    assert any("poetry lock" in body for body in bodies)


def test_no_curl_in_the_workflow_follows_a_redirect() -> None:
    for body in _run_scripts():
        for line in body.splitlines():
            if "curl" not in line:
                continue
            assert " -L" not in line and "--location" not in line, line


def test_no_resolver_log_is_collected_anywhere() -> None:
    text = WORKFLOW.read_text()
    assert "resolver.log" not in text
    assert "--resolver-log" not in text


def test_the_credential_is_wired_the_way_the_precedent_wires_it() -> None:
    text = WORKFLOW.read_text()
    assert '"ci-reader"' in text
    assert "secrets.FORGEJO_READ_TOKEN" in text
    assert "POETRY_HTTP_BASIC_FORGEJO_USERNAME:" not in text
    assert "POETRY_HTTP_BASIC_FORGEJO_PASSWORD:" not in text
    assert "bao read" not in text
    assert "vault read" not in text
    assert "secret/dotmac/forgejo/read-token" in text


def test_the_workflow_never_commits_or_opens_a_pull_request() -> None:
    text = WORKFLOW.read_text()
    assert "permissions:\n  contents: read\n" in text
    for forbidden in ("git commit", "git push", "gh pr", "peter-evans"):
        assert forbidden not in text, forbidden


def test_every_action_is_pinned_by_commit() -> None:
    for step in _steps():
        for line in step.splitlines():
            match = re.search(r"uses: (?!\./)(\S+)", line)
            if match:
                assert re.search(r"@[0-9a-f]{40}$", match.group(1)), line


def test_it_is_dispatch_only() -> None:
    text = WORKFLOW.read_text()
    assert "workflow_dispatch:" in text
    for trigger in ("\n  push:", "\n  pull_request:", "\n  schedule:"):
        assert trigger not in text, trigger


def test_only_the_two_allowed_versions_are_named_as_valid_in_the_dispatch_gate() -> (
    None
):
    """The workflow's own bash refusal names the closed pair literally — the
    Python-side `_parse_movement_args` is the authoritative check (proven
    above), and this asserts the workflow's first-line refusal agrees with it
    rather than drifting to a different pair."""

    text = WORKFLOW.read_text()
    acquire_job = _jobs()["acquire"][0]
    assert TARGETS[FILES] in acquire_job
    assert TARGETS[TAX] in acquire_job
    assert TARGETS[FILES] in text and TARGETS[TAX] in text


def test_no_poetry_binary_setting_is_relied_on_for_this() -> None:
    executed = "\n".join(_commands(s) for s in _steps())
    for setting in ("no-binary", "only-binary", "no_binary", "only_binary"):
        assert setting not in executed, setting


# ── the off-index exemption states a premise that is actually enforced ───────


_PIN = erp_lock.ALLOWED_OFF_INDEX_DEPENDENCIES["dotmac-integration-client"]
_CLIENT = "dotmac-integration-client"


def _pinned_spec(**overrides: object) -> dict[str, object]:
    spec: dict[str, object] = {"git": _PIN.url, "tag": _PIN.tag}
    spec.update(overrides)
    return spec


def _lock_with(**overrides: object) -> dict[str, object]:
    source = {
        "type": "git",
        "url": _PIN.url,
        "reference": _PIN.tag,
        "resolved_reference": _PIN.commit,
    }
    source.update(overrides)
    return {"package": [{"name": _CLIENT, "version": "0.2.0", "source": source}]}


def test_the_pinned_off_index_dependency_is_accepted_as_declared() -> None:
    """POSITIVE CONTROL, and it is the real declaration.

    ERP's own `pyproject.toml` carries this dependency, so a premise that
    refused it would have been a false positive on day one — which is why the
    exemption exists at all.
    """

    assert erp_lock.off_index_pin_problems("t.d." + _CLIENT, _pinned_spec(), _PIN) == []
    assert erp_lock.off_index_lock_problems(_lock_with()) == []


@pytest.mark.parametrize(
    ("label", "url"),
    (
        ("without .git", _PIN.url.removesuffix(".git")),
        ("trailing slash", _PIN.url.removesuffix(".git") + "/"),
        ("upper-case host", _PIN.url.replace("github.com", "GitHub.com")),
    ),
)
def test_harmless_url_spellings_are_equated(label: str, url: str) -> None:
    """Normalisation exists so a different SPELLING of the pinned repository is
    not read as a different repository."""

    assert erp_lock.off_index_pin_problems("t.d", _pinned_spec(git=url), _PIN) == []


@pytest.mark.parametrize(
    ("label", "spec", "expected"),
    (
        (
            "another host under the same name",
            _pinned_spec(git="https://evil.example/dotmac-integration-client.git"),
            "names repository",
        ),
        (
            "http rather than https",
            _pinned_spec(git=_PIN.url.replace("https", "http")),
            "names repository",
        ),
        (
            "ssh form is not guessed at",
            _pinned_spec(git="git@github.com:x/y.git"),
            "names repository",
        ),
        ("a different tag", _pinned_spec(tag="v0.3.0"), "names tag"),
        ("a mutable branch", {"git": _PIN.url, "branch": "main"}, "carries branch"),
        ("a rev beside the tag", _pinned_spec(rev="deadbeef"), "carries rev"),
        ("no tag at all", {"git": _PIN.url}, "missing tag"),
        ("not a table", "some-string", "must be a table"),
    ),
)
def test_the_manifest_half_of_the_premise_is_enforced(
    label: str, spec: object, expected: str
) -> None:
    """Keying on the NAME enforced only the first clause of the premise.

    The premise is "this dependency, from this host, at this immutable tag,
    resolving to these bytes". A changed URL under the same name, or a tag moved
    to point elsewhere, both used to pass — and the allowlisted dependency was
    additionally skipped by every other constraint check, making it the one
    dependency in the manifest that nothing examined.
    """

    problems = erp_lock.off_index_pin_problems("t.d." + _CLIENT, spec, _PIN)
    assert problems, label
    assert any(expected in problem for problem in problems), problems


@pytest.mark.parametrize(
    ("label", "overrides", "expected"),
    (
        ("the tag moved upstream", {"resolved_reference": "b" * 40}, "the tag moved"),
        (
            "resolved_reference is not a commit",
            {"resolved_reference": "v0.2.0"},
            "not a\n        40-character commit".replace("\n        ", " "),
        ),
        ("reference is a branch", {"reference": "main"}, "not the pinned tag"),
        (
            "resolved from another host",
            {"url": "https://evil.example/x.git"},
            "not the pinned",
        ),
        ("not a git source", {"type": "directory"}, "not git"),
    ),
)
def test_the_lock_half_of_the_premise_is_enforced(
    label: str, overrides: dict[str, object], expected: str
) -> None:
    """The half the manifest cannot prove.

    A manifest names a tag; the lock records what that tag RESOLVED to. Checking
    both is what makes the acquired tag, the lock reference and the expected
    commit agree — so a tag moved upstream between the pin being reviewed and
    the lock being generated is refused rather than silently adopted.
    """

    problems = erp_lock.off_index_lock_problems(_lock_with(**overrides))
    assert problems, label
    assert any(expected in problem for problem in problems), problems


def test_a_lock_entry_with_no_source_table_at_all_is_refused() -> None:
    """`source` entirely absent (not merely a wrong shape) must be caught by
    the same `isinstance(source, dict)` guard as an explicitly non-dict
    `source` -- `dict.get` returns `None` for a missing key, and `None` is
    not a `dict` either."""

    lock = {
        "package": [{"name": _CLIENT, "version": "0.2.0"}]
    }  # no "source" key at all
    problems = erp_lock.off_index_lock_problems(lock)
    assert problems
    assert any("no source table" in problem for problem in problems), problems


def test_a_pep508_requirement_cannot_express_the_pinned_form() -> None:
    """A requirement string has nowhere to put a tag or a resolved commit, so a
    pinned dependency appearing in that form is not the pinned dependency."""

    problems = erp_lock._requirement_problems("project.dependencies[0]", _CLIENT)
    assert problems and "cannot express its tag" in problems[0], problems


def test_the_real_erp_manifest_and_lock_satisfy_the_premise() -> None:
    """Non-vacuity against the actual repository, not a fixture.

    If this ever fails, either the pin is stale or ERP's declaration changed —
    and both are things a human must look at rather than a guard relaxing.
    """

    manifest = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    lock = tomllib.loads((ROOT / "poetry.lock").read_text(encoding="utf-8"))
    declared = manifest["tool"]["poetry"]["dependencies"][_CLIENT]
    assert erp_lock.off_index_pin_problems("t.d." + _CLIENT, declared, _PIN) == []
    assert erp_lock.off_index_lock_problems(lock) == []


# ── every `*_problems` validator this module defines is reachable from the
#    command line — a validator with a `def` and zero call sites is an
#    unmonitored region, not a control (this is what `off_index_lock_problems`
#    actually was, before the CLI subcommand above wired it) ────────────────

#: A validator named here is DELIBERATELY defined but not reachable from any
#: CLI subcommand. Empty after this repair: every `*_problems` function
#: `erp_lock.py` defines is reachable from `_run`'s dispatch, directly or
#: through another function it calls. A name may only be added here with a
#: comment stating why it is correct for that validator to be unwired --
#: "not yet wired" is not such a reason.
UNWIRED_PROBLEMS_EXEMPTIONS: dict[str, str] = {}


#: Both `FunctionDef` node types — `async def` is a DISTINCT AST node
#: (`ast.AsyncFunctionDef`), not a `FunctionDef` with a flag, so a check
#: gated on `FunctionDef` alone lets an `async def foo_problems` — dead or
#: alive — through without ever being seen, let alone required to be
#: reachable. Every walk that enumerates function definitions below matches
#: both node types.
_FUNCTION_DEF_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef)


def _problems_function_names(source: str) -> set[str]:
    """Every function this source defines whose name ends `_problems`."""

    tree = ast.parse(source, filename="erp_lock.py")
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, _FUNCTION_DEF_TYPES) and node.name.endswith("_problems")
    }


def _own_calls(node: ast.AST) -> set[str]:
    """Bare-name calls made directly in `node`'s own body -- NOT descending
    into a nested function definition's body.

    A call inside a nested `def` is only made if and when that nested
    function is itself called; crediting it unconditionally to the
    ENCLOSING function would manufacture a reachability edge that does not
    exist statically (the enclosing function might never call the nested
    one, or might pass it around instead of calling it). `ast.walk` over
    the whole subtree does not distinguish these cases, so this walks by
    hand and stops at every nested function boundary; `ast.walk(tree)` at
    the top level still visits that nested `def` as its own node, so its
    OWN calls are still counted -- just only credited to ITS name, not to
    every function that happens to contain it.
    """

    found: set[str] = set()
    for child in ast.iter_child_nodes(node):
        if isinstance(child, _FUNCTION_DEF_TYPES):
            continue
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Name):
            found.add(child.func.id)
        found |= _own_calls(child)
    return found


def _call_graph(source: str) -> dict[str, set[str]]:
    """name -> the set of names it calls, by static AST inspection.

    Only bare `ast.Name` call targets count. An `ast.Attribute` call
    (`self.x()`, `module.x()`) is deliberately NOT credited by its final
    attribute name: crediting it would let an unrelated object's
    same-named method (`foo.off_index_lock_problems()`) or a shadowing
    local rebound to the name manufacture FALSE reachability for a real
    validator that is not actually called. Every validator in this module
    is in fact called as a bare name, so this loses no real edge; it can
    only make the detector MORE willing to call something unreachable,
    which is the safe direction to be wrong in.

    Two functions sharing one name is refused rather than silently
    resolved by "last one wins": the call graph cannot decide which
    definition a caller actually reaches, so reachability-by-name is not a
    decidable question for it, and pretending it is by overwriting the
    earlier entry could hide a real gap behind whichever definition
    happened to be walked last.
    """

    tree = ast.parse(source, filename="erp_lock.py")
    graph: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, _FUNCTION_DEF_TYPES):
            continue
        if node.name in graph:
            raise AssertionError(
                f"two function definitions named {node.name!r} in one "
                "module -- the call graph cannot decide which one a caller "
                "reaches, so reachability-by-name is not decidable for it"
            )
        graph[node.name] = _own_calls(node)
    return graph


def _reachable_from_cli(source: str) -> set[str]:
    """Every function name reachable from `_run`'s dispatch body, transitively.

    `_run` IS the CLI dispatch: `_build_parser`'s subcommands only ever reach
    a validator through a branch of `_run`, so `_run`'s call graph closure is
    exactly "reachable from a CLI subcommand" for this module.
    """

    graph = _call_graph(source)
    seen: set[str] = set()
    frontier = list(graph.get("_run", set()))
    while frontier:
        name = frontier.pop()
        if name in seen:
            continue
        seen.add(name)
        frontier.extend(graph.get(name, set()) - seen)
    return seen


def test_every_problems_function_is_reachable_from_a_cli_subcommand() -> None:
    source = (ROOT / "scripts" / "erp_lock.py").read_text(encoding="utf-8")
    reachable = _reachable_from_cli(source)
    unreachable = _problems_function_names(source) - reachable
    unexempted = sorted(unreachable - set(UNWIRED_PROBLEMS_EXEMPTIONS))
    assert not unexempted, (
        f"{unexempted} are defined but never called from `_run`'s CLI "
        "dispatch, directly or transitively -- either wire a subcommand to "
        "them or add a commented exemption stating why not"
    )


def test_the_unwired_problems_exemption_set_names_nothing_actually_reachable() -> None:
    """A stale exemption is the same failure mode in a new place: a name that
    reads as a considered decision but is not actually true of the code."""

    source = (ROOT / "scripts" / "erp_lock.py").read_text(encoding="utf-8")
    reachable = _reachable_from_cli(source)
    stale = sorted(set(UNWIRED_PROBLEMS_EXEMPTIONS) & reachable)
    assert not stale, f"{stale} are exempted as unwired but are in fact reachable"


def test_the_unwired_problems_exemption_set_starts_empty() -> None:
    assert UNWIRED_PROBLEMS_EXEMPTIONS == {}


# ── sensitivity proof for the reachability guard itself, against a synthetic
#    module — not the real file, so this proves the DETECTOR, independent of
#    whatever erp_lock.py currently contains ──────────────────────────────

_UNWIRED_SOURCE = """
def alpha_problems(x):
    return []

def _run(args):
    if args.command == "beta":
        return _report("beta", beta_problems(args))
    return 1

def beta_problems(x):
    return []
"""

_ALL_WIRED_SOURCE = """
def alpha_problems(x):
    return []

def _run(args):
    if args.command == "alpha":
        return _report("alpha", alpha_problems(args))
    if args.command == "beta":
        return _report("beta", beta_problems(args))
    return 1

def beta_problems(x):
    return []
"""


def test_the_detector_names_a_planted_unwired_validator() -> None:
    reachable = _reachable_from_cli(_UNWIRED_SOURCE)
    unreachable = _problems_function_names(_UNWIRED_SOURCE) - reachable
    assert unreachable == {"alpha_problems"}, unreachable


def test_the_detector_does_not_flag_a_validator_that_is_actually_wired() -> None:
    reachable = _reachable_from_cli(_ALL_WIRED_SOURCE)
    unreachable = _problems_function_names(_ALL_WIRED_SOURCE) - reachable
    assert unreachable == set(), unreachable


_ASYNC_UNWIRED_SOURCE = """
async def fake_problems(x):
    return []

def _run(args):
    if args.command == "beta":
        return _report("beta", beta_problems(args))
    return 1

def beta_problems(x):
    return []
"""


def test_an_async_def_validator_is_seen_and_reported_unreachable() -> None:
    """`async def fake_problems` is a distinct AST node from `FunctionDef`; the
    detector must not let that keyword be a silent escape from the audit."""

    names = _problems_function_names(_ASYNC_UNWIRED_SOURCE)
    assert "fake_problems" in names, "the async def was never even seen"
    reachable = _reachable_from_cli(_ASYNC_UNWIRED_SOURCE)
    assert "fake_problems" not in reachable
    assert "fake_problems" in (names - reachable)


# ── a `*_problems` name must never be REBOUND anywhere in the module — a
#    name whose binding can move is a name whose reachability the detector
#    above cannot decide by static inspection ───────────────────────────────


def _rebound_problems_names(source: str) -> set[str]:
    """Every actual `*_problems` VALIDATOR name (one this source defines with
    `def`/`async def`) that is also assigned, bound as a loop/with/
    comprehension target, taken as a parameter, caught by an `except ... as`
    clause, declared `global`/`nonlocal`, or imported under an alias
    anywhere else in the module — its own `def` is a binding, not a
    rebinding, and is excluded.

    `except X as name:` and `global`/`nonlocal name` are, deliberately on
    Python's part, NOT `ast.Name` nodes — `ast.ExceptHandler.name` and
    `ast.Global`/`ast.Nonlocal.names` hold plain strings, so a check that
    only matches `ast.Name`+`Store` never sees either one, and
    `except SomeError as off_index_lock_problems:` would rebind a real
    validator name invisibly to this guard. Both are matched explicitly
    here rather than by walking for `ast.Name` nodes, because there are
    none to walk to.

    Scoped to names `_problems_function_names` actually returns, not to
    every identifier that happens to end in `_problems`: a local result
    variable such as `_run`'s own `before_problems`/`after_problems` ends in
    that suffix without naming, shadowing, or being confusable with any
    validator, and flagging it would be a false positive, not a finding.
    """

    validator_names = _problems_function_names(source)
    tree = ast.parse(source, filename="erp_lock.py")
    rebound: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Name)
            and isinstance(node.ctx, ast.Store)
            and node.id in validator_names
        ):
            rebound.add(node.id)
        elif isinstance(node, ast.arg) and node.arg in validator_names:
            rebound.add(node.arg)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                bound = alias.asname or alias.name
                if bound in validator_names:
                    rebound.add(bound)
        elif (
            isinstance(node, ast.ExceptHandler)
            and node.name is not None
            and node.name in validator_names
        ):
            rebound.add(node.name)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            for name in node.names:
                if name in validator_names:
                    rebound.add(name)
    return rebound


def test_no_problems_validator_name_is_ever_rebound_in_the_module() -> None:
    source = (ROOT / "scripts" / "erp_lock.py").read_text(encoding="utf-8")
    assert _rebound_problems_names(source) == set()


_REBOUND_SOURCE = """
def alpha_problems(x):
    return []

def _run(args):
    alpha_problems = lambda *_: []
    return alpha_problems(args)
"""

_CLEAN_SOURCE = """
def alpha_problems(x):
    return []

def _run(args):
    return alpha_problems(args)
"""


def test_the_rebinding_detector_names_a_planted_shadow() -> None:
    assert _rebound_problems_names(_REBOUND_SOURCE) == {"alpha_problems"}


def test_the_rebinding_detector_does_not_flag_a_clean_module() -> None:
    assert _rebound_problems_names(_CLEAN_SOURCE) == set()


_EXCEPT_REBOUND_SOURCE = """
def alpha_problems(x):
    return []

def _run(args):
    try:
        return alpha_problems(args)
    except ValueError as alpha_problems:
        return alpha_problems
"""

_EXCEPT_CLEAN_SOURCE = """
def alpha_problems(x):
    return []

def _run(args):
    try:
        return alpha_problems(args)
    except ValueError as exc:
        return exc
"""


def test_the_rebinding_detector_names_a_planted_except_as_shadow() -> None:
    """`except X as name:` binds through a plain string on
    `ast.ExceptHandler.name`, not an `ast.Name` node — this is what a
    `Name`+`Store`-only check cannot see."""

    assert _rebound_problems_names(_EXCEPT_REBOUND_SOURCE) == {"alpha_problems"}


def test_the_rebinding_detector_does_not_flag_an_unrelated_except_name() -> None:
    """POSITIVE CONTROL: an `except ... as` clause that binds a name which is
    not a validator must not be flagged."""

    assert _rebound_problems_names(_EXCEPT_CLEAN_SOURCE) == set()


# ── a nested `def`'s calls belong to ITS name, not to every function that
#    happens to contain it ──────────────────────────────────────────────────

_NESTED_DEF_UNCALLED_SOURCE = """
def beta_problems(x):
    return []

def _run(args):
    def _helper():
        return beta_problems(args)
    return 1
"""

_NESTED_DEF_ACTUALLY_CALLED_SOURCE = """
def beta_problems(x):
    return []

def _run(args):
    def _helper():
        return beta_problems(args)
    return _helper()
"""


def test_a_call_inside_an_uncalled_nested_def_is_not_credited_to_the_outer_function() -> (
    None
):
    """The nested `def _helper` is never itself called, so `beta_problems`
    -- called only from inside it -- must not be reachable through `_run`."""

    reachable = _reachable_from_cli(_NESTED_DEF_UNCALLED_SOURCE)
    assert "beta_problems" not in reachable


def test_a_call_inside_an_actually_called_nested_def_is_still_reachable() -> None:
    """POSITIVE CONTROL: when `_run` actually calls the nested `_helper`,
    `beta_problems` -- reachable through `_helper` -- must still be seen."""

    reachable = _reachable_from_cli(_NESTED_DEF_ACTUALLY_CALLED_SOURCE)
    assert "beta_problems" in reachable
