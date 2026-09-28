# Managed file object reconciliation

ERP's `dotmac-files` module owns only objects beneath
`tenants/<tenant UUID>/files/` and `platform/files/`. Legacy ERP uploads use
other prefixes and different reference fields. They are outside this report.
The one ERP provider code is `erp_s3`.

The caller obtains observations through public `dotmac_files.list_objects`
**before** opening a database session. Then
`app.services.file_object_reconciliation.report_file_objects` accepts an
explicit `TenantScope` or `PlatformScope`, provider code, observation tuple,
observation start time, read session and grace period. It compares objects
with the appropriate stored-file table, and reports:

- all observed managed object keys in the in-process typed report;
- old, unreferenced candidate keys after the grace period (24 hours by default);
- recent unreferenced objects still inside the upload/metadata grace period;
- available or missing metadata rows older than the grace period whose
  in-scope keys were absent from the managed-scope listing;
- metadata keys outside their declared scope as a separate `boundary_drift`
  count and digest list. These are neither missing-object references nor
  orphan candidates.

The safe task result and log contain counts and up to 100 SHA-256 digests per
evidence class, with omitted counts, not raw keys, filenames, object bodies,
or other customer data. A digest is only an evidence correlation handle. A
missing-reference count is a possible mismatch between observations taken at
different times, not proof that an object is absent or a license to rewrite
its state. The row-age grace suppresses rows created while listing was in
flight. Provider errors and out-of-prefix results fail the run. A provider
that silently omits keys cannot be detected by this read-only comparison, so
check listing consistency before interpreting a missing-reference count.

The Celery task
`app.tasks.file_object_reconciliation.report_tenant_file_objects` is an
explicit operator-invoked dry-run for one organization UUID. Its
`grace_hours` argument must be at least 1. It uses `session_for_org`, which
arms ERP and shared-module tenant RLS. It constructs a read-only listing
provider that does not check for or create the bucket; the normal upload
provider's bucket-creation behavior is unchanged. There is no beat entry.
Do not schedule fleet-wide fanout until the deployment's tenant runtime
identity and `mod_files` privileges are verified. The platform plane has no
ERP runtime caller or approved platform session boundary, so it has no task
adapter. A service caller may report it only with an appropriately authorized
platform session.

The SQLite canaries prove service comparison and task ordering.
`tests/integration/test_file_object_reconciliation_rls.py` is the PostgreSQL
runtime-role canary: it inserts two tenant rows in a rolled-back transaction,
switches to `app_user`, and verifies that RLS exposes only the selected tenant
to both raw SQL and this report. Its CI result and deployment privilege
verification remain rollout evidence before operational use.

**Review gate status — how each item is now met (updated 2026-09-28):**

- **Per-object recheck of age and authoritative references immediately
  before each delete.** Met. See "Cleanup" below: `clean_tenant_file_objects`
  rechecks each candidate individually, in order, immediately before its own
  delete — never as a batch precondition.
- **A retention policy.** Met. Michael, 2026-09-28: 7 days (168 hours) is the
  floor an object's age must clear before APPLY may ever delete it
  (`MIN_APPLY_GRACE_HOURS`). A separate 24-hour plan-expiry rule additionally
  bounds how stale the REVIEWED dry-run itself may be — retention bounds the
  object's age; expiry bounds the review's age. Both are enforced by
  `app.services.file_object_cleanup.authorize_apply`.
- **An explicit operator invocation.** Met, unchanged: dry-run then reviewed
  apply, no beat schedule, no automatic trigger.
- **A verified complete listing.** Partially met — see "Listing completeness"
  below: provable for ERP's own provider from the underlying SDK's documented
  behavior, but this is an open ROLLOUT GATE, not a `dotmac_files` guarantee,
  until it is confirmed against the live bucket (see rollout gates below).
- **A named owner and privilege boundary for the platform plane.** NOT met —
  still an open gap. `clean_tenant_file_objects` remains tenant-only by
  construction (it recheck-queries `TenantStoredFile`); the platform plane
  still has no cleanup adapter, same as the report above.

**Durable record:** this decision (168h retention, 24h expiry, per-object
recheck design) is tracked as a completed slice; the durable debt-register
entry recording the remaining rollout gates and the platform-plane gap is
written in a follow-up slice, not this one.

**Rollout gates before the first real apply (none of these are code
changes; each is an operational confirmation):**

1. Bucket versioning on the production object store — confirm it is
   enabled. Deletion is irreversible unless the bucket keeps object
   versions; this was unverified as of this change.
2. A green PostgreSQL RLS canary run
   (`tests/integration/test_file_object_reconciliation_rls.py`) against the
   target deployment.
3. Verified runtime-role `mod_files` privileges for the deployment's tenant
   database role, consistent with the report task's own rollout note above.

## Listing completeness

`dotmac_files.physical.list_objects` (a4) performs NO pagination of its
own — it is exactly `tuple(provider.list(prefix))`. Completeness is
therefore a property of the PROVIDER, not of `dotmac_files`. ERP's provider
(`DotmacFilesS3Provider.list`, `app/services/storage.py`) calls minio-py's
`Minio.list_objects(bucket, prefix=..., recursive=True)` and immediately
materializes the full generator with `tuple(...)`. minio-py's `list_objects`
is documented and implemented to auto-paginate internally (it issues
successive `ListObjectsV2` calls with a continuation token and yields
lazily) — draining that generator to completion, as ERP's provider does,
therefore returns the complete listing. This is a real guarantee, but it is
a guarantee of the `minio` SDK version pinned in `pyproject.toml`, not a
`dotmac_files` contract — a future SDK change or a different provider
implementation could silently break it, which is why this is also listed as
an open rollout gate above pending a live confirmation against the
production bucket and its actual object count. A unit test
(`tests/services/test_file_object_cleanup.py
::test_list_objects_consumes_a_multi_page_generator_completely`) proves the
CONSUMING side: a fake provider yielding a large multi-batch generator is
still fully drained into the returned tuple.

## Cleanup (dry-run, then reviewed apply)

`app.tasks.file_object_reconciliation.clean_tenant_file_objects` is the
operator-invoked tenant cleanup task. It is **not** scheduled anywhere — there
is no beat entry, and every invocation is a deliberate operator action.

The two-step procedure:

1. **Dry-run.** Invoke with `apply=False` (the default), `grace_hours`
   defaulting to 168 (7 days). It lists objects, opens a read-only session,
   builds the same `report_file_objects` report as above, then turns it into
   an `app.services.file_object_cleanup.OrphanCleanupPlan` via
   `plan_orphan_cleanup`, passing THIS run's own real `datetime.now(UTC)` as
   `plan_observed_at`. Nothing is deleted. The returned safe summary carries
   counts (including all-age `managed_objects`/`referenced_objects`/
   `in_flight_unreferenced` for operator visibility, and the digest-bound
   `old_managed_objects`/`old_referenced_objects`/`missing_references`/
   `boundary_drift` — see "Digest stability" below), up to 100 candidate-key
   digests, `older_than`, `plan_observed_at`, and a `plan_digest`. Review the
   summary before proceeding, and copy BOTH `older_than` and
   `plan_observed_at` exactly for apply.
2. **Reviewed apply.** Invoke again with `apply=True` and `expected_plan_digest`,
   `reviewed_older_than`, AND `reviewed_plan_observed_at` set to EXACTLY the
   `plan_digest`, `older_than`, and `plan_observed_at` fields from the
   dry-run summary you reviewed. The task first checks, with no I/O, that
   `reviewed_older_than == reviewed_plan_observed_at - 168h` — the two
   reviewed values must be self-consistent. It then re-lists and re-queries
   at REAL wall-clock time, but labels the resulting report with
   `observed_at=reviewed_plan_observed_at` and a FIXED 168-hour grace —
   never a relative `now() - cutoff` computation. (A relative grace on the
   apply path was tried and rejected twice: first, `older_than = now() -
   grace` recomputed on a later run could never equal the dry-run's
   `older_than`, so the digest could never match and apply always refused;
   second, once fixed, `plan_observed_at` was still DERIVED as `older_than +
   168h` rather than the plan's real observed time, which let an operator
   defeat the 24-hour expiry by choosing a smaller dry-run `grace_hours` to
   manufacture an artificially "fresher"-looking `plan_observed_at` — see
   `app.services.file_object_cleanup`'s `MAX_PLAN_AGE_HOURS` docstring.
   Freezing BOTH the grace and the observation label to the reviewed values
   closes both holes at once.) `authorize_apply` then refuses on digest
   drift, an over-cap candidate count, a plan older than 24 real hours
   (comparing REAL current time against `plan_observed_at`, which for the
   rebuild equals `reviewed_plan_observed_at` exactly), or an untrustworthy
   OLD-object reference view (see below). Only then does the per-object
   recheck-and-delete loop run.
3. **Per-object recheck, one key at a time, in order.** For each candidate
   key, the ENTIRE body below is wrapped: any exception anywhere in it
   (not just the delete call) records that key as `failed`, with the
   exception's class name, and stops the loop immediately.
   (i) opens a short, separate `session_for_org` and checks whether ANY
   `TenantStoredFile` row now claims that key — any provider code, any
   state, via `app.services.file_object_cleanup.is_storage_key_referenced`
   — and closes that session before any storage call; a hit records
   `rechecked_referenced` and skips the delete; (ii) re-observes the live
   object (see "Listing completeness" for why this uses the provider's own
   `list`, scoped to the single key, rather than `dotmac_files.observe_object`
   — that primitive requires an existing metadata row and returns presence
   only, never a modification time, so it cannot be given an orphan
   candidate, which has no row by definition); missing records
   `already_absent`, not-old-enough records `rechecked_too_new`; (iii)
   otherwise deletes that ONE key through
   `app.services.storage.delete_reviewed_file_orphan` (which itself raises
   `dotmac_files.ProviderMismatch`, recorded here as `failed` like any other
   exception, if the live provider's code no longer matches the plan's),
   and logs one line with that key's digest as it happens. The summary
   ALWAYS carries per-outcome counts and up to 100 key digests per outcome
   and is ALWAYS logged; `deleted` is the real, per-object-confirmed count.
   If any key failed, the task raises
   `app.services.file_object_cleanup.CleanupPartialFailure` (carrying the
   full summary as its `.summary` attribute) after logging — the summary is
   never silently dropped, but a partial run is never reported as a plain
   success either.

**Digest stability.** The digest binds only values computed over objects
OLDER than the reviewed cutoff — the OLD-object count, the OLD-referenced
count, `missing_references` (already past-grace by construction), and
`old_boundary_drift` — plus `candidate_keys`, `older_than`, and
`plan_observed_at`. `boundary_drift` itself is reported at EVERY age (the
read-only report and the dry-run summary must show drift regardless of how
recently the out-of-scope row was created) and is deliberately NOT in the
digest; only its old-filtered twin, `old_boundary_drift`, is. The
`CleanupUnsafeReferenceView` safety refusal reads the ALL-AGE
`boundary_drift` count, not the old-filtered one — any drift, however
recent, still refuses. A fresh upload or a fresh metadata row appearing
between a dry-run and its apply is, by construction, not old enough to
affect any digest-bound value, so it can never itself cause
`CleanupPlanDrift`. Raw, all-age counts stay in the summary for visibility
only.

**A normal delete or purge of an OLD file between the dry-run and the apply
IS expected to refuse.** Deleting or purging a candidate (or any other old,
in-scope object) between review and apply changes an old-object count, so
the freshly rebuilt plan's digest no longer matches and apply refuses with
`CleanupPlanDrift`. This is the correct, safe outcome, not a bug: re-run the
dry-run and review the new plan before applying again.

Refusals (all raise before any deletion; none deletes partially):

- `expected_plan_digest`, `reviewed_older_than`, or `reviewed_plan_observed_at`
  missing on an apply — `ValueError`.
- `reviewed_older_than` or `reviewed_plan_observed_at` not a timezone-aware
  ISO-8601 value — `ValueError`.
- `reviewed_older_than != reviewed_plan_observed_at - 168h` — `ValueError`
  (a self-inconsistent reviewed pair; checked before any I/O).
- The freshly rebuilt plan's digest does not match `expected_plan_digest`
  (a candidate key appeared or disappeared, or any OLD-object/missing/
  boundary-drift count changed, since review) —
  `app.services.file_object_cleanup.CleanupPlanDrift`.
- The candidate count exceeds the plan's `max_deletions` (default 100, hard
  ceiling 1000 — a `max_deletions` above the ceiling is refused when the plan
  is built) — `app.services.file_object_cleanup.CleanupCapExceeded`.
- The reviewed plan is more than 24 REAL hours old
  (`now - plan_observed_at > 24h`, using the real current wall-clock time,
  never a reviewed value) — `app.services.file_object_cleanup.CleanupPlanExpired`.
- Every OLD managed object looks unreferenced while candidates exist (this is
  indistinguishable from a hidden-rows or RLS failure making a healthy
  tenant look fully orphaned), or any OLD metadata row was found outside its
  declared scope prefix (`boundary_drift > 0`) —
  `app.services.file_object_cleanup.CleanupUnsafeReferenceView`. There is no
  override flag for this refusal in this slice.
- ANY exception during one candidate's own recheck-and-delete body — a
  reference-check failure, a re-observation failure, a delete failure
  (including a live `dotmac_files.ProviderMismatch`) — stops the loop and,
  once the (always-logged) summary is built, raises
  `app.services.file_object_cleanup.CleanupPartialFailure`.

**`dotmac_files.delete_orphans`/`delete_object`/`finalize_purge` may only be
called from `app/services/storage.py`** — enforced by
`tests/architecture/test_dotmac_files_delete_owner.py` across plain,
aliased, attribute-form, AND submodule (`dotmac_files.physical`) imports,
each with a planted sensitivity proof (guard 1). The same file also asserts
that no module anywhere reaches the raw provider and calls `.delete(` on it
directly (guard 2) — bound to a local name first, or CHAINED
(`get_dotmac_files_provider().delete(...)`), through a plain import or an
aliased module import (`import app.services.storage as s` then
`s.get_dotmac_files_provider()`) — or constructs `DotmacFilesS3Provider(...)`
directly (guard 3), either of which would reach the raw provider without
`delete_reviewed_file_orphan`'s recheck, digest authorization, or
provider-identity assertion. **Known limitation:** guard 2 is an AST-only
scan and does not resolve a provider object passed in as a function
PARAMETER (e.g. `def f(provider): provider.delete(x)`) — only a locally
visible binding or a chained call is followed. A fourth, separate check —
"only storage.py may import `get_dotmac_files_provider` at all" (guard 4) —
is FALSE against this tree (three legitimate non-delete upload callers
already import it: `app/services/file_upload.py`,
`app/api/finance/import_export.py`, `app/tasks/imports.py`) and is therefore
a two-directional RATCHET over today's known callers, not an absolute rule.

Legacy ERP upload prefixes are out of scope for this task, exactly as they
are out of scope for the report above; do not extend `expected_plan_digest`
review or apply to keys outside the `tenants/<uuid>/files/` /
`platform/files/` prefixes.

## Durable deletion record (schema)

Slice 2a (schema only, 2026-09-28): `public.file_orphan_cleanup_runs` (one
row per apply invocation — plan identity, actor, invocation id, status,
outcome counts) and `public.file_orphan_cleanup_deletions` (one row per
candidate key processed — the raw `storage_key`, a `key_digest`, and its
outcome), both tenant-scoped with the same ERP-native
`app.current_organization_id` RLS predicate `sync.source_correlation` uses
(`20260825_retire_dotmac_crm.py`), following the `ar` schema's
outcome/issue composite-FK pattern (`20260906_invoice_sync_outcomes.py`).
Models live in `app/models/file_orphan_cleanup.py`; the three
session/commit-agnostic recorder functions
(`record_cleanup_run_started`/`record_cleanup_key_outcome`/
`record_cleanup_run_finished`) live alongside the plan/authorize logic in
`app/services/file_object_cleanup.py`.

**Slice 2b (2026-09-28): `clean_tenant_file_objects` now writes the durable
record for every apply.** `apply=True` requires a new, non-empty `actor`
argument naming the operator authorizing the delete — refused with
`ValueError` before any I/O if it is empty or missing. The task is now
`bind=True`; its `invocation_id` is the Celery request id
(`self.request.id`) when one is present (a real worker dispatch), or a freshly
generated UUID when called directly (no Celery request context, e.g. in a
test or a one-off script invocation).

After `authorize_apply` succeeds and strictly BEFORE any delete, the task
itself — never a helper — opens ONE records session
(`with session_for_org(org_id) as rec_db:`) and, within it:

1. Calls `record_cleanup_run_started` and commits `rec_db` immediately. No
   key's delete may run before this "running" row is durably committed.
2. Passes `rec_db` and the new run's id into the per-object
   recheck-and-delete loop. After EVERY key's outcome — `deleted`,
   `rechecked_referenced`, `already_absent`, `rechecked_too_new`, or `failed`
   (with its exception class name) — the loop calls `record_cleanup_key_outcome`
   and commits `rec_db` immediately, before considering the next key. The
   loop still opens its own short, separate read session for the reference
   recheck (closed before any storage call), exactly as before.
3. After the loop, calls `record_cleanup_run_finished` with `status=
   "completed"` (or `"partial_failure"` if any key failed) and the per-outcome
   counts, then commits `rec_db` one last time. Only after that does the task
   return the summary, or raise `CleanupPartialFailure`.
4. If recording itself raises anywhere in this block — the insert, or the
   commit — that exception propagates immediately: no further key is
   considered, and a delete can never happen without a preceding, committed
   "started" row.

This session-open-and-commit shape lives directly in the `@shared_task`(
`bind=True`)-decorated function body, so
`tests/architecture/test_own_session_commits.py` classifies it as
`adapter-owned: entry point (Celery task decorator)`, matching every other
task in this codebase that owns its own transaction — not a new
"grandfathered" hit. The per-key commits happen inside
`_recheck_and_delete`, but on `rec_db`, a parameter that function never opens
itself, so they are correctly excluded from that ratchet.

**Querying one run's rows (to support a restore).** Every row is tenant-scoped
by ERP's `app.current_organization_id` RLS predicate — query through a
tenant-scoped session (or as a superuser bypassing RLS for support), never
directly as `app_user` across tenants:

```sql
-- The run itself: plan identity, actor, invocation id, terminal status, counts.
SELECT id, actor, invocation_id, status, candidate_count, outcome_counts,
       started_at, finished_at
FROM public.file_orphan_cleanup_runs
WHERE id = :run_id;

-- Every candidate key this run processed, in the order recorded.
SELECT storage_key, key_digest, outcome, error_class, observed_last_modified,
       recorded_at
FROM public.file_orphan_cleanup_deletions
WHERE run_id = :run_id
ORDER BY recorded_at;
```

A `deleted` row's `storage_key` is exactly the object a restore must recover
from the object store's version history (see rollout gate 1 above — this is
why bucket versioning must be confirmed before the first real apply). A
`status = "partial_failure"` run's `failed` row names the key that stopped
the loop; every row before it in `recorded_at` order was durably processed
(and, for `deleted` rows, actually removed) before the failure.
