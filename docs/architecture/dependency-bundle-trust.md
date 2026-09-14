# Dependency-bundle trust chain

**Status:** slice 1a, since repaired against six adversarial review findings
(the "1a repair" below) — still purely additive. Nothing in this repository
consumes this contract yet. `scripts/dependency_bundle.py`,
`scripts/dependency_normalisation.py`,
`.github/dependency-bundle-policy.json`, and
`docs/architecture/dependency-bundle-duplication-inventory.json` are the
mechanics and canonical policy for the end state; no workflow, Dockerfile,
`pyproject.toml`, or `poetry.lock` changed in this slice.
`scripts/erp_lock.py` changed in exactly two narrow ways, both import-only
(verified against the real diff, not asserted): it now imports
`dependency_normalisation` and its `_normalised` and
`_normalised_repository_url` names each became a one-line alias to
`dependency_normalisation.normalise_name` /
`.normalise_repository_url` in place of a locally-defined function (see
"Named duplication debt" below) — its credentialed acquisition
orchestration is otherwise untouched. A third comparison — the LOCK-level
off-index package NAME match — was also converged onto PEP 503
normalisation, but entirely on the `dependency_bundle.py` side:
`erp_lock.off_index_lock_problems` already normalised both sides of that
comparison, so no further `erp_lock.py` change was needed there.

**The 1a repair.** An adversarial cross-model review of the original slice
found six corrections to the contract this document claims, not scope
expansion: (1) dependency classification was not total — off-index forms
(`git`/`path`/`file`/direct `url`) silently vanished from the digest instead
of being refused, and `[tool.poetry.requires-plugins]` was unchecked; (2)
`optional`, per-dependency `python`, and lock `python-versions`/`optional`
were missing from the digest, so they could change without moving it; (3)
the PEP 503 normaliser was duplicated AND had already diverged between the
two scripts; (4) the bundle manifest took `plan_digest` and file hashes as
independent scalars, so a plan's identity could be attached to a different
plan's files; (5) extraction's size map was never actually recorded in the
manifest schema it claimed to implement; (6) extraction was public,
non-atomic, and did not bound aggregate member count/size/compression
ratio or detect resolved-target aliasing; (7) local run-metadata validation
accepted an all-zero SHA, negative coordinates, either workflow path, and
never checked environment or artifact-name/ownership. Every one of these is
fixed in the code this document now describes; none was a design decision
this document is entitled to just assert away.

## Ownership

`scripts/dependency_bundle.py` owns:

- dependency-surface validation and extraction from `pyproject.toml` +
  `poetry.lock`, with TOTAL classification of every dependency form —
  public, approved Forgejo, or approved off-index, or a named refusal;
  nothing vanishes silently;
- the plan-digest computation (below), including the approved off-index
  identity and every selection-significant field (`optional`, `markers`,
  `extras`, per-dependency `python` constraint, lock `python-versions`);
- the plan's own file/digest closure (`planned_artifacts`);
- canonical bundle-manifest creation, COMPUTED from a `DependencySurface`
  and the producer's actually-acquired files — never accepted as
  independent, uncorrelated scalars (see "Bundle manifest schema" below);
- GitHub run/artifact metadata verification (of already-fetched JSON — this
  module makes no network call; see "Refusal behaviour" for exactly what
  this does and does not prove);
- outer ZIP digest verification;
- private, safe, atomic ZIP extraction (`extract_verified_bundle` is the
  only sanctioned entry point);
- contained-file hash verification;
- local PEP 503 index materialisation;
- candidate-specific rebinding (`bind_bundle_to_candidate`): binding a
  verified bundle to one candidate commit whose own recomputed plan digest
  matches it AND whose own commit SHA — read from `candidate_root`'s `.git`
  refs, never accepted as a parameter — matches too, consuming an
  ALREADY-VERIFIED `RunMetadata` (from a prior `verify_run_metadata` call)
  rather than re-deriving one from unvalidated `bundle_manifest` fields.
  **A caller-supplied SHA beside working-tree reads is not a binding**: the
  candidate's commit is resolved ONCE (`_read_git_head_sha`), then its
  `pyproject.toml`/`poetry.lock` bytes are read as THAT COMMIT's own git
  tree entries — via `extract_dependency_surface_at_commit`, which reads
  blobs through `git cat-file`, never the working tree — and the plan
  digest is computed from those bytes, in that order, before the identity
  is bound. A working-tree read taken as a second, independent observation
  of `candidate_root` (the shape this replaced) could disagree with an
  independently-read HEAD SHA if the checkout were dirty or mutated
  concurrently between the two reads; reading both the SHA and the
  dependency bytes as properties of the SAME pinned commit object closes
  that gap by construction, not by hoping two observations agree.

`scripts/dependency_normalisation.py` owns PEP 503 package-name
normalisation — the ONE place that logic lives, imported by both
`dependency_bundle.py` and `erp_lock.py` (see "Named duplication debt"
below for why this one specific piece is unified rather than merely listed
as an accepted duplicate).

`.github/dependency-bundle-policy.json` owns the reviewed, checked-in facts
a consumer trusts: which repository, which workflows are the producer and
binder, which environment gates the credential, and the compatibility
target. **A consumer reads this file from the verified trusted-workflow
commit — never from a candidate PR checkout.** A candidate branch could
edit its own copy of this file to name itself as the trusted producer; the
whole point of pinning consumption to the trusted workflow's own commit is
that the candidate's copy is never consulted for trust decisions, only its
dependency surface is (to compute its own plan digest for comparison).

## The threat this exists to close

`workflow_dispatch` runs the workflow FILE FROM THE DISPATCHED REF: for a
workflow invoked with `workflow_dispatch`, `github.sha` in that run is the
commit of the ref that was dispatched — which can be any branch in this
same repository — not protected `main`. `.github/workflows/erp-lock.yml`
(and any future analogous workflow) treats `github.sha` as if it were
necessarily a trusted, human-reviewed tooling checkout, while the run holds
`secrets.FORGEJO_READ_TOKEN`. Concretely: an operator (or anyone who can
push a branch and has `workflow_dispatch` access) can push a branch whose
`scripts/erp_lock.py` or `.github/workflows/erp-lock.yml` is modified, and
dispatch that workflow against that branch. `erp-lock.yml`'s `acquire` job
does pin its *tooling checkout* to `github.sha` and separately requires the
resolved *candidate ref* to be reachable from `origin/main` (see that
workflow's "Require the protected main source and exact current main tip"
step) — but `github.sha` for the *run itself* is still the dispatched
branch's commit, so the tooling that enforces that very check, and every
other check in the job, is exactly the bytes the branch author chose. The
repository-scoped read credential is available to that tooling for the
whole job.

The end state removes `secrets.FORGEJO_READ_TOKEN` from every
candidate-branch-reachable workflow entirely. In its place: one trusted,
protected-branch-pinned **producer** workflow resolves the private index
once and publishes a verified, immutable **bundle** (this policy's
`producer_workflow_path`); PR CI, and any other candidate-branch workflow,
consumes that bundle through a **binder** step (`binder_workflow_path`)
that verifies it and materialises it as a local index — never reaching
`registry.dotmac.io` itself, and never holding the read credential.

**Fairness to the current shape:** `erp-lock.yml`'s credential
(`secrets.FORGEJO_READ_TOKEN` / the `FORGEJO_CREDENTIAL` env var it becomes)
is held ONLY inside the `acquire` job — four steps, all inside that one job
(the pre-flight token-presence check, the netrc-based `curl` acquisition
step, the credential-scan-during-acquisition step, and the final
credential-attestation-scan step that runs beside `acquire`'s other work).
`resolve` and `attest` hold none of it. One of those four credentialed steps
*is* the credential scan itself: it legitimately needs the real value in
order to prove the acquired/published bundle does not contain it — that is
not a violation of "the resolver never holds the credential", because that
scan step is not the resolver; it is worth saying explicitly because a
credentialed step whose entire job is scanning FOR the credential looks
like a leak until you see why it must hold the value to do its job. ERP is
therefore already at the "only acquisition holds the token" shape this
document's end state wants; the productionised end state's remaining work
is removing the credential from `acquire` too, by moving that job's
function into the trusted producer workflow and never exposing the
candidate-reachable dispatch surface to it at all. (`dotmac_vendor_control_plane`'s
`kernel-lock.yml`, by contrast, additionally credentials its `attest` job —
that repository is not this one and is out of scope here.)

## The plan digest

The plan digest is the value everything else is checked against. It is
computed ONLY from the parsed dependency surface — never a raw byte hash of
`pyproject.toml` or `poetry.lock`, which would break on a reformatted file
that declares nothing different, and never accepted as an input from an
unverified caller (see `scripts/dependency_bundle.py`'s module docstring,
"The plan digest is the trust boundary").

**Classification is total.** Every dependency declaration resolves to
exactly one of three outcomes — `PublicDependency`, `ForgejoDependency`, or
`ApprovedOffIndexDependency` — or `extract_dependency_surface` raises
`ManifestError`. There is no fourth path where a form the classifier does
not recognise is silently treated as public and dropped: a `git`, `path`,
`file`, or direct `url` dependency form is refused UNLESS it is the exact,
policy-pinned identity in `permitted_off_index_dependencies` (today, only
`dotmac-integration-client`), in which case its exact `(url, tag,
resolved_commit)` triple is verified against the locked `resolved_reference`
and included in the plan document under `"off_index"` — an off-index
addition, or a change to which commit an existing off-index pin resolves
to, always moves the digest.

**Lock-entry classification is likewise total, but over a different set:
every RAW `poetry.lock` `[[package]]` entry, not only a manifest
dependency declaration.** `_lock_packages` classifies a lock entry as
"private" only when its own reference or source URL names the forgejo
host — everything else used to hit an unconditional `continue` and was
never classified, never refused, and never moved the digest, which meant
a candidate lock could add a TRANSITIVE `git`-sourced package from an
arbitrary host, name it as another package's dependency edge, and have
neither the manifest (which never declared it) nor `_lock_packages`
(which only looks at forgejo entries) ever see it. `_classify_and_admit_lock_entries`
closes that: every `[[package]]` entry resolves to exactly one of PUBLIC
(no source, or an ordinary registry source that does not name the
forgejo host), an already-classified forgejo entry, an approved
off-index root's own entry, a PROVEN member of an approved off-index
root's transitive closure (reachable from the root's own lock entry by
following `[package.dependencies]` edges — never merely "it looks
private" or "its reference matches"), or REFUSED with `ManifestError`: a
`git`-sourced entry that is not part of any approved root's proven
closure is refused as an injected, stale, or otherwise unreachable VCS
lock entry (one message covers all three, since they are the identical
fact from this function's point of view — a `git` source with no path
back to an approved root); a lock entry whose source `type` this module
has no policy for at all is refused as an unsupported lock source type.
Every ADMITTED transitive-closure member's own identity (name, source
URL, reference, resolved commit) is returned as an
`OffIndexTransitiveDependency` and folded into the plan document under
`"off_index_transitive"` — admission alone, with no digest sensitivity,
would let two different admitted transitive states share one digest, the
same semantic-collision defect the digest exists to prevent everywhere
else on this surface.

**"Every RAW entry" means over ENTRY POSITIONS, not over a normalised-
identity set.** An earlier version of this classification kept a
normalised-identity-keyed dict as the thing it enumerated: two
`[[package]]` entries whose names normalised to the same identity
collapsed into one dict slot, and classification only ever saw the
survivor — the other entry was never classified, never refused, and
never moved the digest. A `git`-sourced entry reusing a FORGEJO or an
APPROVED OFF-INDEX ROOT's own identity was the sharpest shape: skipped by
the admission logic as "already classified" purely because the identity
matched, landing in neither `lock_packages` nor `off_index_transitive` —
a candidate lock could ship an attacker-controlled VCS dependency under a
colliding name and `compute_plan_digest` would be byte-identical to a
clean surface. `_lock_packages` and `_classify_and_admit_lock_entries`
each now refuse a duplicate identity themselves, by POSITION (an entry's
index in the raw `[[package]]` array), before classifying anything — a
position cannot collapse the way a name can.
`_classify_and_admit_lock_entries` additionally tracks a per-position
disposition and refuses
outright if any position ends unclassified or is classified more than
once, so a future classification branch that forgets to record its
outcome fails the extraction immediately rather than silently dropping
the entry it was supposed to classify.

**Construction:**

1. Parse `pyproject.toml` and `poetry.lock` with `tomllib`.
2. Refuse: an unknown dependency table (`[tool.poetry.dev-dependencies]`,
   PEP 621 `[project.dependencies]`/`[project.optional-dependencies]`, PEP
   735 `[dependency-groups]` — refused outright because it holds plain PEP
   508 requirement strings that can express a direct reference bypassing
   total classification, and this module has no PEP 508 classifier to
   examine one with); `[tool.poetry.requires-plugins]` (Poetry loads and
   imports plugins BEFORE it resolves anything — arbitrary
   candidate-controlled code in a future credential-bearing producer step,
   refused for the identical reason `erp_lock.py` refuses it); a duplicate
   dependency declaration; a present `poetry.toml`; an alternate URL (any
   spelling other than the exact canonical one, including a missing
   trailing slash) on the `forgejo`-named source, or any other named source
   pointing at the same host; a direct registry URL bypassing the named
   source; an off-index dependency form that is not the policy's exact
   pinned identity; a version range (any operator, not an exact pin) on a
   `source = "forgejo"` dependency; and any manifest/lock disagreement (a
   declared forgejo pin or off-index pin with no matching lock entry, a
   different resolved version, or a lock `resolved_reference` that
   disagrees with the pinned commit).
3. Extract: the normalised `forgejo` source URL; every Poetry dependency
   (across `[tool.poetry.dependencies]` and every
   `[tool.poetry.group.<name>.dependencies]`) that declares
   `source = "forgejo"`, including its group, the OWNING GROUP's own
   `optional` flag, PEP-503-normalised name, exact version, markers,
   extras, its OWN `optional` key, and per-dependency `python` constraint;
   every approved off-index dependency's group, group-optional flag, url,
   tag, and resolved commit; every `poetry.lock` `[[package]]` entry whose
   `source.reference == "forgejo"` — direct OR transitive — including its
   normalised name, version, groups, `optional`, `python-versions`,
   `markers`, `extras`, dependencies, full source record, and every `(file,
   hash)` pair; every entry `_classify_and_admit_lock_entries` ADMITS as a
   proven transitive-closure member of an approved off-index root — its
   name, source url, reference, and resolved commit; the lock's own
   `[metadata].lock-version` and `.python-versions`; and the schema/policy
   version plus target Python constraint and platform.
4. Serialise as canonical UTF-8 JSON: `json.dumps(doc, sort_keys=True,
   separators=(",", ":"))` plus a trailing newline.
5. Hash: `SHA256(b"dotmac.erp-dependency-plan.v1\0" + canonical_json_bytes)`.

**What changes the digest:** any private pin version, any private
transitive dependency's presence/version/hash, the forgejo source URL, a
group assignment, a group's own `optional` flag, a marker, an extra, a
dependency's own `optional` key, a per-dependency `python` constraint, a
lock package's `python-versions`/`markers`/`extras`, a wheel/sdist
filename, a published hash, the lock's own format version or
resolution-wide Python constraint, an approved off-index dependency's
pinned URL/tag/commit, or an ADMITTED off-index transitive-closure
member's presence, url, reference, or resolved commit — the
`"off_index_transitive"` key `_classify_and_admit_lock_entries` populates
so that two different admitted transitive states can never share one
digest. Every one of these is proven by a dedicated before/after mutation
test in `tests/architecture/test_dependency_bundle.py`.

**What does not:** TOML comments, whitespace, or key order; the
application's own `[tool.poetry].version`; and any public (non-forgejo,
non-approved-off-index) package's presence, version, or hash — public
packages never enter the parsed `DependencySurface` at all, so they cannot
influence the digest by construction.

## The plan's own file/digest closure

`planned_artifacts(surface)` derives the exact `{filename: sha256}` set the
plan requires, from `surface.lock_packages` alone — never supplied
independently. This is the plan-side half of the closure
`create_bundle_manifest` enforces (see "Bundle manifest schema" below): it
is what makes "attach plan A's digest to plan B's files" a structural
impossibility rather than something every individual check happens to miss.

## Bundle manifest schema

```json
{
  "schema_version": 2,
  "plan_digest": "<64-hex sha256, COMPUTED from the surface, never a caller-supplied scalar>",
  "archive_sha256": "<64-hex sha256>",
  "members": {
    "<member-name>": {
      "sha256": "<64-hex sha256>",
      "size": "<positive int, the validated uncompressed size>",
      "package": "<PEP-503-normalised owning package name>"
    }
  },
  "run": {
    "repository_full_name": "michaelayoade/dotmac_erp",
    "repository_id": <int>,
    "workflow_path": ".github/workflows/dependency-bundle-produce.yml",
    "run_id": <int>,
    "run_attempt": <int>,
    "trusted_workflow_sha": "<40-hex commit sha>",
    "artifact_id": <int>,
    "artifact_name": "erp-dependency-bundle-<plan_digest>",
    "artifact_run_id": "<int, must equal run_id>",
    "environment_name": "forgejo-registry-read-main"
  }
}
```

**`create_bundle_manifest` COMPUTES every binding from the SURFACE and the
REAL FILES on disk — it does not accept any of `plan_digest`,
`archive_sha256`, a member's hash, or a member's size as an independent,
caller-reported scalar.** Its parameters are a `DependencySurface`,
`acquired_files` (a plain `filename -> Path` mapping to what the producer
actually downloaded), `archive_path` (the real outer archive file), and an
already-verified `RunMetadata`. It reads and hashes every acquired file
itself (`sha256_hex(path.read_bytes())`) and compares that AGAINST the
plan's own required digest — a caller's claim about a file's hash or size
is never trusted, only what this function measures directly. `archive_sha256`
is likewise computed from `archive_path`'s real bytes, never accepted as a
scalar. `plan_digest` is derived by calling `compute_plan_digest(surface)`
internally; `members` is built by requiring EXACT closure between
`planned_artifacts(surface)` and `acquired_files` in BOTH directions —
every planned filename must be present in `acquired_files` (`missing`
refuses), and every acquired file must be named by the plan
(`extra`/unaccounted-for refuses). Because there is only one `surface`
input and every byte is independently re-read, there is no seam at which a
second plan's identity, or an unrelated archive, could be substituted for
the real artifacts — this is what closes the "plan A's digest attached to
plan B's files" gap the original schema had (proven in
`test_mixing_one_plans_digest_with_a_different_plans_files_is_refused`).
Closure is checked over the ARCHIVE ITSELF, not only over
`archive_path`'s outer bytes: `create_bundle_manifest` opens the archive
as a ZIP and proves, member by member, that it actually CONTAINS every
acquired file under its exact planned name, with the exact size and
content digest just computed from the real file on disk. Hashing the
archive's own bytes alone would prove nothing about what is inside it — an
archive built from a stale or unrelated acquisition hashes just as validly
as the correct one — so a manifest is refused outright, not merely
produced-and-later-found-unusable at extraction time, if the archive
cannot be opened as a ZIP, is missing a required member, or disagrees with
the acquired file's own size or digest on a member it does contain
(`test_create_bundle_manifest_refuses_a_non_zip_archive`,
`test_create_bundle_manifest_refuses_an_archive_missing_an_acquired_member`,
`test_create_bundle_manifest_refuses_an_archive_member_size_mismatch`,
`test_create_bundle_manifest_refuses_an_archive_member_content_mismatch`).

Every field is independently required, but `create_bundle_manifest` and
`verify_run_metadata` do NOT check the same fields — neither of them takes
a candidate SHA at all (that is `bind_bundle_to_candidate`'s job, and it
derives that SHA from `candidate_root`'s own `.git` refs rather than
accepting one; see "Ownership" above). `create_bundle_manifest` refuses a
missing/mismatched acquired file, a missing/empty archive, a schema_version
other than `MANIFEST_SCHEMA_VERSION`, and a `RunMetadata` that is not
already a validated instance (its own `__post_init__` refuses an
out-of-shape field even for a hand-built one). `verify_run_metadata`
independently refuses a missing repository (name or numeric ID), workflow
path, run id, run attempt, trusted workflow SHA (including the all-zero
null SHA), artifact id, artifact name (must match the pattern for the
CALLER's own recomputed candidate digest), artifact run ownership, and
environment name — a caller cannot supply "most of it" and have the rest
silently defaulted. Member `size` is part of the schema (it was previously
required by extraction's private zip-member reader but never actually
recorded anywhere), which is what lets extraction assert exact size AND
set equality entirely FROM the manifest, with no side-channel input.

## Trust chain (end state; the producer/binder workflows themselves are a
later slice)

1. A human dispatches the **producer** workflow, pinned to protected
   `main`, naming the exact commit whose dependency surface to bundle.
2. The producer job (the only job holding read access to the private index,
   analogous to today's `acquire`) resolves the surface, builds the ZIP,
   computes `plan_digest` via `compute_plan_digest`, and publishes it as a
   GitHub Actions artifact plus a `create_bundle_manifest` record naming its
   own run/artifact identity.
3. PR CI's **binder** step reads `.github/dependency-bundle-policy.json`
   from ITS OWN trusted checkout (the workflow file's commit, which for a
   push-triggered or centrally-defined reusable workflow is not
   candidate-controlled the way `workflow_dispatch`'s `github.sha` is),
   `verify_run_metadata`s the run against policy, passing the CANDIDATE's
   own checked-out tree as `candidate_root` so the function derives the
   expected plan digest itself (repository full name AND numeric ID, the
   PRODUCER workflow path specifically, positive run/attempt/artifact
   coordinates, a non-null commit SHA, the artifact's own run ownership,
   the required environment, and an artifact name matching the
   self-derived candidate plan digest — see "Refusal behaviour" for what
   this step does NOT prove), `verify_archive_digest`s the outer ZIP,
   `extract_verified_bundle`s it (staged, verified, and published
   atomically, with a stated narrow race — see "Extraction is private,
   safe, and atomic" below), and `bind_bundle_to_candidate`s the result —
   passing the same `candidate_root` (so the candidate's plan digest AND
   its actual git-derived commit SHA are both derived, never asserted) and
   the already-verified `RunMetadata` `verify_run_metadata` returned —
   which refuses unless the candidate's own digest equals the bundle's and
   that `RunMetadata` actually corresponds to this same bundle manifest's
   own run record.
4. On success, `build_local_index` materialises a local PEP 503 index the
   candidate's `poetry install` points at instead of
   `registry.dotmac.io` — no credential involved anywhere in PR CI.

## Extraction is private, safe, and atomic

`extract_verified_bundle` is the ONLY sanctioned way to extract a bundle
archive; the raw member-by-member extractor (`_extract_zip_members`) is
private and undocumented as a public API on purpose — nothing outside
`dependency_bundle.py` should be able to extract a ZIP without going
through verification. It:

- refuses a duplicate member name, an absolute path, a `..` traversal
  segment, a symlink, and a member whose name is not in the verified
  manifest (or vice versa);
- refuses two member names whose RESOLVED filesystem target is the same
  path — e.g. `a.whl` and `./a.whl` — not just a literal or lower-cased
  name collision;
- enforces a per-member size cap (`MAX_MEMBER_BYTES`), an aggregate member
  COUNT cap (`MAX_MEMBER_COUNT`), an aggregate uncompressed-size cap
  (`MAX_TOTAL_UNCOMPRESSED_BYTES`), and a per-member compression-ratio cap
  (`MAX_COMPRESSION_RATIO`) — independent limits, because a bundle built of
  many small, individually-legal members can still exhaust the extraction
  host on count or total size, and a highly-compressed member can pass a
  declared-size check while unpacking to something absurd relative to what
  was actually transferred;
- extracts into a FRESH, EXCLUSIVE staging directory
  (`tempfile.mkdtemp(dir=dest_dir.parent)`), never into `dest_dir` directly;
  verifies every member's size and content hash THERE; and only then
  publishes the complete, verified tree with one atomic `os.rename` into
  `dest_dir`, which must not already exist. On ANY failure the staging
  directory is removed and NOTHING is written to `dest_dir` — a caller
  never observes a partially-extracted destination (proven in
  `test_extraction_is_atomic_on_failure_nothing_is_published`). This is
  atomic against SEQUENTIAL failure, not a mutual-exclusion primitive: see
  "Publication has a stated, narrow race" below for the one honestly-named
  gap.

`build_local_index` (local PEP 503 index materialisation) has the
identical shape and the identical stated race: it also builds the WHOLE
index in a fresh staging directory and publishes with one atomic rename,
refusing a pre-existing `index_root`, refusing a package key or filename
that is not a safe bare name (no path separators, not absolute, not
`.`/`..` — a caller-controlled key or filename previously could escape the
staging tree entirely, since `Path.__truediv__` REPLACES the left operand
when the right is absolute), and encoding every filename TWICE before it
reaches a resolver-facing anchor, in two DIFFERENT ways for two DIFFERENT
reasons: `urllib.parse.quote(filename, safe="")` first, so the `href`'s
path segment is the correct URL encoding of the real filename (without
it, `#`/`?` inside a filename truncate the href at the wrong point — e.g.
`pkg#x.whl` used to request `pkg`, not the staged file — and a filename
that already looks percent-encoded, e.g. `%2e%2e...`, was never
re-encoded, one client-side decode away from being read back as a `..`
traversal spelling); then `html.escape(..., quote=True)` second, so that
already-URL-safe segment is also safe to embed in the HTML `href`
attribute. The human-visible anchor TEXT is HTML-escaped only, since it is
not itself a URL.

## Publication has a stated, narrow race

Both `extract_verified_bundle` and `build_local_index` check destination
existence, then call `os.rename`. There is no portable, dependency-free
"rename unless the destination exists" primitive for a directory target in
the Python standard library (POSIX `renameat2(..., RENAME_NOREPLACE)` is
Linux-only and is not exposed by `os`). Both functions re-check existence
a second time immediately before the rename, which narrows the window from
"the whole function's duration" to "the gap between that second check and
the syscall" — but does NOT close it: a concurrent process that creates an
EMPTY destination in that gap would have it silently replaced, because
POSIX `rename(2)` replacing an empty directory target is not an OS-level
error. This is deliberately not overstated as "atomic against concurrent
writers" anywhere in this document or the code: both functions are atomic
against sequential failure (a caller never observes a partial result), and
neither is a mutual-exclusion primitive against a concurrent, uncooperating
writer targeting the identical destination path. The intended usage — each
destination named for its own bundle/plan identity — makes two callers
targeting the same path an operational precondition violation rather than
an expected scenario; external locking is the caller's responsibility if
that assumption does not hold.

## Environment settings

`environment_name: "forgejo-registry-read-main"` is the GitHub Environment
the future producer workflow's credentialed job runs under (an environment
that can be protected-branch-gated and required-reviewer-gated
independently of who can dispatch a workflow file). Binder-side consumption
never runs under this environment and never receives the credential.

## Refusal behaviour

Every function in `scripts/dependency_bundle.py` that can fail raises one of
its `DependencyBundleError` subclasses (`ManifestError`, `PolicyError`,
`BundleVerificationError`, `ExtractionError`) — including the standard
library exceptions the underlying operations can raise. This was not
always true and is now enforced at every site that touches adversarial
input: `zipfile.BadZipFile` and `OSError` from opening or reading a ZIP
archive (`_extract_zip_members`'s archive open and its per-member
open/read/write loop, and `create_bundle_manifest`'s own archive-closure
check — see "Closure over the archive" above), `OSError` from reading an
extracted file (`verify_member_hashes`), from reading an acquired file or
the outer archive (`create_bundle_manifest`), from `scan_for_credential`
reading a target path, from `extract_verified_bundle`'s OWN
parent-directory creation and staging-directory creation
(`dest_dir.parent.mkdir` and `tempfile.mkdtemp` — the try/except
immediately below them, around `_extract_zip_members`/
`verify_member_hashes`, never covered these two calls that run BEFORE
it), and from every filesystem operation
`build_local_index` performs while staging or publishing a local index —
its own parent-directory creation, its staging index root, each package
directory, each package's own `index.html` write, listing the staged root
to build the top-level `index.html`, and that top-level write itself
(these last three sat inside a `except BaseException: cleanup; raise`
block that cleans up but never translates) — are all caught and re-raised
as the matching `DependencyBundleError` subclass. `build_local_index`
additionally refuses a non-string package key OUTRIGHT, before it ever
reaches `normalise_name`'s regex: `re.Pattern.match` raises a raw
`TypeError` on anything that is not a str/bytes-like object, which an
earlier version of this function left uncaught, and a charset-valid but
overlong package name (every character permitted by `normalise_name`, but
the whole string longer than the filesystem's per-component limit) is
refused via the same translated `OSError` path once the filesystem itself
rejects it — the charset/shape checks alone cannot catch a length
violation. A malformed `poetry.lock`
`[[package]]` entry that is not itself a table, or whose `source`,
`dependencies`, `groups`, `markers`, or `extras` field is not the shape
expected, is refused by an explicit type check rather than left to raise a
raw `AttributeError`/`TypeError` when something later calls `.get(...)` on
it. `verify_run_metadata` refuses a non-dict `metadata` or `policy`
outright rather than raising `AttributeError` on the first `.get(...)`.
`normalise_repository_url` catches the raw `ValueError` a malformed
authority (e.g. unbalanced IPv6 brackets) makes `urllib.parse.urlsplit`
itself raise, and returns the input unchanged — exactly like any other
spelling it does not recognise — rather than propagating it.
`load_permitted_off_index_dependencies` refuses a non-string `url`/`tag`
field at the point the policy is read, instead of failing later, far from
the cause. `load_policy`'s `artifact_name_pattern` check proves
`.format(plan_digest=...)` actually succeeds, not merely that the pattern
contains the substring `{plan_digest}` — a pattern with an extra field, a
bad conversion, or unbalanced braces used to reach
`verify_run_metadata`'s own `.format(...)` call as a raw
`KeyError`/`ValueError`/`IndexError`.

**Stated boundary, not silently assumed:** `policy` (the return value of
`load_policy`) is fully validated once, at `load_policy` itself;
`verify_run_metadata` additionally performs a LIGHT defensive check on its
own `metadata`/`policy` parameters (refusing a non-dict outright) without
re-running `load_policy`'s full schema validation — a caller is expected
to have already called `load_policy`, and this is a guard against a
grossly wrong-shaped argument, not a second full validation pass. A
`RunMetadata` instance is validated once, at its own construction
(`__post_init__`), which ALSO refuses construction outright unless the
caller supplies the module-private `_RUN_METADATA_PROVENANCE_TOKEN`
sentinel by identity — `verify_run_metadata` is the only production path
that holds it. Before this, `RunMetadata`'s constructor was fully public
and `__post_init__` validated shape only, so any caller could hand a
shape-valid instance to `create_bundle_manifest`/`bind_bundle_to_candidate`
and have it treated as proof verification ran — a convention ("only
`verify_run_metadata` builds these"), not an enforced boundary; the
sentinel makes constructing one outside that path an unmistakable,
deliberate act rather than something reachable by accident.
`bind_bundle_to_candidate` additionally refuses a `run`
argument that is not a `RunMetadata` instance at all, and cross-checks
that its `run_id`/`artifact_id` actually correspond to the SAME values
inside `bundle_manifest`'s own `run` record — refusing to mix a validly-
shaped `RunMetadata` verified against a DIFFERENT bundle into this one. A
`bundle_manifest` dict is fully shape-checked at every point it is read in
both `extract_verified_bundle` and `bind_bundle_to_candidate` (every
`members[...]`/`run[...]` access is behind an `isinstance`/key-presence
check, never a bare index). None of these functions falls back to fetching
from the registry on a verification failure — see that module's "No
registry fallback" docstring section. A caller that hits a refusal must
obtain a new, independently verifiable bundle; there is no degraded-trust
path.

**`verify_run_metadata` is LOCAL validation only — this is a deliberate,
stated limit, not an oversight.** It proves that a metadata dict, IF
genuine, describes an acceptable run: the right repository (by name AND
immutable numeric ID), the producer workflow specifically, positive
run/attempt/artifact coordinates, a non-null commit SHA, an artifact that
actually belongs to the claimed run, the required environment, and an
artifact name matching the candidate's own plan digest. It does NOT prove
the metadata dict is what GitHub actually says right now — that requires
calling the GitHub API (or otherwise obtaining a provenance attestation)
against the run's own identity and is explicitly **step 2**, out of scope
for this function and this slice. Treat a pass here as "this metadata, if
genuine, describes an acceptable run", never as "this metadata is
genuine" — the live API comparison that closes that gap is a later,
separate piece of work.

## Expiry behaviour

`artifact_retention_days: 14` in policy names the GitHub Actions artifact
retention window the producer workflow applies. A binder that cannot fetch
the artifact because it has expired gets the same refusal as any other
missing-artifact case — expiry is not a distinguished "trust it anyway"
path, it is an ordinary verification failure, and the operator's remedy is
the same either way: dispatch the producer again for the commit that needs
a live bundle.

## Operator dispatch instructions (for the future producer workflow)

1. Confirm the exact 40-hex commit SHA of protected `main` (or the specific
   reviewed commit) whose dependency surface needs a bundle.
2. Dispatch the producer workflow with that SHA as its `ref` input — a
   branch name is refused, for the same reason `erp-lock.yml` refuses one
   today.
3. Wait for the run to complete; note its `run_id` and the artifact's
   `artifact_id` from the run summary.
4. A PR that needs this bundle references it by `plan_digest` (via the
   artifact name pattern `erp-dependency-bundle-{plan_digest}` in policy);
   the binder step locates and verifies it without any further operator
   action.
5. If a PR's own dependency surface changes (a new private pin), a new
   bundle must be produced for the new plan digest — an old bundle's digest
   will not match, and `bind_bundle_to_candidate` refuses rather than
   silently reusing a stale one.

## Named duplication debt (see also the module docstring)

The independent planning pass that decided this slice originally called for
moving `erp_lock.py`'s generic acquisition ORCHESTRATION, hashing,
URL-validation, and credential-scan mechanics into
`scripts/dependency_bundle.py`. That move is deliberately NOT part of this
slice, because `erp_lock.py` backs `.github/workflows/erp-lock.yml`, which
PR #563 currently depends on, and refactoring credential-adjacent
orchestration out from under an in-flight PR is not worth the
destabilisation risk here.

**One exception, drawn narrowly.** A duplicated PURE function — no
credential, no I/O, just semantics — is a different kind of risk than a
duplicated orchestration step: it can silently DRIFT, and it already had.
`dependency_bundle`'s original PEP 503 name normaliser stripped a
leading/trailing separator; `erp_lock`'s did not; the two scripts could
therefore disagree about a package's identity. The fix is
`scripts/dependency_normalisation.py`: the ONE owner of that semantics.
`erp_lock.py`'s only change in this repair is importing it
(`_normalised = dependency_normalisation.normalise_name`) in place of its
own definition — its credentialed acquisition orchestration is otherwise
untouched.

Everything else that overlaps — `sha256_hex`, `approved_artifact_url`, and
the credential-scanning pair (`scan_for_credential`/`credential_sightings`,
`credential_encodings`) — remains a named, ORCHESTRATION-adjacent
duplicate, because unifying it means moving `erp_lock.py`'s credentialed
job shape, which is still out of scope here. **This duplication is
enforced, not just documented, two ways:**

- `docs/architecture/dependency-bundle-duplication-inventory.json` is the
  canonical, two-directional inventory: each entry names the behaviour and
  its two symbol locations (`dependency_bundle_symbol`, `erp_lock_symbol`).
  `tests/architecture/test_dependency_bundle.py` drives BOTH
  implementations of each LISTED duplicated behaviour through ONE shared
  table of adversarial input vectors and asserts they agree on every vector
  — a divergence between the copies is a test failure, not a discovery
  years later. The same test asserts every entry's two symbols still
  resolve to a real callable in both modules (a stale entry describing
  something already unified fails), and asserts the inventory's entry-id
  set is a SUBSET of a baseline hardcoded in the test — the inventory may
  only SHRINK. A new duplicated behaviour added to the inventory without
  the test's baseline being deliberately widened in the same reviewed
  change fails the build.
- **The list alone cannot substitute for one owner of pure semantics**, and
  it was previously satisfiable while broken — it only resolved LISTED
  behaviours, so an UNLISTED duplicate was invisible to it (this is
  precisely how `credential_encodings` sat undetected as a second, verbatim
  copy in both scripts until this repair). `find_unlisted_duplicate_helpers`
  in the same test file independently parses every function body in BOTH
  modules and flags any pair — not already on the list — whose bodies are
  near-identical (a `difflib.SequenceMatcher` ratio at or above 0.8,
  chosen to catch `credential_encodings`'s 0.93 while ignoring the two
  scripts' unrelated `main()` CLI wrappers' incidental 0.74 similarity).
  `test_no_unlisted_duplicate_security_helpers_between_the_two_modules`
  fails the build the moment a new unlisted near-duplicate appears, whether
  it is old debt just found or freshly introduced.

**Retirement condition:** the consumer-cutover slice that points
`erp-lock.yml` and the future producer/binder workflows at this module
moves `erp_lock.py` onto the shared functions here, deletes its own copies,
and removes the corresponding entries from the inventory. When the
inventory is empty, the non-growing guard stands permanently at zero.

## Named divergences from `erp_lock.py` — deliberate, not converged

A body-similarity detector (see above) only catches TEXTUAL duplication.
An independent review found three pairs of SEMANTICALLY related checks
between `dependency_bundle.py` and `erp_lock.py` that the detector could
not see at all — each far below its similarity threshold — plus a
duplicated pair of CONSTANTS the detector cannot see in any form. For each
pair, the question was: converge on one owner, or document and prove the
two are genuinely answering different questions. All four are covered by
tests; none required an `erp_lock.py` change beyond what "Named
duplication debt" above already lists.

- **The exact-version regex.** `erp_lock._EXACT_VERSION` allows exactly
  ONE non-stackable pre/post/dev suffix, because it exists only to
  validate the two specific, already-known `ALLOWED_MOVEMENTS` version
  strings in a closed, reviewed workflow.
  `dependency_bundle._EXACT_VERSION` must recognise the full space of
  exact PEP 440 versions for ANY future forgejo-sourced pin, including a
  real, stackable spelling (e.g. `1.0a1.post1`) `erp_lock`'s narrower
  regex refuses. NOT converged: `dependency_bundle`'s shape is the correct
  one for its broader job. Proven in
  `test_the_version_regex_divergence_is_named_not_a_bug`.
- **The additional-source policy.** `erp_lock.manifest_problems` refuses
  ANY second `[[tool.poetry.source]]` entry, because it validates a
  manifest for a LIVE, credentialed Poetry resolution, where an unrelated
  extra index could still change what that resolution does.
  `dependency_bundle` never runs Poetry and holds no credential; it only
  needs to know whether a second source could be MISTAKEN for the private
  one, so it refuses only a second source that also names the forgejo
  host under a different name. NOT converged: genuinely different threat
  models. Proven in
  `test_an_unrelated_second_source_is_a_named_divergence_not_a_bug`. (The
  one bug found in this same pair — a manifest source URL missing its
  trailing slash used to be ACCEPTED here and already REFUSED by
  `erp_lock` — was NOT a legitimate difference and was converged onto
  `erp_lock`'s stricter, exact-match behaviour; see "The plan digest"
  above.)
- **Lock-package source validation.** `erp_lock.acquisition_plan`'s
  lock-side loop includes a package in its plan by checking ONLY
  `source.reference == "forgejo"` — it never checks `source.type` or
  `source.url`. `dependency_bundle._lock_packages` requires all three
  fields to agree, refusing a lock entry whose `reference` matches but
  whose `type`/`url` do not. The STRICTER behaviour is the objectively
  correct one — keying on one field alone is exactly the spoofable
  shortcut this module exists to refuse elsewhere — but it is NOT
  converged onto `erp_lock.py` in this branch: doing so means tightening
  `acquisition_plan`, which is credentialed acquisition-workflow logic
  outside this branch's bounds (the only permitted `erp_lock.py` changes
  are the import-only ones "Named duplication debt" lists). This is a
  real, tracked, currently-unfixed gap in `erp_lock.py`, named explicitly
  in `_lock_packages`' own docstring and proven with a planted vector in
  `test_lock_packages_is_stricter_than_erp_locks_acquisition_plan` — it
  needs an explicitly authorised follow-up change to `erp_lock.py` itself,
  not a silent carry-forward.

  **This is retirement-coupled debt, not a permanent specification of the
  weaker behaviour**, and the test above is a TWO-DIRECTIONAL RATCHET: it
  fails if the asymmetry widens (either side's check changes in a way that
  moves either of its assertions) exactly as much as if the asymmetry
  disappears (`acquisition_plan` is tightened to also require `type` and
  `url`, so it would then refuse the same malformed `dotmac-kernel` vector
  the test currently asserts it silently accepts). A future authorised
  change that tightens `acquisition_plan` MUST make this test fail — that
  failure is the signal to delete the test's debt-tracking assertion, this
  paragraph, and the "NOT converged" language in `_lock_packages`'s
  docstring together, in the same change. Tightening `acquisition_plan`
  while leaving this test green (by loosening or deleting it first, or by
  updating it quietly) would let the gap close without record; a test that
  stays red until the corresponding documentation catches up is the
  correct outcome, not a build failure to route around.
- **The off-index policy allowlist.** The body-similarity detector cannot
  see duplicated CONSTANTS at all:
  `.github/dependency-bundle-policy.json`'s
  `permitted_off_index_dependencies` and
  `erp_lock.ALLOWED_OFF_INDEX_DEPENDENCIES` name the same pin
  (`dotmac-integration-client`, its url, tag, and commit) in two places
  that nothing compares. A dedicated guard,
  `test_the_live_off_index_policy_agrees_with_erp_locks_hardcoded_allowlist`,
  reads BOTH live sources directly (not values recreated as test-local
  constants) and fails if a human ever edits one without the other.

## Repository ID: resolved

`.github/dependency-bundle-policy.json`'s `repository.id` is
`1141216651` — the real, immutable numeric GitHub repository ID for
`michaelayoade/dotmac_erp`, read from the GitHub API
(`gh api repos/michaelayoade/dotmac_erp --jq .id`) on 2026-09-13, after the
original slice shipped it as `null` because obtaining it required a network
call that slice's authoring bounds forbade.
`scripts/dependency_bundle.py::load_policy` still refuses any policy whose
`repository.id` is not a positive integer — that refusal was never removed,
only proven no longer necessary for the shipped file (kept alive by a test
that plants a bad id: `test_a_non_positive_or_wrongly_typed_repository_id_is_still_refused`,
covering zero, negative, `null`, a numeric STRING, and a float — the string
form matters most, since a looser check would have accepted it).

The numeric ID is checked ALONGSIDE the full name, not instead of it,
because a GitHub repository's `full_name` changes on rename or transfer
while its numeric `id` never does; checking both means a policy written
against `michaelayoade/dotmac_erp` cannot be silently satisfied by some
other repository that briefly held that name after a rename, and a rename
of THIS repository does not, by itself, invalidate an otherwise-correct
policy binding (though the `full_name` check would still need updating to
match).
