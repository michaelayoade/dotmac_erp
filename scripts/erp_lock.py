"""The refusals `.github/workflows/erp-lock.yml` needs, out of the YAML.

ERP must repin `dotmac-files` 0.1.0a2 -> 0.1.0a4 and `dotmac-tax`
0.1.0a3 -> 0.1.0a4. Both are published to the same PRIVATE Forgejo index that
`dotmac_vendor_control_plane`'s `kernel-lock.yml` / `scripts/kernel_lock.py`
resolve `dotmac-kernel` against, and a lock entry for a privately-published
distribution is the one part of a pin change that cannot be written by hand:
its sha256 values are facts about published artifacts, obtainable only by
resolving against the index that holds them. That workflow's shape and
reasoning are reused here; this module is not a copy of it — ERP moves TWO
packages, not one, and ERP's manifest carries a pre-existing legitimate git
dependency the kernel-lock guard would have refused outright (see
`ALLOWED_OFF_INDEX_DEPENDENCIES` below).

## The closed allowlist of movements

`ALLOWED_MOVEMENTS` names exactly two moves. A caller cannot ask this module to
move `dotmac-files` to `0.1.0a5`, or move a third package at all — that would
be a different, unreviewed change, and extending the allowlist is itself an
edit to this file that a human reviews, not a workflow input.

## The one accommodation ERP's own manifest needs

`pyproject.toml` already declares
`dotmac-integration-client = { git = "...", tag = "v0.2.0" }` — a legitimate,
pre-existing dependency that has nothing to do with the private index. The
upstream guard this module is adapted from refuses EVERY off-index dependency
form (`git`/`path`/`url`/`file`) anywhere in the manifest, on the theory that
Poetry keys HTTP credentials by source NAME and an off-index form is a vector
for redirecting them or for running unreviewed code in the resolver. That
theory is still right for anything NEW; it would also refuse ERP's own,
unrelated, already-shipped dependency, which is not a fresh finding, it is a
false positive. So the one name ERP already carries is allowlisted BY NAME,
and every other off-index form is refused exactly as upstream refuses it.

## The subjects

* `set-versions` — move both pins in the manifest, refusing unless the ref's
  OWN tree declares the exact allowed OLD version for each package, exactly
  once, and refusing again unless the rewrite lands on the exact allowed NEW
  version, exactly once — enumerated across every dependency surface Poetry
  and PEP 621 / PEP 735 accept.
* `manifest-guard` — the ref under resolution supplies the manifest; refuses
  anything that would misdirect `FORGEJO_READ_TOKEN` or hand the resolver a
  dependency form it does not recognise.
* `acquire` — download the closed bundle from the private index, in the only
  job that holds the credential. Runs no Poetry and no package code.
* `mirror-manifest` / `restore` — point the manifest at the local bundle for a
  secret-free resolution, then restore the real URL and recompute the lock's
  content-hash.
* `wheel-only` — no release in the resolution may lack a usable wheel, so no
  PEP 517 build backend can run — see `kernel_lock.wheel_only_problems`'s
  reasoning, reused verbatim; the predicate is identical.
* `verify` — the lock's hashes for BOTH moved packages are the bytes the index
  published.
* `drift` — the whole lock outside the two moved entries must be identical;
  each moved entry may differ only in `version`, `files`, and `dependencies`
  — and `dependencies` is bounded separately, by `wheel-dependencies`, to
  names the acquired wheel's own `Requires-Dist` actually declares.
* `evidence` — the manifest/lock PAIR, bound to each other and to the run, with
  a credential scan that refuses rather than passes when there is nothing
  configured to look for.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import html.parser
import json
import os
import re
from dataclasses import dataclass
import subprocess
import tomllib
import urllib.parse
import zipfile
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

# ── the closed allowlist of movements ───────────────────────────────────────

#: The ONLY movements this workflow may perform. A version outside this table
#: — for either side of either package — is refused BY NAME, not silently
#: coerced or ignored. Extending this table is a reviewed diff to this file,
#: never a workflow input.
ALLOWED_MOVEMENTS: dict[str, tuple[str, str]] = {
    "dotmac-files": ("0.1.0a2", "0.1.0a4"),
    "dotmac-tax": ("0.1.0a3", "0.1.0a4"),
}

#: The private Forgejo PyPI index this workflow resolves against. Two forms
#: because ERP itself carries two: `pyproject.toml`'s `[[tool.poetry.source]]`
#: names the URL WITH a trailing slash, and every `[package.source].url` in
#: `poetry.lock` names it WITHOUT one — Poetry normalises the two independently
#: and this module has to match each one exactly where it is read.
MANIFEST_INDEX_URL = "https://registry.dotmac.io/api/packages/dotmac/pypi/simple/"
LOCK_INDEX_URL = "https://registry.dotmac.io/api/packages/dotmac/pypi/simple"

#: The ONLY origin an index-supplied link may name, and the only path prefix
#: under it. A simple index page is INDEX-CONTROLLED data: every href on it is
#: attacker-influenced the moment the index is.
ARTIFACT_ORIGIN = "https://registry.dotmac.io"
ARTIFACT_PATH_PREFIX = "/api/packages/dotmac/pypi/"

#: The Poetry source name the credential is keyed to. `POETRY_HTTP_BASIC_
#: FORGEJO_PASSWORD` binds to this NAME, not to the URL.
INDEX_SOURCE_NAME = "forgejo"

#: The read-only Forgejo identity the credential belongs to (see
#: `.github/workflows/ci.yml`'s own `https://ci-reader:${FORGEJO_READ_TOKEN}@...`
#: usage). Used only to build the basic-auth encoding the scan looks for.
INDEX_USERNAME = "ci-reader"

#: The environment variable the workflow hands the credential to this module
#: in. Never a command-line argument, where it would appear in a process
#: listing.
CREDENTIAL_ENV = "FORGEJO_CREDENTIAL"

#: The two packages this workflow exists to move. Everything else in the lock
#: is required to be identical. These two may additionally change `version`,
#: `files` and `dependencies` — `dependencies` ONLY because a version move can
#: legitimately shift the moved package's own floor on a transitive
#: dependency (e.g. a new `dotmac-files` release raising its `dotmac-kernel`
#: floor), and that permitted freedom is bounded elsewhere, by
#: `wheel_dependency_problems`, against the acquired wheel's own
#: `Requires-Dist` — never left unchecked. `source`, `extras`, `optional`,
#: `groups`, `python-versions`, `description` and anything new stay held to
#: the same identity as every other package.
MUTABLE_PACKAGES = frozenset(ALLOWED_MOVEMENTS)
MUTABLE_FIELDS = frozenset({"version", "files", "dependencies"})

#: The two files a consumer must apply TOGETHER. The lock's content-hash is
#: derived from the manifest, so either one alone describes a tree that does
#: not exist.
PAIR = ("pyproject.toml", "poetry.lock")

#: `poetry.toml` in the project directory configures the Poetry that is about
#: to resolve it — TLS verification, binary-vs-source preference and
#: credential lookup are all settable there. Refused outright rather than
#: reasoned about, same as `kernel_lock.CANDIDATE_CONFIG_FILES`.
CANDIDATE_CONFIG_FILES = ("poetry.toml",)


#: ERP's own, pre-existing, unrelated dependency. See the module docstring's
#: "one accommodation" section for why this single name is exempt from the
#: off-index-dependency refusal and nothing else is.
@dataclass(frozen=True)
class OffIndexPin:
    """The FULL identity of one permitted off-index dependency.

    A name alone is not an enforceable premise. The exemption's premise is "this
    specific vetted dependency, from this host, at this immutable tag, which
    resolves to these bytes" -- and keying on the name enforced only the first
    clause. Changing the URL under the same name, or moving the tag to point
    somewhere else, both passed.

    `commit` is the PEELED commit of the annotated tag, which is what Poetry
    records as `resolved_reference`. A tag is a mutable pointer; the commit is
    not, so the commit is what makes this pin immutable in the same sense a
    peeled release tag is.
    """

    url: str
    tag: str
    commit: str


#: The one accommodation ERP's own manifest needs, pinned in full. Adding a
#: member, or changing any field of one, is an edit to this file that a human
#: reviews -- never a workflow input.
ALLOWED_OFF_INDEX_DEPENDENCIES: dict[str, OffIndexPin] = {
    "dotmac-integration-client": OffIndexPin(
        url="https://github.com/michaelayoade/dotmac-integration-client.git",
        tag="v0.2.0",
        commit="a4fe55f4ed704c556c4d1e3cc728ec4ef0dd8042",
    )
}

#: Keys a pinned off-index dependency may carry, and nothing else. `rev` and
#: `branch` are refused by their absence here: a branch is a mutable pointer, and
#: a `rev` beside a `tag` would give two answers to "which commit".
_OFF_INDEX_PERMITTED_KEYS = frozenset({"git", "tag"})
_RESOLVED_COMMIT = re.compile(r"\A[0-9a-f]{40}\Z")


def _normalised_repository_url(url: str) -> str:
    """Enough normalisation to compare two spellings of one repository.

    Deliberately narrow: case-folded scheme and host, a stripped trailing slash
    and a single optional `.git` suffix. It does NOT try to equate ssh and https
    forms or resolve redirects -- a spelling this does not recognise is refused
    rather than guessed at, because a guess here decides where the resolver
    reaches.
    """

    text = url.strip()
    parts = urllib.parse.urlsplit(text)
    if parts.scheme.lower() != "https" or not parts.netloc:
        return text
    path = parts.path.rstrip("/")
    if path.endswith(".git"):
        path = path[: -len(".git")]
    return urllib.parse.urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), path, "", "")
    )


def off_index_pin_problems(where: str, spec: Any, pin: OffIndexPin) -> list[str]:
    """A pinned off-index dependency declares EXACTLY its pinned identity."""

    if not isinstance(spec, dict):
        return [
            f"{where} is a {type(spec).__name__}; a pinned off-index dependency "
            "must be a table carrying `git` and `tag`"
        ]
    problems: list[str] = []
    unexpected = sorted(set(spec) - _OFF_INDEX_PERMITTED_KEYS)
    if unexpected:
        problems.append(
            f"{where} carries {', '.join(unexpected)}; a pinned off-index "
            f"dependency may carry only {sorted(_OFF_INDEX_PERMITTED_KEYS)}. A "
            "`branch` is a mutable pointer and a `rev` beside a `tag` gives two "
            "answers to which commit."
        )
    missing = sorted(_OFF_INDEX_PERMITTED_KEYS - set(spec))
    if missing:
        problems.append(f"{where} is missing {', '.join(missing)}")
        return problems
    declared_url = _normalised_repository_url(str(spec["git"]))
    expected_url = _normalised_repository_url(pin.url)
    if declared_url != expected_url:
        problems.append(
            f"{where} names repository {declared_url!r}, not the pinned "
            f"{expected_url!r}"
        )
    if str(spec["tag"]) != pin.tag:
        problems.append(
            f"{where} names tag {spec['tag']!r}, not the pinned {pin.tag!r}"
        )
    return problems


def off_index_lock_problems(lock: dict[str, Any]) -> list[str]:
    """Every pinned off-index dependency, as the LOCK resolved it.

    This is the half the manifest cannot prove. A manifest names a tag; the lock
    records what that tag RESOLVED to. Requiring `reference` to equal the pinned
    tag AND `resolved_reference` to equal the pinned commit is what makes the
    three agree -- so a tag moved upstream between the pin being reviewed and
    the lock being generated is refused rather than silently adopted.
    """

    problems: list[str] = []
    for name, pin in sorted(ALLOWED_OFF_INDEX_DEPENDENCIES.items()):
        entries = [
            entry
            for entry in lock.get("package", [])
            if _normalised(str(entry.get("name"))) == _normalised(name)
        ]
        if len(entries) != 1:
            problems.append(
                f"the lock carries {len(entries)} {name} entries, expected one"
            )
            continue
        source = entries[0].get("source")
        if not isinstance(source, dict):
            problems.append(f"the lock's {name} entry records no source table")
            continue
        if source.get("type") != "git":
            problems.append(
                f"the lock resolved {name} as {source.get('type')!r}, not git"
            )
        declared = _normalised_repository_url(str(source.get("url", "")))
        if declared != _normalised_repository_url(pin.url):
            problems.append(
                f"the lock resolved {name} from {declared!r}, not the pinned "
                f"{_normalised_repository_url(pin.url)!r}"
            )
        if source.get("reference") != pin.tag:
            problems.append(
                f"the lock's {name} reference is {source.get('reference')!r}, "
                f"not the pinned tag {pin.tag!r}"
            )
        resolved = str(source.get("resolved_reference", ""))
        if not _RESOLVED_COMMIT.match(resolved):
            problems.append(
                f"the lock's {name} resolved_reference {resolved!r} is not a "
                "40-character commit"
            )
        elif resolved != pin.commit:
            problems.append(
                f"the lock resolved {name} {pin.tag} to {resolved}, not the "
                f"pinned commit {pin.commit}; the tag moved or the pin is stale"
            )
    return problems


_CONTENT_HASH_LINE = re.compile(r'^(content-hash = ")[^"]*(")$', re.MULTILINE)

#: Constraint-table keys `poetry.core.factory.Factory.create_dependency`
#: actually reads.
RECOGNISED_CONSTRAINT_KEYS = frozenset(
    {
        "allow-prereleases",
        "branch",
        "develop",
        "extras",
        "file",
        "git",
        "markers",
        "optional",
        "path",
        "platform",
        "python",
        "rev",
        "source",
        "subdirectory",
        "tag",
        "url",
        "version",
    }
)

#: Dependency forms whose resolution reads or executes something the index
#: does not name.
OFF_INDEX_DEPENDENCY_KEYS = ("file", "git", "path", "url")

#: Every dotted path this module TRAVERSES for dependencies.
TRAVERSED_DEPENDENCY_TABLES = (
    "dependency-groups",
    "project.dependencies",
    "project.optional-dependencies",
    "tool.poetry.dependencies",
    "tool.poetry.dev-dependencies",
)

#: `tool.poetry.group.<name>` may carry these and nothing else.
RECOGNISED_GROUP_KEYS = frozenset({"dependencies", "include-groups", "optional"})

_PLAIN_CONSTRAINT = re.compile(r"^[0-9A-Za-z .,!*+<>=^~|-]+$")
_EXACT_VERSION = re.compile(r"^[0-9]+(\.[0-9]+)*((a|b|rc|\.post|\.dev)[0-9]+)?$")
_DIRECT_REFERENCE = re.compile(r"@\s*[A-Za-z][A-Za-z0-9+.-]*:")
_PEP508_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")

_PAIR_PROSE = """\
This lock and this pyproject.toml are ONE artifact. The lock's content-hash is
derived from the manifest, so applying either alone leaves a tree whose lock
does not describe its own manifest.

To verify what you downloaded, from inside the artifact directory:

  sha256sum -c SHA256SUMS

To verify the pair is the pair this run produced, and not two files from two
runs: recompute pair-binding as the sha256 of exactly these bytes, newline
terminated, in this order:

  pyproject.toml sha256:<the pyproject.toml digest above>
  poetry.lock sha256:<the poetry.lock digest above>

To verify the pair against Poetry itself, copy BOTH files over a checkout of
the ref above and run `poetry check --lock`. Applying one without the other
fails that command on the content-hash, which is the detection this binding
exists to make possible.

This workflow performed NO release and NO deployment. It committed nothing,
opened no pull request, and pushed no tag. Apply both files together in a
reviewed pull request that names this run's coordinates below.
"""


class Refusal(Exception):
    """A condition this module refuses to proceed past."""


def _load_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def _normalised(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


# ── set-versions: move both pins, or refuse ─────────────────────────────────


def _declaration_pattern(package: str) -> re.Pattern[str]:
    escaped = re.escape(package)
    # ERP writes `{version = "..."}` with NO space after the brace; the guard
    # this was adapted from required `{ version` WITH one, because that is how
    # the other repository happens to format it. Anchored on the literal spacing
    # it matched ZERO declarations here and refused ERP's own manifest on day
    # one — a guard that cannot read the tree it guards. Whitespace is therefore
    # optional around the brace and the equals signs, and nowhere else: the
    # match is still exactly one declaration or a refusal.
    return re.compile(rf'({escaped}\s*=\s*\{{\s*version\s*=\s*")[^"]+(")')


def replace_version(text: str, package: str, version: str) -> str:
    """Move ONE pin, or refuse.

    Exactly one declaration, because a manifest with two is a manifest where
    "the pin" is ambiguous, and editing the wrong one produces a lock that
    resolves a version nobody asked for while reporting success.
    """

    pattern = _declaration_pattern(package)
    edited, count = pattern.subn(rf"\g<1>{version}\g<2>", text)
    if count != 1:
        raise Refusal(
            f"expected exactly one {package} version declaration in the form "
            f'`{package} = {{ version = "..." }}`, matched {count}. Refusing '
            "rather than guessing which one the pin is."
        )
    return edited


# ── declaration enumeration: every surface, for both directions ────────────


def _poetry_constraint_tables(
    manifest: dict[str, Any],
) -> list[tuple[str, dict[str, Any]]]:
    poetry = manifest.get("tool", {}).get("poetry", {})
    if not isinstance(poetry, dict):
        return []
    tables: list[tuple[str, dict[str, Any]]] = []
    for key in ("dependencies", "dev-dependencies"):
        table = poetry.get(key)
        if isinstance(table, dict):
            tables.append((f"tool.poetry.{key}", table))
    groups = poetry.get("group", {})
    if isinstance(groups, dict):
        for name, group in sorted(groups.items()):
            if not isinstance(group, dict):
                continue
            deps = group.get("dependencies")
            if isinstance(deps, dict):
                tables.append((f"tool.poetry.group.{name}.dependencies", deps))
    return tables


def _pep508_requirement_lists(
    manifest: dict[str, Any],
) -> list[tuple[str, list[Any]]]:
    lists: list[tuple[str, list[Any]]] = []
    project = manifest.get("project", {})
    if isinstance(project, dict):
        listed = project.get("dependencies")
        if isinstance(listed, list):
            lists.append(("project.dependencies", listed))
        optional = project.get("optional-dependencies")
        if isinstance(optional, dict):
            for extra, requirements in sorted(optional.items()):
                if isinstance(requirements, list):
                    lists.append(
                        (f"project.optional-dependencies.{extra}", requirements)
                    )
    groups = manifest.get("dependency-groups", {})
    if isinstance(groups, dict):
        for name, requirements in sorted(groups.items()):
            if isinstance(requirements, list):
                lists.append((f"dependency-groups.{name}", requirements))
    return lists


def declarations_of(manifest: dict[str, Any], package: str) -> list[str]:
    """Every location that declares `package`, across every surface.

    Point 4 of the workflow's contract: enumerate `tool.poetry.dependencies`,
    `tool.poetry.dev-dependencies`, `tool.poetry.group.*.dependencies`, PEP 621
    `project.dependencies` / `optional-dependencies`, and PEP 735
    `dependency-groups`, and require EXACTLY ONE across all of them — two
    declarations let the validated one and the rewritten one differ.
    """

    found: list[str] = []
    for table, deps in _poetry_constraint_tables(manifest):
        if package in deps:
            found.append(f"{table}.{package}")
    target = _normalised(package)
    for location, requirements in _pep508_requirement_lists(manifest):
        for index, requirement in enumerate(requirements):
            if not isinstance(requirement, str):
                continue
            match = _PEP508_NAME.match(requirement)
            if match and _normalised(match.group(1)) == target:
                found.append(f"{location}[{index}]")
    return found


def declared_version(manifest: dict[str, Any], package: str) -> str | None:
    """The version a SINGLE `tool.poetry.dependencies`-shaped declaration
    names. Returns `None` for a bare-string or PEP 508 declaration; those are
    still counted by `declarations_of`, they just have no `version` key for
    this function to read."""

    for _table, deps in _poetry_constraint_tables(manifest):
        spec = deps.get(package)
        if isinstance(spec, dict):
            return spec.get("version")
        if isinstance(spec, str):
            return spec
    return None


def movement_problems(manifest: dict[str, Any], expect: dict[str, str]) -> list[str]:
    """Every declaration of every package in `expect` names EXACTLY the given
    version, declared EXACTLY once. Used both before the edit (expect =
    old versions) and after it (expect = new versions)."""

    problems: list[str] = []
    for package, version in sorted(expect.items()):
        locations = declarations_of(manifest, package)
        if len(locations) != 1:
            problems.append(
                f"{package}: expected exactly one declaration, found "
                f"{len(locations)} ({', '.join(locations) or 'none'})"
            )
            continue
        actual = declared_version(manifest, package)
        if actual != version:
            problems.append(
                f"{package}: declared {actual!r} at {locations[0]}, expected "
                f"{version!r}"
            )
    return problems


# ── manifest-guard: the candidate checkout ──────────────────────────────────


def checkout_problems(root: Path) -> list[str]:
    problems: list[str] = []
    for name in CANDIDATE_CONFIG_FILES:
        if (root / name).exists():
            problems.append(
                f"the checkout under resolution carries `{name}`; that file "
                "configures the Poetry that is about to resolve it. The ref "
                "supplies the manifest, never the configuration."
            )
    return problems


# ── manifest-guard: the dependency traversal ────────────────────────────────


def _walk_keys(node: Any, prefix: str = "") -> Iterator[str]:
    if not isinstance(node, dict):
        return
    for key, value in node.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        yield path
        yield from _walk_keys(value, path)


def _is_traversed(path: str) -> bool:
    if path in TRAVERSED_DEPENDENCY_TABLES:
        return True
    parts = path.split(".")
    if parts[:3] == ["tool", "poetry", "group"] and len(parts) == 5:
        return parts[4] == "dependencies"
    return any(
        path.startswith(f"{table}.") for table in TRAVERSED_DEPENDENCY_TABLES
    ) or (parts[:3] == ["tool", "poetry", "group"] and len(parts) > 5)


def unrecognised_dependency_tables(manifest: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    for path in _walk_keys(manifest):
        if "dependenc" not in path.rsplit(".", 1)[-1]:
            continue
        if _is_traversed(path):
            continue
        problems.append(
            f"`{path}` names dependencies and is not a form this guard "
            "traverses. Refusing rather than resolving something unexamined."
        )
    return problems


def _constraint_problems(where: str, spec: Any) -> list[str]:
    problems: list[str] = []
    if isinstance(spec, list):
        for index, item in enumerate(spec):
            problems += _constraint_problems(f"{where}[{index}]", item)
        return problems
    if isinstance(spec, str):
        if not _PLAIN_CONSTRAINT.fullmatch(spec):
            problems.append(
                f"{where} is the constraint {spec!r}, which is not a plain "
                "version constraint; a bare Poetry constraint may not carry a "
                "scheme, an authority or a direct reference"
            )
        return problems
    if not isinstance(spec, dict):
        problems.append(
            f"{where} is a {type(spec).__name__}, which is not a dependency "
            "shape this guard recognises"
        )
        return problems

    for key in sorted(set(spec) - RECOGNISED_CONSTRAINT_KEYS):
        problems.append(
            f"{where} carries the unrecognised key `{key}`; this guard refuses "
            "a dependency form it does not understand rather than assuming it "
            "is harmless"
        )
    package = where.rsplit(".", 1)[-1].split("[", 1)[0]
    pin = ALLOWED_OFF_INDEX_DEPENDENCIES.get(package)
    if pin is None:
        for key in OFF_INDEX_DEPENDENCY_KEYS:
            if key in spec:
                problems.append(
                    f"{where} is a `{key}` dependency; resolving it reads or "
                    "executes something the index does not name"
                )
    source = spec.get("source")
    if source is not None and source != INDEX_SOURCE_NAME:
        problems.append(
            f"{where} resolves from source {source!r}, which this job does not "
            "hold a credential for"
        )
    return problems


def _requirement_problems(where: str, requirement: Any) -> list[str]:
    if isinstance(requirement, dict):
        if set(requirement) == {"include-group"}:
            return []
        return [
            f"{where} is a table with keys {sorted(requirement)}; the only "
            "table PEP 735 defines here is `include-group`"
        ]
    if not isinstance(requirement, str):
        return [
            f"{where} is a {type(requirement).__name__}, not a PEP 508 "
            "requirement string"
        ]
    match = _PEP508_NAME.match(requirement)
    name = match.group(1) if match else ""
    if name in ALLOWED_OFF_INDEX_DEPENDENCIES:
        # A PEP 508 requirement string cannot carry a `git`/`tag` table, so a
        # pinned dependency appearing in this form is not the pinned form.
        return [
            f"{where} names {name}, which is pinned as an off-index table "
            "dependency; a PEP 508 requirement string cannot express its tag "
            "and resolved commit"
        ]
    if _DIRECT_REFERENCE.search(requirement) or "://" in requirement:
        return [
            f"{where} is the direct reference {requirement!r}; a PEP 508 URL "
            "reaches outside the index exactly as a `url`, `file`, `path` or "
            "`git` table does"
        ]
    return []


def _group_shape_problems(manifest: dict[str, Any]) -> list[str]:
    poetry = manifest.get("tool", {}).get("poetry", {})
    groups = poetry.get("group", {}) if isinstance(poetry, dict) else {}
    problems: list[str] = []
    if not isinstance(groups, dict):
        return problems
    for name, group in sorted(groups.items()):
        if not isinstance(group, dict):
            problems.append(f"tool.poetry.group.{name} is not a table")
            continue
        for key in sorted(set(group) - RECOGNISED_GROUP_KEYS):
            problems.append(
                f"tool.poetry.group.{name} carries the unrecognised key "
                f"`{key}`; a group form this guard does not understand is "
                "refused, not resolved"
            )
    return problems


def dependency_problems(manifest: dict[str, Any]) -> list[str]:
    """Every dependency, in every form Poetry and PEP 621/735 accept, judged.

    `ALLOWED_OFF_INDEX_DEPENDENCIES` is the one exemption from the off-index
    refusal below — see the module docstring's "one accommodation" section.
    """

    problems = unrecognised_dependency_tables(manifest)
    problems += _group_shape_problems(manifest)

    for table, deps in _poetry_constraint_tables(manifest):
        for name, spec in sorted(deps.items()):
            pin = ALLOWED_OFF_INDEX_DEPENDENCIES.get(name)
            if pin is not None:
                # NOT skipped. The previous version continued here, so the one
                # exempted dependency was the only one in the manifest that
                # nothing examined at all. It is now held to its full pinned
                # identity instead.
                problems += off_index_pin_problems(f"{table}.{name}", spec, pin)
                continue
            problems += _constraint_problems(f"{table}.{name}", spec)

    for location, requirements in _pep508_requirement_lists(manifest):
        for index, requirement in enumerate(requirements):
            problems += _requirement_problems(f"{location}[{index}]", requirement)

    return problems


def manifest_problems(
    manifest: dict[str, Any], target_versions: dict[str, str]
) -> list[str]:
    """Everything about this manifest that would misdirect the credential.

    `target_versions` is the NEW version expected for each moved package,
    e.g. `{"dotmac-files": "0.1.0a4", "dotmac-tax": "0.1.0a4"}`.
    """

    problems: list[str] = []
    sources = manifest.get("tool", {}).get("poetry", {}).get("source", [])
    if not isinstance(sources, list):
        sources = []

    named = [entry for entry in sources if entry.get("name") == INDEX_SOURCE_NAME]
    if len(named) != 1:
        problems.append(
            f"expected exactly one `[[tool.poetry.source]]` named "
            f"{INDEX_SOURCE_NAME!r}, found {len(named)}"
        )
    for entry in named:
        url = str(entry.get("url", ""))
        if url != MANIFEST_INDEX_URL:
            problems.append(
                f"source {INDEX_SOURCE_NAME!r} points at {url!r}, not "
                f"{MANIFEST_INDEX_URL!r} — the credential is keyed to the "
                "NAME, so this would send it to that host"
            )
    for entry in sources:
        name = str(entry.get("name", "?"))
        if name == INDEX_SOURCE_NAME:
            continue
        problems.append(
            f"unexpected `[[tool.poetry.source]]` {name!r} at "
            f"{entry.get('url', '?')!r}; this job resolves against one index"
        )

    problems += dependency_problems(manifest)

    if "requires-plugins" in manifest.get("tool", {}).get("poetry", {}):
        problems.append(
            "`tool.poetry.requires-plugins` is declared; Poetry installs and "
            "imports plugins before it resolves, which is arbitrary code in "
            "the step that holds the credential"
        )

    for package, version in sorted(target_versions.items()):
        table_entries = dict(_poetry_constraint_tables(manifest))
        spec = None
        for deps in table_entries.values():
            if package in deps:
                spec = deps[package]
                break
        if spec is None:
            problems.append(f"no {package} dependency to resolve")
        elif isinstance(spec, dict):
            if spec.get("version") != version:
                problems.append(
                    f"{package} declares {spec.get('version')!r} after the "
                    f"edit, asked for {version!r}"
                )
            if spec.get("source") != INDEX_SOURCE_NAME:
                problems.append(
                    f"{package} resolves from {spec.get('source')!r}, not "
                    f"{INDEX_SOURCE_NAME!r}"
                )
        else:
            problems.append(
                f"{package} is declared as a bare constraint, so nothing "
                f"binds it to the {INDEX_SOURCE_NAME!r} index"
            )
    return problems


# ── the index is data: every link it supplies is validated ──────────────────


class _Links(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        for key, value in attrs:
            if key == "href" and value:
                self.hrefs.append(value)


def index_links(page: str) -> list[str]:
    parser = _Links()
    parser.feed(page)
    return parser.hrefs


def approved_artifact_url(href: str, page_url: str) -> str:
    """Resolve an index-supplied href, or refuse it. See
    `kernel_lock.approved_artifact_url` for the full reasoning; the predicate
    is identical, only the approved origin/prefix constants differ per repo
    (here, they do not — same private index)."""

    if not href.strip():
        raise Refusal("the index page carries an empty href")
    resolved = urllib.parse.urljoin(page_url, href.split("#", 1)[0])
    parts = urllib.parse.urlsplit(resolved)
    if parts.scheme != "https":
        raise Refusal(
            f"index link {href!r} resolves to {parts.scheme or '(none)'}://, "
            "not https; the credential is never offered over a scheme that "
            "does not authenticate the server"
        )
    if "@" in parts.netloc:
        raise Refusal(
            f"index link {href!r} carries userinfo in its authority, which is "
            "both a credential and a host-spoofing surface"
        )
    approved = urllib.parse.urlsplit(ARTIFACT_ORIGIN)
    authority = approved.netloc.lower()
    if parts.netloc.lower() not in {authority, f"{authority}:443"}:
        raise Refusal(
            f"index link {href!r} resolves to origin {parts.netloc!r}, not "
            f"{approved.netloc!r}; refusing to point an authenticated transfer "
            "at a host the index chose"
        )
    if ".." in parts.path.split("/"):
        raise Refusal(f"index link {href!r} still traverses after resolution")
    if not parts.path.startswith(ARTIFACT_PATH_PREFIX):
        raise Refusal(
            f"index link {href!r} resolves to path {parts.path!r}, which is "
            f"not under {ARTIFACT_PATH_PREFIX!r} — a `..` chain that walks out "
            "of the package directory lands here"
        )
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def curl_argv(url: str, target: Path) -> list[str]:
    return [
        "curl",
        "--netrc",
        "--proto",
        "=https",
        "--max-redirs",
        "0",
        "--silent",
        "--show-error",
        "-w",
        "%{http_code}",
        "-o",
        str(target),
        url,
    ]


def transfer_problems(url: str, status: str) -> list[str]:
    if status == "200":
        return []
    if status.startswith("3"):
        return [
            f"{url} answered {status}: a redirect. Redirects are NOT followed "
            "here — a followed redirect is a second destination the index "
            "chose, and `curl --netrc` would offer the credential to it if its "
            "host matched the netrc entry. Refusing."
        ]
    return [f"{url} answered {status}, not 200"]


def fetch(url: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(  # noqa: S603
        curl_argv(url, target),
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise Refusal(f"curl failed for {url}: {completed.stderr.strip()}")
    problems = transfer_problems(url, completed.stdout.strip())
    if problems:
        raise Refusal(problems[0])


# ── acquire: a closed bundle, downloaded once, by the only job with a key ───


def artifact_belongs_to(filename: str, package: str, version: str) -> bool:
    prefix = f"{_normalised(package).replace('-', '_')}-{version}"
    stem = _normalised(filename.split("-")[0]) if "-" in filename else ""
    if stem and stem != _normalised(package):
        return False
    return filename.lower().startswith(prefix.lower()) and (
        filename[len(prefix) : len(prefix) + 1] in {"-", "."}
    )


def acquisition_plan(
    manifest: dict[str, Any], lock: dict[str, Any], target_versions: dict[str, str]
) -> dict[str, str]:
    """Which private packages the bundle must CLOSE over, and at which
    version. See `kernel_lock.acquisition_plan` for the full reasoning."""

    plan: dict[str, str] = {}
    for table, deps in _poetry_constraint_tables(manifest):
        for name, spec in sorted(deps.items()):
            if not isinstance(spec, dict):
                continue
            if spec.get("source") != INDEX_SOURCE_NAME:
                continue
            version = spec.get("version")
            if not isinstance(version, str) or not _EXACT_VERSION.fullmatch(version):
                raise Refusal(
                    f"{table}.{name} resolves from {INDEX_SOURCE_NAME!r} with "
                    f"the constraint {version!r}. The offline bundle can only "
                    "be closed around exact pins; a range would need this "
                    "module to decide what it resolves to."
                )
            plan[name] = version

    for entry in lock.get("package", []):
        source = entry.get("source", {})
        if not isinstance(source, dict):
            continue
        if source.get("reference") != INDEX_SOURCE_NAME:
            continue
        name = str(entry.get("name"))
        plan.setdefault(name, str(entry.get("version")))

    for package in ALLOWED_MOVEMENTS:
        if package not in plan:
            raise Refusal(f"no {package} dependency bound to {INDEX_SOURCE_NAME!r}")
    plan.update(target_versions)
    return plan


def artifact_names(package: str, version: str) -> set[str]:
    stem = package.replace("-", "_")
    return {f"{stem}-{version}-py3-none-any.whl", f"{stem}-{version}.tar.gz"}


# ── bounding `dependencies` to the wheel's own metadata, no credential ──────
#
# Point 7's second half: for the two moved packages, `dependencies` is one of
# the fields a pin move may change, but it should not be free to say anything.
# The wheel ITSELF (already downloaded, already hashed, no further credential
# needed) carries its own `Requires-Dist` in `*.dist-info/METADATA`, and that
# is a fact about the artifact independent of the private index. This compares
# NAMES only, not full PEP 508 specifiers — Poetry's lock constraint syntax and
# a wheel's raw `Requires-Dist` syntax are not the same grammar, and comparing
# them field-for-field would be this module re-deriving Poetry's own resolver
# logic. Comparing the two NAME SETS is still a real bound: an entry naming a
# dependency the wheel never declares, or omitting one the wheel unconditionally
# requires, is refused.


def wheel_requires_dist(wheel_path: Path) -> list[str]:
    """Every `Requires-Dist` line in the wheel's own `METADATA`, as published."""

    with zipfile.ZipFile(wheel_path) as archive:
        metadata_name = next(
            (
                name
                for name in archive.namelist()
                if name.endswith(".dist-info/METADATA")
            ),
            None,
        )
        if metadata_name is None:
            raise Refusal(f"{wheel_path.name} carries no `.dist-info/METADATA`")
        text = archive.read(metadata_name).decode("utf-8", errors="replace")
    return [
        line.split(":", 1)[1].strip()
        for line in text.splitlines()
        if line.lower().startswith("requires-dist:")
    ]


def unconditional_requirement_names(requirements: Iterable[str]) -> set[str]:
    """Every distribution named by an UNCONDITIONAL `Requires-Dist` entry.

    An `extra ==` marker names an optional-extra dependency, which Poetry's
    lock only carries when that extra is actually pulled in — comparing it
    against the base `[package.dependencies]` table would be comparing two
    different questions. A plain environment marker (`python_version`,
    `sys_platform`, ...) still names an unconditional distribution, just one
    Poetry may or may not have resolved for THIS environment; excluding those
    too would silently widen what this bound catches, so only `extra ==` is
    treated as conditional here.
    """

    names: set[str] = set()
    for requirement in requirements:
        marker = requirement.split(";", 1)[1] if ";" in requirement else ""
        if "extra" in marker:
            continue
        match = _PEP508_NAME.match(requirement)
        if match:
            names.add(_normalised(match.group(1)))
    return names


def wheel_dependency_problems(
    package: str, lock_dependencies: dict[str, Any], requires_dist: Iterable[str]
) -> list[str]:
    """The lock's `[package.dependencies]` NAMES, against the wheel's own
    unconditional `Requires-Dist` NAMES. Bounded to names, not full
    constraints — see the section header above for why."""

    wheel_names = unconditional_requirement_names(requires_dist)
    lock_names = {
        _normalised(name) for name in lock_dependencies if name.lower() != "python"
    }
    problems: list[str] = []
    missing_from_lock = sorted(wheel_names - lock_names)
    extra_in_lock = sorted(lock_names - wheel_names)
    if missing_from_lock:
        problems.append(
            f"{package}: the wheel's own Requires-Dist names {missing_from_lock}, "
            "absent from the lock's [package.dependencies]"
        )
    if extra_in_lock:
        problems.append(
            f"{package}: the lock's [package.dependencies] names {extra_in_lock}, "
            "which the wheel's own Requires-Dist never declares"
        )
    return problems


# ── wheel-only: the resolver is never handed something it must build ────────
#
# See `kernel_lock.wheel_only_problems` for the full mechanism this is copied
# from unchanged: the predicate is a property of Poetry 2.4.1's
# `HTTPRepository._get_info_from_links`, not of ERP, and the pinned version is
# the same one.

_WHEEL_FILENAME = re.compile(
    r"^(?P<namever>(?P<name>.+?)-(?P<ver>\d[^-]*))"
    r"(-(?P<build>\d[^-]*))?"
    r"-(?P<pyver>[^-]+)"
    r"-(?P<abi>[^-]+)"
    r"-(?P<plat>[^-]+)"
    r"\.whl$"
)


def parseable_wheels(filenames: Iterable[str]) -> list[str]:
    return sorted(name for name in filenames if _WHEEL_FILENAME.match(name))


def wheel_only_problems(
    offers: Iterable[tuple[str, str, Iterable[str]]],
) -> list[str]:
    problems: list[str] = []
    for name, version, filenames in offers:
        files = sorted(filenames)
        if not files:
            problems.append(
                f"{name} {version} offers no published files at all. Nothing "
                "here can say where its metadata would come from, and a "
                "release with no files is not an index release — it is a "
                "directory, a git clone or a URL, every one of which resolves "
                "by running a build backend."
            )
        elif not parseable_wheels(files):
            problems.append(
                f"{name} {version} offers {files} and not one usable wheel "
                "among them. Resolution would have to take its metadata from "
                "the source distribution, which unpacks the archive and, when "
                "PKG-INFO carries no `Requires-Dist`, EXECUTES that project's "
                "PEP 517 build backend inside this job. A dependency with no "
                "usable wheel is refused; there is no fallback that builds it."
            )
    return problems


def lock_wheel_problems(lock: dict[str, Any]) -> list[str]:
    return wheel_only_problems(
        (
            str(entry.get("name")),
            str(entry.get("version")),
            [str(item.get("file")) for item in entry.get("files", [])],
        )
        for entry in lock.get("package", [])
    )


def acquire(plan: dict[str, str], out: Path) -> dict[str, str]:
    """Download the closed bundle and lay it out as a local PEP 503 index.
    Runs in the ONLY job that holds the credential, and runs no Poetry and no
    package code: `curl`, `sha256`, and writing HTML."""

    files_dir = out / "files"
    simple_dir = out / "simple"
    files_dir.mkdir(parents=True, exist_ok=True)
    digests: dict[str, str] = {}

    for package, version in sorted(plan.items()):
        page = out / "pages" / f"{package}.html"
        fetch(f"{LOCK_INDEX_URL}/{package}/", page)
        wanted: dict[str, str] = {}
        for href in index_links(page.read_text(encoding="utf-8", errors="replace")):
            url = approved_artifact_url(href, f"{LOCK_INDEX_URL}/{package}/")
            filename = url.rsplit("/", 1)[-1]
            if artifact_belongs_to(filename, package, version):
                wanted[filename] = url
        if not wanted:
            raise Refusal(
                f"{package} {version} is not published on the index. This is "
                "a refusal in its own right: a resolver error later would "
                "report it as a lock problem rather than as 'it was never "
                "published', which is the fact that matters."
            )
        offered = wheel_only_problems([(package, version, list(wanted))])
        if offered:
            raise Refusal(offered[0])
        if package in ALLOWED_MOVEMENTS:
            expected = artifact_names(package, version)
            if set(wanted) != expected:
                raise Refusal(
                    f"the index publishes {sorted(wanted)} for {package} "
                    f"{version}; this workflow pins the pair {sorted(expected)}"
                )
        for filename, url in sorted(wanted.items()):
            target = files_dir / filename
            fetch(url, target)
            digests[filename] = sha256_hex(target.read_bytes())

        if package in ALLOWED_MOVEMENTS:
            wheel_names = parseable_wheels(wanted)
            if wheel_names:
                requires = wheel_requires_dist(files_dir / sorted(wheel_names)[0])
                requires_dir = out / "requires"
                requires_dir.mkdir(parents=True, exist_ok=True)
                (requires_dir / f"{package}.json").write_text(
                    json.dumps(requires, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )

        page_dir = simple_dir / package
        page_dir.mkdir(parents=True, exist_ok=True)
        rows = "\n".join(
            f'    <a href="../../files/{name}#sha256={digests[name]}">{name}</a><br>'
            for name in sorted(wanted)
        )
        page_dir.joinpath("index.html").write_text(
            f"<!DOCTYPE html>\n<html><body>\n{rows}\n</body></html>\n",
            encoding="utf-8",
        )

    (out / "digests.json").write_text(
        json.dumps(digests, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return digests


# ── the mirror swap, and putting the real URL back ──────────────────────────


def point_at_mirror(manifest_text: str, mirror_url: str) -> str:
    if manifest_text.count(MANIFEST_INDEX_URL) != 1:
        raise Refusal(
            f"expected exactly one {MANIFEST_INDEX_URL!r} in the manifest, "
            f"found {manifest_text.count(MANIFEST_INDEX_URL)}"
        )
    return manifest_text.replace(MANIFEST_INDEX_URL, mirror_url)


def restore_index_url(
    text: str, mirror_url: str, real_url: str, *, required: bool = True
) -> str:
    if required and mirror_url not in text:
        raise Refusal(f"{mirror_url!r} does not appear; nothing to restore")
    return text.replace(mirror_url, real_url)


def set_content_hash(lock_text: str, digest: str) -> str:
    edited, count = _CONTENT_HASH_LINE.subn(rf"\g<1>{digest}\g<2>", lock_text)
    if count != 1:
        raise Refusal(
            f"expected exactly one `content-hash` line in the lock, matched {count}"
        )
    return edited


def poetry_content_hash(manifest: Path, lock: Path) -> str:
    """Poetry's OWN content hash for a manifest, computed offline. See
    `kernel_lock.poetry_content_hash` for the full reasoning."""

    try:
        from poetry.packages.locker import Locker
    except ImportError as error:  # pragma: no cover - environment-dependent
        raise Refusal(
            "Poetry is not importable, so its content hash cannot be asked "
            f"for: {error}"
        ) from error
    locker = Locker(lock, _load_toml(manifest))
    digest = getattr(locker, "_content_hash", None)
    if not isinstance(digest, str) or not digest:
        raise Refusal(
            "Poetry's Locker did not yield a content hash; its internals have "
            "moved and this step must be rewritten against the new shape "
            "rather than guessed at"
        )
    return digest


# ── verify: the lock's hashes are the index's bytes ─────────────────────────


def hash_problems(
    lock: dict[str, Any], digests: dict[str, str], target_versions: dict[str, str]
) -> list[str]:
    problems: list[str] = []
    for package, version in sorted(target_versions.items()):
        entries = [e for e in lock.get("package", []) if e.get("name") == package]
        if len(entries) != 1:
            problems.append(
                f"the lock carries {len(entries)} {package} entries, expected one"
            )
            continue
        entry = entries[0]
        if entry.get("version") != version:
            problems.append(
                f"lock resolved {package} {entry.get('version')!r}, asked for "
                f"{version!r}"
            )
        expected = artifact_names(package, version)
        locked = {f["file"]: f["hash"] for f in entry.get("files", [])}
        if set(locked) != expected:
            problems.append(
                f"the {package} entry names {sorted(locked)}; the pair this "
                f"workflow verifies is {sorted(expected)}"
            )
            continue
        for name, digest in locked.items():
            if name not in digests:
                problems.append(
                    f"{package}: the lock names {name!r}, which is not a file "
                    "this run downloaded from the index"
                )
                continue
            if digest != f"sha256:{digests[name]}":
                problems.append(
                    f"{name}: lock says {digest!r}, the published bytes hash "
                    f"to sha256:{digests[name]}"
                )
    return problems


# ── drift ────────────────────────────────────────────────────────────────────


def _entries(lock: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    found: dict[tuple[str, str], dict[str, Any]] = {}
    for entry in lock.get("package", []):
        key = (str(entry.get("name")), str(entry.get("version")))
        if key in found:
            raise Refusal(f"the lock carries two entries for {key}")
        found[key] = entry
    if not found:
        raise Refusal("the lock has no packages at all")
    return found


def _changed_fields(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    return sorted(
        field
        for field in set(before) | set(after)
        if before.get(field) != after.get(field)
    )


def _moved_entry_problems(
    package: str,
    old: dict[tuple[str, str], dict[str, Any]],
    new: dict[tuple[str, str], dict[str, Any]],
) -> list[str]:
    old_entry = {key: value for key, value in old.items() if key[0] == package}
    new_entry = {key: value for key, value in new.items() if key[0] == package}
    if not new_entry:
        return [f"the resolved lock has no {package} entry"]
    if len(new_entry) != 1 or len(old_entry) != 1:
        return [
            f"expected exactly one {package} entry on each side, found "
            f"{len(old_entry)} before and {len(new_entry)} after"
        ]
    if old_entry == new_entry:
        return [f"{package} did not move; this lock says nothing"]

    before = next(iter(old_entry.values()))
    after = next(iter(new_entry.values()))
    problems = [
        f"{package} changed `{field}`, which a pin move may not change: "
        f"{before.get(field)!r} -> {after.get(field)!r}"
        for field in _changed_fields(before, after)
        if field not in MUTABLE_FIELDS
    ]
    source = after.get("source")
    if isinstance(source, dict) and source.get("url") != LOCK_INDEX_URL:
        problems.append(
            f"{package} resolved from {source.get('url')!r}, not {LOCK_INDEX_URL!r}"
        )
    return problems


def drift_problems(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """Everything outside the two moved pins that is not identical."""

    problems: list[str] = []
    old = _entries(before)
    new = _entries(after)

    old_other = {
        key: value for key, value in old.items() if key[0] not in MUTABLE_PACKAGES
    }
    new_other = {
        key: value for key, value in new.items() if key[0] not in MUTABLE_PACKAGES
    }

    for name, version in sorted(set(new_other) - set(old_other)):
        problems.append(f"{name} {version} appeared")
    for name, version in sorted(set(old_other) - set(new_other)):
        problems.append(f"{name} {version} disappeared")
    for key in sorted(set(old_other) & set(new_other)):
        fields = _changed_fields(old_other[key], new_other[key])
        if fields:
            problems.append(f"{key[0]} {key[1]} changed {', '.join(fields)}")

    for package in sorted(MUTABLE_PACKAGES):
        problems += _moved_entry_problems(package, old, new)

    old_meta = before.get("metadata", {})
    new_meta = after.get("metadata", {})
    for field in sorted(set(old_meta) | set(new_meta)):
        if field == "content-hash":
            continue
        if old_meta.get(field) != new_meta.get(field):
            problems.append(
                f"lock metadata `{field}` changed: "
                f"{old_meta.get(field)!r} -> {new_meta.get(field)!r}"
            )
    old_hash = old_meta.get("content-hash")
    new_hash = new_meta.get("content-hash")
    if not old_hash or not new_hash:
        problems.append("a lock is missing `metadata.content-hash` entirely")
    elif old_hash == new_hash:
        problems.append(
            "the content-hash is unchanged, so the manifest edit never reached "
            "the lock and this lock does not describe the edited manifest"
        )

    for table in sorted((set(before) | set(after)) - {"package", "metadata"}):
        if before.get(table) != after.get(table):
            problems.append(f"the lock's top-level `{table}` table changed")
    return problems


# ── evidence ─────────────────────────────────────────────────────────────────


def credential_encodings(credential: str) -> dict[str, str]:
    if not credential:
        raise Refusal(
            f"{CREDENTIAL_ENV} is unset or empty. There is nothing to scan the "
            "evidence for, so this scan cannot say the evidence is clean."
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


def credential_sightings(paths: Iterable[Path], credential: str) -> list[str]:
    forms = credential_encodings(credential)
    found: list[str] = []
    for path in sorted(paths):
        text = path.read_text(encoding="utf-8", errors="replace")
        for label, form in forms.items():
            if form in text:
                found.append(f"{path.name}: {label}")
    return found


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def pair_binding(digests: dict[str, str]) -> str:
    missing = [name for name in PAIR if name not in digests]
    if missing:
        raise Refusal(f"cannot bind the pair without {', '.join(missing)}")
    body = "".join(f"{name} sha256:{digests[name]}\n" for name in PAIR)
    return sha256_hex(body.encode("utf-8"))


def sha256sums(digests: dict[str, str]) -> str:
    return "".join(f"{digests[name]}  {name}\n" for name in sorted(digests))


def coordinates_text(
    coordinates: dict[str, str],
    digests: dict[str, str],
    content_hash: str,
    binding: str,
) -> str:
    rows: list[tuple[str, str]] = list(coordinates.items())
    rows.append(("", ""))
    rows += [(f"sha256:{name}", digest) for name, digest in sorted(digests.items())]
    rows.append(("pair-binding", binding))
    rows.append(("lock-content-hash", content_hash))
    width = max(len(label) for label, _ in rows)
    lines = [
        f"{label.ljust(width)}  {value}".rstrip() if label else ""
        for label, value in rows
    ]
    return "\n".join(lines) + "\n\n" + _PAIR_PROSE


def build_evidence(
    out: Path,
    manifest: Path,
    lock: Path,
    coordinates: dict[str, str],
    credential: str,
) -> list[str]:
    out.mkdir(parents=True, exist_ok=True)
    written: dict[str, bytes] = {
        "pyproject.toml": manifest.read_bytes(),
        "poetry.lock": lock.read_bytes(),
    }
    for name, payload in written.items():
        (out / name).write_bytes(payload)

    digests = {name: sha256_hex(payload) for name, payload in written.items()}
    with (out / "poetry.lock").open("rb") as handle:
        content_hash = str(tomllib.load(handle)["metadata"]["content-hash"])
    (out / "coordinates.txt").write_text(
        coordinates_text(coordinates, digests, content_hash, pair_binding(digests)),
        encoding="utf-8",
    )
    everything = {
        path.name: sha256_hex(path.read_bytes())
        for path in out.iterdir()
        if path.is_file()
    }
    (out / "SHA256SUMS").write_text(sha256sums(everything), encoding="utf-8")
    return credential_sightings(
        [path for path in out.iterdir() if path.is_file()], credential
    )


# ── CLI ──────────────────────────────────────────────────────────────────────


def _report(subject: str, problems: list[str]) -> int:
    if problems:
        for problem in problems:
            print(f"::error::{subject}: {problem}")
        return 1
    print(f"{subject}: clean")
    return 0


def _parse_movement_args(values: list[str]) -> dict[str, str]:
    """`--movement name=version` (repeatable) -> a dict, refusing any name not
    in `ALLOWED_MOVEMENTS` and any value that is not that movement's `new`."""

    result: dict[str, str] = {}
    for item in values:
        if "=" not in item:
            raise Refusal(f"--movement must be name=version, got {item!r}")
        name, version = item.split("=", 1)
        if name not in ALLOWED_MOVEMENTS:
            raise Refusal(
                f"{name} is not an allowed movement; the only allowed "
                f"movements are {sorted(ALLOWED_MOVEMENTS)}"
            )
        _old, new = ALLOWED_MOVEMENTS[name]
        if version != new:
            raise Refusal(
                f"{name} version {version!r} is not an allowed movement; the "
                f"only allowed movement for {name} is "
                f"{ALLOWED_MOVEMENTS[name][0]!r} -> {new!r}"
            )
        result[name] = version
    if set(result) != set(ALLOWED_MOVEMENTS):
        raise Refusal(
            f"expected a --movement for each of {sorted(ALLOWED_MOVEMENTS)}, "
            f"got {sorted(result)}"
        )
    return result


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    subcommands = parser.add_subparsers(dest="command", required=True)

    edit = subcommands.add_parser("set-versions")
    edit.add_argument("--manifest", type=Path, required=True)
    edit.add_argument("--movement", action="append", default=[], dest="movements")

    guard = subcommands.add_parser("manifest-guard")
    guard.add_argument("--manifest", type=Path, required=True)
    guard.add_argument("--checkout", type=Path, required=True)
    guard.add_argument("--movement", action="append", default=[], dest="movements")

    fetch_bundle = subcommands.add_parser("acquire")
    fetch_bundle.add_argument("--manifest", type=Path, required=True)
    fetch_bundle.add_argument("--lock", type=Path, required=True)
    fetch_bundle.add_argument(
        "--movement", action="append", default=[], dest="movements"
    )
    fetch_bundle.add_argument("--out", type=Path, required=True)

    aim = subcommands.add_parser("mirror-manifest")
    aim.add_argument("--manifest", type=Path, required=True)
    aim.add_argument("--mirror-url", required=True)

    restore = subcommands.add_parser("restore")
    restore.add_argument("--manifest", type=Path, required=True)
    restore.add_argument("--lock", type=Path, required=True)
    restore.add_argument("--mirror-url", required=True)

    verify = subcommands.add_parser("verify")
    verify.add_argument("--lock", type=Path, required=True)
    verify.add_argument("--digests", type=Path, required=True)
    verify.add_argument("--movement", action="append", default=[], dest="movements")

    wheel_only = subcommands.add_parser("wheel-only")
    wheel_only.add_argument("--lock", type=Path, required=True)

    wheel_deps = subcommands.add_parser("wheel-dependencies")
    wheel_deps.add_argument("--lock", type=Path, required=True)
    wheel_deps.add_argument("--requires-dir", type=Path, required=True)

    drift = subcommands.add_parser("drift")
    drift.add_argument("--before", type=Path, required=True)
    drift.add_argument("--after", type=Path, required=True)

    evidence = subcommands.add_parser("evidence")
    evidence.add_argument("--out", type=Path, required=True)
    evidence.add_argument("--manifest", type=Path, required=True)
    evidence.add_argument("--lock", type=Path, required=True)
    evidence.add_argument("--coordinate", action="append", default=[])
    return parser


def _run(args: argparse.Namespace) -> int:
    if args.command == "set-versions":
        target_versions = _parse_movement_args(args.movements)
        old_versions = {name: pair[0] for name, pair in ALLOWED_MOVEMENTS.items()}
        text = args.manifest.read_text(encoding="utf-8")
        before_problems = movement_problems(tomllib.loads(text), old_versions)
        if before_problems:
            for problem in before_problems:
                print(
                    f"::error::the ref does not declare the expected OLD "
                    f"versions: {problem}"
                )
            return 1
        for package, version in sorted(target_versions.items()):
            text = replace_version(text, package, version)
        after_problems = movement_problems(tomllib.loads(text), target_versions)
        if after_problems:
            for problem in after_problems:
                print(
                    f"::error::the rewrite did not land on the expected NEW "
                    f"versions: {problem}"
                )
            return 1
        args.manifest.write_text(text, encoding="utf-8")
        for package, version in sorted(target_versions.items()):
            print(f"{package} -> {version}")
        return 0
    if args.command == "manifest-guard":
        target_versions = _parse_movement_args(args.movements)
        return _report(
            "the tree under resolution",
            checkout_problems(args.checkout)
            + manifest_problems(_load_toml(args.manifest), target_versions),
        )
    if args.command == "acquire":
        target_versions = _parse_movement_args(args.movements)
        plan = acquisition_plan(
            _load_toml(args.manifest), _load_toml(args.lock), target_versions
        )
        for package, version in sorted(plan.items()):
            print(f"bundling {package} {version}")
        digests = acquire(plan, args.out)
        for name, digest in sorted(digests.items()):
            print(f"{name}  {digest}")
        return 0
    if args.command == "mirror-manifest":
        args.manifest.write_text(
            point_at_mirror(args.manifest.read_text(encoding="utf-8"), args.mirror_url),
            encoding="utf-8",
        )
        print(f"the private source now points at {args.mirror_url}")
        return 0
    if args.command == "restore":
        args.manifest.write_text(
            restore_index_url(
                args.manifest.read_text(encoding="utf-8"),
                args.mirror_url,
                MANIFEST_INDEX_URL,
            ),
            encoding="utf-8",
        )
        args.lock.write_text(
            restore_index_url(
                args.lock.read_text(encoding="utf-8"),
                args.mirror_url,
                LOCK_INDEX_URL,
                required=False,
            ),
            encoding="utf-8",
        )
        digest = poetry_content_hash(args.manifest, args.lock)
        args.lock.write_text(
            set_content_hash(args.lock.read_text(encoding="utf-8"), digest),
            encoding="utf-8",
        )
        print(f"the index URL is restored and the content-hash is {digest}")
        return 0
    if args.command == "verify":
        target_versions = _parse_movement_args(args.movements)
        digests = json.loads(args.digests.read_text(encoding="utf-8"))
        return _report(
            "the lock against the published bytes",
            hash_problems(_load_toml(args.lock), digests, target_versions),
        )
    if args.command == "wheel-only":
        return _report(
            "the resolution is wheel-only",
            lock_wheel_problems(_load_toml(args.lock)),
        )
    if args.command == "wheel-dependencies":
        lock = _load_toml(args.lock)
        problems: list[str] = []
        for package in sorted(ALLOWED_MOVEMENTS):
            requires_file = args.requires_dir / f"{package}.json"
            if not requires_file.exists():
                problems.append(
                    f"{package}: no acquired Requires-Dist recorded at "
                    f"{requires_file}; the bundle did not carry a wheel for it"
                )
                continue
            requires = json.loads(requires_file.read_text(encoding="utf-8"))
            entries = [e for e in lock.get("package", []) if e.get("name") == package]
            if len(entries) != 1:
                problems.append(
                    f"{package}: the lock carries {len(entries)} entries, expected one"
                )
                continue
            deps = entries[0].get("dependencies", {})
            problems += wheel_dependency_problems(package, deps, requires)
        return _report(
            "[package.dependencies] against the wheel's own metadata", problems
        )
    if args.command == "drift":
        return _report(
            "unrelated lock drift",
            drift_problems(_load_toml(args.before), _load_toml(args.after)),
        )
    if args.command != "evidence":
        raise Refusal(f"unknown command {args.command!r}")
    credential = os.environ.get(CREDENTIAL_ENV, "")
    coordinates = dict(item.split("=", 1) for item in args.coordinate if "=" in item)
    sightings = build_evidence(
        args.out, args.manifest, args.lock, coordinates, credential
    )
    return _report("the credential reached the evidence", sightings)


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        return _run(args)
    except Refusal as refusal:
        print(f"::error::{refusal}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
