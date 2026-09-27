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
not recheck age or references. Automatic deletion is not enabled by this
change.
