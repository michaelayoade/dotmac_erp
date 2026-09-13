# Dependency-bundle trust chain

**Status:** slice 1a — purely additive. Nothing in this repository consumes
this contract yet. `scripts/dependency_bundle.py`,
`.github/dependency-bundle-policy.json`, and
`docs/architecture/dependency-bundle-duplication-inventory.json` are the
mechanics and canonical policy for the end state; no workflow, Dockerfile,
`scripts/erp_lock.py`, `pyproject.toml`, or `poetry.lock` changed in this
slice.

## Ownership

`scripts/dependency_bundle.py` owns:

- dependency-surface validation and extraction from `pyproject.toml` +
  `poetry.lock`;
- the plan-digest computation (below);
- canonical bundle-manifest creation;
- GitHub run/artifact metadata verification (of already-fetched JSON — this
  module makes no network call);
- outer ZIP digest verification;
- safe ZIP extraction;
- contained-file hash verification;
- local PEP 503 index materialisation;
- candidate-specific rebinding (binding a verified bundle to one candidate
  commit whose own recomputed plan digest matches it).

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

**Construction:**

1. Parse `pyproject.toml` and `poetry.lock` with `tomllib`.
2. Refuse: an unknown dependency table (`[tool.poetry.dev-dependencies]`,
   PEP 621 `[project.dependencies]`/`[project.optional-dependencies]`); a
   duplicate forgejo-sourced dependency declaration; a present
   `poetry.toml`; an alternate URL on the `forgejo`-named source, or any
   other named source pointing at the same host; a direct registry URL
   bypassing the named source; a version range (any operator, not an exact
   pin) on a `source = "forgejo"` dependency; and any manifest/lock
   disagreement (a declared forgejo pin with no matching lock entry, or a
   different resolved version).
3. Extract: the normalised `forgejo` source URL; every Poetry dependency
   (across `[tool.poetry.dependencies]` and every
   `[tool.poetry.group.<name>.dependencies]`) that declares
   `source = "forgejo"`, including its group, PEP-503-normalised name, exact
   version, markers, and extras; every `poetry.lock` `[[package]]` entry
   whose `source.reference == "forgejo"` — direct OR transitive — including
   its normalised name, version, groups, dependencies, full source record,
   and every `(file, hash)` pair; and the schema/policy version plus target
   Python constraint and platform.
4. Serialise as canonical UTF-8 JSON: `json.dumps(doc, sort_keys=True,
   separators=(",", ":"))` plus a trailing newline.
5. Hash: `SHA256(b"dotmac.erp-dependency-plan.v1\0" + canonical_json_bytes)`.

**What changes the digest:** any private pin version, any private
transitive dependency's presence/version/hash, the forgejo source URL, a
group assignment, a marker, an extra, a wheel/sdist filename, or a
published hash.

**What does not:** TOML comments, whitespace, or key order; the
application's own `[tool.poetry].version`; and any public (non-forgejo)
package's presence, version, or hash — public packages never enter the
parsed `DependencySurface` at all, so they cannot influence the digest by
construction.

## Bundle manifest schema

```json
{
  "schema_version": 1,
  "plan_digest": "<64-hex sha256>",
  "archive_sha256": "<64-hex sha256>",
  "members": {"<member-name>": "<64-hex sha256>", "...": "..."},
  "run": {
    "repository_full_name": "michaelayoade/dotmac_erp",
    "repository_id": <int>,
    "workflow_path": ".github/workflows/dependency-bundle-produce.yml",
    "run_id": <int>,
    "run_attempt": <int>,
    "trusted_workflow_sha": "<40-hex commit sha>",
    "artifact_id": <int>,
    "artifact_name": "erp-dependency-bundle-<plan_digest>"
  }
}
```

Every field is independently required; `create_bundle_manifest` and
`verify_run_metadata` refuse a missing repository, candidate SHA, trusted
workflow SHA, run id, run attempt, plan digest, archive digest, or member
hash independently of every other field's presence — a caller cannot supply
"most of it" and have the rest silently defaulted.

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
   fetches the bundle manifest and archive via the GitHub API,
   `verify_run_metadata`s the run against policy (repository full name AND
   numeric ID, workflow path, environment name), `verify_archive_digest`s
   the outer ZIP, `safe_extract_zip`s it against the manifest's declared
   members, `verify_member_hashes`s every extracted file, computes the
   CANDIDATE's own plan digest from the PR's checked-out
   `pyproject.toml`/`poetry.lock`, and `bind_bundle_to_candidate`s — which
   refuses unless the candidate's own digest equals the bundle's.
4. On success, `build_local_index` materialises a local PEP 503 index the
   candidate's `poetry install` points at instead of
   `registry.dotmac.io` — no credential involved anywhere in PR CI.

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
moving `erp_lock.py`'s generic acquisition, hashing, URL-validation, and
credential-scan mechanics into `scripts/dependency_bundle.py`. That move is
deliberately NOT part of this slice, because `erp_lock.py` backs
`.github/workflows/erp-lock.yml`, which PR #563 currently depends on, and
refactoring credential-adjacent code out from under an in-flight PR is not
worth the destabilisation risk here.

Instead, `scripts/dependency_bundle.py` implements fresh copies of three
overlapping behaviours: `sha256_hex`, `approved_artifact_url`, and
`credential_encodings`/`scan_for_credential`. **This duplication is
enforced, not just documented:**

- `docs/architecture/dependency-bundle-duplication-inventory.json` is the
  canonical, two-directional inventory: each entry names the behaviour and
  its two symbol locations (`dependency_bundle_symbol`,
  `erp_lock_symbol`).
- `tests/architecture/test_dependency_bundle.py` drives BOTH
  implementations of each duplicated behaviour through ONE shared table of
  adversarial input vectors and asserts they agree on every vector — a
  divergence between the copies is a test failure, not a discovery years
  later.
- The same test asserts every entry's two symbols still resolve to a real
  callable in both modules (a stale entry describing something already
  unified fails), and asserts the inventory's entry-id set is a SUBSET of a
  baseline hardcoded in the test — the inventory may only SHRINK. A new
  duplicated behaviour added to the inventory without the test's baseline
  being deliberately widened in the same reviewed change fails the build.

**Retirement condition:** the consumer-cutover slice that points
`erp-lock.yml` and the future producer/binder workflows at this module
moves `erp_lock.py` onto the shared functions here, deletes its own copies,
and removes the corresponding entries from the inventory. When the
inventory is empty, the non-growing guard stands permanently at zero.

## Unresolved: the repository ID field

`.github/dependency-bundle-policy.json`'s `repository.id` is currently
`null`, with an `id_status` field stating it is unresolved. This is not an
oversight in this slice: obtaining the real, immutable numeric GitHub
repository ID for `michaelayoade/dotmac_erp` requires a GitHub API call
(`gh api repos/michaelayoade/dotmac_erp --jq .id`, or reading it from the
repository's own Settings page), and this implementation slice was bounded
to make no network call of any kind. Nothing in this repository consumes
the policy file yet, so shipping it with an unresolved ID does not create a
trust gap today — but `scripts/dependency_bundle.py::load_policy` refuses
to accept the policy while `repository.id` is not a positive integer, so
this cannot be silently forgotten: the first attempt to actually use this
policy (in the consumer-cutover slice) will fail loudly and name this exact
section as the fix. A human with network access must fill in the real value
before that slice lands.

The numeric ID matters ALONGSIDE the full name, not instead of it, because
a GitHub repository's `full_name` changes on rename or transfer while its
numeric `id` never does; checking both means a policy written against
"`michaelayoade/dotmac_erp`" cannot be silently satisfied by some other
repository that briefly held that name after a rename, and a rename of THIS
repository does not, by itself, invalidate an otherwise-correct policy
binding (though the `full_name` check would still need updating to match).
