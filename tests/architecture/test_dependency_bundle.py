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
import inspect
import json
import os
import subprocess
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


# ── total classification over EVERY lock entry, including lock-only ones ──


def test_an_injected_transitive_git_lock_entry_is_refused(tmp_path: Path) -> None:
    """Plants the exact defect the closure redesign closes: a `git`-sourced
    `[[package]]` entry with no dependency edge from ANY approved off-index
    root -- an injected, unreachable VCS package a candidate lock could add
    on its own. It used to hit `_lock_packages`'s unconditional `continue`
    (it does not "look private": no forgejo reference, no forgejo host) and
    vanish from both the refusal surface and the digest. It must now be
    refused by name."""

    injected_lock = (
        _OFF_INDEX_LOCK
        + """
[[package]]
name = "evil-transitive"
version = "1.0"
python-versions = ">=3.11"
groups = ["main"]
files = []

[package.source]
type = "git"
url = "https://evil.example.com/evil.git"
reference = "main"
resolved_reference = \""""
        + ("d" * 40)
        + """"
"""
    )
    root = _project_root(tmp_path, _OFF_INDEX_MANIFEST, injected_lock)
    with pytest.raises(
        db.ManifestError, match="not part of the proven transitive closure"
    ):
        db.extract_dependency_surface(root, _GOOD_PIN)


def _reachable_transitive_lock(resolved_reference: str) -> str:
    lock_with_edge = _OFF_INDEX_LOCK.replace(
        'resolved_reference = "a4fe55f4ed704c556c4d1e3cc728ec4ef0dd8042"',
        'resolved_reference = "a4fe55f4ed704c556c4d1e3cc728ec4ef0dd8042"\n\n'
        "[package.dependencies]\n"
        'some-transitive-lib = "^1.0"',
    )
    return (
        lock_with_edge
        + f"""
[[package]]
name = "some-transitive-lib"
version = "1.0"
python-versions = ">=3.11"
groups = ["main"]
files = []

[package.source]
type = "git"
url = "https://github.com/michaelayoade/some-transitive-lib.git"
reference = "main"
resolved_reference = "{resolved_reference}"
"""
    )


def test_a_transitive_dependency_of_an_approved_off_index_root_is_admitted(
    tmp_path: Path,
) -> None:
    """The near-miss half of the closure proof: a `git`-sourced lock entry
    IS admitted, without refusal, when it is reachable from an approved
    off-index root's own `[package.dependencies]` edge -- the exact shape
    the injected-entry test above shows is refused when that edge is
    absent. It is recorded, by its own identity, as an
    `OffIndexTransitiveDependency` -- admission alone, with no record, is
    the residual gap the digest-sensitivity test below closes."""

    root = _project_root(
        tmp_path, _OFF_INDEX_MANIFEST, _reachable_transitive_lock("e" * 40)
    )
    surface = db.extract_dependency_surface(root, _GOOD_PIN)
    assert len(surface.off_index_dependencies) == 1, (
        "the transitive closure member must be admitted silently -- it is "
        "not itself a manifest-declared off-index dependency"
    )
    assert len(surface.off_index_transitive_dependencies) == 1
    transitive = surface.off_index_transitive_dependencies[0]
    assert transitive.normalised_name == "some-transitive-lib"
    assert transitive.resolved_commit == "e" * 40


def test_an_admitted_transitive_off_index_dependencys_identity_moves_the_digest(
    tmp_path: Path,
) -> None:
    """Michael's ruling: an admitted transitive member's identity must
    contribute to the digest exactly as an approved root's does, or two
    different transitive off-index states could share one digest -- the
    same semantic-collision defect the digest exists to prevent. Measured
    before/after, holding everything else fixed: only the ADMITTED
    transitive member's resolved commit changes."""

    before_root = _project_root(
        tmp_path / "before", _OFF_INDEX_MANIFEST, _reachable_transitive_lock("e" * 40)
    )
    after_root = _project_root(
        tmp_path / "after", _OFF_INDEX_MANIFEST, _reachable_transitive_lock("f" * 40)
    )
    before = db.compute_plan_digest(
        db.extract_dependency_surface(before_root, _GOOD_PIN)
    )
    after = db.compute_plan_digest(db.extract_dependency_surface(after_root, _GOOD_PIN))
    assert before != after, (
        f"an admitted transitive off-index dependency's resolved commit "
        f"changed but the digest did not: before={before} after={after}"
    )


def test_an_admitted_transitive_off_index_dependency_does_not_make_the_digest_hypersensitive(
    tmp_path: Path,
) -> None:
    """The other half of the sensitivity proof: adding the new
    `off_index_transitive_dependencies` field must not make the digest
    move for something it should not move for. `[package.dependencies]`
    key ORDER is exactly such a case -- Poetry's own TOML writer output
    order is not semantically significant, and `build_plan_document`
    canonicalises it via `dict.items()`'s own deterministic (insertion)
    order for the `dependencies` sub-mapping regardless of how many extra
    unrelated keys the source table carries; what must NOT move the
    digest here is which of two textually-different-but-semantically-
    identical single-dependency edges produced the SAME admitted closure
    member -- covered by asserting the same transitive lock built via
    `_reachable_transitive_lock` twice, independently, produces the same
    digest both times (no hidden nondeterminism from `frozenset`/`dict`
    iteration order in `_off_index_transitive_closure` or
    `_classify_and_admit_lock_entries`)."""

    root_a = _project_root(
        tmp_path / "a", _OFF_INDEX_MANIFEST, _reachable_transitive_lock("e" * 40)
    )
    root_b = _project_root(
        tmp_path / "b", _OFF_INDEX_MANIFEST, _reachable_transitive_lock("e" * 40)
    )
    digest_a = db.compute_plan_digest(db.extract_dependency_surface(root_a, _GOOD_PIN))
    digest_b = db.compute_plan_digest(db.extract_dependency_surface(root_b, _GOOD_PIN))
    assert digest_a == digest_b, (
        "the identical transitive lock shape must produce the identical "
        f"digest deterministically: a={digest_a} b={digest_b}"
    )


# ── the enumeration invariant: classification is total over IDENTITIES,
# not merely over raw entries -- a normalised name keying a collection is
# sound for COMPARISON and unsound for ENUMERATION, and this whole block
# proves the specific place that distinction was crossed. ─────────────────


def _evil_git_package(
    name: str, resolved_reference: str, *, host: str = "evil.example.com"
) -> str:
    return f"""
[[package]]
name = "{name}"
version = "1.0"
python-versions = ">=3.11"
groups = ["main"]
files = []

[package.source]
type = "git"
url = "https://{host}/evil.git"
reference = "main"
resolved_reference = "{resolved_reference}"
"""


def test_a_duplicate_lock_identity_pair_is_refused_hyphen_spelling_first(
    tmp_path: Path,
) -> None:
    """The last-wins collapse this closes: two `[[package]]` entries whose
    names normalise to the SAME identity (`evil-transitive` /
    `evil_transitive`) -- neither forgejo, neither an approved off-index
    root -- used to collapse into one `entries_by_identity` dict slot,
    silently dropping whichever entry lost the collision from
    classification entirely. Both spelling orders are planted (this test
    and its sibling below) because the defect was ORDER-DEPENDENT
    (last-wins): a fix that only refuses one order would pass a test that
    only plants that order.

    DESIGNED BREAK CONDITION: reverting the identity-uniqueness check in
    `_lock_packages` (or its own independent twin in
    `_classify_and_admit_lock_entries`) makes this raise nothing -- one of
    the two entries silently vanishes from classification instead.
    """

    lock = (
        BASE_LOCK
        + _evil_git_package("evil-transitive", "d" * 40)
        + _evil_git_package("evil_transitive", "e" * 40)
    )
    root = _project_root(tmp_path, BASE_PYPROJECT, lock)
    with pytest.raises(db.ManifestError, match="normalise to the same identity"):
        db.extract_dependency_surface(root)


def test_a_duplicate_lock_identity_pair_is_refused_underscore_spelling_first(
    tmp_path: Path,
) -> None:
    """The opposite ordering of the test above -- see its docstring for
    why both orders are planted independently rather than sharing one."""

    lock = (
        BASE_LOCK
        + _evil_git_package("evil_transitive", "e" * 40)
        + _evil_git_package("evil-transitive", "d" * 40)
    )
    root = _project_root(tmp_path, BASE_PYPROJECT, lock)
    with pytest.raises(db.ManifestError, match="normalise to the same identity"):
        db.extract_dependency_surface(root)


def _lock_with_injected_forgejo_reuse(*, injected_first: bool) -> str:
    dotmac_kernel_entry = """
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
"""
    injected_entry = _evil_git_package("dotmac_kernel", "f" * 40)
    requests_entry = """
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
"""
    metadata = """
[metadata]
lock-version = "2.1"
python-versions = ">=3.11,<3.13"
content-hash = "0000000000000000000000000000000000000000000000000000000000000000"
"""
    ordered = (
        [requests_entry, injected_entry, dotmac_kernel_entry]
        if injected_first
        else [requests_entry, dotmac_kernel_entry, injected_entry]
    )
    return "".join(ordered) + metadata


def test_a_git_entry_reusing_the_forgejo_identity_is_refused_when_injected_before_it(
    tmp_path: Path,
) -> None:
    """The sharper shape of the same defect: a `git`-sourced entry from an
    ARBITRARY host, reusing the real forgejo package's own identity
    (`dotmac_kernel` normalises to the same identity as `dotmac-kernel`).
    Before this repair, the admission loop skipped ANY entry whose
    identity was already in `forgejo_lock_identities` as "already
    classified" -- identity membership alone was wrongly treated as proof
    that the entry currently being looked at was the real forgejo one.

    DESIGNED BREAK CONDITION: without the identity-uniqueness refusal,
    this injected entry is silently skipped as "already classified" —
    neither refused nor admitted — and `compute_plan_digest` would be
    unaffected by its presence.
    """

    root = _project_root(
        tmp_path,
        BASE_PYPROJECT,
        _lock_with_injected_forgejo_reuse(injected_first=True),
    )
    with pytest.raises(db.ManifestError, match="normalise to the same identity"):
        db.extract_dependency_surface(root)


def test_a_git_entry_reusing_the_forgejo_identity_is_refused_when_injected_after_it(
    tmp_path: Path,
) -> None:
    """The opposite ordering of the test above."""

    root = _project_root(
        tmp_path,
        BASE_PYPROJECT,
        _lock_with_injected_forgejo_reuse(injected_first=False),
    )
    with pytest.raises(db.ManifestError, match="normalise to the same identity"):
        db.extract_dependency_surface(root)


def _lock_with_injected_off_index_root_reuse(*, injected_first: bool) -> str:
    root_entry = """
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
    injected_entry = _evil_git_package("dotmac_integration_client", "c" * 40)
    parts = (
        [injected_entry, root_entry] if injected_first else [root_entry, injected_entry]
    )
    return BASE_LOCK + "".join(parts)


def test_a_git_entry_reusing_an_approved_off_index_roots_identity_is_refused_when_injected_before_it(
    tmp_path: Path,
) -> None:
    """Point 6's vector: a `git` entry from an ARBITRARY host colliding
    with an APPROVED OFF-INDEX ROOT's identity, not a forgejo one --
    `dotmac_integration_client` normalises to the same identity as the
    real `dotmac-integration-client` root.

    UNLIKE the forgejo-identity-reuse pair above, this exact vector was
    ALREADY refused upstream, before this repair existed:
    `_verify_off_index_lock_entry` (which runs before
    `_classify_and_admit_lock_entries`, and whose own `matches` list
    comprehension normalises EVERY candidate's name via
    `normalise_name_for_identity` and collects ALL of them, not just the
    first) already requires `len(matches) == 1` for the approved root's
    own identity, and raises "must have exactly one poetry.lock entry,
    found 2" for two same-identity entries. This test does not plant a
    previously-unguarded hole; it pins that pre-existing refusal and
    proves the new positional classification path does not weaken it --
    the refusal now actually observed here comes from `_lock_packages`'s
    own duplicate-identity check, which runs earlier still and produces a
    DIFFERENT message than `_verify_off_index_lock_entry`'s.

    DESIGNED BREAK CONDITION: reverting BOTH `_lock_packages`'s and
    `_classify_and_admit_lock_entries`'s duplicate-identity refusals still
    leaves this refused, by `_verify_off_index_lock_entry` -- but with a
    message that does not contain "normalise to the same identity", so
    this test's `match=` stops hitting and it fails, proving the
    positional repair is still the thing this test is sensitive to, even
    though it is not the ONLY thing standing between this vector and
    admission.
    """

    root = _project_root(
        tmp_path,
        _OFF_INDEX_MANIFEST,
        _lock_with_injected_off_index_root_reuse(injected_first=True),
    )
    with pytest.raises(db.ManifestError, match="normalise to the same identity"):
        db.extract_dependency_surface(root, _GOOD_PIN)


def test_a_git_entry_reusing_an_approved_off_index_roots_identity_is_refused_when_injected_after_it(
    tmp_path: Path,
) -> None:
    """The opposite ordering of the test above; see its docstring for why
    this vector was already refused upstream and what this test actually
    pins."""

    root = _project_root(
        tmp_path,
        _OFF_INDEX_MANIFEST,
        _lock_with_injected_off_index_root_reuse(injected_first=False),
    )
    with pytest.raises(db.ManifestError, match="normalise to the same identity"):
        db.extract_dependency_surface(root, _GOOD_PIN)


def test_the_classification_loop_accounts_for_every_outcome_type(
    tmp_path: Path,
) -> None:
    """The positional conservation invariant's CLEAN-TREE half: a lock
    exercising every outcome bucket in one pass -- an ordinary public
    entry (`requests`), an already-classified forgejo entry
    (`dotmac-kernel`), an already-classified approved off-index root
    (`dotmac-integration-client`), and an admitted transitive member
    (`some-transitive-lib`) -- must extract successfully without tripping
    `_classify_and_admit_lock_entries`'s internal `disposition`
    accounting (every position must land in exactly one bucket; none may
    stay `None`, none may be recorded twice). This proves the accounting
    does not FALSELY fire on legitimate, fully-classified input.

    The 'fires on a genuinely missing or duplicated disposition' half is a
    DESIGN CLAIM, not an executed test: today's classification loop has
    no code path that reaches the bottom of an iteration without
    recording exactly one outcome via `_record` or aborting the function
    outright via `raise` -- every branch is already exhaustive by
    construction, which is precisely what the positional check exists to
    keep true for a change that has not been written yet. Tried, as a
    design check, against this exact implementation: a plausible new
    branch for an unhandled `source_type` (e.g. `"hg"`) that appends to
    `admitted` and `continue`s without calling `_record` trips the
    trailing `unclassified` check immediately, because `disposition[i]`
    stays `None`; a branch that calls `_record` and then falls through to
    another `_record` call for the same position (a forgotten `continue`)
    trips `_record`'s own already-set check immediately. Neither escapes
    unnoticed.
    """

    root = _project_root(
        tmp_path, _OFF_INDEX_MANIFEST, _reachable_transitive_lock("e" * 40)
    )
    surface = db.extract_dependency_surface(root, _GOOD_PIN)
    assert len(surface.off_index_transitive_dependencies) == 1


def test_classify_and_admit_lock_entries_refuses_a_duplicate_identity_even_when_called_directly(
    tmp_path: Path,
) -> None:
    """Defense-in-depth proof: `_classify_and_admit_lock_entries` refuses
    a duplicate identity ITSELF, not merely because `_lock_packages`
    already refused it upstream. Calls the function directly with a hand-
    built `lock` dict that never passed through `_lock_packages` at all,
    so this cannot be satisfied by the upstream refusal -- only the
    function's own `first_seen_at` check can catch it here.

    DESIGNED BREAK CONDITION: removing `_classify_and_admit_lock_entries`'s
    own duplicate-identity check (while leaving `_lock_packages`'s intact)
    would not change this test's normal `extract_dependency_surface`
    callers at all -- they would still be refused upstream -- but this
    DIRECT call would then raise nothing, silently returning a tuple that
    accounts for only one of the two same-identity entries.
    """

    lock = {
        "package": [
            {
                "name": "evil-transitive",
                "version": "1.0",
                "source": {
                    "type": "git",
                    "url": "https://evil.example.com/evil.git",
                    "reference": "main",
                    "resolved_reference": "d" * 40,
                },
            },
            {
                "name": "evil_transitive",
                "version": "1.0",
                "source": {
                    "type": "git",
                    "url": "https://evil.example.com/evil.git",
                    "reference": "main",
                    "resolved_reference": "e" * 40,
                },
            },
        ]
    }
    with pytest.raises(db.ManifestError, match="normalise to the same identity"):
        db._classify_and_admit_lock_entries(lock, frozenset(), ())


def test_an_unsupported_lock_source_type_is_refused(tmp_path: Path) -> None:
    """Total classification's third bucket: a lock entry whose source is
    neither public (no source, or an ordinary non-forgejo `legacy` index),
    forgejo, nor `git` at all -- this module has no policy for any other
    source `type` and refuses it outright rather than silently letting it
    through as though it were public."""

    unsupported_lock = (
        _OFF_INDEX_LOCK
        + """
[[package]]
name = "local-directory-dep"
version = "1.0"
python-versions = ">=3.11"
groups = ["main"]
files = []

[package.source]
type = "directory"
url = "../local-directory-dep"
"""
    )
    root = _project_root(tmp_path, _OFF_INDEX_MANIFEST, unsupported_lock)
    with pytest.raises(db.ManifestError, match="unsupported lock source type"):
        db.extract_dependency_surface(root, _GOOD_PIN)


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
    """A test fixture standing in for an already-verified `RunMetadata` —
    the deliberate, visible bypass `_RUN_METADATA_PROVENANCE_TOKEN`'s own
    docstring names: reaching for this module-private sentinel by name is
    what makes constructing one outside `verify_run_metadata` an
    unmistakable act rather than something a caller can do by accident
    just by matching every field's shape."""

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
        _provenance_token=db._RUN_METADATA_PROVENANCE_TOKEN,
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


def _write_archive(
    tmp_path: Path, acquired: dict[str, Path], name: str = "bundle.zip"
) -> Path:
    """Build a GENUINE ZIP archive containing every `acquired` file under
    its real member name and real bytes.

    This replaces an earlier fixture that wrote non-ZIP "pretend outer
    archive bytes" and expected `create_bundle_manifest` to accept it --
    which it always did, because construction only hashed the archive's
    own bytes and never opened it to check what was inside (finding 5).
    That made the archive-closure property unreachable by every test using
    the old fixture: a real bug there could never have made any of them
    fail. Building a real archive here is what makes the closure check
    below actually exercised by the existing "happy path" tests, and see
    `test_create_bundle_manifest_refuses_a_non_zip_archive` and its
    neighbours below for the fixtures that plant the specific archive/
    acquired-file mismatches the check must refuse.
    """

    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as archive:
        for member_name, source_path in acquired.items():
            archive.writestr(member_name, source_path.read_bytes())
    return path


def test_create_bundle_manifest_computes_plan_digest_and_sizes(tmp_path: Path) -> None:
    surface = _manifest_test_surface()
    wheel_path = _write_wheel(tmp_path)
    archive_path = _write_archive(tmp_path, {_MANIFEST_TEST_WHEEL_NAME: wheel_path})
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
    archive_path = _write_archive(tmp_path, {})
    with pytest.raises(db.BundleVerificationError, match="not acquired"):
        db.create_bundle_manifest(
            surface=surface, acquired_files={}, archive_path=archive_path, run=_run()
        )


def test_create_bundle_manifest_refuses_an_unaccounted_for_acquired_member(
    tmp_path: Path,
) -> None:
    surface = _manifest_test_surface()
    wheel_path = _write_wheel(tmp_path)
    extra_path = _write_bytes(tmp_path, "extra-unplanned-file.whl", b"extra")
    acquired = {
        _MANIFEST_TEST_WHEEL_NAME: wheel_path,
        "extra-unplanned-file.whl": extra_path,
    }
    archive_path = _write_archive(tmp_path, acquired)
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
    archive_path = _write_archive(tmp_path, {_MANIFEST_TEST_WHEEL_NAME: wrong_path})
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
    acquired_a = {
        _MANIFEST_TEST_WHEEL_NAME: wheel_a_path,
        _SECOND_MANIFEST_TEST_WHEEL_NAME: wheel_b_path,
    }
    archive_path = _write_archive(tmp_path, acquired_a)

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


# ── finding 5: construction-time closure must cover the ARCHIVE ──────────
# (previously `create_bundle_manifest` only hashed `archive_path`'s own
# bytes; it never checked the archive actually CONTAINED the acquired
# files with matching names, sizes, and digests -- and the fixture used
# throughout this file passed non-ZIP "pretend outer archive bytes", so
# this property was never reachable by any existing test.)


def test_create_bundle_manifest_refuses_a_non_zip_archive(tmp_path: Path) -> None:
    """The exact historic fixture bug this finding traces to: every test
    above used to write non-ZIP "pretend outer archive bytes" and
    `create_bundle_manifest` accepted them, because construction only
    hashed the outer bytes and never opened the archive to see what, if
    anything, was inside. That is now refused outright.

    DESIGNED BREAK CONDITION: if `create_bundle_manifest` is reverted to
    hash `archive_path` without opening it as a ZIP, this test fails --
    the manifest would be produced instead of refused, exactly reproducing
    the fixture-masked gap finding 5 named.
    """

    surface = _manifest_test_surface()
    wheel_path = _write_wheel(tmp_path)
    archive_path = _write_bytes(tmp_path, "bundle.zip", b"pretend outer archive bytes")
    with pytest.raises(db.BundleVerificationError, match="ZIP archive"):
        db.create_bundle_manifest(
            surface=surface,
            acquired_files={_MANIFEST_TEST_WHEEL_NAME: wheel_path},
            archive_path=archive_path,
            run=_run(),
        )


def test_create_bundle_manifest_refuses_an_archive_missing_an_acquired_member(
    tmp_path: Path,
) -> None:
    """A genuine ZIP that simply does not contain the acquired file at all
    must be refused -- pairing a real archive with an unrelated file set is
    exactly the "unusable supposedly closed artifact" finding 5 describes.

    DESIGNED BREAK CONDITION: if the archive-membership check is removed
    (construction goes back to trusting `archive_path`'s outer digest
    alone), this test fails, because a manifest naming a member the
    archive does not contain would be produced instead of refused.
    """

    surface = _manifest_test_surface()
    wheel_path = _write_wheel(tmp_path)
    archive_path = tmp_path / "bundle.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("unrelated-file.txt", b"nothing to do with the plan")
    with pytest.raises(db.BundleVerificationError, match="does not contain"):
        db.create_bundle_manifest(
            surface=surface,
            acquired_files={_MANIFEST_TEST_WHEEL_NAME: wheel_path},
            archive_path=archive_path,
            run=_run(),
        )


def test_create_bundle_manifest_refuses_an_archive_member_size_mismatch(
    tmp_path: Path,
) -> None:
    """A member present under the RIGHT name but the WRONG size -- the
    archive names the acquired file but does not actually carry the same
    bytes that were read and hashed from disk.

    DESIGNED BREAK CONDITION: without the per-member size cross-check,
    this test fails, because an archive whose member disagrees in size
    with the acquired file would still be accepted.
    """

    surface = _manifest_test_surface()
    wheel_path = _write_wheel(tmp_path)
    archive_path = tmp_path / "bundle.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(
            _MANIFEST_TEST_WHEEL_NAME, _MANIFEST_TEST_WHEEL_BYTES + b"more"
        )
    with pytest.raises(db.BundleVerificationError, match="declares size"):
        db.create_bundle_manifest(
            surface=surface,
            acquired_files={_MANIFEST_TEST_WHEEL_NAME: wheel_path},
            archive_path=archive_path,
            run=_run(),
        )


def test_create_bundle_manifest_refuses_an_archive_member_content_mismatch(
    tmp_path: Path,
) -> None:
    """A member present under the right name and the SAME size, but
    different CONTENT -- the size cross-check alone would not catch this;
    only a content-digest comparison does.

    DESIGNED BREAK CONDITION: without the per-member content-digest
    cross-check (as opposed to only the size check above), this test
    fails, because same-size-but-different-content archive bytes would
    still be accepted and paired with the acquired file's real digest in
    the manifest.
    """

    surface = _manifest_test_surface()
    wheel_path = _write_wheel(tmp_path)
    same_size_different_bytes = bytes((b + 1) % 256 for b in _MANIFEST_TEST_WHEEL_BYTES)
    assert len(same_size_different_bytes) == len(_MANIFEST_TEST_WHEEL_BYTES)
    assert same_size_different_bytes != _MANIFEST_TEST_WHEEL_BYTES
    archive_path = tmp_path / "bundle.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(_MANIFEST_TEST_WHEEL_NAME, same_size_different_bytes)
    with pytest.raises(db.BundleVerificationError, match="content digest"):
        db.create_bundle_manifest(
            surface=surface,
            acquired_files={_MANIFEST_TEST_WHEEL_NAME: wheel_path},
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


#: A FULLY shape-valid `run` record -- extract_verified_bundle now checks
#: every one of these fields itself (the finding this closes: it used to
#: read only `members`), so every manifest fixture below must carry a
#: complete one, not an empty placeholder `{}`.
_VALID_MANIFEST_RUN: dict = {
    "repository_full_name": "michaelayoade/dotmac_erp",
    "repository_id": 1141216651,
    "workflow_path": ".github/workflows/dependency-bundle-produce.yml",
    "run_id": 111,
    "run_attempt": 1,
    "trusted_workflow_sha": "a" * 40,
    "artifact_id": 222,
    "artifact_name": "erp-dependency-bundle-x",
    "artifact_run_id": 111,
    "environment_name": "forgejo-registry-read-main",
}


def _manifest_for(members: dict[str, bytes]) -> dict:
    return {
        "schema_version": 2,
        "plan_digest": "a" * 64,
        "archive_sha256": "b" * 64,
        "members": {
            name: {
                "sha256": db.sha256_hex(data),
                "size": len(data),
                "package": "dotmac-kernel",
            }
            for name, data in members.items()
        },
        "run": dict(_VALID_MANIFEST_RUN),
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


# ── extraction's own filesystem calls must also be translated: the
# already-fixed inner extraction/hashing try/except never covered
# dest_dir.parent.mkdir or tempfile.mkdtemp, which run BEFORE it ────────


def test_extraction_refuses_when_its_parent_directory_cannot_be_created(
    tmp_path: Path,
) -> None:
    """Parent-directory creation (`dest_dir.parent.mkdir(...)`) sat OUTSIDE
    this function's translation boundary -- a raw `OSError`
    (`NotADirectoryError`, from a path component that is actually a file)
    used to escape straight past callers that expect only
    `ExtractionError`/`BundleVerificationError`.

    DESIGNED BREAK CONDITION: removing the try/except wrapping
    `dest_dir.parent.mkdir(...)` reintroduces the raw `OSError`, which
    `pytest.raises(db.ExtractionError)` below does not catch.
    """

    members = {"a.whl": b"AAAA"}
    archive = _make_zip(tmp_path, members)
    blocking_file = tmp_path / "not-a-directory"
    blocking_file.write_bytes(b"this is a file, not a directory")
    dest = blocking_file / "nested" / "out"
    with pytest.raises(db.ExtractionError, match="cannot create parent directory"):
        db.extract_verified_bundle(archive, dest, _manifest_for(members))


def test_extraction_refuses_when_the_staging_directory_cannot_be_created(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`tempfile.mkdtemp` sat unwrapped between the (also-fixed) parent-
    directory creation immediately above it and the try/except that
    already protects extraction and hashing -- a raw `OSError` from
    `mkdtemp` itself (no space, no permission, a directory raced away)
    would have escaped this function untranslated. `dest_dir.parent`
    genuinely exists here (unlike the test above), isolating this call
    site from the parent-creation one.

    DESIGNED BREAK CONDITION: removing the try/except around the
    `tempfile.mkdtemp(...)` call reintroduces the raw `OSError`, which
    `pytest.raises(db.ExtractionError)` below does not catch.
    """

    members = {"a.whl": b"AAAA"}
    archive = _make_zip(tmp_path, members)
    dest = tmp_path / "out"

    def _raise(*args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
        raise OSError("simulated: cannot create staging directory")

    monkeypatch.setattr(db.tempfile, "mkdtemp", _raise)
    with pytest.raises(db.ExtractionError, match="cannot create a staging directory"):
        db.extract_verified_bundle(archive, dest, _manifest_for(members))


def _mutate(manifest: dict, mutation) -> dict:  # noqa: ANN001
    mutated = json.loads(json.dumps(manifest))
    mutation(mutated)
    return mutated


@pytest.mark.parametrize(
    "reason,mutation",
    [
        (
            "wrong schema_version",
            lambda m: m.__setitem__("schema_version", 1),
        ),
        (
            "missing schema_version",
            lambda m: m.__delitem__("schema_version"),
        ),
        (
            "non-hex plan_digest",
            lambda m: m.__setitem__("plan_digest", "not-hex"),
        ),
        (
            "missing plan_digest",
            lambda m: m.__delitem__("plan_digest"),
        ),
        (
            "non-hex archive_sha256",
            lambda m: m.__setitem__("archive_sha256", "not-hex"),
        ),
        (
            "run is not a dict",
            lambda m: m.__setitem__("run", "not-a-dict"),
        ),
        (
            "run missing a required field",
            lambda m: m["run"].__delitem__("run_id"),
        ),
        (
            "run has a negative coordinate",
            lambda m: m["run"].__setitem__("run_id", -1),
        ),
        (
            "run has the null trusted_workflow_sha",
            lambda m: m["run"].__setitem__("trusted_workflow_sha", "0" * 40),
        ),
        (
            "a member is missing its package field",
            lambda m: m["members"]["a.whl"].__delitem__("package"),
        ),
    ],
)
def test_extract_verified_bundle_refuses_every_malformed_manifest_field(
    tmp_path: Path, reason: str, mutation
) -> None:
    """Sensitivity proof for the finding this closes: extract_verified_bundle
    used to read ONLY `members`, silently ignoring `schema_version`,
    `plan_digest`, `archive_sha256`, `run`, and each member's own
    `package` field. Plants a defect in each, one at a time, holding
    everything else fixed at a fully valid manifest -- see
    test_a_clean_bundle_extracts_and_publishes_atomically for the
    near-miss half (the same shape, unmutated, still succeeds)."""

    members = {"a.whl": b"AAAA"}
    archive = _make_zip(tmp_path, members)
    manifest = _mutate(_manifest_for(members), mutation)
    with pytest.raises(db.BundleVerificationError):
        db.extract_verified_bundle(archive, tmp_path / "out", manifest)
    assert not (tmp_path / "out").exists(), reason


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
    manifest = _manifest_for({"a.whl": b"AAAA", "./a.whl": b"BBBB"})
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
    manifest["members"]["missing.whl"] = {
        "sha256": "c" * 64,
        "size": 10,
        "package": "dotmac-kernel",
    }
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
    markup into that page.

    The fixture carries `<`, `>`, `&`, and `"` but deliberately NO `/` --
    a later finding-3 repair (see
    `test_build_local_index_refuses_a_filename_containing_a_path_separator`,
    which already independently proves the separator-refusal property)
    makes `build_local_index` refuse any filename containing a path
    separator before it ever reaches HTML generation. The original
    fixture here was `'inject"><script>alert(1)</script>.whl'`, whose
    closing `</script>` tag contains a `/`; once the separator refusal
    landed, that fixture could no longer reach this test's assertions at
    all -- `build_local_index` raised first, `index.html` was never
    written, and this test died on the `build_local_index` call rather
    than proving anything about escaping. This is the third instance on
    this branch of a fixture that exercised its target property only
    incidentally, so tightening a neighbouring rule silently disabled it."""

    source_dir = tmp_path / "source"
    source_dir.mkdir()
    filename = 'inject"><script>alert(1)<script>.whl'
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


# ── finding 6: local-index filenames must be URL-quoted, not merely
# HTML-escaped, before they reach a resolver-facing href ─────────────────
# (`html.escape` and `urllib.parse.quote` solve two different problems: one
# stops a filename from breaking out of the HTML attribute/text context,
# the other stops it from being reinterpreted as part of the URL's own
# grammar once a resolver actually requests the href.)


def test_build_local_index_url_encodes_a_hash_character_in_the_href(
    tmp_path: Path,
) -> None:
    """`html.escape` does not touch `#`, `?`, or `%` -- none of those are
    HTML metacharacters. A filename like `pkg#x.whl` therefore used to
    produce the RAW href `pkg#x.whl#sha256=...`; a URL fragment (`#...`)
    is never sent to the server, so a resolver following that link would
    request `pkg`, not the staged file `pkg#x.whl`.

    DESIGNED BREAK CONDITION: if the href goes back to being built from
    bare `html.escape(filename, ...)` instead of
    `urllib.parse.quote(filename, safe="")`, this test fails -- the href's
    path segment (everything before `#sha256=`) would be `pkg` instead of
    the percent-encoded real filename.
    """

    source_dir = tmp_path / "source"
    source_dir.mkdir()
    filename = "pkg#x.whl"
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
    href_path_segment = html_text.split('href="', 1)[1].split("#sha256=", 1)[0]
    assert href_path_segment == urllib.parse.quote(filename, safe=""), (
        "the href's path segment must be the PERCENT-ENCODED real filename, "
        "not truncated at a literal '#' inside the filename"
    )
    assert (index_root / "simple" / "dotmac-kernel" / filename).is_file()


def test_build_local_index_url_encodes_a_literal_percent_in_the_href(
    tmp_path: Path,
) -> None:
    """A filename that already LOOKS percent-encoded (e.g. a literal `%`,
    `2`, `e` sequence -- distinct from an actual `..` traversal segment,
    which is refused outright by the separator/dotdot checks above) must
    have its own `%` characters re-encoded (`%` -> `%25`) before reaching
    the href. Without that, a client that percent-decodes the href once
    would read the embedded `%2e%2e` back as a literal `..` spelling,
    resolving somewhere other than the staged file.

    DESIGNED BREAK CONDITION: without quoting the filename before it
    reaches the href, this test fails -- the raw `%2e%2e...` spelling
    would appear un-re-encoded in the href, one decode away from a
    traversal spelling.
    """

    source_dir = tmp_path / "source"
    source_dir.mkdir()
    filename = "%2e%2e-not-actually-traversal.whl"
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
    assert "%252e%252e" in html_text, (
        "a literal '%' in the filename must itself be percent-encoded "
        "('%' -> '%25') in the href, not passed through raw"
    )
    assert f'href="{filename}' not in html_text


# ── finding 7: build_local_index's exception-translation boundary must be
# real, not merely advertised in dependency-bundle-trust.md ──────────────


def test_build_local_index_refuses_a_non_string_package_key(tmp_path: Path) -> None:
    """A non-string package key used to reach `normalise_name`'s regex
    directly; `re.Pattern.match` raises a raw `TypeError` on anything that
    is not a str/bytes-like object -- a leak straight through the
    advertised "every function ... raises one of its DependencyBundleError
    subclasses" boundary.

    DESIGNED BREAK CONDITION: removing the explicit `isinstance` check
    before the `normalise_name` call reintroduces the raw `TypeError`,
    which `pytest.raises(db.BundleVerificationError)` below does not
    catch (`TypeError` is not a `BundleVerificationError`), failing this
    test.
    """

    index_root = tmp_path / "index"
    with pytest.raises(db.BundleVerificationError, match="is not a string"):
        db.build_local_index(index_root, {123: []})  # type: ignore[dict-item]
    assert not index_root.exists()


def test_build_local_index_refuses_an_overlong_charset_valid_package_name(
    tmp_path: Path,
) -> None:
    """A package name built entirely from characters `normalise_name`
    permits, but too LONG for the filesystem to accept as one path
    component, is never rejected by the charset/shape checks above it --
    only the filesystem itself refuses it, with a raw `OSError`
    (`ENAMETOOLONG`), which must be translated rather than left to escape
    this function.

    DESIGNED BREAK CONDITION: removing the `try/except OSError` around
    `pkg_dir.mkdir(...)` reintroduces the raw `OSError`, which
    `pytest.raises(db.BundleVerificationError)` below does not catch.
    """

    index_root = tmp_path / "index"
    overlong_name = "a" * 4096  # charset-valid; exceeds NAME_MAX on every
    # filesystem this repository targets (typically 255 bytes/component)
    with pytest.raises(
        db.BundleVerificationError, match="cannot create package directory"
    ):
        db.build_local_index(index_root, {overlong_name: []})
    assert not index_root.exists()


def test_build_local_index_refuses_when_its_parent_directory_cannot_be_created(
    tmp_path: Path,
) -> None:
    """Parent-directory creation (`index_root.parent.mkdir(...)`) sat
    OUTSIDE the boundary that translates every other failure in this
    function into a `DependencyBundleError` subclass -- a raw `OSError`
    (here `NotADirectoryError`, from a path component that is actually a
    file) used to escape straight past callers that expect only
    `DependencyBundleError`.

    DESIGNED BREAK CONDITION: removing the `try/except OSError` wrapping
    `index_root.parent.mkdir(...)` reintroduces the raw `OSError`, which
    `pytest.raises(db.BundleVerificationError)` below does not catch.
    """

    blocking_file = tmp_path / "not-a-directory"
    blocking_file.write_bytes(b"this is a file, not a directory")
    index_root = blocking_file / "nested" / "index"
    with pytest.raises(
        db.BundleVerificationError, match="cannot create parent directory"
    ):
        db.build_local_index(index_root, {})


# ── sibling sweep: build_local_index's staging-phase writes and its own
# root listing sat inside the SAME untranslating `except BaseException:
# cleanup; raise` block as everything above -- that block cleans up, it
# never translates -- so a raw OSError from any of them would still have
# escaped even after the mkdir-focused finding-7 repair above. ──────────


def _one_package_index_fixture(tmp_path: Path) -> tuple[Path, dict]:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    wheel_path = source_dir / "wheel.whl"
    wheel_path.write_bytes(b"wheel bytes")
    index_root = tmp_path / "index"
    packages = {
        "dotmac-kernel": [("wheel.whl", db.sha256_hex(b"wheel bytes"), wheel_path)]
    }
    return index_root, packages


def test_build_local_index_refuses_when_a_package_index_cannot_be_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A raw `OSError` from the per-package `index.html` write (disk full,
    permission denied) used to escape this function untranslated -- the
    surrounding `except BaseException:` only cleans up the staging
    directory and re-raises WHATEVER it caught, unchanged.

    DESIGNED BREAK CONDITION: removing the try/except wrapping the
    per-package `(pkg_dir / "index.html").write_text(...)` call
    reintroduces the raw `OSError`, which
    `pytest.raises(db.BundleVerificationError)` below does not catch.
    """

    index_root, packages = _one_package_index_fixture(tmp_path)
    real_write_text = db.Path.write_text

    def _maybe_raise(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
        if self.parent.name == "dotmac-kernel":
            raise OSError("simulated: cannot write package index")
        return real_write_text(self, *args, **kwargs)

    monkeypatch.setattr(db.Path, "write_text", _maybe_raise)
    with pytest.raises(db.BundleVerificationError, match="cannot write package index"):
        db.build_local_index(index_root, packages)


def test_build_local_index_refuses_when_the_staged_root_cannot_be_listed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Listing the staged index root (`root_dir.iterdir()`, to build the
    top-level `index.html`) sat inside the same untranslating cleanup
    block as the write above.

    DESIGNED BREAK CONDITION: removing the try/except wrapping
    `root_dir.iterdir()` reintroduces the raw `OSError`, which
    `pytest.raises(db.BundleVerificationError)` below does not catch.
    """

    index_root, packages = _one_package_index_fixture(tmp_path)

    def _raise(self):  # noqa: ANN001, ANN202
        raise OSError("simulated: cannot list staged index root")

    monkeypatch.setattr(db.Path, "iterdir", _raise)
    with pytest.raises(
        db.BundleVerificationError, match="cannot list staged index root"
    ):
        db.build_local_index(index_root, packages)


def test_build_local_index_refuses_when_the_root_index_cannot_be_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Writing the top-level `index.html` (the LAST write of this
    function's staging phase, after every package is written and listed)
    sat inside the same untranslating cleanup block too.

    DESIGNED BREAK CONDITION: removing the try/except wrapping the
    root-level `(root_dir / "index.html").write_text(...)` call
    reintroduces the raw `OSError`, which
    `pytest.raises(db.BundleVerificationError)` below does not catch.
    """

    index_root, packages = _one_package_index_fixture(tmp_path)
    real_write_text = db.Path.write_text

    def _maybe_raise(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
        if self.parent.name == "simple":
            raise OSError("simulated: cannot write root index")
        return real_write_text(self, *args, **kwargs)

    monkeypatch.setattr(db.Path, "write_text", _maybe_raise)
    with pytest.raises(db.BundleVerificationError, match="cannot write root index"):
        db.build_local_index(index_root, packages)


# ── item 7: strong local run-metadata validation ─────────────────────


def _valid_policy() -> dict:
    return {
        "repository": {"full_name": "michaelayoade/dotmac_erp", "id": 1141216651},
        "producer_workflow_path": ".github/workflows/dependency-bundle-produce.yml",
        "binder_workflow_path": ".github/workflows/dependency-bundle-bind.yml",
        "environment_name": "forgejo-registry-read-main",
        "artifact_name_pattern": "erp-dependency-bundle-{plan_digest}",
    }


def _write_fake_git_head(root: Path, sha: str) -> None:
    """A minimal, detached-HEAD-shaped `.git` directory: `.git/HEAD`
    containing a raw 40-hex commit SHA directly, and NOTHING else -- no
    object store, no real commit. `_read_git_head_sha` reads and refuses
    this before anything tries to resolve `sha` as a real object, which is
    exactly what makes this fake usable for tests of THAT refusal (e.g. the
    null-SHA case below) and unusable for anything that goes on to read a
    blob at the resolved commit -- `git cat-file` has no object store to
    search here. Use `_init_real_git_repo` for a candidate a binding test
    needs to actually read blobs from."""

    git_dir = root / ".git"
    git_dir.mkdir(exist_ok=True)
    (git_dir / "HEAD").write_text(sha + "\n", encoding="utf-8")


def _run_git_fixture_command(root: Path, argv: list[str]) -> str:
    completed = subprocess.run(  # noqa: S603
        ["git", *argv],  # noqa: S607
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "test@example.invalid",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "test@example.invalid",
        },
    )
    return completed.stdout.strip()


def _init_real_git_repo(root: Path) -> str:
    """Initialise `root` as an ACTUAL git repository and commit its current
    files, returning the real resulting commit SHA.

    `extract_dependency_surface_at_commit` (which `bind_bundle_to_candidate`
    now uses) reads blobs via `git cat-file blob <sha>:<path>` -- that
    requires a real object store, which a bare `.git/HEAD` file (see
    `_write_fake_git_head`) does not provide. Git commit SHAs are
    content-addressed, so a fixed placeholder like the old
    `_CANDIDATE_HEAD_SHA = "c" * 40` cannot be engineered as a real commit's
    identity; callers that need to assert the bound SHA must capture and
    compare against this function's return value instead."""

    _run_git_fixture_command(root, ["init", "-q"])
    _run_git_fixture_command(root, ["add", "-A"])
    _run_git_fixture_command(
        root, ["commit", "-q", "-m", "test fixture commit", "--no-gpg-sign"]
    )
    return _run_git_fixture_command(root, ["rev-parse", "HEAD"])


def _candidate_root(tmp_path: Path) -> Path:
    """A real, on-disk candidate tree, committed as a real git repository —
    `verify_run_metadata` derives the candidate's plan digest by reading
    its working tree, and `bind_bundle_to_candidate` derives both its
    commit SHA and its plan digest from this same real commit's git-tree
    blobs, rather than accepting either as a bare caller-supplied value."""

    root = tmp_path / "candidate"
    root.mkdir(exist_ok=True)
    project_root = _project_root(root, BASE_PYPROJECT, BASE_LOCK)
    _init_real_git_repo(project_root)
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


def test_a_shape_valid_runmetadata_without_the_provenance_token_is_refused() -> None:
    """The defect this closes: a `RunMetadata` used to be accepted by
    `create_bundle_manifest`/`bind_bundle_to_candidate` as proof
    verification ran merely because every field happened to be
    shape-valid -- ANY caller could build one directly, without ever going
    through `verify_run_metadata`. Every field below is exactly as valid
    as `_run()`'s; the only thing missing is the provenance token, and
    that alone must now be refused."""

    with pytest.raises(db.BundleVerificationError, match="verify_run_metadata"):
        db.RunMetadata(
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


def test_a_runmetadata_with_the_wrong_provenance_token_is_refused() -> None:
    """The near-miss: a caller-supplied object that merely LOOKS like a
    token (any other object, including a freshly-constructed sentinel) is
    not `_RUN_METADATA_PROVENANCE_TOKEN` by identity, and is refused
    exactly like no token at all -- this is an identity check, not a
    truthiness or type check a forged object could satisfy."""

    with pytest.raises(db.BundleVerificationError, match="verify_run_metadata"):
        db.RunMetadata(
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
            _provenance_token=object(),
        )


def test_verify_run_metadata_itself_produces_a_provenanced_runmetadata(
    tmp_path: Path,
) -> None:
    """The near-miss's other half: the REAL path, `verify_run_metadata`,
    must still succeed -- proving the token requirement refuses a bypass
    without refusing genuine verification."""

    candidate_root = _candidate_root(tmp_path)
    digest = _candidate_digest(candidate_root)
    result = db.verify_run_metadata(
        _valid_run_metadata(digest), _valid_policy(), candidate_root=candidate_root
    )
    assert isinstance(result, db.RunMetadata)


# ── candidate binding ──────────────────────────────────────────────────


def test_binding_refuses_a_digest_mismatch(tmp_path: Path) -> None:
    surface = _manifest_test_surface()
    wheel_path = _write_wheel(tmp_path)
    archive_path = _write_archive(tmp_path, {_MANIFEST_TEST_WHEEL_NAME: wheel_path})
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
    _init_real_git_repo(candidate_root)
    return candidate_root


def test_binding_succeeds_when_digests_match(tmp_path: Path) -> None:
    surface = _manifest_test_surface()
    wheel_path = _write_wheel(tmp_path)
    archive_path = _write_archive(tmp_path, {_MANIFEST_TEST_WHEEL_NAME: wheel_path})
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
    expected_sha = db._read_git_head_sha(candidate_root)
    binding = db.bind_bundle_to_candidate(manifest, _run(), candidate_root)
    assert binding.bundle_run_id == 111
    assert binding.candidate_sha == expected_sha, (
        "the binding must report the SHA read from the candidate's own "
        "real git commit, not an asserted value -- commit SHAs are "
        "content-addressed, so this compares against the SAME resolver "
        "the module itself uses, never a fixed placeholder"
    )


def test_bind_bundle_to_candidate_reads_the_committed_blob_not_a_dirtied_working_tree(
    tmp_path: Path,
) -> None:
    """The governing property of the immutable-commit redesign: a caller-
    supplied SHA beside working-tree reads is not a binding. Plants the
    exact defect the redesign closes -- dirty the candidate's working-tree
    `pyproject.toml` AFTER it is committed, without committing the change
    -- and shows `bind_bundle_to_candidate` still binds successfully using
    the COMMITTED (matching) surface, never the dirtied one.

    The near-miss half of the proof: `extract_dependency_surface` (the
    WORKING-TREE reader) on the same, now-dirty tree RAISES outright (the
    dirtied manifest declares a forgejo dependency the committed lock
    never resolved) -- confirming the dirty write actually changed
    something observable, so a version of `bind_bundle_to_candidate` that
    read the working tree (the pre-redesign shape) would have refused this
    exact candidate. The fact that binding still SUCCEEDS is the proof
    that it is reading the pinned commit's blobs instead, never the
    dirtied working-tree file of the same name."""

    surface = _manifest_test_surface()
    wheel_path = _write_wheel(tmp_path)
    archive_path = _write_archive(tmp_path, {_MANIFEST_TEST_WHEEL_NAME: wheel_path})
    manifest = db.create_bundle_manifest(
        surface=surface,
        acquired_files={_MANIFEST_TEST_WHEEL_NAME: wheel_path},
        archive_path=archive_path,
        run=_run(),
    )
    candidate_root = _matching_candidate_root(tmp_path, "dirtied-candidate")
    committed_sha = db._read_git_head_sha(candidate_root)

    # Dirty the working tree AFTER the commit, without committing: declare
    # a direct forgejo dependency the committed lock never resolved.
    (candidate_root / "pyproject.toml").write_text(
        base_pyproject('dotmac-files = {version = "9.9.9", source = "forgejo"}\n'),
        encoding="utf-8",
    )

    with pytest.raises(db.ManifestError, match="no corresponding poetry.lock entry"):
        db.extract_dependency_surface(candidate_root)

    binding = db.bind_bundle_to_candidate(manifest, _run(), candidate_root)
    assert binding.candidate_sha == committed_sha
    assert binding.plan_digest == manifest["plan_digest"], (
        "bind_bundle_to_candidate must derive the candidate's plan digest "
        "from the PINNED COMMIT's blobs, not the dirtied working tree -- "
        "it dirtied differently above and this must still match the "
        "bundle's committed-surface digest"
    )


def test_extract_dependency_surface_at_commit_refuses_a_commit_missing_the_lock(
    tmp_path: Path,
) -> None:
    """A commit that never tracked `poetry.lock` at all must be refused by
    name, via `git cat-file` failing to resolve the blob -- not crash with
    an unhandled subprocess or git error."""

    root = tmp_path / "no-lock-repo"
    root.mkdir()
    (root / "pyproject.toml").write_text(BASE_PYPROJECT, encoding="utf-8")
    sha = _init_real_git_repo(root)
    with pytest.raises(db.ManifestError, match="poetry.lock"):
        db.extract_dependency_surface_at_commit(root, sha)


def test_binding_refuses_a_candidate_with_no_git_checkout(tmp_path: Path) -> None:
    """Finding 4: candidate_sha used to be a bare parameter the caller
    asserted; it is now derived from candidate_root's own .git, and a
    candidate that is not a git checkout at all must be refused rather
    than silently accepted with no SHA to report."""

    surface = _manifest_test_surface()
    wheel_path = _write_wheel(tmp_path)
    archive_path = _write_archive(tmp_path, {_MANIFEST_TEST_WHEEL_NAME: wheel_path})
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
    archive_path = _write_archive(tmp_path, {_MANIFEST_TEST_WHEEL_NAME: wheel_path})
    manifest = db.create_bundle_manifest(
        surface=surface,
        acquired_files={_MANIFEST_TEST_WHEEL_NAME: wheel_path},
        archive_path=archive_path,
        run=_run(),
    )
    null_sha_root = tmp_path / "null-sha-candidate"
    null_sha_root.mkdir()
    _project_root(null_sha_root, BASE_PYPROJECT, BASE_LOCK)
    _write_fake_git_head(null_sha_root, sha="0" * 40)
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
    archive_path = _write_archive(tmp_path, {_MANIFEST_TEST_WHEEL_NAME: wheel_path})
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
    archive_path = _write_archive(tmp_path, {_MANIFEST_TEST_WHEEL_NAME: wheel_path})
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


# ── fail-open lock-name filtering: _lock_packages must REFUSE, not merely
#    not-crash-on, a missing/null/non-string/empty/charset-invalid name ────
#
# An isinstance(...) guard used as a list-comprehension FILTER (as
# `_verify_off_index_lock_entry` does) silently drops a malformed entry
# from consideration instead of refusing it -- indistinguishable from "no
# such entry" even though the entry exists in malformed form, and the plan
# digest would then be computed over an incomplete surface. `_lock_packages`
# is the earlier validator that must refuse every such name outright, for
# every [[package]] entry, before any digest is produced.

_LOCK_PACKAGE_NAME_REFUSAL_VECTORS: list[tuple[object, bool, str]] = [
    ("dotmac-kernel", False, "a normal valid name is accepted"),
    ("", True, "an empty string name is refused, not silently accepted"),
    (
        "dotmac kernel!",
        True,
        "a charset-invalid name (space and '!') is refused",
    ),
    ("-dotmac-kernel", True, "a name with a leading separator is refused"),
    (None, True, "a null name is refused"),
    (123, True, "a non-string (int) name is refused"),
]


@pytest.mark.parametrize(
    "raw_name,expect_refusal,reason",
    _LOCK_PACKAGE_NAME_REFUSAL_VECTORS,
    ids=[v[2] for v in _LOCK_PACKAGE_NAME_REFUSAL_VECTORS],
)
def test_lock_packages_refuses_every_malformed_name_before_any_digest(
    raw_name: object, expect_refusal: bool, reason: str
) -> None:
    """Sensitivity proof for the fail-open fix: plants each malformed-name
    defect in turn (empty, charset-invalid, leading-separator, null,
    non-string) and shows `_lock_packages` names it with a `ManifestError`
    before reaching the forgejo-source checks below it -- and plants the
    near-miss (a normal valid name) and shows that one is NOT refused."""

    lock = {
        "package": [
            {
                "name": raw_name,
                "version": "0.1.0a1",
                "source": {
                    "type": "legacy",
                    "reference": db.FORGEJO_SOURCE_NAME,
                    "url": db.FORGEJO_LOCK_URL,
                },
                "files": [],
            }
        ]
    }
    if expect_refusal:
        with pytest.raises(db.ManifestError, match="name"):
            db._lock_packages(lock)
    else:
        packages = db._lock_packages(lock)
        assert [p.name for p in packages] == [raw_name], reason


def test_verify_off_index_lock_entrys_isinstance_filter_is_redundant_by_construction() -> (
    None
):
    """`_verify_off_index_lock_entry`'s `isinstance(p.get("name"), str)`
    filter is documented as redundant-by-construction because
    `_lock_packages` has already refused every malformed-name entry in the
    SAME `lock` dict, earlier. This proves the premise directly: called in
    isolation (bypassing `_lock_packages` entirely, exactly as it would run
    if a future change broke the ordering), the filter does NOT raise for a
    non-string-named entry -- it silently drops it, which is the fail-open
    shape. `_lock_packages` on that identical lock DOES raise. The two
    results together are why the ordering (proven separately below) is
    load-bearing."""

    dep = db.ApprovedOffIndexDependency(
        name=_OFF_INDEX_PIN_NAME,
        normalised_name=db.normalise_name(_OFF_INDEX_PIN_NAME),
        group="main",
        url=_OFF_INDEX_PIN_URL,
        tag=_OFF_INDEX_PIN_TAG,
        resolved_commit=_OFF_INDEX_PIN_COMMIT,
        group_optional=False,
    )
    lock_with_non_string_name = {
        "package": [
            {
                "name": 123,
                "source": {
                    "type": "git",
                    "url": _OFF_INDEX_PIN_URL,
                    "reference": _OFF_INDEX_PIN_TAG,
                    "resolved_reference": _OFF_INDEX_PIN_COMMIT,
                },
            }
        ]
    }
    # Called alone, without _lock_packages having run first, the filter
    # merely reports "no match" -- it does not name or refuse the malformed
    # entry. This is the fail-open behaviour that makes the ordering below
    # load-bearing rather than cosmetic.
    with pytest.raises(db.ManifestError, match="must have exactly one"):
        db._verify_off_index_lock_entry(dep, lock_with_non_string_name)

    # The SAME lock, run through the earlier validator, is refused by name.
    with pytest.raises(db.ManifestError, match="name"):
        db._lock_packages(lock_with_non_string_name)


def test_lock_packages_runs_before_off_index_lock_verification_in_extract_dependency_surface() -> (
    None
):
    """Ordering proof. `_verify_off_index_lock_entry`'s `isinstance` filter
    is safe only because `_build_dependency_surface` always calls
    `_lock_packages(lock)` -- which walks every `[[package]]` entry in that
    exact `lock` dict and refuses a malformed name -- BEFORE it calls
    `_verify_off_index_lock_entry(off_index_dep, lock)` on the same `lock`;
    and `_classify_and_admit_lock_entries`'s own duplicate-identity check
    is redundant-by-construction in exactly the same way, one call later.

    CORRECTED TARGET: this test used to inspect `extract_dependency_surface`
    itself, which is a thin loader that only calls `_build_dependency_surface`
    -- it never calls `_lock_packages`, `_verify_off_index_lock_entry`, or
    `_classify_and_admit_lock_entries` directly, so the AST walk below found
    NONE of them there and the test's own `assert ..._call_lines` guards
    would have failed immediately. `_build_dependency_surface` is the
    function that actually makes these three calls, in this order, on the
    SAME `lock` dict -- it is now the one inspected here.

    This is an AST inspection, not a behavioural one: with today's code,
    every call order would raise the SAME observable `ManifestError` for a
    malformed name, because `_lock_packages` runs unconditionally either
    way inside `_build_dependency_surface` -- a black-box test cannot
    distinguish the orders. The property under test is the ORDER OF
    EXECUTION itself.

    One-line break condition: this test fails the moment
    `_build_dependency_surface` calls `_verify_off_index_lock_entry` or
    `_classify_and_admit_lock_entries` on `lock` at or before the point it
    calls `_lock_packages(lock)`, or calls `_classify_and_admit_lock_entries`
    at or before `_verify_off_index_lock_entry` -- at that point the
    redundant-by-construction filters documented in both callees become the
    ONLY gate against a malformed or duplicate-identity lock package name,
    and each is a silent filter, not a refusal.
    """

    source = inspect.getsource(db._build_dependency_surface)
    func_def = ast.parse(source).body[0]
    assert isinstance(func_def, ast.FunctionDef)

    lock_packages_call_lines: list[int] = []
    verify_off_index_call_lines: list[int] = []
    classify_and_admit_call_lines: list[int] = []
    for node in ast.walk(func_def):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "_lock_packages":
                lock_packages_call_lines.append(node.lineno)
            elif node.func.id == "_verify_off_index_lock_entry":
                verify_off_index_call_lines.append(node.lineno)
            elif node.func.id == "_classify_and_admit_lock_entries":
                classify_and_admit_call_lines.append(node.lineno)

    assert lock_packages_call_lines, (
        "_build_dependency_surface no longer calls _lock_packages directly "
        "-- this test can no longer prove the ordering it exists to prove"
    )
    assert verify_off_index_call_lines, (
        "_build_dependency_surface no longer calls "
        "_verify_off_index_lock_entry directly -- this test can no longer "
        "prove the ordering it exists to prove"
    )
    assert classify_and_admit_call_lines, (
        "_build_dependency_surface no longer calls "
        "_classify_and_admit_lock_entries directly -- this test can no "
        "longer prove the ordering it exists to prove"
    )
    assert max(lock_packages_call_lines) < min(verify_off_index_call_lines), (
        "_lock_packages must run, for the whole lock, before "
        "_verify_off_index_lock_entry is ever called on that same lock -- "
        "otherwise the isinstance(name, str) filter documented as "
        "redundant-by-construction in _verify_off_index_lock_entry becomes "
        "the ONLY gate against a malformed lock package name, and it is a "
        "silent filter, not a refusal"
    )
    assert max(verify_off_index_call_lines) < min(classify_and_admit_call_lines), (
        "_verify_off_index_lock_entry must run, for every approved "
        "off-index dependency, before _classify_and_admit_lock_entries is "
        "ever called on that same lock -- _classify_and_admit_lock_entries "
        "carries its own independent duplicate-identity refusal precisely "
        "because this ordering is not guaranteed by anything but this test"
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


def test_scan_for_credential_refuses_when_a_target_path_cannot_be_read(
    tmp_path: Path,
) -> None:
    """`scan_for_credential`'s own `path.read_text(...)` sat outside this
    module's translation boundary -- a target path that cannot be read
    (here, one that was simply never written) raised a raw `OSError`
    (`FileNotFoundError`) instead of the `DependencyBundleError` every
    other adversarial-input site in this module raises.

    DESIGNED BREAK CONDITION: removing the try/except around
    `path.read_text(...)` reintroduces the raw `OSError`, which
    `pytest.raises(db.BundleVerificationError)` below does not catch.
    """

    missing_target = tmp_path / "never-written.txt"
    with pytest.raises(db.BundleVerificationError, match="cannot read"):
        db.scan_for_credential([missing_target], CREDENTIAL)


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
    """The baseline is a two-directional ratchet, not a ceiling.

    A retired entry must shrink `BASELINE_INVENTORY_IDS` in the SAME
    reviewed change that removes it from the inventory — a one-directional
    `actual_ids <= BASELINE_INVENTORY_IDS` check stayed green when an
    entry was deleted without lowering the baseline, which let the same id
    be reintroduced later and pass again unnoticed (the "permanently at
    zero" retirement this ratchet is documented to enforce). DESIGNED
    BREAK CONDITION: replacing this `==` with the old `<=` makes this test
    pass again even though a planted deletion (removing an entry from the
    inventory JSON without touching `BASELINE_INVENTORY_IDS`) is present —
    that is exactly the defect this equality check exists to catch, and is
    how a reviewer can confirm this test still names it.
    """

    inventory = _load_inventory()
    actual_ids = {entry["id"] for entry in inventory["entries"]}
    assert actual_ids == BASELINE_INVENTORY_IDS, (
        f"the duplication inventory and BASELINE_INVENTORY_IDS disagree: "
        f"grown by {actual_ids - BASELINE_INVENTORY_IDS}, shrunk by "
        f"{BASELINE_INVENTORY_IDS - actual_ids}. A NEW duplicated behaviour "
        "must not be added, and a retired one must not be removed, without "
        "a deliberate, reviewed update to BASELINE_INVENTORY_IDS in this "
        "same test file and same change — otherwise a retired id could be "
        "silently reintroduced later."
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
    """TWO-DIRECTIONAL RATCHET, not a permanent specification of the WEAKER
    behaviour.

    NOT converged (tracked as a real, security-relevant gap in
    erp_lock.acquisition_plan, out of scope for this branch to fix):
    erp_lock's lock-side loop includes a package by checking ONLY
    `source.reference == "forgejo"`, never `type` or `url`. This module's
    _lock_packages requires all three to agree, refusing a mismatched
    combination. A lock entry with the right `reference` but a WRONG url
    is refused here and silently trusted there.

    This test must fail in BOTH directions, not just one:

    - If the asymmetry WIDENS (`_lock_packages` gets even stricter, or
      `acquisition_plan` gets even looser, in a way that changes either
      assertion below), this test fails and says so.
    - If the asymmetry DISAPPEARS (an authorised, in-scope change tightens
      `erp_lock.acquisition_plan` to also require `type` and `url` to
      agree), the second assertion below fails, because a converged
      `acquisition_plan` would then refuse the same malformed
      `dotmac-kernel` entry instead of silently including it at
      `"0.1.0a1"`. That failure is CORRECT and expected: convergence must
      be done in the same change that deletes this test's debt-tracking
      assertion (and this docstring's "NOT converged" paragraph, and the
      matching entry in
      `docs/architecture/dependency-bundle-trust.md`'s "Named divergences
      from erp_lock.py" section) — never by quietly loosening or removing
      this test first and leaving the debt undocumented, and never by
      leaving this test red because the gap was closed by hand elsewhere.
      A tightened `acquisition_plan` that leaves this test passing
      unchanged would mean the test stopped proving anything; it must
      break instead, on purpose, as the signal to finish the retirement.
    """

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
