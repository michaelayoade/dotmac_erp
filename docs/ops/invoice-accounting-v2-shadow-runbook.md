# Invoice accounting v2 shadow runbook

The shadow consumer proves the Self-Care v2 accounting projection against an
ERP-owned durable outcome ledger. It does not create, update, or post ERP
invoices and it is not in the Celery beat schedule.

## Release prerequisites

1. Deploy Self-Care PR #2947 and run its invoice-line snapshot migration.
2. Deploy ERP PRs #477, #478, #479, #480, and #481 together. Run the #480
   Alembic migration before starting the new ERP application or worker image.
3. Deploy this PR only after those changes are present. It adds no migration.

## Non-production validation

Invoke a targeted invoice first:

```text
app.tasks.dotmac_sub.run_dotmac_sub_invoice_accounting_v2_shadow
args: ["<organization-uuid>", "<self-care-invoice-uuid>", 1]
```

Then invoke a bounded organization batch with `invoice_id` omitted. A result is
successful when every admitted revision is durably classified as `ready`,
`blocked`, or `not_applicable`. A `blocked` result is consumed permanent source
evidence, not a retryable task failure. A malformed/unknown contract returns
`retryable: false` and rolls back the whole batch so the cursor cannot pass it.

Do not add the task to the beat schedule and do not redirect the existing v1
posting workflow until the proposed durable-outcome ADR is accepted and the
shadow results have been reconciled.
