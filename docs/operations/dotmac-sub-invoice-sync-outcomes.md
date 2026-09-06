# Self-Care invoice synchronization outcomes

ERP records the disposition of each consumed
`invoice-accounting-sync.v2` source revision before advancing the invoice
cursor. These rows are evidence; they are not an alternative invoice, retry
queue, or posting path.

## Ownership and safety

- Self-Care owns invoice header, line, tax and discount facts.
- ERP owns tax/account mappings and accounting entries.
- `ready` may proceed to the existing finance posting owner.
- `blocked` and `not_applicable` never post accounting entries.
- The ledger stores identifiers, typed issue codes and numeric comparisons. It
  does not store customer identity, invoice memo text or arbitrary payloads.
- Both tables use forced tenant RLS. Queries must still include an explicit
  `organization_id` predicate; RLS is the second boundary, not the first.

## Recurrence and resolution

The unique source identity is `(organization_id, source_invoice_id,
source_updated_at)`. Re-observing the same projection fingerprint increments
`occurrence_count` and `last_seen_at`. A different fingerprint at that same
revision is a contract violation and aborts the run.

A later `ready` revision sets `resolved_at` on older blocked revisions. It does
not delete their issues. Targeted replay of one `invoice_id` must not rewind the
global cursor.

## Operator review

Review open issue counts by typed code, not by copying invoice descriptions or
customer data into logs:

```sql
SELECT i.issue_code, count(*) AS open_outcomes
FROM ar.dotmac_sub_invoice_sync_outcome o
JOIN ar.dotmac_sub_invoice_sync_issue i
  ON i.organization_id = o.organization_id
 AND i.outcome_id = o.outcome_id
WHERE o.organization_id = :organization_id
  AND o.disposition = 'blocked'
  AND o.resolved_at IS NULL
GROUP BY i.issue_code
ORDER BY open_outcomes DESC, i.issue_code;
```

Fix the owning fact or mapping, publish/observe a later Self-Care revision, and
use targeted replay to validate it. Never update an outcome, issue, invoice or
tax mapping merely to make the ledger appear clear.

## Migration characteristics

`20260906_invoice_sync_outcomes` is additive. It creates two tables, indexes,
constraints, forced RLS policies and explicit `app_user` grants. It performs no
invoice backfill, posting, deletion or historical tax inference.
