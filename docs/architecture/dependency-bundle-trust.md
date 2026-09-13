# Dependency-bundle trust chain

**Status:** slice 1a, since repaired against six adversarial review findings
(the "1a repair" below) — still purely additive. Nothing in this repository
consumes this contract yet. `scripts/dependency_bundle.py`,
`scripts/dependency_normalisation.py`,
`.github/dependency-bundle-policy.json`, and
`docs/architecture/dependency-bundle-duplication-inventory.json` are the
mechanics and canonical policy for the end state; no workflow, Dockerfile,
`pyproject.toml`, or `poetry.lock` changed in this slice.
`scripts/erp_lock.py` changed in exactly one narrow way: it now imports
`dependency_normalisation.normalise_name` instead of defining its own copy
(see "Named duplication debt" below) — its credentialed acquisition
orchestration is untouched.

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
- candidate-specific rebinding (binding a verified bundle to one candidate
  commit whose own recomputed plan digest matches it).

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

**Construction:**

1. Parse `pyproject.toml` and `poetry.lock` with `tomllib`.
2. Refuse: an unknown dependency table (`[tool.poetry.dev-dependencies]`,
   PEP 621 `[project.dependencies]`/`[project.optional-dependencies]`);
   `[tool.poetry.requires-plugins]` (Poetry loads and imports plugins
   BEFORE it resolves anything — arbitrary candidate-controlled code in a
   future credential-bearing producer step, refused for the identical
   reason `erp_lock.py` refuses it); a duplicate dependency declaration; a
   present `poetry.toml`; an alternate URL on the `forgejo`-named source, or
   any other named source pointing at the same host; a direct registry URL
   bypassing the named source; an off-index dependency form that is not the
   policy's exact pinned identity; a version range (any operator, not an
   exact pin) on a `source = "forgejo"` dependency; and any manifest/lock
   disagreement (a declared forgejo pin or off-index pin with no matching
   lock entry, a different resolved version, or a lock `resolved_reference`
   that disagrees with the pinned commit).
3. Extract: the normalised `forgejo` source URL; every Poetry dependency
   (across `[tool.poetry.dependencies]` and every
   `[tool.poetry.group.<name>.dependencies]`) that declares
   `source = "forgejo"`, including its group, PEP-503-normalised name, exact
   version, markers, extras, `optional`, and per-dependency `python`
   constraint; every approved off-index dependency's group, url, tag, and
   resolved commit; every `poetry.lock` `[[package]]` entry whose
   `source.reference == "forgejo"` — direct OR transitive — including its
   normalised name, version, groups, `optional`, `python-versions`,
   dependencies, full source record, and every `(file, hash)` pair; and the
   schema/policy version plus target Python constraint and platform.
4. Serialise as canonical UTF-8 JSON: `json.dumps(doc, sort_keys=True,
   separators=(",", ":"))` plus a trailing newline.
5. Hash: `SHA256(b"dotmac.erp-dependency-plan.v1\0" + canonical_json_bytes)`.

**What changes the digest:** any private pin version, any private
transitive dependency's presence/version/hash, the forgejo source URL, a
group assignment, a marker, an extra, `optional`, a per-dependency `python`
constraint, a lock package's `python-versions`, a wheel/sdist filename, a
published hash, or an approved off-index dependency's pinned URL/tag/commit.
Every one of these is proven by a dedicated before/after mutation test in
`tests/architecture/test_dependency_bundle.py`.

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

**`create_bundle_manifest` COMPUTES every binding rather than accepting it
as an independent scalar.** It takes a `DependencySurface` and a mapping of
`AcquiredMember`s (what the producer job actually downloaded and hashed) —
never a bare `plan_digest` string or `member_hashes` dict. `plan_digest` is
derived by calling `compute_plan_digest(surface)` internally; `members` is
built by requiring EXACT closure between `planned_artifacts(surface)` and
the acquired members in BOTH directions — every planned `(filename,
sha256)` must be present in `acquired_members` with an agreeing digest
(`missing` refuses), and every acquired member must be named by the plan
(`extra`/unaccounted-for refuses). Because there is only one `surface`
input, there is no seam at which a second plan's identity could be
substituted for a different file set — this is what closes the "plan A's
digest attached to plan B's files" gap the original schema had (proven in
`test_mixing_one_plans_digest_with_a_different_plans_files_is_refused`).

Every field is independently required; `create_bundle_manifest` and
`verify_run_metadata` refuse a missing repository, candidate SHA, trusted
workflow SHA, run id, run attempt, plan digest, archive digest, or member
hash independently of every other field's presence — a caller cannot supply
"most of it" and have the rest silently defaulted. Member `size` is now
part of the schema (it was previously required by `safe_extract_zip` but
never actually recorded anywhere), which is what lets extraction assert
exact size AND set equality entirely FROM the manifest, with no
side-channel input.

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
   computes the CANDIDATE's own plan digest from the PR's checked-out
   `pyproject.toml`/`poetry.lock` FIRST, fetches the bundle manifest and
   archive via the GitHub API, `verify_run_metadata`s the run against
   policy with that candidate digest as `expected_plan_digest` (repository
   full name AND numeric ID, the PRODUCER workflow path specifically,
   positive run/attempt/artifact coordinates, a non-null commit SHA, the
   artifact's own run ownership, the required environment, and an artifact
   name matching the candidate's plan digest — see "Refusal behaviour" for
   what this step does NOT prove), `verify_archive_digest`s the outer ZIP,
   `extract_verified_bundle`s it (staged, verified, and published
   atomically — see "Extraction is private, safe, and atomic" below), and
   `bind_bundle_to_candidate`s the result — which refuses unless the
   candidate's own digest equals the bundle's.
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
  `test_extraction_is_atomic_on_failure_nothing_is_published`).

## Environment settings

`environment_name: "forgejo-registry-read-main"` is the GitHub Environment
the future producer workflow's credentialed job runs under (an environment
that can be protected-branch-gated and required-reviewer-gated
independently of who can dispatch a workflow file). Binder-side consumption
never runs under this environment and never receives the credential.

## Refusal behaviour

Every function in `scripts/dependency_bundle.py` that can fail raises one of
its `DependencyBundleError` subclasses (`ManifestError`, `PolicyError`,
`BundleVerificationError`, `ExtractionError`). None of them falls back to
fetching from the registry on a verification failure — see that module's
"No registry fallback" docstring section. A caller that hits a refusal must
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
