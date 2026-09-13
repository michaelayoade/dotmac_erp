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
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import tomllib
import urllib.parse
import zipfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dependency_normalisation import normalise_name

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
PLAN_SCHEMA_VERSION = 2

#: The bundle manifest's own schema version (see `create_bundle_manifest`).
MANIFEST_SCHEMA_VERSION = 2

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
    return isinstance(url, str) and FORGEJO_HOST in url


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
    dependencies: tuple[ForgejoDependency, ...]
    off_index_dependencies: tuple[ApprovedOffIndexDependency, ...]
    lock_packages: tuple[LockPackage, ...]


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


def _dependency_groups(poetry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {"main": poetry.get("dependencies", {}) or {}}
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
        groups[group_name] = group_body["dependencies"] or {}
    return groups


def _classify_forgejo_spec(
    name: str, spec: dict[str, Any], group: str
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
        normalised_name=normalise_name(name),
        group=group,
        version=str(version),
        markers=markers,
        extras=tuple(sorted(extras_value)),
        optional=optional_value,
        python_constraint=python_constraint,
    )


def _classify_off_index_spec(
    name: str,
    spec: dict[str, Any],
    group: str,
    permitted_off_index: Mapping[str, OffIndexPin],
) -> ApprovedOffIndexDependency:
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
    if spec["git"] != pin.url:
        raise ManifestError(
            f"{group}.{name}: names git url {spec['git']!r}, not the exact "
            f"pinned {pin.url!r}"
        )
    if spec["tag"] != pin.tag:
        raise ManifestError(
            f"{group}.{name}: names tag {spec['tag']!r}, not the exact "
            f"pinned {pin.tag!r}"
        )
    return ApprovedOffIndexDependency(
        name=name,
        normalised_name=normalise_name(name),
        group=group,
        url=pin.url,
        tag=pin.tag,
        resolved_commit=pin.commit,
    )


def _classify_dependency(
    name: str,
    spec: Any,
    group: str,
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
                f"{name}[{index}]", item, group, permitted_off_index
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
        return _classify_off_index_spec(name, spec, group, permitted_off_index)

    if source is None:
        return PublicDependency(name=name, group=group)

    if source != FORGEJO_SOURCE_NAME:
        raise ManifestError(
            f"{group}.{name}: unrecognised dependency source {source!r}; "
            f"only {FORGEJO_SOURCE_NAME!r} is a known private source"
        )

    return _classify_forgejo_spec(name, spec, group)


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
    normalised = url.rstrip("/")
    if normalised != FORGEJO_LOCK_URL:
        raise ManifestError(
            f"the forgejo source url {url!r} is not the approved index "
            f"{FORGEJO_MANIFEST_URL!r} — an alternate Forgejo URL is refused"
        )
    if entry.get("priority") != "explicit":
        raise ManifestError("the forgejo source must declare priority = 'explicit'")
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
    packages_raw = lock.get("package", [])
    if not isinstance(packages_raw, list):
        raise ManifestError("poetry.lock [[package]] must be an array")
    packages: list[LockPackage] = []
    for pkg in packages_raw:
        source = pkg.get("source") or {}
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
        packages.append(
            LockPackage(
                name=name,
                normalised_name=normalise_name(name),
                version=version,
                groups=tuple(sorted(str(g) for g in groups_raw)),
                optional=optional_value,
                python_versions=python_versions,
                dependencies=dict(pkg.get("dependencies", {}) or {}),
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
    matches = [
        p for p in packages_raw if isinstance(p, dict) and p.get("name") == dep.name
    ]
    if len(matches) != 1:
        raise ManifestError(
            f"approved off-index dependency {dep.name!r} must have exactly "
            f"one poetry.lock entry, found {len(matches)}"
        )
    source = matches[0].get("source") or {}
    if (
        source.get("type") != "git"
        or source.get("url") != dep.url
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
        result[name] = OffIndexPin(url=entry["url"], tag=entry["tag"], commit=commit)
    return result


def extract_dependency_surface(
    project_root: Path, permitted_off_index: Mapping[str, OffIndexPin] | None = None
) -> DependencySurface:
    """Parse, validate, and extract the private dependency surface from
    `project_root`'s `pyproject.toml` + `poetry.lock`.

    `permitted_off_index` is the policy's off-index allowlist (see
    `load_permitted_off_index_dependencies`) — defaults to empty, meaning
    every off-index dependency form is refused; a caller that wants ERP's
    real, pinned `dotmac-integration-client` exemption must pass it in
    explicitly, from a loaded policy, never invent it.

    Raises `ManifestError` for every unrecognised or disagreeing shape this
    module's docstring and `docs/architecture/dependency-bundle-trust.md`
    enumerate. Never returns a partial surface on a refusal.
    """

    permitted_off_index = permitted_off_index or {}

    if (project_root / "poetry.toml").exists():
        raise ManifestError(
            "a project-local poetry.toml is present; it can reconfigure TLS "
            "verification and credential lookup for the very resolution "
            "this module is trying to plan-digest, and is refused outright"
        )
    manifest = _load_toml(project_root / "pyproject.toml")
    lock = _load_toml(project_root / "poetry.lock")
    _refuse_unknown_dependency_surfaces(manifest)
    poetry = manifest["tool"]["poetry"]
    forgejo_url = _forgejo_source_url(poetry)
    groups = _dependency_groups(poetry)

    dependencies: list[ForgejoDependency] = []
    off_index_dependencies: list[ApprovedOffIndexDependency] = []
    seen_by_normalised_name: dict[str, str] = {}
    for group_name, table in groups.items():
        if not isinstance(table, dict):
            raise ManifestError(f"[{group_name}].dependencies must be a table")
        for dep_name, spec in table.items():
            classified = _classify_dependency(
                dep_name, spec, group_name, permitted_off_index
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

    target_python = poetry.get("dependencies", {}).get("python")
    if not isinstance(target_python, str) or not target_python:
        raise ManifestError("[tool.poetry.dependencies].python must be a string")

    return DependencySurface(
        schema_version=PLAN_SCHEMA_VERSION,
        forgejo_source_url=forgejo_url,
        target_python=target_python,
        target_platform=TARGET_PLATFORM,
        dependencies=tuple(dependencies),
        off_index_dependencies=tuple(off_index_dependencies),
        lock_packages=tuple(lock_packages),
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
        "dependencies": [
            {
                "name": dep.normalised_name,
                "group": dep.group,
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
                "url": dep.url,
                "tag": dep.tag,
                "resolved_commit": dep.resolved_commit,
            }
            for dep in sorted(
                surface.off_index_dependencies,
                key=lambda d: (d.group, d.normalised_name),
            )
        ],
        "lock_packages": [
            {
                "name": pkg.normalised_name,
                "version": pkg.version,
                "groups": list(pkg.groups),
                "optional": pkg.optional,
                "python_versions": pkg.python_versions,
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
    this is not provenance."""

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


def verify_run_metadata(
    metadata: dict[str, Any], policy: dict[str, Any], *, expected_plan_digest: str
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
    name matching policy's pattern for `expected_plan_digest` — the plan
    digest the CALLER independently computed from its own candidate surface,
    never taken from the metadata itself.
    """

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
        try:
            value = int(metadata[field])
        except (TypeError, ValueError) as exc:
            raise BundleVerificationError(
                f"run metadata field {field!r} is not integer-shaped: {exc}"
            ) from exc
        if isinstance(metadata[field], bool) or value <= 0:
            raise BundleVerificationError(
                f"run metadata field {field!r} must be a positive integer, "
                f"got {metadata[field]!r}"
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
    if not isinstance(expected_plan_digest, str) or not _SHA256_HEX.match(
        expected_plan_digest
    ):
        raise BundleVerificationError(
            "expected_plan_digest must be a 64-hex sha256 string"
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
    staging_dir.mkdir(parents=True, exist_ok=True)
    extracted: list[str] = []
    with zipfile.ZipFile(archive_path) as archive:
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
            target.parent.mkdir(parents=True, exist_ok=True)
            written = 0
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
        digest = sha256_hex(path.read_bytes())
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

    try:
        os.rename(staging_dir, dest_dir)
    except OSError:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise
    return extracted


# ── canonical bundle manifest ──────────────────────────────────────────


@dataclass(frozen=True)
class AcquiredMember:
    """One file the producer job actually downloaded and hashed, before it
    is bound into a manifest against the plan."""

    filename: str
    sha256: str
    size: int


def create_bundle_manifest(
    *,
    surface: DependencySurface,
    acquired_members: Mapping[str, AcquiredMember],
    run: RunMetadata,
    archive_sha256: str,
    schema_version: int = MANIFEST_SCHEMA_VERSION,
) -> dict[str, Any]:
    """Build the canonical bundle manifest by COMPUTING every binding from
    `surface` and `acquired_members` — never accepting a bare plan digest or
    member-hash mapping as independent, uncorrelated scalars.

    This is what makes "plan A's digest attached to plan B's files" a
    structural impossibility rather than something every individual check
    happens to miss: `plan_digest` is derived from `surface` itself, and the
    manifest requires EXACT plan-to-member closure against that SAME
    surface's `planned_artifacts` — every planned `(filename, sha256)` must
    be present in `acquired_members` with an agreeing digest, and every
    acquired member must be named by the plan. There is only one surface
    input, so there is no seam at which a second plan's identity could be
    substituted for the files.
    """

    if not _SHA256_HEX.match(archive_sha256):
        raise BundleVerificationError("archive_sha256 must be a 64-hex sha256 string")

    planned = planned_artifacts(surface)
    if not planned:
        raise BundleVerificationError(
            "a dependency surface with no forgejo-hosted files cannot "
            "produce a bundle manifest"
        )
    planned_by_filename = {artifact.filename: artifact for artifact in planned}
    planned_names = set(planned_by_filename)
    acquired_names = set(acquired_members)

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
        acquired = acquired_members[filename]
        if not isinstance(acquired.sha256, str) or not _SHA256_HEX.match(
            acquired.sha256
        ):
            raise BundleVerificationError(
                f"{filename!r} was acquired with a malformed digest {acquired.sha256!r}"
            )
        if acquired.sha256 != planned_artifact.sha256:
            raise BundleVerificationError(
                f"{filename!r} was acquired with digest {acquired.sha256}, "
                f"but the plan requires {planned_artifact.sha256}"
            )
        if (
            not isinstance(acquired.size, int)
            or isinstance(acquired.size, bool)
            or acquired.size <= 0
        ):
            raise BundleVerificationError(
                f"{filename!r} carries a non-positive size {acquired.size!r}"
            )
        members[filename] = {
            "sha256": acquired.sha256,
            "size": acquired.size,
            "package": planned_artifact.package_normalised_name,
        }

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
    """Materialise a local PEP 503 "simple" index under `index_root/simple`.

    `packages` maps a PEP-503-normalised package name to a list of
    `(filename, sha256_hex, source_path)` triples — each `source_path` is a
    file this module has ALREADY verified (via `verify_member_hashes`). This
    function does not itself trust the hash; it only copies bytes and
    records the hash fragment PEP 503 uses for its own integrity check.
    """

    root_dir = index_root / "simple"
    root_dir.mkdir(parents=True, exist_ok=True)
    for normalised_pkg_name, files in packages.items():
        if normalised_pkg_name != normalise_name(normalised_pkg_name):
            raise BundleVerificationError(
                f"package key {normalised_pkg_name!r} is not PEP-503-normalised"
            )
        pkg_dir = root_dir / normalised_pkg_name
        pkg_dir.mkdir(parents=True, exist_ok=True)
        anchors = []
        for filename, digest_hex, source_path in sorted(files, key=lambda t: t[0]):
            if not _SHA256_HEX.match(digest_hex):
                raise BundleVerificationError(
                    f"{filename!r} carries a malformed sha256 {digest_hex!r}"
                )
            if not source_path.is_file():
                raise BundleVerificationError(
                    f"local index source file missing for {filename!r}: {source_path}"
                )
            (pkg_dir / filename).write_bytes(source_path.read_bytes())
            anchors.append(
                f'<a href="{filename}#sha256={digest_hex}">{filename}</a><br/>'
            )
        (pkg_dir / "index.html").write_text(
            "<!DOCTYPE html><html><body>\n" + "\n".join(anchors) + "\n</body></html>\n",
            encoding="utf-8",
        )
    package_names = sorted(p.name for p in root_dir.iterdir() if p.is_dir())
    root_anchors = [f'<a href="{name}/">{name}</a><br/>' for name in package_names]
    (root_dir / "index.html").write_text(
        "<!DOCTYPE html><html><body>\n"
        + "\n".join(root_anchors)
        + "\n</body></html>\n",
        encoding="utf-8",
    )


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
    bundle_manifest: dict[str, Any], candidate_sha: str, candidate_plan_digest: str
) -> CandidateBinding:
    """Bind a verified bundle to one candidate commit.

    `candidate_plan_digest` must be independently recomputed by the caller
    from the CANDIDATE's own checked-out `pyproject.toml`/`poetry.lock`
    (`compute_plan_digest(extract_dependency_surface(candidate_root, ...))`)
    — never taken as a bare string from the candidate. Refuses if the
    candidate's digest disagrees with the bundle's: a candidate whose
    private dependency surface does not match gets no bundle, not a
    downgraded warning.
    """

    if not isinstance(candidate_sha, str) or not _COMMIT_SHA.match(candidate_sha):
        raise BundleVerificationError("candidate_sha must be a 40-hex commit SHA")
    if not isinstance(candidate_plan_digest, str) or not _SHA256_HEX.match(
        candidate_plan_digest
    ):
        raise BundleVerificationError(
            "candidate_plan_digest must be a 64-hex sha256 string"
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
    run = bundle_manifest.get("run")
    if not isinstance(run, dict) or "run_id" not in run or "artifact_id" not in run:
        raise BundleVerificationError(
            "bundle manifest carries no verified run metadata to bind against"
        )
    try:
        run_id = int(run["run_id"])
        artifact_id = int(run["artifact_id"])
    except (TypeError, ValueError) as exc:
        raise BundleVerificationError(
            f"bundle manifest run metadata is not integer-shaped: {exc}"
        ) from exc
    return CandidateBinding(
        candidate_sha=candidate_sha,
        plan_digest=candidate_plan_digest,
        bundle_run_id=run_id,
        bundle_artifact_id=artifact_id,
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
