"""Own the dependency-surface plan digest and the verified bundle it binds.

## Why this exists

`workflow_dispatch` runs the workflow FILE FROM THE DISPATCHED REF: when a
workflow is dispatched against a branch, `github.sha` in that run is the
dispatched ref's commit, not protected `main`. Every credentialed workflow in
this repository today (`scripts/erp_lock.py` and its
`.github/workflows/erp-lock.yml`) treats `github.sha` as a trusted tooling
checkout while the run holds `secrets.FORGEJO_READ_TOKEN` — so a
same-repository branch can dispatch its own modified tooling bytes and
receive the read credential for the private Forgejo index. The end state
this module works toward is: remove that repository-scoped secret entirely,
and make PR CI consume a verified, IMMUTABLE dependency bundle produced once
by a trusted, protected-branch-pinned workflow, instead of every candidate
branch reaching the registry itself.

This module is the shared mechanics for that end state: computing the
PLAN DIGEST that says "this exact private dependency surface" without ever
running Poetry or reaching a network; verifying that a GitHub Actions
artifact and the run that produced it are what a policy says they must be
(LOCALLY — see `verify_run_metadata`'s docstring for exactly what is and is
not covered); verifying and safely extracting the bundle archive; and
materialising it as a local, offline PEP 503 index a candidate's resolver
can point at.

Nothing in this repository calls this module yet (see the docstring section
"Named duplication debt, and its retirement condition" below, and
`docs/architecture/dependency-bundle-trust.md`). This slice is deliberately
additive only.

## The plan digest is the trust boundary

The plan digest is computed ONLY from the parsed dependency surface
(`pyproject.toml` + `poetry.lock`, via `tomllib` — never a raw byte hash of
either file, which would fail on a reformatted-but-identical file). It is
never accepted as an authoritative INPUT to anything in this module: every
function that consumes a digest recomputes or independently verifies it
against a source it trusts (a locally re-parsed manifest/lock pair, or a
value already bound to verified GitHub run metadata) — nothing here ever
takes "trust me, this is the digest" from an unverified caller.

See `compute_plan_digest` for the exact construction:
`SHA256(b"dotmac.erp-dependency-plan.v1\\0" + canonical_json_bytes)`.

Classification of every dependency form is TOTAL: `_classify_dependency`
returns exactly one of `PublicDependency`, `ForgejoDependency`, or
`ApprovedOffIndexDependency`, or raises `ManifestError` — nothing vanishes
silently. `[tool.poetry.requires-plugins]` is refused outright, for the same
reason `erp_lock.py` refuses it: Poetry loads and imports plugins BEFORE it
resolves anything, which would be arbitrary candidate-controlled code
running in a future credential-bearing producer step.

## Named duplication debt, and its retirement condition

`scripts/erp_lock.py` already owns generic acquisition orchestration
(`fetch`, `curl_argv`), hashing (`sha256_hex`), origin/path-prefix URL
validation (`approved_artifact_url`), and credential scanning
(`credential_encodings`/`credential_sightings`) for the SAME private Forgejo
index this module targets. Moving that acquisition ORCHESTRATION out of
`erp_lock.py` is DELIBERATELY NOT done in this slice: it backs
`.github/workflows/erp-lock.yml`, which PR #563 currently depends on, and
refactoring credential-adjacent orchestration out from under an in-flight PR
is not worth the destabilisation risk here.

What is NOT acceptable is a duplicated PURE function — one with no
credential, no I/O orchestration, just semantics — silently drifting between
the two copies. That already happened once: `dependency_bundle`'s PEP 503
name normaliser stripped a leading/trailing separator and `erp_lock`'s did
not, so the two scripts could disagree about a package's identity. The fix
is `scripts/dependency_normalisation.py`: the ONE owner of that semantics,
imported by both scripts. `sha256_hex`, `approved_artifact_url`, and the
credential-scanning pair remain named, tracked, deliberate ORCHESTRATION
copies — they exist because moving `erp_lock.py`'s CREDENTIALED job shape is
out of scope here, not because nobody decided.

Both are enforced, not just documented:

* `docs/architecture/dependency-bundle-duplication-inventory.json` is the
  canonical, machine-readable list of every accepted duplicated
  ORCHESTRATION helper and its two locations.
* `tests/architecture/test_dependency_bundle.py` drives BOTH implementations
  of each LISTED duplicated helper through one shared table of adversarial
  vectors, asserts every listed entry's two symbols still exist, asserts the
  inventory's entry set is a SUBSET of a hardcoded baseline (shrink-only),
  AND separately scans every function defined in either module for a
  near-duplicate in the other that is NOT on the list — an unlisted
  duplicate fails the build, whether it is old or newly introduced.

Retirement condition: the consumer-cutover slice that points
`.github/workflows/erp-lock.yml` and any new bundle-producer/binder workflow
at this module moves `erp_lock.py`'s remaining listed orchestration helpers
onto the shared functions here, deletes its own copies, and removes the
corresponding entries from the duplication inventory. When the inventory is
empty, the non-growing guard stands permanently at zero.

## No registry fallback

Every function in this module that can fail, fails CLOSED: it raises one of
the `DependencyBundleError` subclasses below. None of them contains a path
that, on a verification failure, falls back to fetching from
`registry.dotmac.io` (or anywhere else) instead. A caller that wants the
real bytes after a refusal must go get a new, independently verifiable
bundle — never patch around the refusal from inside this module.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
import urllib.parse
import zipfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dependency_normalisation import (
    normalise_name,
    normalise_name_for_identity,
    normalise_repository_url,
)

# ── errors ───────────────────────────────────────────────────────────────


class DependencyBundleError(Exception):
    """Base for every refusal this module makes.

    There is no registry fallback anywhere in this module: every failure
    path in every public function raises a subclass of this, and nothing
    catches one of these and quietly proceeds with a network call instead.
    """


class ManifestError(DependencyBundleError):
    """The dependency surface (`pyproject.toml` + `poetry.lock`) is not in
    the exact recognised shape this module knows how to plan-digest."""


class PolicyError(DependencyBundleError):
    """`.github/dependency-bundle-policy.json` is missing, malformed, or
    genuinely unresolved — see `load_policy`."""


class BundleVerificationError(DependencyBundleError):
    """A bundle, its run metadata, or its archive digest failed
    verification against policy or against a locally recomputed value."""


class ExtractionError(DependencyBundleError):
    """A ZIP archive member is unsafe to extract, or extraction produced
    bytes that disagree with the verified manifest."""


# ── constants ────────────────────────────────────────────────────────────

#: The domain-separated hash construction. Changing this string is a schema
#: break, never a silent behaviour change — bump it deliberately and update
#: every consumer at once.
PLAN_DIGEST_DOMAIN = b"dotmac.erp-dependency-plan.v1\0"

#: The plan document's own schema version, carried inside the hashed JSON so
#: a future incompatible change to the document shape changes every digest
#: rather than colliding with the old scheme.
PLAN_SCHEMA_VERSION = 3

#: The bundle manifest's own schema version (see `create_bundle_manifest`).
MANIFEST_SCHEMA_VERSION = 2

#: `.github/dependency-bundle-policy.json`'s own schema version — checked
#: by `load_policy`, which previously accepted ANY value here.
POLICY_SCHEMA_VERSION = 1

#: The one named Poetry source this repository's manifest declares for its
#: private packages. See `pyproject.toml`'s `[[tool.poetry.source]]`.
FORGEJO_SOURCE_NAME = "forgejo"

#: The two spellings of the one approved private index this repository
#: resolves against. `pyproject.toml`'s `[[tool.poetry.source]]` names the
#: URL WITH a trailing slash; every `[package.source].url` in `poetry.lock`
#: names it WITHOUT one — Poetry normalises the two independently, and this
#: module matches each one exactly where it is read, same as
#: `scripts/erp_lock.py`'s `MANIFEST_INDEX_URL` / `LOCK_INDEX_URL`.
FORGEJO_MANIFEST_URL = "https://registry.dotmac.io/api/packages/dotmac/pypi/simple/"
FORGEJO_LOCK_URL = "https://registry.dotmac.io/api/packages/dotmac/pypi/simple"

#: The host every index-supplied or manifest-supplied URL is checked against
#: before it is trusted to mean "the private index".
FORGEJO_HOST = "registry.dotmac.io"

#: The origin and path prefix an index-supplied artifact link must resolve
#: to — see `approved_artifact_url`. NAMED DUPLICATION (see the module
#: docstring's "named duplication debt" section and
#: `docs/architecture/dependency-bundle-duplication-inventory.json`): these
#: mirror `erp_lock.ARTIFACT_ORIGIN` / `erp_lock.ARTIFACT_PATH_PREFIX`
#: exactly, for the same private index.
ARTIFACT_ORIGIN = f"https://{FORGEJO_HOST}"
ARTIFACT_PATH_PREFIX = "/api/packages/dotmac/pypi/"

#: The read-only Forgejo identity a held credential's basic-auth encoding is
#: built against — see `credential_encodings`. NAMED DUPLICATION: mirrors
#: `erp_lock.INDEX_USERNAME`.
INDEX_USERNAME = "ci-reader"

#: A forgejo-sourced dependency's `version` must be an EXACT PEP 440-shaped
#: version — no operators, no ranges, no wildcards. Poetry itself treats a
#: bare version string as an exact-equality constraint (unlike npm's caret
#: default), so this refuses only what would already be an unreviewable
#: range if Poetry's own parser saw it (`^`, `~`, `>=`, `<`, `*`, a comma of
#: multiple constraints, or a space).
#:
#: NAMED, DELIBERATE DIVERGENCE from `erp_lock._EXACT_VERSION` (finding 9),
#: not a bug to converge: erp_lock's regex allows exactly ONE of a
#: pre-release/post-release/dev-release suffix
#: (`(a|b|rc|\.post|\.dev)[0-9]+`, non-stackable) because it exists to
#: validate only the two specific, already-known `ALLOWED_MOVEMENTS`
#: version strings in a closed, reviewed workflow. This module must
#: recognise the full space of exact PEP 440 versions for ANY future
#: forgejo-sourced pin, including a real PEP 440 spelling erp_lock's
#: narrower regex refuses: a version stacking more than one suffix, e.g.
#: `1.0a1.post1`. Three independent optional groups (pre-release, then
#: post-release, then dev-release) is the CORRECT PEP 440 shape here; a
#: single non-stackable alternation would silently refuse a legitimate
#: future pin. See `test_the_version_regex_divergence_is_named_not_a_bug`
#: for the planted proof.
_EXACT_VERSION = re.compile(
    r"\A[0-9]+(\.[0-9]+)*((a|b|rc)[0-9]+)?(\.post[0-9]+)?(\.dev[0-9]+)?\Z"
)

#: Poetry dependency-spec keys this module recognises on a forgejo-sourced
#: dependency table. Anything else is an unrecognised dependency form and is
#: refused rather than silently ignored.
_FORGEJO_SPEC_KEYS = frozenset(
    {"version", "source", "markers", "extras", "optional", "python"}
)

#: Off-index dependency forms — any of these keys means Poetry would resolve
#: the package from somewhere other than the named index (running arbitrary
#: VCS/build-backend code to do it). Every one of these keys is refused
#: UNLESS the dependency is `dotmac-integration-client` at its exact pinned
#: identity — see `ApprovedOffIndexDependency` and `load_policy`.
_OFF_INDEX_KEYS = ("git", "path", "url", "file")

#: Keys a pinned off-index dependency's manifest spec may carry, and nothing
#: else. `rev`/`branch` are refused by their absence: a branch is a mutable
#: pointer, and a `rev` beside a `tag` would give two answers to which
#: commit.
_OFF_INDEX_PERMITTED_SPEC_KEYS = frozenset({"git", "tag"})

#: Per-member size cap during safe extraction — generous for a wheel/sdist
#: bundle, but bounded, so a crafted "small on disk, huge when read" member
#: cannot exhaust the extraction host. Checked against BOTH the ZIP's
#: declared size and the actual bytes read (the latter is the zip-bomb
#: guard: a lying declared size does not buy more).
MAX_MEMBER_BYTES = 200 * 1024 * 1024

#: Aggregate caps, independent of the per-member cap above: a bundle with
#: many small, individually-legal members can still exhaust the extraction
#: host on count or total size, and a highly-compressed member can pass the
#: per-member declared-size check while unpacking to something absurd
#: relative to what was actually transferred.
MAX_MEMBER_COUNT = 512
MAX_TOTAL_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024
MAX_COMPRESSION_RATIO = 100

#: The CI/runtime target this module's plan documents record. Derived from
#: this repository's own committed configuration, not invented: CI runs on
#: `ubuntu-latest` (`.github/workflows/ci.yml`) and the runtime/dependency
#: image is `python:3.12-slim` (`Dockerfile`) — both x86_64 Debian-based
#: Linux.
TARGET_PLATFORM = "linux_x86_64"

_SHA256_HEX = re.compile(r"\A[0-9a-f]{64}\Z")
_SHA256_PREFIXED = re.compile(r"\Asha256:([0-9a-f]{64})\Z")
_COMMIT_SHA = re.compile(r"\A[0-9a-f]{40}\Z")
_NULL_SHA = "0" * 40


def _is_exact_version(value: Any) -> bool:
    return isinstance(value, str) and bool(_EXACT_VERSION.match(value))


def _mentions_forgejo_host(url: Any) -> bool:
    """Whether `url` names the forgejo host — a PARSED, case-folded
    hostname comparison, never a substring test. A substring test is both
    too loose (`https://evil.example/registry.dotmac.io/x` would match) and
    too strict in the direction that matters here: it is CASE-SENSITIVE, so
    `https://REGISTRY.DOTMAC.IO/...` silently failed to match and was
    treated as public/unrelated instead of private — exactly the "private
    state omitted as public" failure this module exists to refuse.
    `urllib.parse.urlsplit(...).hostname` is already lower-cased per its
    own documented behaviour; `.lower()` here is defensive, not load-
    bearing.
    """

    if not isinstance(url, str):
        return False
    try:
        hostname = urllib.parse.urlsplit(url).hostname
    except ValueError:
        return False
    return hostname is not None and hostname.lower() == FORGEJO_HOST.lower()


def _bare_sha256(value: str) -> str:
    """`poetry.lock` file hashes are written `sha256:<hex>`. Refuses
    anything else rather than guessing at a bare-hex or other-algorithm
    form."""

    match = _SHA256_PREFIXED.match(value)
    if not match:
        raise ManifestError(
            f"lock file hash {value!r} is not in the expected 'sha256:<64-hex>' form"
        )
    return match.group(1)


def _normalise_name_for_manifest(name: str, *, where: str) -> str:
    """`normalise_name`, mapped to this module's own refusal type.

    `dependency_normalisation.normalise_name` raises a plain `ValueError`
    for a name that normalises to something starting or ending with `-` —
    it stays dependency-free rather than importing `ManifestError`. This
    module operates on genuinely adversarial manifest/lock input, so every
    call site converts that `ValueError` into a `ManifestError` naming
    exactly where the invalid name was found.
    """

    try:
        return normalise_name(name)
    except ValueError as exc:
        raise ManifestError(f"{where}: {exc}") from exc


# ── dependency-surface extraction ───────────────────────────────────────


@dataclass(frozen=True)
class ForgejoDependency:
    """One `source = "forgejo"` dependency declaration from `pyproject.toml`."""

    name: str
    normalised_name: str
    group: str
    version: str
    markers: str | None
    extras: tuple[str, ...]
    optional: bool
    python_constraint: str | None
    #: The OWNING GROUP's own `optional` flag (`[tool.poetry.group.<name>]
    #: .optional`) — distinct from this dependency's own `optional` key.
    #: Poetry installs an entire optional group or none of it; flipping the
    #: group's flag changes default install selection for every dependency
    #: in it without touching a single dependency table.
    group_optional: bool


@dataclass(frozen=True)
class OffIndexPin:
    """The full, reviewed identity of one permitted off-index dependency —
    see `docs/architecture/dependency-bundle-trust.md` and
    `.github/dependency-bundle-policy.json`'s
    `permitted_off_index_dependencies`."""

    url: str
    tag: str
    commit: str


@dataclass(frozen=True)
class ApprovedOffIndexDependency:
    """One manifest dependency resolved against the policy's off-index
    allowlist rather than the Forgejo index — included in the plan document
    so an off-index addition can never be silent, and so a change to WHICH
    off-index identity is pinned changes the digest."""

    name: str
    normalised_name: str
    group: str
    url: str
    tag: str
    resolved_commit: str
    #: See `ForgejoDependency.group_optional` — the same owning-group flag,
    #: tracked identically for an off-index pin.
    group_optional: bool


@dataclass(frozen=True)
class OffIndexTransitiveDependency:
    """One lock-only package admitted because it is a PROVEN member of an
    approved off-index root's transitive closure (see
    `_off_index_transitive_closure`) — never itself a manifest declaration.

    Included in the plan document for the identical reason
    `ApprovedOffIndexDependency` is: an admitted entry the digest cannot
    distinguish is a semantic collision the digest exists to prevent — two
    different transitive off-index states (a different resolved commit, a
    different source URL) must never share a digest just because
    admission only checked REACHABILITY and stopped there."""

    name: str
    normalised_name: str
    url: str
    reference: str
    resolved_commit: str


@dataclass(frozen=True)
class PublicDependency:
    """A dependency resolved from the default (public) index. Recorded only
    so classification is provably total; it never enters the plan digest —
    see the module docstring and `docs/architecture/dependency-bundle-trust.md`
    for why a public package's presence/version/hash must not move it."""

    name: str
    group: str


@dataclass(frozen=True)
class LockPackage:
    """One `poetry.lock` `[[package]]` record whose source reference is
    `forgejo` — direct OR transitive; the plan digest covers both, since a
    transitive private dependency is just as much a private input as a
    direct one."""

    name: str
    normalised_name: str
    version: str
    groups: tuple[str, ...]
    optional: bool
    python_versions: str
    #: `[[package]].markers` — the resolver-level marker Poetry attached to
    #: this LOCKED package (distinct from a dependency declaration's own
    #: `markers` key), and `[package.extras]` — the extra-name to
    #: requirement-string-list mapping Poetry recorded for this package.
    #: Both are selection-significant: they can change which requirements
    #: activate without moving any version or hash.
    markers: str | None
    extras: dict[str, list[str]]
    dependencies: dict[str, Any]
    source_type: str
    source_url: str
    source_reference: str
    files: tuple[dict[str, str], ...]


@dataclass(frozen=True)
class DependencySurface:
    """The complete, validated private-dependency surface a plan digest is
    computed from."""

    schema_version: int
    forgejo_source_url: str
    target_python: str
    target_platform: str
    #: `poetry.lock`'s own `[metadata]` table — `lock-version` (the lock
    #: FILE FORMAT version, e.g. "2.1") and `python-versions` (the
    #: resolution-wide Python constraint the whole lock was solved against,
    #: distinct from any one dependency's own constraint). Both are
    #: selection-significant: a different lock format or a different
    #: resolution-wide Python target can change what a fresh resolve would
    #: produce even with every individual pin unchanged.
    lock_format_version: str
    lock_python_versions: str
    dependencies: tuple[ForgejoDependency, ...]
    off_index_dependencies: tuple[ApprovedOffIndexDependency, ...]
    lock_packages: tuple[LockPackage, ...]
    #: Every lock-only package `_classify_and_admit_lock_entries` admits
    #: as a PROVEN transitive member of an approved off-index root's
    #: closure — see `OffIndexTransitiveDependency`. Defaults to `()` so
    #: every existing direct `DependencySurface(...)` construction (tests,
    #: and any surface with no off-index dependency at all) is unaffected;
    #: `build_plan_document` still includes this field's key unconditionally,
    #: an empty list included, so its PRESENCE in the hashed document never
    #: depends on whether it happens to be empty.
    off_index_transitive_dependencies: tuple[OffIndexTransitiveDependency, ...] = ()


def _load_toml(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ManifestError(f"cannot read {path}: {exc}") from exc
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ManifestError(f"cannot parse {path} as TOML: {exc}") from exc


def _refuse_unknown_dependency_surfaces(manifest: dict[str, Any]) -> None:
    poetry = manifest.get("tool", {}).get("poetry")
    if not isinstance(poetry, dict):
        raise ManifestError("manifest has no [tool.poetry] table")
    if "dev-dependencies" in poetry:
        raise ManifestError(
            "legacy [tool.poetry.dev-dependencies] is refused; this module "
            "only recognises [tool.poetry.group.<name>.dependencies]"
        )
    if "requires-plugins" in poetry:
        raise ManifestError(
            "[tool.poetry.requires-plugins] is refused; Poetry loads and "
            "imports plugins BEFORE it resolves anything, which is arbitrary "
            "candidate-controlled code execution in a future credential-"
            "bearing producer step (see erp_lock.py's identical refusal)"
        )
    project = manifest.get("project", {})
    if isinstance(project, dict) and (
        "dependencies" in project or "optional-dependencies" in project
    ):
        raise ManifestError(
            "PEP 621 [project.dependencies]/[project.optional-dependencies] "
            "are refused; this manifest is Poetry-only and a second "
            "dependency-declaration surface is unrecognised, not merged"
        )
    if "dependency-groups" in manifest:
        raise ManifestError(
            "[dependency-groups] (PEP 735) is refused. erp_lock.py already "
            "traverses this surface (`_pep508_requirement_lists`), because a "
            "plain PEP 508 requirement string there can be a DIRECT "
            "REFERENCE (`name @ https://...`) that bypasses total "
            "classification the same way an off-index Poetry table does — "
            "this module has no PEP 508 requirement-string classifier and "
            "must refuse the surface outright rather than silently ignore "
            "it, until it gains one"
        )


def _dependency_groups(
    poetry: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], dict[str, bool]]:
    """Returns `(groups, group_optional)`. `group_optional` carries each
    group's OWN `[tool.poetry.group.<name>].optional` flag — Poetry
    installs an entire optional group or none of it, so this flag is
    selection-significant independently of any one dependency's own
    `optional` key, and previously was accepted but never read past its
    key-shape check.
    """

    groups: dict[str, dict[str, Any]] = {"main": poetry.get("dependencies", {}) or {}}
    group_optional: dict[str, bool] = {"main": False}
    group_table = poetry.get("group", {}) or {}
    if not isinstance(group_table, dict):
        raise ManifestError("[tool.poetry.group] must be a table")
    for group_name, group_body in group_table.items():
        if not isinstance(group_body, dict) or "dependencies" not in group_body:
            raise ManifestError(
                f"[tool.poetry.group.{group_name}] has no [dependencies] table"
            )
        extra_keys = set(group_body) - {"dependencies", "optional"}
        if extra_keys:
            raise ManifestError(
                f"[tool.poetry.group.{group_name}] declares unrecognised keys "
                f"{sorted(extra_keys)}"
            )
        optional_value = group_body.get("optional", False)
        if not isinstance(optional_value, bool):
            raise ManifestError(
                f"[tool.poetry.group.{group_name}].optional must be a boolean"
            )
        groups[group_name] = group_body["dependencies"] or {}
        group_optional[group_name] = optional_value
    return groups, group_optional


def _classify_forgejo_spec(
    name: str, spec: dict[str, Any], group: str, group_optional: bool
) -> ForgejoDependency:
    unexpected = set(spec) - _FORGEJO_SPEC_KEYS
    if unexpected:
        raise ManifestError(
            f"{group}.{name}: forgejo dependency carries unrecognised keys "
            f"{sorted(unexpected)}"
        )
    version = spec.get("version")
    if not _is_exact_version(version):
        raise ManifestError(
            f"{group}.{name}: forgejo dependency must pin an EXACT version "
            f"with no range operator, got {version!r}"
        )
    markers = spec.get("markers")
    if markers is not None and not isinstance(markers, str):
        raise ManifestError(f"{group}.{name}: markers must be a string")
    extras_value = spec.get("extras", [])
    if not isinstance(extras_value, list) or not all(
        isinstance(e, str) for e in extras_value
    ):
        raise ManifestError(f"{group}.{name}: extras must be a list of strings")
    optional_value = spec.get("optional", False)
    if not isinstance(optional_value, bool):
        raise ManifestError(f"{group}.{name}: optional must be a boolean")
    python_constraint = spec.get("python")
    if python_constraint is not None and not isinstance(python_constraint, str):
        raise ManifestError(f"{group}.{name}: python must be a string")
    return ForgejoDependency(
        name=name,
        normalised_name=_normalise_name_for_manifest(name, where=f"{group}.{name}"),
        group=group,
        version=str(version),
        markers=markers,
        extras=tuple(sorted(extras_value)),
        optional=optional_value,
        python_constraint=python_constraint,
        group_optional=group_optional,
    )


def _classify_off_index_spec(
    name: str,
    spec: Any,
    group: str,
    group_optional: bool,
    permitted_off_index: Mapping[str, OffIndexPin],
) -> ApprovedOffIndexDependency:
    """A pinned off-index dependency declares EXACTLY its pinned identity —
    mirrors `erp_lock.off_index_pin_problems`'s exact predicate, including
    its explicit non-dict guard (a crash on a malformed spec is not a
    refusal) and its use of the one shared `normalise_repository_url`
    (see `dependency_normalisation`'s docstring for the divergence this
    fixes: this function used to compare `spec["git"]` to `pin.url` RAW).
    """

    if not isinstance(spec, dict):
        raise ManifestError(
            f"{group}.{name}: a pinned off-index dependency must be a table "
            f"carrying `git` and `tag`, got a {type(spec).__name__}"
        )
    pin = permitted_off_index.get(name)
    if pin is None:
        present_keys = sorted(set(spec) & set(_OFF_INDEX_KEYS))
        raise ManifestError(
            f"{group}.{name}: off-index dependency form ({', '.join(present_keys)}) "
            "is not permitted; resolving it would read or execute something "
            "the index does not name, and this name is not on the policy's "
            "off-index allowlist"
        )
    unexpected = set(spec) - _OFF_INDEX_PERMITTED_SPEC_KEYS
    if unexpected:
        raise ManifestError(
            f"{group}.{name}: pinned off-index dependency carries "
            f"unrecognised keys {sorted(unexpected)}; only "
            f"{sorted(_OFF_INDEX_PERMITTED_SPEC_KEYS)} are permitted"
        )
    missing = _OFF_INDEX_PERMITTED_SPEC_KEYS - set(spec)
    if missing:
        raise ManifestError(
            f"{group}.{name}: pinned off-index dependency is missing {sorted(missing)}"
        )
    declared_url = normalise_repository_url(str(spec["git"]))
    expected_url = normalise_repository_url(pin.url)
    if declared_url != expected_url:
        raise ManifestError(
            f"{group}.{name}: names repository {declared_url!r} (from "
            f"{spec['git']!r}), not the pinned {expected_url!r}"
        )
    if spec["tag"] != pin.tag:
        raise ManifestError(
            f"{group}.{name}: names tag {spec['tag']!r}, not the exact "
            f"pinned {pin.tag!r}"
        )
    return ApprovedOffIndexDependency(
        name=name,
        normalised_name=_normalise_name_for_manifest(name, where=f"{group}.{name}"),
        group=group,
        url=pin.url,
        tag=pin.tag,
        resolved_commit=pin.commit,
        group_optional=group_optional,
    )


def _classify_dependency(
    name: str,
    spec: Any,
    group: str,
    group_optional: bool,
    permitted_off_index: Mapping[str, OffIndexPin],
) -> ForgejoDependency | ApprovedOffIndexDependency | PublicDependency:
    """Classify ONE dependency declaration into exactly one of three
    outcomes — public, approved Forgejo, or approved off-index — or raise
    `ManifestError`. Nothing is ever silently dropped: every code path below
    either returns one of the three dataclasses or raises.
    """

    if name == "python":
        return PublicDependency(name=name, group=group)

    if isinstance(spec, str):
        if "://" in spec or spec.strip().startswith("@"):
            raise ManifestError(
                f"{group}.{name}: the plain constraint {spec!r} is a direct "
                "reference, which reaches outside the index exactly as a "
                "url/file/path/git table does"
            )
        return PublicDependency(name=name, group=group)

    if isinstance(spec, list):
        for index, item in enumerate(spec):
            classified = _classify_dependency(
                f"{name}[{index}]", item, group, group_optional, permitted_off_index
            )
            if not isinstance(classified, PublicDependency):
                raise ManifestError(
                    f"{group}.{name}[{index}]: a multiple-constraint "
                    "dependency list naming a private or off-index form is "
                    "an unrecognised dependency shape; this module refuses "
                    "rather than guessing which branch is authoritative"
                )
        return PublicDependency(name=name, group=group)

    if not isinstance(spec, dict):
        raise ManifestError(
            f"{group}.{name}: dependency spec must be a string, table, or "
            f"list, got {type(spec).__name__}"
        )

    source = spec.get("source")
    url_value = spec.get("url")

    if source is None and _mentions_forgejo_host(url_value):
        raise ManifestError(
            f"{group}.{name}: direct registry URL {url_value!r} bypasses "
            "the declared 'forgejo' source; a private package must be "
            "resolved through the named source, never a direct URL"
        )

    off_index_keys_present = [key for key in _OFF_INDEX_KEYS if key in spec]
    if source is None and off_index_keys_present:
        return _classify_off_index_spec(
            name, spec, group, group_optional, permitted_off_index
        )

    if source is None:
        return PublicDependency(name=name, group=group)

    if source != FORGEJO_SOURCE_NAME:
        raise ManifestError(
            f"{group}.{name}: unrecognised dependency source {source!r}; "
            f"only {FORGEJO_SOURCE_NAME!r} is a known private source"
        )

    return _classify_forgejo_spec(name, spec, group, group_optional)


def _forgejo_source_url(poetry: dict[str, Any]) -> str:
    sources = poetry.get("source", [])
    if not isinstance(sources, list):
        raise ManifestError("[tool.poetry.source] must be an array of tables")
    forgejo_sources = [
        s
        for s in sources
        if isinstance(s, dict) and s.get("name") == FORGEJO_SOURCE_NAME
    ]
    if len(forgejo_sources) != 1:
        raise ManifestError(
            "expected exactly one [[tool.poetry.source]] named "
            f"{FORGEJO_SOURCE_NAME!r}, found {len(forgejo_sources)}"
        )
    entry = forgejo_sources[0]
    url = entry.get("url")
    if not isinstance(url, str):
        raise ManifestError("the forgejo source declares no url")
    # NAMED CONVERGENCE (finding 9): this used to accept the url with OR
    # without a trailing slash (`url.rstrip("/")` before comparing).
    # `erp_lock.manifest_problems` requires the EXACT spelling
    # (`url != MANIFEST_INDEX_URL`, no stripping) for the identical check —
    # a manifest source url missing its trailing slash would pass here and
    # fail there. erp_lock's stricter, already-reviewed behaviour wins, for
    # the same reason established for `normalise_repository_url`: refusing
    # an unrecognised spelling is the correct property, not guessing that a
    # near-miss spelling means the same thing.
    if url != FORGEJO_MANIFEST_URL:
        raise ManifestError(
            f"the forgejo source url {url!r} is not the approved index "
            f"{FORGEJO_MANIFEST_URL!r} — an alternate spelling, including a "
            "missing or different trailing slash, is refused rather than "
            "guessed at"
        )
    normalised = url.rstrip("/")
    if entry.get("priority") != "explicit":
        raise ManifestError("the forgejo source must declare priority = 'explicit'")
    # NAMED, DELIBERATE DIVERGENCE (finding 9), not a bug to converge:
    # `erp_lock.manifest_problems` refuses ANY second `[[tool.poetry.source]]`
    # entry at all, because it is validating a manifest for a LIVE,
    # credentialed Poetry resolution — an unrelated extra index could still
    # change what that resolution does. This module never runs Poetry and
    # holds no credential; it only needs to know whether a SECOND source
    # could be mistaken for the private one, so it refuses only a second
    # source that also names the forgejo host under a different name. A
    # manifest with a second, genuinely unrelated public source is refused
    # by `erp_lock.manifest_problems` and accepted here — see
    # `test_an_unrelated_second_source_is_a_named_divergence_not_a_bug` for
    # the planted proof this is intentional, not an oversight.
    for other in sources:
        if not isinstance(other, dict) or other is entry:
            continue
        if other.get("name") != FORGEJO_SOURCE_NAME and _mentions_forgejo_host(
            other.get("url")
        ):
            raise ManifestError(
                f"source {other.get('name')!r} also names the forgejo host "
                "under a different source name; that is a second, "
                "unreviewed route to the same private index"
            )
    return normalised


def _lock_packages(lock: dict[str, Any]) -> list[LockPackage]:
    """Every `poetry.lock` `[[package]]` entry whose source is forgejo.

    KNOWN, TRACKED, NOT-CONVERGED DIVERGENCE (finding 9) from
    `erp_lock.acquisition_plan`'s lock-side loop: that function includes a
    lock package in its plan by checking ONLY
    `source.get("reference") == INDEX_SOURCE_NAME` — it never checks
    `source.get("type")` or `source.get("url")`. A lock entry with
    `reference = "forgejo"` but a WRONG `type` or `url` would be silently
    trusted there. This function is stricter: it requires `type`,
    `reference`, AND `url` to all agree with the canonical forgejo shape,
    refusing a mismatched combination outright (see the `ManifestError`
    below). The stricter behaviour is the CORRECT one — keying on one field
    alone is exactly the spoofable shortcut this module exists to refuse
    elsewhere. This is NOT converged onto `erp_lock.py` in this slice: doing
    so means tightening `acquisition_plan`, which is credentialed
    acquisition-workflow logic this branch's bounds keep out of scope
    (see the module docstring's "named duplication debt" section). Tracked
    here, and proven with a planted vector, in
    `test_lock_packages_is_stricter_than_erp_locks_acquisition_plan` —
    treat tightening `erp_lock.acquisition_plan` to match as a decided,
    separate, authorised change, not something to do quietly inside a
    "converge the duplicate" pass.
    """

    packages_raw = lock.get("package", [])
    if not isinstance(packages_raw, list):
        raise ManifestError("poetry.lock [[package]] must be an array")
    packages: list[LockPackage] = []
    for pkg in packages_raw:
        if not isinstance(pkg, dict):
            raise ManifestError(
                f"poetry.lock [[package]] entry must be a table, got a "
                f"{type(pkg).__name__}"
            )
        # Validate the name FIRST, for EVERY package, before the
        # looks_private/continue branch below -- not only for entries that
        # already look forgejo-private. A missing, null, non-string, empty,
        # or charset-invalid name must raise HERE, before any digest is
        # produced from this lock, because an entry that does not "look
        # private" under this function's own narrow test can still be the
        # exact entry `_verify_off_index_lock_entry` searches the RAW lock
        # for by name — a fail-open `isinstance` filter there would
        # otherwise silently exclude a malformed-name entry from that
        # search rather than refusing it, which is precisely the "answers
        # without being able to refuse" defect this module exists to
        # prevent. `_normalise_name_for_manifest` covers charset and
        # edge-separator validity; the emptiness/type check here covers
        # what that function's own `not name` guard already refuses, named
        # explicitly so the reader does not have to trust that a later
        # function catches it.
        raw_name = pkg.get("name")
        if not isinstance(raw_name, str) or not raw_name:
            raise ManifestError(
                "poetry.lock [[package]] entry has a missing, null, empty, "
                f"or non-string name: {pkg!r}"
            )
        _normalise_name_for_manifest(
            raw_name, where=f"poetry.lock package {raw_name!r}"
        )
        source = pkg.get("source")
        if not isinstance(source, dict):
            source = {}
        looks_private = source.get("reference") == FORGEJO_SOURCE_NAME or (
            _mentions_forgejo_host(source.get("url"))
        )
        if not looks_private:
            continue
        if (
            source.get("type") != "legacy"
            or source.get("reference") != FORGEJO_SOURCE_NAME
            or source.get("url") != FORGEJO_LOCK_URL
        ):
            raise ManifestError(
                f"lock package {pkg.get('name')!r} has a malformed forgejo "
                f"source reference: {source!r}"
            )
        files_raw = pkg.get("files", [])
        if not isinstance(files_raw, list):
            raise ManifestError(
                f"lock package {pkg.get('name')!r} has a non-list files entry"
            )
        files: list[dict[str, str]] = []
        for f in files_raw:
            if not isinstance(f, dict) or "file" not in f or "hash" not in f:
                raise ManifestError(
                    f"lock package {pkg.get('name')!r} has a malformed file "
                    f"entry: {f!r}"
                )
            files.append({"file": str(f["file"]), "hash": str(f["hash"])})
        name = pkg.get("name")
        version = pkg.get("version")
        if not isinstance(name, str) or not isinstance(version, str):
            raise ManifestError(f"lock package entry missing name/version: {pkg!r}")
        groups_raw = pkg.get("groups", [])
        optional_value = pkg.get("optional", False)
        if not isinstance(optional_value, bool):
            raise ManifestError(f"lock package {name!r} has a non-boolean optional")
        python_versions = pkg.get("python-versions", "")
        if not isinstance(python_versions, str):
            raise ManifestError(
                f"lock package {name!r} has a non-string python-versions"
            )
        markers = pkg.get("markers")
        if markers is not None and not isinstance(markers, str):
            raise ManifestError(f"lock package {name!r} has a non-string markers")
        extras_raw = pkg.get("extras", {})
        if not isinstance(extras_raw, dict):
            raise ManifestError(f"lock package {name!r} has a non-table extras")
        extras: dict[str, list[str]] = {}
        for extra_name, requirement_list in extras_raw.items():
            if not isinstance(requirement_list, list) or not all(
                isinstance(r, str) for r in requirement_list
            ):
                raise ManifestError(
                    f"lock package {name!r} extra {extra_name!r} must be a "
                    "list of requirement strings"
                )
            extras[str(extra_name)] = list(requirement_list)
        if not isinstance(groups_raw, list):
            raise ManifestError(f"lock package {name!r} has a non-list groups")
        dependencies_raw = pkg.get("dependencies", {})
        if not isinstance(dependencies_raw, dict):
            raise ManifestError(f"lock package {name!r} has a non-table dependencies")
        packages.append(
            LockPackage(
                name=name,
                normalised_name=_normalise_name_for_manifest(
                    name, where=f"lock package {name!r}"
                ),
                version=version,
                groups=tuple(sorted(str(g) for g in groups_raw)),
                optional=optional_value,
                python_versions=python_versions,
                markers=markers,
                extras=extras,
                dependencies=dict(dependencies_raw),
                source_type=str(source["type"]),
                source_url=str(source["url"]),
                source_reference=str(source["reference"]),
                files=tuple(sorted(files, key=lambda d: d["file"])),
            )
        )
    return packages


def _verify_off_index_lock_entry(
    dep: ApprovedOffIndexDependency, lock: dict[str, Any]
) -> None:
    """The manifest names a TAG; the lock records what that tag RESOLVED to.
    Requiring `reference` to equal the pinned tag AND `resolved_reference`
    to equal the pinned commit is what makes manifest, policy, and lock
    agree — a tag moved upstream between review and lock generation is
    refused rather than silently adopted."""

    packages_raw = lock.get("package", [])
    # NAMED CONVERGENCE: this used to match the lock package name by RAW
    # equality against `dep.name`. `erp_lock.off_index_lock_problems`
    # PEP-503-normalises both sides of the identical comparison, so
    # `Dotmac_Integration_Client` in the lock would pass there and fail
    # here. Converged onto `normalise_name_for_identity` — the TOTAL,
    # never-raising form (see `dependency_normalisation`'s "Two forms, for
    # two genuinely different contracts"), because this IS an identity
    # comparison against a lock-file name that may be malformed: a name
    # that does not cleanly normalise must become a non-match, not a
    # raised `ManifestError` from this specific comparison — the surrounding
    # `len(matches) != 1` check already turns "no match" into the correct
    # refusal.
    dep_identity = normalise_name_for_identity(dep.name)
    # `isinstance(p.get("name"), str)` here is REDUNDANT-BY-CONSTRUCTION,
    # not a real gate: every entry in `packages_raw` has already passed
    # through `_lock_packages`, which raises `ManifestError` for a missing,
    # null, non-string, empty, OR charset-invalid `name` on EVERY package
    # in this exact `lock` dict, before `extract_dependency_surface` ever
    # calls this function (see `_lock_packages`'s name check, and
    # `extract_dependency_surface`, which calls `_lock_packages(lock)`
    # strictly before `_verify_off_index_lock_entry(off_index_dep, lock)`
    # on the same `lock`). This filter therefore can never actually drop a
    # malformed entry today. It is kept only as defense-in-depth, and it
    # MUST stay documented as fail-open-shaped: if that ordering is ever
    # broken — `_verify_off_index_lock_entry` called on a `lock` that did
    # not first pass through `_lock_packages` — this `isinstance` check
    # reverts to being the ONLY gate, and it is a FILTER, not a refusal: it
    # would silently drop a malformed-name entry from `matches` rather than
    # raising, which can turn a present-but-malformed lock entry into an
    # indistinguishable "no match" and let the plan digest be computed over
    # an incomplete surface. `test_lock_packages_runs_before_off_index_lock_verification`
    # proves the ordering this redundancy depends on; if that test ever
    # fails, this comment's premise is false and this filter is load-bearing
    # again.
    matches = [
        p
        for p in packages_raw
        if isinstance(p, dict)
        and isinstance(p.get("name"), str)
        and normalise_name_for_identity(p["name"]) == dep_identity
    ]
    if len(matches) != 1:
        raise ManifestError(
            f"approved off-index dependency {dep.name!r} must have exactly "
            f"one poetry.lock entry, found {len(matches)}"
        )
    source = matches[0].get("source")
    if not isinstance(source, dict):
        source = {}
    declared_lock_url = normalise_repository_url(str(source.get("url", "")))
    expected_lock_url = normalise_repository_url(dep.url)
    if (
        source.get("type") != "git"
        or declared_lock_url != expected_lock_url
        or source.get("reference") != dep.tag
        or source.get("resolved_reference") != dep.resolved_commit
    ):
        raise ManifestError(
            f"approved off-index dependency {dep.name!r}'s lock source does "
            f"not match its pinned identity: {source!r}"
        )
    if not _COMMIT_SHA.match(str(source.get("resolved_reference", ""))):
        raise ManifestError(
            f"approved off-index dependency {dep.name!r}'s lock "
            "resolved_reference is not a 40-hex commit"
        )


# ── total classification over EVERY lock entry, including lock-only ones ──
#
# `_lock_packages` above only ever concerned itself with entries that
# "look private" (forgejo reference or forgejo host) — everything else hit
# an unconditional `continue` and was never classified, never refused, and
# never moved the digest. That is a real gap: a candidate lock can add a
# TRANSITIVE `source.type = "git"` package from an arbitrary host, name it
# as another package's dependency edge, and neither the manifest (which
# never declared it) nor `_lock_packages` (which only looks at forgejo
# entries) ever sees it — a later offline resolution could still fetch and
# build it. `_classify_and_admit_lock_entries` closes this: every
# `[[package]]` entry is classified as PUBLIC (no source, or an ordinary
# `legacy` index source that does not name the forgejo host), the proven
# transitive closure of an APPROVED OFF-INDEX ROOT (reachable from an
# `ApprovedOffIndexDependency`'s own lock entry by following
# `[package.dependencies]` edges — never merely "it looks private" or "its
# reference matches"), or REFUSED — each refusal named independently:
# an unsupported source `type` this module has no policy for at all, or a
# `git`-sourced entry that is not part of any approved root's proven
# closure (this single message covers an outright INJECTED VCS dependency,
# a STALE one left behind after its root's dependency edge changed, and an
# otherwise UNREACHABLE one alike — all three are the identical fact from
# this function's point of view: a `git` source with no path back to an
# approved root. Every ADMITTED closure member's own identity (name,
# source URL, resolved commit) also becomes part of the plan digest, via
# `OffIndexTransitiveDependency` — admission alone, with no digest
# sensitivity, would let two different transitive off-index states share
# one digest, which is the same semantic-collision defect the digest
# exists to prevent everywhere else on this surface.


def _lock_entry_source_type(pkg: dict[str, Any]) -> str | None:
    source = pkg.get("source")
    if not isinstance(source, dict) or not source:
        return None
    value = source.get("type")
    return value if isinstance(value, str) else "<non-string-type>"


def _off_index_transitive_closure(
    entries_by_identity: dict[str, dict[str, Any]],
    approved_root_identities: frozenset[str],
) -> frozenset[str]:
    """Every lock-entry identity reachable from an approved off-index
    root's OWN lock entry by following `[package.dependencies]` edges,
    including the roots themselves. A dependency edge that names a
    normalised identity with no matching lock entry at all is simply not
    followed further (that dangling edge is not this function's concern —
    `extract_dependency_surface` already refuses a manifest/lock
    disagreement for anything it directly requires)."""

    closure: set[str] = set()
    frontier: list[str] = list(approved_root_identities)
    while frontier:
        identity = frontier.pop()
        if identity in closure:
            continue
        closure.add(identity)
        entry = entries_by_identity.get(identity)
        if entry is None:
            continue
        raw_dependencies = entry.get("dependencies", {})
        if not isinstance(raw_dependencies, dict):
            continue
        for dep_name in raw_dependencies:
            if not isinstance(dep_name, str) or not dep_name:
                continue
            frontier.append(normalise_name_for_identity(dep_name))
    return frozenset(closure)


def _classify_and_admit_lock_entries(
    lock: dict[str, Any],
    forgejo_lock_identities: frozenset[str],
    off_index_dependencies: tuple[ApprovedOffIndexDependency, ...],
) -> tuple[OffIndexTransitiveDependency, ...]:
    """Refuses every lock entry that is neither a forgejo entry (already
    classified by `_lock_packages`), an approved off-index root (already
    verified by `_verify_off_index_lock_entry`), a member of an approved
    root's proven transitive closure, nor an ordinary public entry.

    Returns every ADMITTED transitive closure member, as
    `OffIndexTransitiveDependency` — the caller folds these into the plan
    digest, exactly as it does an approved root, so that two different
    admitted transitive states can never share one digest."""

    packages_raw = lock.get("package", [])
    if not isinstance(packages_raw, list):
        return ()  # _lock_packages already refused this shape

    entries_by_identity: dict[str, dict[str, Any]] = {}
    for pkg in packages_raw:
        if not isinstance(pkg, dict):
            return ()  # _lock_packages already refused this shape
        raw_name = pkg.get("name")
        if not isinstance(raw_name, str) or not raw_name:
            return ()  # _lock_packages already refused this shape
        entries_by_identity[normalise_name_for_identity(raw_name)] = pkg

    approved_root_identities = frozenset(
        dep.normalised_name for dep in off_index_dependencies
    )
    closure = _off_index_transitive_closure(
        entries_by_identity, approved_root_identities
    )

    admitted: list[OffIndexTransitiveDependency] = []
    for identity, pkg in entries_by_identity.items():
        if identity in forgejo_lock_identities or identity in approved_root_identities:
            continue
        source_type = _lock_entry_source_type(pkg)
        if source_type is None or source_type == "legacy":
            source = pkg.get("source")
            url = source.get("url") if isinstance(source, dict) else None
            if not _mentions_forgejo_host(url):
                continue  # an ordinary public entry
        if source_type == "git":
            if identity in closure:
                source = pkg.get("source")
                source = source if isinstance(source, dict) else {}
                admitted.append(
                    OffIndexTransitiveDependency(
                        name=str(pkg.get("name")),
                        normalised_name=identity,
                        url=str(source.get("url", "")),
                        reference=str(source.get("reference", "")),
                        resolved_commit=str(source.get("resolved_reference", "")),
                    )
                )
                continue
            raise ManifestError(
                f"lock package {pkg.get('name')!r} has a git source but is "
                "not part of the proven transitive closure of any approved "
                "off-index dependency; refusing an injected, stale, or "
                "otherwise unreachable VCS lock entry"
            )
        raise ManifestError(
            f"lock package {pkg.get('name')!r} has an unsupported lock "
            f"source type {source_type!r}; this module classifies every "
            "lock entry as public, forgejo, or an approved off-index "
            "dependency's proven closure member, and refuses anything it "
            "cannot place in one of those, rather than silently ignoring it"
        )
    return tuple(admitted)


def load_permitted_off_index_dependencies(
    policy: dict[str, Any],
) -> dict[str, OffIndexPin]:
    """Parse policy's `permitted_off_index_dependencies` into `OffIndexPin`s.

    This is what makes the policy's off-index allowlist NON-inert: before
    this function existed and was wired into `extract_dependency_surface`,
    the policy file's allowlist was reviewed prose that nothing read.
    """

    raw = policy.get("permitted_off_index_dependencies", {})
    if not isinstance(raw, dict):
        raise PolicyError("policy permitted_off_index_dependencies must be a table")
    result: dict[str, OffIndexPin] = {}
    for name, entry in raw.items():
        if not isinstance(entry, dict):
            raise PolicyError(
                f"policy permitted_off_index_dependencies.{name} must be a table"
            )
        missing = {"url", "tag", "commit"} - set(entry)
        if missing:
            raise PolicyError(
                f"policy permitted_off_index_dependencies.{name} is missing "
                f"{sorted(missing)}"
            )
        commit = entry["commit"]
        if not isinstance(commit, str) or not _COMMIT_SHA.match(commit):
            raise PolicyError(
                f"policy permitted_off_index_dependencies.{name}.commit must "
                "be a 40-hex commit SHA"
            )
        url = entry["url"]
        if not isinstance(url, str) or not url:
            raise PolicyError(
                f"policy permitted_off_index_dependencies.{name}.url must "
                "be a non-empty string"
            )
        tag = entry["tag"]
        if not isinstance(tag, str) or not tag:
            raise PolicyError(
                f"policy permitted_off_index_dependencies.{name}.tag must "
                "be a non-empty string"
            )
        result[name] = OffIndexPin(url=url, tag=tag, commit=commit)
    return result


def extract_dependency_surface(
    project_root: Path, permitted_off_index: Mapping[str, OffIndexPin] | None = None
) -> DependencySurface:
    """Parse, validate, and extract the private dependency surface from
    `project_root`'s CURRENT WORKING-TREE `pyproject.toml` + `poetry.lock`.

    `permitted_off_index` is the policy's off-index allowlist (see
    `load_permitted_off_index_dependencies`) — defaults to empty, meaning
    every off-index dependency form is refused; a caller that wants ERP's
    real, pinned `dotmac-integration-client` exemption must pass it in
    explicitly, from a loaded policy, never invent it.

    Raises `ManifestError` for every unrecognised or disagreeing shape this
    module's docstring and `docs/architecture/dependency-bundle-trust.md`
    enumerate. Never returns a partial surface on a refusal.

    **This reads the WORKING TREE, not a pinned commit.** It exists for
    local/CLI use (`_cmd_plan_digest`) and for tests that construct a bare
    `tmp_path` project with no git repository at all. Any caller that needs
    to bind a plan digest to a specific candidate commit identity — i.e.
    `bind_bundle_to_candidate` and anything upstream of it — MUST use
    `extract_dependency_surface_at_commit` instead: see that function's
    docstring, and this module's "reading dependency bytes from a PINNED
    COMMIT" section, for why a working-tree read beside an independently
    read commit SHA is not a binding.
    """

    permitted_off_index = permitted_off_index or {}
    poetry_toml_present = (project_root / "poetry.toml").exists()
    manifest = _load_toml(project_root / "pyproject.toml")
    lock = _load_toml(project_root / "poetry.lock")
    return _build_dependency_surface(
        manifest, lock, poetry_toml_present, permitted_off_index
    )


def extract_dependency_surface_at_commit(
    repo_root: Path,
    commit_sha: str,
    permitted_off_index: Mapping[str, OffIndexPin] | None = None,
) -> DependencySurface:
    """`extract_dependency_surface`, but every byte comes from `commit_sha`'s
    own git tree — via `_read_git_blob_at_commit`/`_git_path_exists_at_commit`
    — never `repo_root`'s current working-tree files.

    This is the ONLY correct way to compute a plan digest that will be
    bound to `commit_sha` as a candidate identity: the digest and the SHA
    must describe the SAME object, derived in that order (resolve the
    commit once, read its blobs, compute the plan, then bind the identity
    to that same commit) — not two independent observations of
    `repo_root` that happen to be taken close together in time and are
    hoped to agree. See `compute_candidate_plan_digest_at_commit` and
    `bind_bundle_to_candidate`, the two callers that must use this.
    """

    permitted_off_index = permitted_off_index or {}
    poetry_toml_present = _git_path_exists_at_commit(
        repo_root, commit_sha, "poetry.toml"
    )
    manifest_bytes = _read_git_blob_at_commit(repo_root, commit_sha, "pyproject.toml")
    lock_bytes = _read_git_blob_at_commit(repo_root, commit_sha, "poetry.lock")
    try:
        manifest = tomllib.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ManifestError(
            f"cannot parse pyproject.toml as TOML at commit {commit_sha}: {exc}"
        ) from exc
    try:
        lock = tomllib.loads(lock_bytes.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ManifestError(
            f"cannot parse poetry.lock as TOML at commit {commit_sha}: {exc}"
        ) from exc
    return _build_dependency_surface(
        manifest, lock, poetry_toml_present, permitted_off_index
    )


def _build_dependency_surface(
    manifest: dict[str, Any],
    lock: dict[str, Any],
    poetry_toml_present: bool,
    permitted_off_index: Mapping[str, OffIndexPin],
) -> DependencySurface:
    """The parse-independent core of `extract_dependency_surface`: given
    already-parsed `manifest`/`lock` documents (from wherever their bytes
    came from — working tree or a pinned commit) and whether a
    `poetry.toml` is present alongside them, validate and extract the
    private dependency surface. Neither this function nor anything it
    calls touches the filesystem or git."""

    if poetry_toml_present:
        raise ManifestError(
            "a project-local poetry.toml is present; it can reconfigure TLS "
            "verification and credential lookup for the very resolution "
            "this module is trying to plan-digest, and is refused outright"
        )
    _refuse_unknown_dependency_surfaces(manifest)
    poetry = manifest["tool"]["poetry"]
    forgejo_url = _forgejo_source_url(poetry)
    groups, group_optional = _dependency_groups(poetry)

    dependencies: list[ForgejoDependency] = []
    off_index_dependencies: list[ApprovedOffIndexDependency] = []
    seen_by_normalised_name: dict[str, str] = {}
    for group_name, table in groups.items():
        if not isinstance(table, dict):
            raise ManifestError(f"[{group_name}].dependencies must be a table")
        for dep_name, spec in table.items():
            classified = _classify_dependency(
                dep_name,
                spec,
                group_name,
                group_optional[group_name],
                permitted_off_index,
            )
            if isinstance(classified, PublicDependency):
                continue
            identity_name = classified.normalised_name
            prior = seen_by_normalised_name.get(identity_name)
            if prior is not None:
                raise ManifestError(
                    f"dependency {identity_name!r} is declared more than "
                    f"once ({prior!r} and {dep_name!r} in group "
                    f"{group_name!r}); a duplicate declaration is refused, "
                    "not merged"
                )
            seen_by_normalised_name[identity_name] = dep_name
            if isinstance(classified, ForgejoDependency):
                dependencies.append(classified)
            else:
                off_index_dependencies.append(classified)

    lock_packages = _lock_packages(lock)
    lock_by_name = {p.normalised_name: p for p in lock_packages}
    for dep in dependencies:
        lock_pkg = lock_by_name.get(dep.normalised_name)
        if lock_pkg is None:
            raise ManifestError(
                f"forgejo dependency {dep.name!r} has no corresponding "
                "poetry.lock entry; manifest and lock disagree"
            )
        if lock_pkg.version != dep.version:
            raise ManifestError(
                f"forgejo dependency {dep.name!r} pins {dep.version!r} but "
                f"poetry.lock resolved {lock_pkg.version!r}; manifest and "
                "lock disagree"
            )

    for off_index_dep in off_index_dependencies:
        _verify_off_index_lock_entry(off_index_dep, lock)

    off_index_transitive_dependencies = _classify_and_admit_lock_entries(
        lock, frozenset(lock_by_name), tuple(off_index_dependencies)
    )

    target_python = poetry.get("dependencies", {}).get("python")
    if not isinstance(target_python, str) or not target_python:
        raise ManifestError("[tool.poetry.dependencies].python must be a string")

    lock_metadata = lock.get("metadata")
    if not isinstance(lock_metadata, dict):
        raise ManifestError("poetry.lock has no [metadata] table")
    lock_format_version = lock_metadata.get("lock-version")
    if not isinstance(lock_format_version, str) or not lock_format_version:
        raise ManifestError("poetry.lock [metadata].lock-version must be a string")
    lock_python_versions = lock_metadata.get("python-versions")
    if not isinstance(lock_python_versions, str) or not lock_python_versions:
        raise ManifestError("poetry.lock [metadata].python-versions must be a string")

    return DependencySurface(
        schema_version=PLAN_SCHEMA_VERSION,
        forgejo_source_url=forgejo_url,
        target_python=target_python,
        target_platform=TARGET_PLATFORM,
        lock_format_version=lock_format_version,
        lock_python_versions=lock_python_versions,
        dependencies=tuple(dependencies),
        off_index_dependencies=tuple(off_index_dependencies),
        lock_packages=tuple(lock_packages),
        off_index_transitive_dependencies=off_index_transitive_dependencies,
    )


# ── plan digest ──────────────────────────────────────────────────────────


def canonical_json_bytes(document: Any) -> bytes:
    """Canonical UTF-8 JSON: sorted keys, compact separators, trailing
    newline. Comments, TOML whitespace, and key order never survive into
    this — they are gone by the time a dataclass exists."""

    return (
        json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        + "\n"
    ).encode("utf-8")


def build_plan_document(surface: DependencySurface) -> dict[str, Any]:
    """The exact structure `compute_plan_digest` hashes. Exposed separately
    so a caller — and a test — can inspect what changed without re-deriving
    the hash construction."""

    return {
        "schema": surface.schema_version,
        "source": {"name": FORGEJO_SOURCE_NAME, "url": surface.forgejo_source_url},
        "target": {
            "python": surface.target_python,
            "platform": surface.target_platform,
        },
        "lock_metadata": {
            "lock_format_version": surface.lock_format_version,
            "python_versions": surface.lock_python_versions,
        },
        "dependencies": [
            {
                "name": dep.normalised_name,
                "group": dep.group,
                "group_optional": dep.group_optional,
                "version": dep.version,
                "markers": dep.markers,
                "extras": list(dep.extras),
                "optional": dep.optional,
                "python": dep.python_constraint,
            }
            for dep in sorted(
                surface.dependencies, key=lambda d: (d.group, d.normalised_name)
            )
        ],
        "off_index": [
            {
                "name": dep.normalised_name,
                "group": dep.group,
                "group_optional": dep.group_optional,
                "url": dep.url,
                "tag": dep.tag,
                "resolved_commit": dep.resolved_commit,
            }
            for dep in sorted(
                surface.off_index_dependencies,
                key=lambda d: (d.group, d.normalised_name),
            )
        ],
        "off_index_transitive": [
            {
                "name": dep.normalised_name,
                "url": dep.url,
                "reference": dep.reference,
                "resolved_commit": dep.resolved_commit,
            }
            for dep in sorted(
                surface.off_index_transitive_dependencies,
                key=lambda d: d.normalised_name,
            )
        ],
        "lock_packages": [
            {
                "name": pkg.normalised_name,
                "version": pkg.version,
                "groups": list(pkg.groups),
                "optional": pkg.optional,
                "python_versions": pkg.python_versions,
                "markers": pkg.markers,
                "extras": {k: list(v) for k, v in sorted(pkg.extras.items())},
                "dependencies": pkg.dependencies,
                "source": {
                    "type": pkg.source_type,
                    "url": pkg.source_url,
                    "reference": pkg.source_reference,
                },
                "files": [dict(f) for f in pkg.files],
            }
            for pkg in sorted(surface.lock_packages, key=lambda p: p.normalised_name)
        ],
    }


def compute_plan_digest(surface: DependencySurface) -> str:
    """`SHA256(b"dotmac.erp-dependency-plan.v1\\0" + canonical_json_bytes)`.

    Never accept a caller-supplied digest as authoritative in place of
    calling this — see the module docstring's "plan digest is the trust
    boundary" section.
    """

    payload = PLAN_DIGEST_DOMAIN + canonical_json_bytes(build_plan_document(surface))
    return hashlib.sha256(payload).hexdigest()


def compute_candidate_plan_digest(
    candidate_root: Path, permitted_off_index: Mapping[str, OffIndexPin] | None = None
) -> str:
    """The ONE way to obtain a candidate's own plan digest: parse ITS OWN
    checked-out `pyproject.toml`/`poetry.lock` and hash the result.

    `verify_run_metadata` and `bind_bundle_to_candidate` both call this
    rather than accepting a candidate digest as an independent parameter —
    a bare digest string is a digest a caller chooses, which defeats the
    entire purpose of a binding/verification check whose job is to prove
    the candidate's OWN surface produced it. There is no other way into
    either of those two functions' "this is the candidate's digest" input.
    """

    surface = extract_dependency_surface(candidate_root, permitted_off_index)
    return compute_plan_digest(surface)


def compute_candidate_plan_digest_at_commit(
    repo_root: Path,
    commit_sha: str,
    permitted_off_index: Mapping[str, OffIndexPin] | None = None,
) -> str:
    """`compute_candidate_plan_digest`, but parsed from `commit_sha`'s own
    git tree via `extract_dependency_surface_at_commit`, never `repo_root`'s
    current working-tree files.

    `bind_bundle_to_candidate` MUST call this, not
    `compute_candidate_plan_digest`, and MUST pass it the exact same
    `commit_sha` it binds the candidate identity to — see this module's
    "reading dependency bytes from a PINNED COMMIT" section for why a
    plan digest computed from the working tree cannot be trusted to
    describe the same object as a commit SHA read independently.
    """

    surface = extract_dependency_surface_at_commit(
        repo_root, commit_sha, permitted_off_index
    )
    return compute_plan_digest(surface)


def _read_git_head_sha(repo_root: Path) -> str:
    """Read `repo_root`'s ACTUAL current commit SHA from its own on-disk
    git refs — no subprocess, no network, and no trust in a caller-supplied
    string. `bind_bundle_to_candidate` calls this instead of accepting a
    `candidate_sha` parameter, for the identical reason
    `compute_candidate_plan_digest` replaced a bare digest parameter: a SHA
    a caller supplies is a SHA a caller chooses.

    Handles a plain `.git` directory (attached HEAD via `refs/heads/<name>`
    or a loose ref, detached HEAD as a raw SHA, and a ref resolved through
    `packed-refs` when there is no loose ref file) and the one-hop
    `.git`-as-a-file worktree/submodule pointer form
    (`gitdir: <path>`). Does NOT resolve a worktree's `commondir`
    indirection for a branch ref kept only in the main checkout's
    `packed-refs` — that is a stated, narrower limitation, not a silent
    gap: refuses with a clear message rather than guessing.
    """

    sha = _resolve_git_head_sha(repo_root)
    if sha == _NULL_SHA:
        raise BundleVerificationError(
            f"{repo_root}'s .git HEAD resolves to the all-zero null SHA, "
            "which is never a real commit"
        )
    return sha


def _resolve_git_head_sha(repo_root: Path) -> str:
    git_path = repo_root / ".git"
    if git_path.is_file():
        try:
            pointer = git_path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise BundleVerificationError(f"cannot read {git_path}: {exc}") from exc
        if not pointer.startswith("gitdir:"):
            raise BundleVerificationError(
                f"{git_path} is a file but does not name a gitdir: {pointer!r}"
            )
        target = pointer[len("gitdir:") :].strip()
        target_path = Path(target)
        git_dir = target_path if target_path.is_absolute() else (repo_root / target)
        git_dir = git_dir.resolve()
    elif git_path.is_dir():
        git_dir = git_path
    else:
        raise BundleVerificationError(
            f"{repo_root} is not a git checkout (no .git file or directory)"
        )

    head_path = git_dir / "HEAD"
    try:
        head_text = head_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise BundleVerificationError(f"cannot read {head_path}: {exc}") from exc

    if _COMMIT_SHA.match(head_text):
        return head_text
    if not head_text.startswith("ref:"):
        raise BundleVerificationError(
            f"{head_path} is neither a commit SHA nor a ref pointer: {head_text!r}"
        )
    ref_name = head_text[len("ref:") :].strip()
    if not ref_name:
        raise BundleVerificationError(f"{head_path} names an empty ref")

    ref_path = git_dir / ref_name
    if ref_path.is_file():
        try:
            ref_text = ref_path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise BundleVerificationError(f"cannot read {ref_path}: {exc}") from exc
        if not _COMMIT_SHA.match(ref_text):
            raise BundleVerificationError(
                f"{ref_path} does not contain a 40-hex commit SHA: {ref_text!r}"
            )
        return ref_text

    packed_refs_path = git_dir / "packed-refs"
    if packed_refs_path.is_file():
        try:
            packed_text = packed_refs_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise BundleVerificationError(
                f"cannot read {packed_refs_path}: {exc}"
            ) from exc
        for line in packed_text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or stripped.startswith("^"):
                continue
            parts = stripped.split(" ", 1)
            if (
                len(parts) == 2
                and parts[1].strip() == ref_name
                and _COMMIT_SHA.match(parts[0])
            ):
                return parts[0]

    raise BundleVerificationError(
        f"cannot resolve ref {ref_name!r} to a commit SHA under {git_dir} "
        "(no loose ref file, and no matching entry in packed-refs)"
    )


# ── reading dependency bytes from a PINNED COMMIT, never the working tree ──
#
# A caller-supplied SHA beside working-tree reads is not a binding: the
# original `extract_dependency_surface(candidate_root, ...)` parsed
# `candidate_root`'s CURRENT on-disk `pyproject.toml`/`poetry.lock`, while
# `bind_bundle_to_candidate` independently read `candidate_root`'s CURRENT
# `.git` HEAD via `_read_git_head_sha`. Those are two separate observations
# of `candidate_root`, taken as two separate filesystem operations with no
# atomicity between them -- a dirty checkout, or a concurrent `git checkout`/
# commit landing between the two reads, can make `candidate_sha=A` and
# `plan_digest=B` where commit A never actually contained the bytes that
# produced B. `extract_dependency_surface_at_commit` closes this: the commit
# is resolved ONCE by the caller, and both files are read as that commit's
# own tree entries -- via `git cat-file`, which reads git's OBJECT STORE,
# never the working tree -- so the digest and the SHA describe the same
# object by construction, not by hoping two reads landed together.
#
# This uses `subprocess` (unlike the rest of this module, which reads
# `.git`'s loose refs directly to avoid a subprocess dependency for the
# comparatively simple "what does HEAD point to" question). Reading a
# blob's bytes correctly requires resolving git's object store, which may
# be loose objects OR a packfile with delta-compressed entries -- there is
# no dependency-free, correct-for-every-checkout way to do that without
# either reimplementing zlib-inflate-plus-delta-resolution-plus-packfile-
# indexing by hand (a large, security-sensitive undertaking to get exactly
# right) or shelling out to the `git` binary that already implements it
# correctly. `erp_lock.py` already establishes the precedent of invoking
# `git`/`curl` via `subprocess.run` with a fixed argv list, `shell=False`,
# and no interpolated shell string (see `fetch`, `curl_argv`) -- this
# follows the identical shape: a fixed argv, `cwd=repo_root`, no shell.


def _run_git(repo_root: Path, argv: list[str]) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(  # noqa: S603
            ["git", *argv],
            cwd=repo_root,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise BundleVerificationError(
            f"cannot invoke git {argv!r} in {repo_root}: {exc}"
        ) from exc


def _read_git_blob_at_commit(repo_root: Path, commit_sha: str, path: str) -> bytes:
    """The exact bytes `path` held in `commit_sha`'s tree — via git's own
    object store, never `repo_root`'s current working-tree file of the
    same name. Raises `ManifestError` if `commit_sha` does not have `path`
    at all (the caller decides whether that is a refusal or an "absent" —
    see `_git_path_exists_at_commit` for a caller that wants the latter)."""

    if not _COMMIT_SHA.match(commit_sha):
        raise BundleVerificationError(
            f"{commit_sha!r} is not a 40-hex commit SHA; refusing to read "
            "a blob at an unpinned or malformed commit reference"
        )
    completed = _run_git(repo_root, ["cat-file", "blob", f"{commit_sha}:{path}"])
    if completed.returncode != 0:
        raise ManifestError(
            f"cannot read {path!r} at commit {commit_sha} from {repo_root}'s "
            f"git object store: {completed.stderr.decode('utf-8', 'replace').strip()}"
        )
    return completed.stdout


def _git_path_exists_at_commit(repo_root: Path, commit_sha: str, path: str) -> bool:
    """Whether `commit_sha`'s tree contains `path` — via `git cat-file -e`,
    the tree recorded in the commit OBJECT, never `repo_root`'s current
    working-tree directory listing."""

    if not _COMMIT_SHA.match(commit_sha):
        raise BundleVerificationError(
            f"{commit_sha!r} is not a 40-hex commit SHA; refusing to probe "
            "an unpinned or malformed commit reference"
        )
    completed = _run_git(repo_root, ["cat-file", "-e", f"{commit_sha}:{path}"])
    return completed.returncode == 0


# ── planned artifacts (the plan's own file/digest closure) ────────────────


@dataclass(frozen=True)
class PlannedArtifact:
    """One file the plan says must be acquired from the private index —
    derived from `DependencySurface.lock_packages`, never supplied
    independently."""

    package_normalised_name: str
    filename: str
    sha256: str


def planned_artifacts(surface: DependencySurface) -> tuple[PlannedArtifact, ...]:
    """Every `(filename, sha256)` the plan requires, across every forgejo
    lock package. This is the plan-side half of the closure `
    create_bundle_manifest` enforces against the acquired archive."""

    artifacts: list[PlannedArtifact] = []
    seen: dict[str, PlannedArtifact] = {}
    for pkg in surface.lock_packages:
        for f in pkg.files:
            digest = _bare_sha256(f["hash"])
            artifact = PlannedArtifact(
                package_normalised_name=pkg.normalised_name,
                filename=f["file"],
                sha256=digest,
            )
            prior = seen.get(artifact.filename)
            if prior is not None and prior != artifact:
                raise ManifestError(
                    f"the plan names {artifact.filename!r} twice with "
                    f"disagreeing digests ({prior.sha256} vs {digest}); "
                    "ambiguous plan"
                )
            seen[artifact.filename] = artifact
            artifacts.append(artifact)
    return tuple(sorted(set(artifacts), key=lambda a: a.filename))


# ── policy ───────────────────────────────────────────────────────────────

_REQUIRED_POLICY_KEYS = frozenset(
    {
        "schema_version",
        "repository",
        "forgejo_source",
        "producer_workflow_path",
        "binder_workflow_path",
        "environment_name",
        "target",
        "artifact_name_pattern",
        "artifact_retention_days",
        "permitted_off_index_dependencies",
    }
)

REQUIRED_ENVIRONMENT_NAME = "forgejo-registry-read-main"


def load_policy(path: Path) -> dict[str, Any]:
    """Load and validate `.github/dependency-bundle-policy.json`.

    Refuses to return a policy whose `repository.id` is not a real, positive
    integer immutable GitHub repository ID.
    """

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise PolicyError(f"cannot read policy {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise PolicyError(f"cannot parse policy {path} as JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise PolicyError("policy document must be a JSON object")
    missing = _REQUIRED_POLICY_KEYS - set(data)
    if missing:
        raise PolicyError(f"policy missing required keys: {sorted(missing)}")
    repo = data["repository"]
    if (
        not isinstance(repo, dict)
        or not isinstance(repo.get("full_name"), str)
        or not repo.get("full_name")
    ):
        raise PolicyError("policy repository.full_name must be a non-empty string")
    repo_id = repo.get("id")
    if not isinstance(repo_id, int) or isinstance(repo_id, bool) or repo_id <= 0:
        raise PolicyError(
            "policy repository.id must be a positive integer, immutable "
            "GitHub repository ID. It is currently UNRESOLVED — this "
            "policy file must not be trusted by any consumer until a human "
            "fills in the real numeric ID (see "
            "docs/architecture/dependency-bundle-trust.md)"
        )
    if data.get("environment_name") != REQUIRED_ENVIRONMENT_NAME:
        raise PolicyError(
            f"policy environment_name must be {REQUIRED_ENVIRONMENT_NAME!r}, "
            f"got {data.get('environment_name')!r}"
        )
    forgejo_source = data["forgejo_source"]
    if (
        not isinstance(forgejo_source, dict)
        or forgejo_source.get("name") != FORGEJO_SOURCE_NAME
        or forgejo_source.get("url") != FORGEJO_LOCK_URL
    ):
        raise PolicyError(
            "policy forgejo_source must name "
            f"{{'name': {FORGEJO_SOURCE_NAME!r}, 'url': {FORGEJO_LOCK_URL!r}}}"
        )
    schema_version = data.get("schema_version")
    if (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version != POLICY_SCHEMA_VERSION
    ):
        raise PolicyError(
            f"policy schema_version must be exactly {POLICY_SCHEMA_VERSION}, "
            f"got {schema_version!r}"
        )
    target = data.get("target")
    if (
        not isinstance(target, dict)
        or not isinstance(target.get("python"), str)
        or not target.get("python")
        or target.get("platform") != TARGET_PLATFORM
    ):
        raise PolicyError(
            "policy target must be a table with a non-empty 'python' string "
            f"and 'platform' == {TARGET_PLATFORM!r}"
        )
    retention_days = data.get("artifact_retention_days")
    if (
        not isinstance(retention_days, int)
        or isinstance(retention_days, bool)
        or retention_days <= 0
    ):
        raise PolicyError(
            "policy artifact_retention_days must be a positive integer, got "
            f"{retention_days!r}"
        )
    for path_field in ("producer_workflow_path", "binder_workflow_path"):
        path_value = data.get(path_field)
        if (
            not isinstance(path_value, str)
            or not path_value.startswith(".github/workflows/")
            or not (path_value.endswith(".yml") or path_value.endswith(".yaml"))
        ):
            raise PolicyError(
                f"policy {path_field} must be a string under "
                "'.github/workflows/' ending in '.yml' or '.yaml', got "
                f"{path_value!r}"
            )
    if data["producer_workflow_path"] == data["binder_workflow_path"]:
        raise PolicyError(
            "policy producer_workflow_path and binder_workflow_path must "
            "name two different workflow files"
        )
    artifact_name_pattern = data.get("artifact_name_pattern")
    if not isinstance(artifact_name_pattern, str) or "{plan_digest}" not in (
        artifact_name_pattern
    ):
        raise PolicyError(
            "policy artifact_name_pattern must be a string containing the "
            f"literal '{{plan_digest}}' placeholder, got {artifact_name_pattern!r}"
        )
    # A substring check alone does not prove `.format(plan_digest=...)`
    # actually succeeds: an extra field (`"{plan_digest}{other}"`), a bad
    # conversion/format spec, or unbalanced braces all still contain the
    # literal substring and would previously reach
    # `verify_run_metadata`'s `.format(...)` call as a raw
    # `KeyError`/`ValueError`/`IndexError`. Proving the format call itself
    # succeeds, with only `plan_digest` supplied, is what actually
    # validates the pattern.
    try:
        artifact_name_pattern.format(plan_digest="0" * 64)
    except (KeyError, IndexError, ValueError) as exc:
        raise PolicyError(
            f"policy artifact_name_pattern {artifact_name_pattern!r} is not "
            f"a valid format string taking only 'plan_digest': {exc}"
        ) from exc
    return data


# ── GitHub run/artifact metadata verification (LOCAL ONLY) ────────────────
#
# Everything in this section validates a metadata DICT the caller already
# fetched. It proves internal consistency and agreement with policy; it does
# NOT prove the dict is what GitHub actually says right now — that requires
# calling the GitHub API (or otherwise obtaining a provenance attestation)
# and is explicitly STEP 2, out of scope for this module. Treat a pass here
# as "this metadata, if genuine, describes an acceptable run" — never as
# "this metadata is genuine".

_RUN_METADATA_FIELDS = (
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
)

_POSITIVE_INT_FIELDS = (
    "repository_id",
    "run_id",
    "run_attempt",
    "artifact_id",
    "artifact_run_id",
)


@dataclass(frozen=True)
class RunMetadata:
    """The verified-LOCALLY identity of the GitHub Actions run and artifact
    a bundle claims to come from. See this section's module-level note:
    this is not provenance.

    `__post_init__` re-validates every field's own shape — a positive int
    where an int is required, a real commit SHA, non-empty strings — so a
    HAND-BUILT `RunMetadata` (bypassing `verify_run_metadata` entirely)
    cannot hold an out-of-shape value either. This does not, and cannot,
    prove the values are genuine; it only closes the gap where "any
    hand-built `RunMetadata` is accepted" meant a malformed one could reach
    `create_bundle_manifest` untouched.
    """

    repository_full_name: str
    repository_id: int
    workflow_path: str
    run_id: int
    run_attempt: int
    trusted_workflow_sha: str
    artifact_id: int
    artifact_name: str
    artifact_run_id: int
    environment_name: str

    def __post_init__(self) -> None:
        for field_name in (
            "repository_id",
            "run_id",
            "run_attempt",
            "artifact_id",
            "artifact_run_id",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise BundleVerificationError(
                    f"RunMetadata.{field_name} must be a positive integer, got {value!r}"
                )
        for field_name in (
            "repository_full_name",
            "workflow_path",
            "artifact_name",
            "environment_name",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value:
                raise BundleVerificationError(
                    f"RunMetadata.{field_name} must be a non-empty string"
                )
        if self.trusted_workflow_sha == _NULL_SHA:
            raise BundleVerificationError(
                "RunMetadata.trusted_workflow_sha is the all-zero null SHA"
            )
        if not _COMMIT_SHA.match(self.trusted_workflow_sha):
            raise BundleVerificationError(
                "RunMetadata.trusted_workflow_sha must be a 40-hex commit SHA"
            )


def verify_run_metadata(
    metadata: dict[str, Any],
    policy: dict[str, Any],
    *,
    candidate_root: Path,
    permitted_off_index: Mapping[str, OffIndexPin] | None = None,
) -> RunMetadata:
    """Verify already-fetched GitHub API run/artifact metadata against
    policy — LOCAL validation only, described in this section's module-level
    docstring note. This function makes NO network call itself.

    Requires: the PRODUCER workflow specifically (never the binder path —
    a binder run is a consumer, not a source of a bundle to trust);
    positive run/attempt/artifact/repository coordinates; a non-null commit
    SHA; the artifact's OWN run id matching the run id claimed (so an
    artifact from a different, unrelated run cannot be attached to this
    run's identity); the exact required environment name; and an artifact
    name matching policy's pattern for the candidate's OWN plan digest.

    That digest is never accepted as a caller-supplied string: `candidate_root`
    names the candidate's checked-out tree, and this function RECOMPUTES the
    digest itself, via `compute_candidate_plan_digest`, exactly as
    `bind_bundle_to_candidate` does. A caller that supplied a bare digest
    string here could pass `metadata["artifact_name"]`'s own embedded digest
    right back at this check and have it trivially agree with itself — the
    module's own stated principle ("a digest a caller supplies is a digest a
    caller chooses") applies here as much as it does to binding.
    """

    expected_plan_digest = compute_candidate_plan_digest(
        candidate_root, permitted_off_index
    )

    if not isinstance(metadata, dict):
        raise BundleVerificationError(
            f"run metadata must be a dict, got a {type(metadata).__name__}"
        )
    if not isinstance(policy, dict) or not isinstance(policy.get("repository"), dict):
        raise BundleVerificationError(
            "policy must be a dict shaped like load_policy's return value"
        )

    missing = [
        field for field in _RUN_METADATA_FIELDS if metadata.get(field) in (None, "")
    ]
    if missing:
        raise BundleVerificationError(
            f"run metadata missing required fields: {sorted(missing)}"
        )
    if metadata["repository_full_name"] != policy["repository"]["full_name"]:
        raise BundleVerificationError(
            "run metadata repository_full_name "
            f"{metadata['repository_full_name']!r} does not match policy "
            f"{policy['repository']['full_name']!r}"
        )
    for field in _POSITIVE_INT_FIELDS:
        value = metadata[field]
        # `int(1.9) == 1` truncates silently -- a float, a numeric string,
        # or a bool must be refused OUTRIGHT rather than coerced, because
        # coercion is exactly how a wrong coordinate would slip through
        # looking like a right one.
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise BundleVerificationError(
                f"run metadata field {field!r} must be a positive integer, "
                f"got {value!r}"
            )
    if int(metadata["repository_id"]) != int(policy["repository"]["id"]):
        raise BundleVerificationError(
            "run metadata repository_id does not match policy's immutable "
            "numeric repository ID; a name match alone is not enough "
            "because a repository can be renamed or transferred"
        )
    if metadata["workflow_path"] != policy["producer_workflow_path"]:
        raise BundleVerificationError(
            f"run metadata workflow_path {metadata['workflow_path']!r} is "
            "not the policy's PRODUCER workflow path; only a producer run "
            "may be the source of a bundle"
        )
    trusted_sha = str(metadata["trusted_workflow_sha"])
    if trusted_sha == _NULL_SHA:
        raise BundleVerificationError(
            "trusted_workflow_sha is the all-zero null SHA, which is never "
            "a real commit"
        )
    if not _COMMIT_SHA.match(trusted_sha):
        raise BundleVerificationError(
            f"trusted_workflow_sha must be a 40-hex commit SHA, got {trusted_sha!r}"
        )
    if int(metadata["artifact_run_id"]) != int(metadata["run_id"]):
        raise BundleVerificationError(
            "artifact_run_id does not match run_id; the artifact does not "
            "belong to the claimed run"
        )
    if metadata["environment_name"] != policy["environment_name"]:
        raise BundleVerificationError(
            f"run metadata environment_name {metadata['environment_name']!r} "
            f"does not match policy {policy['environment_name']!r}"
        )
    expected_artifact_name = policy["artifact_name_pattern"].format(
        plan_digest=expected_plan_digest
    )
    if metadata["artifact_name"] != expected_artifact_name:
        raise BundleVerificationError(
            f"run metadata artifact_name {metadata['artifact_name']!r} does "
            f"not match the expected {expected_artifact_name!r} for this "
            "plan digest"
        )
    return RunMetadata(
        repository_full_name=str(metadata["repository_full_name"]),
        repository_id=int(metadata["repository_id"]),
        workflow_path=str(metadata["workflow_path"]),
        run_id=int(metadata["run_id"]),
        run_attempt=int(metadata["run_attempt"]),
        trusted_workflow_sha=trusted_sha,
        artifact_id=int(metadata["artifact_id"]),
        artifact_name=str(metadata["artifact_name"]),
        artifact_run_id=int(metadata["artifact_run_id"]),
        environment_name=str(metadata["environment_name"]),
    )


# ── duplicated units (see the module docstring's "named duplication debt"
#    section and docs/architecture/dependency-bundle-duplication-inventory.json)
# ────────────────────────────────────────────────────────────────────────


def sha256_hex(data: bytes) -> str:
    """The sha256 hex digest of `data`.

    NAMED DUPLICATION: this is deliberately the same one-line operation as
    `erp_lock.sha256_hex` — see the module docstring. Kept as its own
    function (rather than inlined everywhere) so the duplication inventory
    can name ONE symbol per side.
    """

    return hashlib.sha256(data).hexdigest()


def approved_artifact_url(href: str, page_url: str) -> str:
    """Resolve an index-supplied href, or refuse it.

    NAMED DUPLICATION: this mirrors `erp_lock.approved_artifact_url`'s exact
    defensive shape for the same private index — https-only, no embedded
    userinfo, exact origin match, refusal of a `..` traversal segment
    whether literal or revealed by one or two rounds of percent-decoding,
    refusal of a literal or decoded backslash (a PEP 503 artifact path never
    legitimately carries one, and whether the origin treats it as a
    separator is not ours to assume), and a required path prefix. Written
    fresh rather than imported — see the module docstring.
    """

    if not href.strip():
        raise BundleVerificationError("the index page carries an empty href")
    resolved = urllib.parse.urljoin(page_url, href.split("#", 1)[0])
    parts = urllib.parse.urlsplit(resolved)
    if parts.scheme != "https":
        raise BundleVerificationError(
            f"index link {href!r} resolves to {parts.scheme or '(none)'}://, not https"
        )
    if "@" in parts.netloc:
        raise BundleVerificationError(
            f"index link {href!r} carries userinfo in its authority"
        )
    approved = urllib.parse.urlsplit(ARTIFACT_ORIGIN)
    authority = approved.netloc.lower()
    if parts.netloc.lower() not in {authority, f"{authority}:443"}:
        raise BundleVerificationError(
            f"index link {href!r} resolves to origin {parts.netloc!r}, not "
            f"{approved.netloc!r}"
        )
    if ".." in parts.path.split("/"):
        raise BundleVerificationError(
            f"index link {href!r} still traverses after resolution"
        )
    if "\\" in parts.path:
        raise BundleVerificationError(
            f"index link {href!r} contains a literal backslash in its path"
        )
    try:
        urllib.parse.unquote(parts.path, errors="strict")
    except UnicodeDecodeError as exc:
        raise BundleVerificationError(
            f"index link {href!r} carries a percent-escape that does not "
            f"decode as valid UTF-8 ({exc})"
        ) from exc
    decoded_once = urllib.parse.unquote(parts.path)
    if ".." in decoded_once.split("/"):
        raise BundleVerificationError(
            f"index link {href!r} decodes to a `..` segment ({decoded_once!r})"
        )
    if "\\" in decoded_once:
        raise BundleVerificationError(
            f"index link {href!r} decodes to a literal backslash ({decoded_once!r})"
        )
    decoded_twice = urllib.parse.unquote(decoded_once)
    if ".." in decoded_twice.split("/"):
        raise BundleVerificationError(
            f"index link {href!r} decodes to a `..` segment after DOUBLE "
            f"decoding ({decoded_twice!r})"
        )
    if "\\" in decoded_twice:
        raise BundleVerificationError(
            f"index link {href!r} decodes to a literal backslash after "
            f"DOUBLE decoding ({decoded_twice!r})"
        )
    if not parts.path.startswith(ARTIFACT_PATH_PREFIX):
        raise BundleVerificationError(
            f"index link {href!r} resolves to path {parts.path!r}, which is "
            f"not under {ARTIFACT_PATH_PREFIX!r}"
        )
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def credential_encodings(credential: str) -> dict[str, str]:
    """Every form a held credential could appear in inside a text file.

    NAMED DUPLICATION: mirrors `erp_lock.credential_encodings`. Refuses an
    empty credential rather than reporting a scan as clean when there was
    nothing to look for — a scan that can vacuously pass proves nothing.
    """

    if not credential:
        raise BundleVerificationError(
            "no credential was supplied to scan for. There is nothing to "
            "scan the evidence for, so this scan cannot say the evidence is "
            "clean."
        )
    basic = f"{INDEX_USERNAME}:{credential}".encode()
    candidates = {
        "the credential itself": credential,
        "percent-encoded": urllib.parse.quote(credential, safe=""),
        "percent-encoded, spaces as +": urllib.parse.quote_plus(credential),
        "base64": base64.b64encode(credential.encode()).decode("ascii"),
        f"base64 basic-auth ({INDEX_USERNAME}:...)": base64.b64encode(basic).decode(
            "ascii"
        ),
    }
    first_label: dict[str, str] = {}
    for label, form in candidates.items():
        first_label.setdefault(form, label)
    return {label: form for form, label in first_label.items()}


def scan_for_credential(paths: Iterable[Path], credential: str) -> list[str]:
    """Scan `paths` for any encoding of `credential`.

    NAMED DUPLICATION: mirrors `erp_lock.credential_sightings`.
    """

    forms = credential_encodings(credential)
    found: list[str] = []
    for path in sorted(paths):
        text = path.read_text(encoding="utf-8", errors="replace")
        for label, form in forms.items():
            if form in text:
                found.append(f"{path.name}: {label}")
    return found


# ── archive digest ─────────────────────────────────────────────────────


def verify_archive_digest(archive_path: Path, expected_sha256: str) -> str:
    """Verify the OUTER archive's own bytes, before it is ever opened as a
    ZIP. `expected_sha256` must already be a verified value (e.g. bound to
    the artifact's GitHub-reported digest) — this function does not fetch
    or trust anything else."""

    if not isinstance(expected_sha256, str) or not _SHA256_HEX.match(expected_sha256):
        raise BundleVerificationError(
            "expected archive digest must be a 64-hex sha256 string"
        )
    try:
        data = archive_path.read_bytes()
    except OSError as exc:
        raise BundleVerificationError(
            f"cannot read archive {archive_path}: {exc}"
        ) from exc
    digest = sha256_hex(data)
    if digest != expected_sha256:
        raise BundleVerificationError(
            f"archive digest mismatch: expected {expected_sha256}, got {digest}"
        )
    return digest


# ── safe, private, atomic extraction ───────────────────────────────────


def _is_within(base: Path, target: Path) -> bool:
    try:
        target.relative_to(base)
    except ValueError:
        return False
    return True


def _extract_zip_members(
    archive_path: Path, staging_dir: Path, expected_members: dict[str, int]
) -> list[str]:
    """Extract `archive_path` into `staging_dir`, refusing anything the
    verified bundle manifest did not name.

    PRIVATE: this writes into a caller-supplied staging directory and
    performs no publication step and no cleanup of its own — callers MUST
    go through `extract_verified_bundle`, which stages, verifies, and
    publishes atomically, and which cleans up a failed staging directory.
    Nothing outside this module should extract a ZIP without going through
    that verified path.

    `expected_members` is the exact `{member_name: declared_uncompressed_size}`
    mapping taken from the already-verified bundle manifest — never derived
    from the archive itself. Refuses: a duplicate member name, an absolute
    path, a `..` traversal segment, a symlink, a member whose RESOLVED
    target collides with another member's (e.g. `a.whl` and `./a.whl`), a
    member whose name is not in `expected_members`, an `expected_members`
    entry the archive does not contain, a size mismatch against the
    declared/verified size, a member over `MAX_MEMBER_BYTES`, more than
    `MAX_MEMBER_COUNT` members, more than `MAX_TOTAL_UNCOMPRESSED_BYTES` in
    aggregate, a member whose compression ratio exceeds
    `MAX_COMPRESSION_RATIO`, and — during extraction, independent of the
    declared size — any member whose actual bytes exceed the declared size
    (the zip-bomb guard: a lying declared size does not buy more).
    """

    staging_dir = staging_dir.resolve()
    try:
        staging_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ExtractionError(
            f"cannot create staging directory {staging_dir}: {exc}"
        ) from exc
    extracted: list[str] = []
    try:
        archive = zipfile.ZipFile(archive_path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise ExtractionError(
            f"cannot open {archive_path} as a ZIP archive: {exc}"
        ) from exc
    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_MEMBER_COUNT:
            raise ExtractionError(
                f"archive contains {len(infos)} members, exceeding the "
                f"{MAX_MEMBER_COUNT}-member cap"
            )
        names = [info.filename for info in infos]
        if len(names) != len(set(names)):
            raise ExtractionError("archive contains a duplicate member name")

        seen_lower: dict[str, str] = {}
        seen_resolved: dict[Path, str] = {}
        total_declared_size = 0
        for info in infos:
            name = info.filename
            if name not in expected_members:
                raise ExtractionError(
                    f"archive member {name!r} is not named in the verified "
                    "bundle manifest"
                )
            lowered = name.lower()
            if lowered in seen_lower and seen_lower[lowered] != name:
                raise ExtractionError(
                    f"archive members {seen_lower[lowered]!r} and {name!r} "
                    "collide case-insensitively"
                )
            seen_lower[lowered] = name

            if (
                name.startswith("/")
                or name.startswith("\\")
                or Path(name).is_absolute()
            ):
                raise ExtractionError(f"archive member {name!r} uses an absolute path")
            if ".." in Path(name).parts:
                raise ExtractionError(
                    f"archive member {name!r} contains a path-traversal segment"
                )
            resolved_target = (staging_dir / name).resolve()
            if not _is_within(staging_dir, resolved_target):
                raise ExtractionError(
                    f"archive member {name!r} resolves outside the "
                    "destination directory"
                )
            if resolved_target in seen_resolved:
                raise ExtractionError(
                    f"archive members {seen_resolved[resolved_target]!r} and "
                    f"{name!r} resolve to the SAME target path "
                    f"({resolved_target}); a platform-separator or "
                    "relative-segment alias is refused"
                )
            seen_resolved[resolved_target] = name

            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise ExtractionError(f"archive member {name!r} is a symlink")

            declared_size = info.file_size
            if declared_size != expected_members[name]:
                raise ExtractionError(
                    f"archive member {name!r} declares size {declared_size}, "
                    f"the verified manifest expects {expected_members[name]}"
                )
            if declared_size > MAX_MEMBER_BYTES:
                raise ExtractionError(
                    f"archive member {name!r} declares {declared_size} bytes, "
                    f"exceeding the {MAX_MEMBER_BYTES}-byte per-member cap"
                )
            if info.compress_size > 0:
                ratio = declared_size / info.compress_size
                if ratio > MAX_COMPRESSION_RATIO:
                    raise ExtractionError(
                        f"archive member {name!r} has a compression ratio of "
                        f"{ratio:.1f}, exceeding the {MAX_COMPRESSION_RATIO}x "
                        "cap; refusing as a suspected zip bomb"
                    )
            total_declared_size += declared_size
            if total_declared_size > MAX_TOTAL_UNCOMPRESSED_BYTES:
                raise ExtractionError(
                    "archive's aggregate declared uncompressed size exceeds "
                    f"the {MAX_TOTAL_UNCOMPRESSED_BYTES}-byte cap"
                )

        for expected_name in expected_members:
            if expected_name not in names:
                raise ExtractionError(
                    f"the verified bundle manifest expects member "
                    f"{expected_name!r}, which the archive does not contain"
                )

        running_total = 0
        for info in infos:
            target = staging_dir / info.filename
            written = 0
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, open(target, "wb") as sink:
                    while True:
                        chunk = source.read(1024 * 1024)
                        if not chunk:
                            break
                        written += len(chunk)
                        running_total += len(chunk)
                        if written > MAX_MEMBER_BYTES:
                            raise ExtractionError(
                                f"archive member {info.filename!r} exceeded the "
                                f"{MAX_MEMBER_BYTES}-byte cap while extracting "
                                "(declared size cannot be trusted; this is the "
                                "zip-bomb guard)"
                            )
                        if running_total > MAX_TOTAL_UNCOMPRESSED_BYTES:
                            raise ExtractionError(
                                "aggregate extracted bytes exceeded "
                                f"{MAX_TOTAL_UNCOMPRESSED_BYTES}; refusing (zip-"
                                "bomb guard)"
                            )
                        sink.write(chunk)
            except (zipfile.BadZipFile, OSError) as exc:
                raise ExtractionError(
                    f"cannot extract archive member {info.filename!r}: {exc}"
                ) from exc
            if written != info.file_size:
                raise ExtractionError(
                    f"archive member {info.filename!r} extracted "
                    f"{written} bytes but declared {info.file_size}"
                )
            extracted.append(info.filename)
    return extracted


def verify_member_hashes(dest_dir: Path, expected_hashes: dict[str, str]) -> None:
    """Verify every extracted file's content hash against the verified
    bundle manifest's per-member hash. Independent of extraction's size
    checks — this is the content proof, not the shape proof."""

    for name, expected_hex in expected_hashes.items():
        if not isinstance(expected_hex, str) or not _SHA256_HEX.match(expected_hex):
            raise BundleVerificationError(
                f"expected hash for {name!r} is not a 64-hex sha256 string"
            )
        path = dest_dir / name
        if not path.is_file():
            raise BundleVerificationError(
                f"expected extracted member {name!r} is missing on disk"
            )
        try:
            digest = sha256_hex(path.read_bytes())
        except OSError as exc:
            raise BundleVerificationError(
                f"cannot read extracted member {name!r}: {exc}"
            ) from exc
        if digest != expected_hex:
            raise BundleVerificationError(
                f"extracted member {name!r} hash mismatch: expected "
                f"{expected_hex}, got {digest}"
            )


def extract_verified_bundle(
    archive_path: Path, dest_dir: Path, bundle_manifest: dict[str, Any]
) -> list[str]:
    """The ONLY sanctioned way to extract a bundle archive.

    Extracts into a FRESH, EXCLUSIVE staging directory (never `dest_dir`
    directly), verifies every member's size (via `_extract_zip_members`)
    and content hash (via `verify_member_hashes`) THERE, and only then
    publishes the complete, verified tree to `dest_dir` with a single atomic
    rename. `dest_dir` must not already exist — this function materialises a
    fresh tree, it does not merge into or overwrite one. On ANY failure —
    an unsafe member, a hash mismatch, or an unexpected error — the staging
    directory is removed and NOTHING is written to `dest_dir`; a caller
    never observes a partially-extracted destination.

    STATED, NARROW RACE (not claimed to be closed): existence is checked
    both here and again immediately before the final rename, but there is
    no portable, dependency-free "rename unless the destination exists"
    primitive for a directory target in the Python standard library
    (POSIX `renameat2(..., RENAME_NOREPLACE)` is Linux-only and is not
    exposed by `os`). A concurrent process that creates an EMPTY `dest_dir`
    in the narrow window between the second check and `os.rename` would
    have it silently replaced, because POSIX `rename(2)` replacing an
    empty directory target is not an OS-level error. This function is
    atomic against sequential failure (a caller never sees a partial
    result); it is not a mutual-exclusion primitive against a concurrent,
    uncooperating writer targeting the SAME `dest_dir` — that is expected
    not to happen (each destination is expected to be named for its own
    bundle identity), and external locking is the caller's responsibility
    if it might.
    """

    if dest_dir.exists():
        raise ExtractionError(
            f"destination {dest_dir} already exists; extract_verified_bundle "
            "materialises a fresh tree and refuses to merge into or "
            "overwrite one"
        )
    members = bundle_manifest.get("members")
    if not isinstance(members, dict) or not members:
        raise BundleVerificationError("bundle manifest carries no members to extract")
    expected_sizes: dict[str, int] = {}
    expected_hashes: dict[str, str] = {}
    for name, record in members.items():
        if (
            not isinstance(record, dict)
            or not isinstance(record.get("size"), int)
            or isinstance(record.get("size"), bool)
            or record["size"] <= 0
            or not isinstance(record.get("sha256"), str)
            or not _SHA256_HEX.match(record["sha256"])
        ):
            raise BundleVerificationError(
                f"bundle manifest member {name!r} is malformed: {record!r}"
            )
        expected_sizes[name] = record["size"]
        expected_hashes[name] = record["sha256"]

    dest_dir.parent.mkdir(parents=True, exist_ok=True)
    staging_dir = Path(
        tempfile.mkdtemp(prefix=f".{dest_dir.name}.staging.", dir=str(dest_dir.parent))
    )
    try:
        extracted = _extract_zip_members(archive_path, staging_dir, expected_sizes)
        verify_member_hashes(staging_dir, expected_hashes)
    except BaseException:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise

    if dest_dir.exists():
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise ExtractionError(
            f"destination {dest_dir} was created concurrently while staging; "
            "refusing to publish over it"
        )
    try:
        os.rename(staging_dir, dest_dir)
    except OSError as exc:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise ExtractionError(
            f"cannot publish extracted bundle to {dest_dir}: {exc}"
        ) from exc
    return extracted


# ── canonical bundle manifest ──────────────────────────────────────────


def create_bundle_manifest(
    *,
    surface: DependencySurface,
    acquired_files: Mapping[str, Path],
    archive_path: Path,
    run: RunMetadata,
    schema_version: int = MANIFEST_SCHEMA_VERSION,
) -> dict[str, Any]:
    """Build the canonical bundle manifest by COMPUTING every binding from
    `surface` and the ACTUAL FILES on disk — never accepting a plan digest,
    a member hash, a member size, or the archive digest as an independent,
    caller-reported scalar.

    `acquired_files` maps each planned filename to the real path the
    producer downloaded it to; this function reads every one of those
    files and hashes/sizes them itself — it does not trust a caller's
    report of what a file's hash or size supposedly was. `archive_path` is
    likewise the real outer archive file; `archive_sha256` is computed from
    it here, never accepted as a scalar. `plan_digest` is derived by
    calling `compute_plan_digest(surface)` internally, and the manifest
    requires EXACT plan-to-member closure against that SAME surface's
    `planned_artifacts` — every planned filename must be present in
    `acquired_files`, and every acquired file must be named by the plan.
    There is only one surface input and every byte is independently
    re-read, so there is no seam at which a second plan's identity, or an
    unrelated archive, could be substituted for the real artifacts.
    """

    if schema_version != MANIFEST_SCHEMA_VERSION:
        raise BundleVerificationError(
            f"schema_version must be {MANIFEST_SCHEMA_VERSION}, got {schema_version!r}"
        )

    planned = planned_artifacts(surface)
    if not planned:
        raise BundleVerificationError(
            "a dependency surface with no forgejo-hosted files cannot "
            "produce a bundle manifest"
        )
    planned_by_filename = {artifact.filename: artifact for artifact in planned}
    planned_names = set(planned_by_filename)
    acquired_names = set(acquired_files)

    missing = planned_names - acquired_names
    if missing:
        raise BundleVerificationError(
            f"the plan requires {sorted(missing)}, which were not acquired"
        )
    extra = acquired_names - planned_names
    if extra:
        raise BundleVerificationError(
            f"{sorted(extra)} were acquired but the plan does not name them; "
            "every acquired member must be accounted for by the plan"
        )

    members: dict[str, dict[str, Any]] = {}
    for filename, planned_artifact in sorted(planned_by_filename.items()):
        path = acquired_files[filename]
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise BundleVerificationError(
                f"cannot read acquired file {filename!r} at {path}: {exc}"
            ) from exc
        actual_sha256 = sha256_hex(data)
        actual_size = len(data)
        if actual_sha256 != planned_artifact.sha256:
            raise BundleVerificationError(
                f"{filename!r} was acquired with digest {actual_sha256}, "
                f"but the plan requires {planned_artifact.sha256}"
            )
        if actual_size <= 0:
            raise BundleVerificationError(f"{filename!r} is empty on disk")
        members[filename] = {
            "sha256": actual_sha256,
            "size": actual_size,
            "package": planned_artifact.package_normalised_name,
        }

    try:
        archive_bytes = archive_path.read_bytes()
    except OSError as exc:
        raise BundleVerificationError(
            f"cannot read archive {archive_path}: {exc}"
        ) from exc
    archive_sha256 = sha256_hex(archive_bytes)

    plan_digest = compute_plan_digest(surface)
    return {
        "schema_version": schema_version,
        "plan_digest": plan_digest,
        "archive_sha256": archive_sha256,
        "members": members,
        "run": {
            "repository_full_name": run.repository_full_name,
            "repository_id": run.repository_id,
            "workflow_path": run.workflow_path,
            "run_id": run.run_id,
            "run_attempt": run.run_attempt,
            "trusted_workflow_sha": run.trusted_workflow_sha,
            "artifact_id": run.artifact_id,
            "artifact_name": run.artifact_name,
            "artifact_run_id": run.artifact_run_id,
            "environment_name": run.environment_name,
        },
    }


# ── local PEP 503 materialisation ────────────────────────────────────────


def build_local_index(
    index_root: Path, packages: dict[str, list[tuple[str, str, Path]]]
) -> None:
    """Materialise a local PEP 503 "simple" index under `index_root/simple`,
    atomically.

    `packages` maps a PEP-503-normalised package name to a list of
    `(filename, sha256_hex, source_path)` triples — each `source_path` is a
    file this module has ALREADY verified (via `verify_member_hashes`). This
    function does not itself trust the hash; it only copies bytes and
    records the hash fragment PEP 503 uses for its own integrity check.

    Same property as `extract_verified_bundle`, applied here: the WHOLE
    index is built in a fresh, exclusive staging directory and published
    with one atomic rename. `index_root` must not already exist. Previously
    this function wrote directly into `index_root`, package by package and
    file by file — a failure partway (a malformed key, a missing source
    file, a full disk) left an already-published package A sitting beside a
    partially-written package B, and the root index was rebuilt from
    whatever package directories happened to exist on disk, silently
    trusting that stale/partial state as resolver input on a retry. There
    is no retry-merge path now: a caller that needs to rebuild calls this
    again against a fresh `index_root`.

    STATED, NARROW RACE (not claimed to be closed) — identical to
    `extract_verified_bundle`'s: existence is checked both here and again
    immediately before the final rename, but there is no portable,
    dependency-free "rename unless the destination exists" primitive for a
    directory target in the Python standard library. A concurrent process
    that creates an EMPTY `index_root` in the narrow window before
    `os.rename` would have it silently replaced. This function is atomic
    against sequential failure; it is not a mutual-exclusion primitive
    against a concurrent, uncooperating writer targeting the SAME
    `index_root`.
    """

    if index_root.exists():
        raise BundleVerificationError(
            f"destination {index_root} already exists; build_local_index "
            "materialises a fresh tree and refuses to merge into or "
            "overwrite one"
        )
    index_root.parent.mkdir(parents=True, exist_ok=True)
    try:
        staging_root = Path(
            tempfile.mkdtemp(
                prefix=f".{index_root.name}.staging.", dir=str(index_root.parent)
            )
        )
    except OSError as exc:
        raise BundleVerificationError(
            f"cannot create a staging directory beside {index_root}: {exc}"
        ) from exc

    try:
        root_dir = staging_root / "simple"
        root_dir.mkdir(parents=True, exist_ok=True)
        for normalised_pkg_name, files in packages.items():
            try:
                canonical_name = normalise_name(normalised_pkg_name)
            except ValueError as exc:
                raise BundleVerificationError(
                    f"package key {normalised_pkg_name!r} is not a valid "
                    f"PEP 503 name: {exc}"
                ) from exc
            if normalised_pkg_name != canonical_name:
                raise BundleVerificationError(
                    f"package key {normalised_pkg_name!r} is not PEP-503-normalised"
                )
            pkg_dir = root_dir / normalised_pkg_name
            resolved_pkg_dir = pkg_dir.resolve()
            if not _is_within(root_dir.resolve(), resolved_pkg_dir):
                raise BundleVerificationError(
                    f"package key {normalised_pkg_name!r} resolves outside "
                    "the index directory"
                )
            pkg_dir.mkdir(parents=True, exist_ok=True)
            anchors = []
            for filename, digest_hex, source_path in sorted(files, key=lambda t: t[0]):
                # A caller-controlled filename must be a bare filename: no
                # path separator, not absolute, not `.`/`..`. Without this,
                # `pkg_dir / filename` can write outside `pkg_dir` the same
                # way an unvalidated package-name key can write outside
                # `root_dir` -- and `Path.__truediv__` REPLACES the left
                # side entirely when the right side is absolute.
                if (
                    not filename
                    or "/" in filename
                    or "\\" in filename
                    or filename in (".", "..")
                    or Path(filename).is_absolute()
                ):
                    raise BundleVerificationError(
                        f"filename {filename!r} is not a safe bare filename "
                        "(no path separators, not absolute, not '.' or '..')"
                    )
                if not _SHA256_HEX.match(digest_hex):
                    raise BundleVerificationError(
                        f"{filename!r} carries a malformed sha256 {digest_hex!r}"
                    )
                if not source_path.is_file():
                    raise BundleVerificationError(
                        f"local index source file missing for {filename!r}: "
                        f"{source_path}"
                    )
                try:
                    (pkg_dir / filename).write_bytes(source_path.read_bytes())
                except OSError as exc:
                    raise BundleVerificationError(
                        f"cannot stage {filename!r}: {exc}"
                    ) from exc
                # Escaped before ever reaching a resolver-facing anchor: a
                # filename is caller-controlled and this HTML is served to
                # a real package resolver.
                safe_filename = html.escape(filename, quote=True)
                safe_digest = html.escape(digest_hex, quote=True)
                anchors.append(
                    f'<a href="{safe_filename}#sha256={safe_digest}">'
                    f"{safe_filename}</a><br/>"
                )
            (pkg_dir / "index.html").write_text(
                "<!DOCTYPE html><html><body>\n"
                + "\n".join(anchors)
                + "\n</body></html>\n",
                encoding="utf-8",
            )
        package_names = sorted(p.name for p in root_dir.iterdir() if p.is_dir())
        root_anchors = [
            f'<a href="{html.escape(name, quote=True)}/">'
            f"{html.escape(name, quote=True)}</a><br/>"
            for name in package_names
        ]
        (root_dir / "index.html").write_text(
            "<!DOCTYPE html><html><body>\n"
            + "\n".join(root_anchors)
            + "\n</body></html>\n",
            encoding="utf-8",
        )
    except BaseException:
        shutil.rmtree(staging_root, ignore_errors=True)
        raise

    if index_root.exists():
        shutil.rmtree(staging_root, ignore_errors=True)
        raise BundleVerificationError(
            f"destination {index_root} was created concurrently while "
            "staging; refusing to publish over it"
        )
    try:
        os.rename(staging_root, index_root)
    except OSError as exc:
        shutil.rmtree(staging_root, ignore_errors=True)
        raise BundleVerificationError(
            f"cannot publish staged index to {index_root}: {exc}"
        ) from exc


# ── candidate-specific rebinding ──────────────────────────────────────────


@dataclass(frozen=True)
class CandidateBinding:
    """Proof that one specific candidate commit's OWN dependency surface
    matches one specific verified bundle's plan digest."""

    candidate_sha: str
    plan_digest: str
    bundle_run_id: int
    bundle_artifact_id: int


def bind_bundle_to_candidate(
    bundle_manifest: dict[str, Any],
    run: RunMetadata,
    candidate_root: Path,
    permitted_off_index: Mapping[str, OffIndexPin] | None = None,
) -> CandidateBinding:
    """Bind a verified bundle to one candidate commit.

    Three things this function derives or cross-checks itself, rather than
    trusting a caller's assertion of any of them:

    * The candidate's commit SHA — via `_read_git_head_sha`, reading
      `candidate_root`'s own `.git` refs. A caller that could assert any
      SHA here could bind a real, verified bundle to an unrelated commit
      it never actually checked out.
    * The candidate's plan digest — via
      `compute_candidate_plan_digest_at_commit`, parsing THAT SAME
      resolved commit's own git-tree blobs, never `candidate_root`'s
      current working-tree files. **The commit is resolved ONCE, then the
      digest is derived from that exact commit, then the identity is
      bound to that exact commit — in that order.** A caller-supplied SHA
      read beside an independent working-tree parse is not a binding: a
      dirty checkout, or a concurrent `git checkout`/commit landing
      between two separate reads of `candidate_root`, could otherwise
      produce `candidate_sha=A, plan_digest=B` where commit A never
      actually contained the bytes that produced B. Reading both the SHA
      and the dependency bytes as properties of ONE pinned commit object
      closes that gap by construction, not by hoping two observations
      happen to agree.
    * That `run` — an ALREADY-VERIFIED `RunMetadata`, produced by a prior
      `verify_run_metadata` call, never re-derived here from
      `bundle_manifest`'s own raw `run` dict with a loose `int()` coercion
      (which would accept `True` and truncate `1.9`) — actually
      corresponds to THIS `bundle_manifest`'s own run record. Without that
      cross-check, a valid `RunMetadata` verified against a DIFFERENT
      bundle could be passed alongside this one and produce a binding that
      mixes an unrelated run into it.

    Refuses if the candidate's digest disagrees with the bundle's: a
    candidate whose private dependency surface does not match gets no
    bundle, not a downgraded warning.
    """

    candidate_sha = _read_git_head_sha(candidate_root)
    candidate_plan_digest = compute_candidate_plan_digest_at_commit(
        candidate_root, candidate_sha, permitted_off_index
    )
    bundle_digest = bundle_manifest.get("plan_digest")
    if not isinstance(bundle_digest, str) or not _SHA256_HEX.match(bundle_digest):
        raise BundleVerificationError(
            "bundle manifest carries no valid plan_digest to bind against"
        )
    if candidate_plan_digest != bundle_digest:
        raise BundleVerificationError(
            "candidate's own dependency-surface plan digest does not match "
            f"the bundle's: candidate={candidate_plan_digest} "
            f"bundle={bundle_digest}. This candidate cannot use this "
            "bundle; it must select or wait for one whose plan digest "
            "matches its own manifest+lock."
        )
    if not isinstance(run, RunMetadata):
        raise BundleVerificationError(
            "run must be an already-verified RunMetadata (from "
            "verify_run_metadata), not a raw dict"
        )
    manifest_run = bundle_manifest.get("run")
    if (
        not isinstance(manifest_run, dict)
        or manifest_run.get("run_id") != run.run_id
        or manifest_run.get("artifact_id") != run.artifact_id
    ):
        raise BundleVerificationError(
            "the verified run metadata does not correspond to this bundle "
            "manifest's own run record; refusing to mix an unrelated run "
            "into this binding"
        )
    return CandidateBinding(
        candidate_sha=candidate_sha,
        plan_digest=candidate_plan_digest,
        bundle_run_id=run.run_id,
        bundle_artifact_id=run.artifact_id,
    )


# ── CLI ─────────────────────────────────────────────────────────────────


def _cmd_plan_digest(args: argparse.Namespace) -> int:
    permitted_off_index = {}
    if args.policy:
        policy = load_policy(Path(args.policy))
        permitted_off_index = load_permitted_off_index_dependencies(policy)
    surface = extract_dependency_surface(Path(args.project_root), permitted_off_index)
    digest = compute_plan_digest(surface)
    if args.document:
        print(json.dumps(build_plan_document(surface), sort_keys=True, indent=2))
    print(digest)
    return 0


def _cmd_verify_archive(args: argparse.Namespace) -> int:
    digest = verify_archive_digest(Path(args.archive), args.expected_sha256)
    print(digest)
    return 0


def _cmd_verify_policy(args: argparse.Namespace) -> int:
    policy = load_policy(Path(args.policy))
    print(json.dumps(policy, sort_keys=True, indent=2))
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dependency_bundle",
        description=(
            "Dependency-surface plan digest and verified-bundle mechanics "
            "(see the module docstring for the trust model)."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan_digest = subparsers.add_parser(
        "plan-digest",
        help="compute the plan digest for a project's pyproject.toml + poetry.lock",
    )
    plan_digest.add_argument("project_root", help="directory containing both files")
    plan_digest.add_argument(
        "--policy",
        help="path to .github/dependency-bundle-policy.json, for the "
        "off-index allowlist",
    )
    plan_digest.add_argument(
        "--document",
        action="store_true",
        help="also print the canonical plan document that was hashed",
    )
    plan_digest.set_defaults(func=_cmd_plan_digest)

    verify_archive = subparsers.add_parser(
        "verify-archive",
        help="verify a ZIP archive's outer sha256 digest",
    )
    verify_archive.add_argument("archive", help="path to the archive")
    verify_archive.add_argument("expected_sha256", help="64-hex sha256 to require")
    verify_archive.set_defaults(func=_cmd_verify_archive)

    verify_policy = subparsers.add_parser(
        "verify-policy",
        help="load and validate .github/dependency-bundle-policy.json",
    )
    verify_policy.add_argument("policy", help="path to the policy JSON file")
    verify_policy.set_defaults(func=_cmd_verify_policy)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except DependencyBundleError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
