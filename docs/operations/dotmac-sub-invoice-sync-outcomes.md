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

## Integrator ProductPort boundary

ERP publishes an authenticated ProductPort descriptor v3 for
`invoices.accounting_sync.observation.v1`. It declares the generic
`dotmac.io/product-observation/v1` wire and ERP's own observation JSON Schema
plus canonical contract digest; the Sub connector claims that digest, never
publishes a competing schema. The descriptor stays `configured_disabled`
until a separately authorized activation. An isolated ERP Integrator instance
must have its own receipt database and worker, separate from Sub messaging.

The write and mirror routes admit only the configured binding, destination
scope and `sub_accounting` connector installation. Set
`INTEGRATOR_INVOICE_SYNC_BINDING_ID`, `INTEGRATOR_INVOICE_SYNC_SCOPE_KIND`,
`INTEGRATOR_INVOICE_SYNC_SCOPE_REF`, and
`INTEGRATOR_INVOICE_SYNC_INSTALLATION_ID` for the intended deployment; the
shipped binding/installation IDs and scope ref are placeholders. The service
principal supplies `organization_id`; neither the envelope nor its scope can
select a tenant. `Idempotency-Key` is an HTTP header bound to the entire
accepted envelope, so changing provider identity or provenance under one key
conflicts instead of silently replaying. Domain parity remains keyed by the
invoice revision and Sub's forwarded digest, not by that transport key.

## Recurrence and resolution

The unique source identity is `(organization_id, source_invoice_id,
source_updated_at, digest_version)`. Re-observing the same projection
fingerprint increments `occurrence_count` and `last_seen_at`. A different
fingerprint at that same revision and `digest_version` is a contract
violation and aborts the run.

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

`20260918_invoice_sync_canonical_evidence` is metadata-only: it renames both
tables out of the live path into a frozen archive and creates a fresh, empty
pair of tables at the original names. It performs no data copy, backfill or
row rewrite. See "Frozen legacy tables" below.

`digest_version` is `NOT NULL` with no default on the fresh canonical table,
so any application instance still running the pre-cutover code (writing
without `digest_version`) will fail every write to this table immediately
after this migration commits — the table name is unchanged, so the failure
looks like a write error, not a missing-table error. Deploy the code that
sets `digest_version` (or pause the shadow task) across this migration, not
after it.

## Frozen legacy tables

Every row `20260906_invoice_sync_outcomes` ever wrote used ERP's own,
now-deleted, confirmed-buggy local fingerprint algorithm — never a digest
forwarded from Self-Care's canonical projection. ERP never archived the
original full projection that produced a stored `projection_fingerprint`, and
Self-Care's feed reconstructs from current state, not a historical snapshot,
so a present-day re-fetch cannot prove a historical match. Those rows are
**historically unverifiable**.

Michael's ruling: preserve them exactly as written, forever, but exclude them
structurally from ever counting as canonical parity or cutover-success
evidence — never delete rows, never delete or block the underlying invoices.

`20260918_invoice_sync_canonical_evidence` renamed the live tables to
`ar.dotmac_sub_invoice_sync_outcome_legacy` and
`ar.dotmac_sub_invoice_sync_issue_legacy`, revoked `INSERT`/`UPDATE`/`DELETE`/
`TRUNCATE` from `app_user` on both (SELECT is preserved — tenant RLS still
applies to forensic/audit reads), and created a fresh, empty pair of tables
at the original familiar names
(`ar.dotmac_sub_invoice_sync_outcome`/`ar.dotmac_sub_invoice_sync_issue`) for
canonical-scheme evidence only, going forward. Canonical rows carry a
required `digest_version` and their fingerprint is always forwarded verbatim
from Self-Care's canonical digest — never computed locally.

A query against `ar.dotmac_sub_invoice_sync_outcome`/`ar.dotmac_sub_invoice_
sync_issue` after this migration sees only canonical-scheme evidence,
correctly scoped and initially empty. To review the frozen legacy evidence
(forensic/audit only — never as canonical parity or cutover proof), query the
`_legacy`-suffixed tables directly, e.g.:

```sql
SELECT count(*) FROM ar.dotmac_sub_invoice_sync_outcome_legacy
WHERE organization_id = :organization_id;
```
