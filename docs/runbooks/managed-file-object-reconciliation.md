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
   `plan_orphan_cleanup`. Nothing is deleted. The returned safe summary
   carries counts (including `managed_objects`, `referenced_objects`,
   `in_flight_unreferenced`, `missing_references`, `boundary_drift` — the
   exact reference-view fields apply will re-verify), up to 100 candidate-key
   digests, `older_than`, `plan_observed_at`, and a `plan_digest` — a SHA-256
   over all of the above plus the scope kind, tenant id, and provider code.
   Review the summary before proceeding.
2. **Reviewed apply.** Invoke again with `apply=True` and BOTH
   `expected_plan_digest` and `reviewed_older_than` set to EXACTLY the
   `plan_digest` and `older_than` fields from the dry-run summary you
   reviewed. `older_than` is a fixed point in time, not a relative
   `grace_hours` — the apply run derives its own grace period from this
   run's fresh observation time minus that fixed cutoff, and refuses unless
   the cutoff is still at least 168 hours (7 days) old. (A relative
   `grace_hours` on the apply path was tried and rejected: `older_than =
   now() - grace` recomputed on a later run can never equal the dry-run's
   `older_than`, so the plan digest — which binds `older_than` — could never
   match and apply would always refuse. Binding the review to the dry-run's
   fixed cutoff instead of a relative window is what makes a real apply
   possible.) The task recomputes the plan from a fresh listing and a fresh
   session (never a cached one), confirms the fresh report's cutoff still
   matches `reviewed_older_than`, and calls `authorize_apply`, which refuses
   on digest drift, an over-cap candidate count, a plan older than 24 hours
   (`plan_observed_at`, derived as `older_than + 168h`, compared against
   "now"), or a reference view that cannot be trusted (see below). Only
   then does the per-object recheck-and-delete loop run.
3. **Per-object recheck, one key at a time, in order.** For each candidate
   key: (i) opens a short, separate `session_for_org` and checks whether ANY
   `TenantStoredFile` row now claims that key — any provider code, any
   state — and closes that session before any storage call; a hit records
   `rechecked_referenced` and skips the delete; (ii) re-observes the live
   object (see "Listing completeness" for why this uses the provider's own
   `list`, scoped to the single key, rather than `dotmac_files.observe_object`
   — that primitive requires an existing metadata row and returns presence
   only, never a modification time, so it cannot be given an orphan
   candidate, which has no row by definition); missing records
   `already_absent`, not-old-enough records `rechecked_too_new`; (iii)
   otherwise deletes that ONE key through
   `app.services.storage.delete_reviewed_file_orphan`, recording `deleted`
   or, on an exception, `failed` — and STOPS the loop at the first failure.
   The summary carries per-outcome counts and up to 100 key digests per
   outcome; `deleted` is the real, per-object-confirmed count, and raw keys
   are never logged.

Refusals (all raise before any deletion; none deletes partially):

- `expected_plan_digest` missing on an apply — `ValueError`.
- `reviewed_older_than` missing, or not a timezone-aware ISO-8601 value, on
  an apply — `ValueError`.
- `reviewed_older_than` is less than 168 hours (7 days) before this run's
  observation time — `ValueError`.
- The fresh report's cutoff no longer matches `reviewed_older_than`, or the
  fresh plan's digest does not match `expected_plan_digest` (a key appeared
  or disappeared, or any reference-view count changed, between review and
  apply) — `app.services.file_object_cleanup.CleanupPlanDrift`.
- The candidate count exceeds the plan's `max_deletions` (default 100, hard
  ceiling 1000 — a `max_deletions` above the ceiling is refused when the plan
  is built) — `app.services.file_object_cleanup.CleanupCapExceeded`.
- The reviewed plan is more than 24 hours old (`now - plan_observed_at > 24h`,
  equivalently: `reviewed_older_than` more than 24h + 168h before now) —
  `app.services.file_object_cleanup.CleanupPlanExpired`.
- Every managed object looks unreferenced while candidates exist (this is
  indistinguishable from a hidden-rows or RLS failure making a healthy
  tenant look fully orphaned), or any metadata row was found outside its
  declared scope prefix (`boundary_drift > 0`) —
  `app.services.file_object_cleanup.CleanupUnsafeReferenceView`. There is no
  override flag for this refusal in this slice.
- The live provider's code does not match the plan's `provider_code` at the
  moment of an individual delete — `dotmac_files.ProviderMismatch`, raised
  by `app.services.storage.delete_reviewed_file_orphan`.

**`dotmac_files.delete_orphans` (and the paired `delete_object` /
`finalize_purge` primitives) may only be called from
`app/services/storage.py`** — enforced by
`tests/architecture/test_dotmac_files_delete_owner.py`, with a planted
sensitivity proof (a direct call, an aliased import, and an attribute-form
call are all caught; a same-named local function is not).

Legacy ERP upload prefixes are out of scope for this task, exactly as they
are out of scope for the report above; do not extend `expected_plan_digest`
review or apply to keys outside the `tenants/<uuid>/files/` /
`platform/files/` prefixes.
