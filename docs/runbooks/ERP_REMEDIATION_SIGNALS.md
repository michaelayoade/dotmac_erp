# ERP remediation signals

These signals turn recurring production-log findings into low-cardinality
dashboard series. They are initially **non-paging**. Validate normal production
rates for at least seven days before proposing notification thresholds in
`deploy/product.toml`.

Do not add organization, employee, invoice, payment, request, or task IDs as
metric labels. Use structured logs for record-level investigation.

## Dashboard queries

| Condition | PromQL |
|---|---|
| Invoice revisions quarantined | `sum(increase(dotmac_sub_invoice_sync_rows_total{outcome="source_accounting_mismatch"}[1h]))` |
| Invoice runs reaching their work limit | `sum(increase(dotmac_sub_invoice_sync_limits_total[1h]))` |
| Permanent staff mapping failures | `sum(increase(dotmac_sub_staff_sync_rows_total{outcome="permanent_failure"}[1h]))` |
| Transient staff integration failures | `sum(increase(dotmac_sub_staff_sync_rows_total{outcome="transient_failure"}[1h]))` |
| Incremental sync lock contention | `sum(increase(dotmac_sub_incremental_lock_contention_total[1h]))` |
| Ledger balance drift detected | `sum(increase(outbox_reconciliation_total{result="drift_found"}[1h]))` |
| Audit dispatch exceptions | `sum(increase(audit_dispatch_events_total{outcome="failure"}[1h]))` |
| Incremental phase failures | `sum by (task) (increase(job_runs_total{task="run_dotmac_sub_incremental_sync_phase",status="failure"}[1h]))` |

Use a dashboard annotation, not a page, when any series first becomes nonzero.
After the observation period, a sustained nonzero rate may become a ticket only
when an owner, threshold, recovery expression, and tested runbook are available.

## Remediation

### Invoice quarantine

1. Confirm the source header subtotal and tax agree with the source lines.
2. Correct and republish the source revision; never bypass the ERP accounting
   invariant or advance a parked cursor manually.
3. Replay a bounded batch and confirm the accepted counter increases without a
   corresponding accounting-mismatch increase.

### Staff mapping failures

1. Use the structured `dotmac_sub_staff_sync_failed` log event to produce the
   employee and department remediation list.
2. Correct the service-team or reviewed Person Party binding in the owning
   system.
3. Retry only corrected employees. Permanent failures should not be retried
   blindly.

### Incremental lock contention

1. Compare lock contention with phase duration and Celery task failures.
2. Confirm one workflow owns the organization lock and its heartbeat is fresh.
3. Reclaim only a stale lease using the existing maintenance path. Do not
   delete advisory locks or run overlapping workflows manually.

### Ledger drift

1. Confirm `outbox_reconciliation_total{result="repaired"}` follows detected
   drift.
2. Compare the rebuilt projection with posted ledger lines.
3. Escalate repeated drift to the finance platform owner; never edit projected
   balances directly.

### Audit dispatch failure

Audit dispatch currently runs inside the caller transaction and does not create
a durable failed-event queue. A failure therefore requires correlating the
structured log with the originating business record and verifying whether the
immutable audit row exists. Do not claim replay is possible unless durable
failure evidence is added in a separately reviewed design.
