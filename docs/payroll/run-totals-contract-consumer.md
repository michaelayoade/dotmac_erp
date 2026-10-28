# Payroll run totals calculation consumer

This dependent ERP change requires the released `dotmac-payroll==0.1.0a2`
artifact and its exact Forgejo pin/lock. The module successor is currently an
unreleased candidate. The coordinator owns that dependency edit after release;
the prepared tree cannot pass import collection or the BOM pin gate beforehand.
No local tests were run for this cross-repository slice.

ERP remains the authoritative writer of salary slips, run decisions and
liabilities. This change does not install the payroll module's tables, Alembic
lineage, runtime admission or service, and does not create module runs,
calculations, liabilities or settlement evidence. The BOM separately declares
`calculation-contract-consumer` with a closed public-symbol and callsite
allowlist. Existing storage composition and lineage checks remain intact.

`run_totals.refresh_run_totals` takes a scoped parent `SELECT FOR UPDATE` lock
before flushing the caller's session and selecting
salary-slip scalar columns scoped by organization and run. This includes the
last pending generation link, pending draft edits and pending deletes even
with `autoflush=False`. The lock is held until caller commit/rollback, so
concurrent changes to distinct slips cannot publish a stale aggregate under
PostgreSQL Read Committed isolation. Generation and linked draft edit/delete
paths take the parent lock before changing children; submit paths take it
before re-reading state and validating transitions. Scalar reads bypass the identity map's unrounded Python
amounts and honor the database's persisted `Numeric(18, 2)` values. The adapter
passes immutable `PayrollAmountSnapshot` inputs with slip UUID references and
registry-resolved currency to `aggregate_payroll_run_totals`. It maps the result
back to the existing ERP header count/gross/deduction/net columns. Both run
submission paths refresh totals before changing status. Draft edit and delete
paths refresh the linked header in the same caller-owned transaction.

The bank export still checks every destination and preserves its Zenith
formatting. It refreshes queried slip instances from storage, scopes them by
organization and run, and passes only positive net payment amounts to the
existing bank command. Before file generation, `validate_payment_total` checks
those amounts against a scalar read of the persisted header. A mismatch refuses
the file with generic text; it does not repair an authorized header, emit salary
amounts, or record settlement. Net is independent of gross minus deductions.

Hosted CI must run the authored adapter, contract-import and BOM canaries and
existing payroll/export/lifecycle tests after dependency resolution. The
adapter canaries use an actual SQLAlchemy session with autoflush disabled and
isolated mapped projections, not mocked aggregate results. The PostgreSQL proofs live in
`tests/integration/test_payroll_run_totals_contract.py`, selected by the hosted
integration lane. They require hosted `TEST_DATABASE_URL` and fail if it is
absent. Disposable test schemas permit committed rows across independent
sessions and are dropped during cleanup. Precision/last-link/scope/edit/delete
coverage uses real PostgreSQL; the concurrency canary observes the second
session blocked on the first session's run lock via `pg_blocking_pids`, then
proves the final header includes both committed edits. SQLite unit coverage is
non-authoritative for PostgreSQL precision and concurrency. No test was run
locally.
