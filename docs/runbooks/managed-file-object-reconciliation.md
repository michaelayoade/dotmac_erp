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
provider's bucket-creation behavior is unchanged. There is no beat entry and no delete
path. Do not schedule fleet-wide fanout until the deployment's tenant runtime
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

**Review gate for future deletion:** establish a named owner and privilege
boundary for the platform plane, a verified complete listing, a tested
per-object recheck of age and authoritative references immediately before
each delete, a retention policy, and an explicit operator invocation. A
candidate in this dry-run must never be passed directly to
`dotmac_files.delete_orphans`; that primitive validates prefix only and does
not recheck age or references.

## Cleanup (dry-run, then reviewed apply)

`app.tasks.file_object_reconciliation.clean_tenant_file_objects` is the
operator-invoked tenant cleanup task. It is **not** scheduled anywhere — there
is no beat entry, and every invocation is a deliberate operator action.

The two-step procedure:

1. **Dry-run.** Invoke with `apply=False` (the default). It lists objects,
   opens a read-only session, builds the same `report_file_objects` report as
   above, then turns it into an `app.services.file_object_cleanup
   .OrphanCleanupPlan` via `plan_orphan_cleanup`. Nothing is deleted. The
   returned safe summary carries counts, up to 100 candidate-key digests, and
   a `plan_digest` — a SHA-256 over the scope kind, tenant id, provider code,
   `older_than`, and the exact sorted candidate key list. Review the summary
   (digest count, in-flight/missing/boundary-drift counts) before proceeding.
2. **Reviewed apply.** Invoke again with `apply=True` and BOTH
   `expected_plan_digest` and `reviewed_older_than` set to EXACTLY the
   `plan_digest` and `older_than` fields from the dry-run summary you
   reviewed. `older_than` is a fixed point in time, not a relative
   `grace_hours` — the apply run derives its own grace period from this
   run's fresh observation time minus that fixed cutoff, and refuses unless
   the cutoff is still at least 72 hours old. (A relative `grace_hours` on
   the apply path was tried and rejected: `older_than = now() - grace`
   recomputed on a later run can never equal the dry-run's `older_than`, so
   the plan digest — which binds `older_than` — could never match and apply
   would always refuse. Binding the review to the dry-run's fixed cutoff
   instead of a relative window is what makes a real apply possible.) The
   task recomputes the plan from a fresh listing and a fresh session (never
   a cached one), confirms the fresh report's cutoff still matches
   `reviewed_older_than`, and only proceeds if the resulting plan's digest
   still matches `expected_plan_digest`. Only then does it call the sole
   deletion seam, `app.services.storage.delete_reviewed_file_orphans`, with
   the plan's exact keys, and it never logs raw keys — only the same safe
   summary shape, with `dry_run: False` and a `deleted` count.

Refusals (all raise before any deletion; none deletes partially):

- `expected_plan_digest` missing on an apply — `ValueError`.
- `reviewed_older_than` missing, or not a timezone-aware ISO-8601 value, on
  an apply — `ValueError`.
- `reviewed_older_than` is less than 72 hours before this run's observation
  time — `ValueError`.
- The fresh report's cutoff no longer matches `reviewed_older_than`, or the
  fresh plan's digest does not match `expected_plan_digest` (a key appeared
  or disappeared between review and apply) —
  `app.services.file_object_cleanup.CleanupPlanDrift`.
- The candidate count exceeds the plan's `max_deletions` (default 100, hard
  ceiling 1000 — a `max_deletions` above the ceiling is refused when the plan
  is built) — `app.services.file_object_cleanup.CleanupCapExceeded`.

**Deletion is irreversible unless the bucket keeps object versions.**
Whether the production bucket has versioning enabled is unverified as of this
change — confirm bucket versioning before the first real apply. Legacy ERP
upload prefixes are out of scope for this task, exactly as they are out of
scope for the report above; do not extend `expected_plan_digest` review or
apply to keys outside the `tenants/<uuid>/files/` / `platform/files/`
prefixes.
