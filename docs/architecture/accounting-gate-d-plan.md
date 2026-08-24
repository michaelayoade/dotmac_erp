# Accounting gate D — clean bootstrap and rehearsal

Status: **planned, not started.** Gate C is merged: the
`dotmac-accounting==0.1.0a1` lineage is composed and its storage exists with
authority disabled. ADR-0003 supersedes the former historical-replay plan.

Gate D now prepares a fresh ERP database for new books. It does **not** replay
the legacy journal or posted-ledger populations. The production ERP remains
authoritative for historical transactions until final cutover and becomes a
read-only archive afterwards.

## Boundary

Gate D may:

- load reviewed chart, fiscal-calendar and dimension masters through the
  module's published commands;
- create one Finance-approved opening journal at the cutover date, supported by
  signed subsidiary schedules;
- run representative business consequences through ERP adapters and compare
  their module-owned results with accepted expectations;
- operate only on a disposable, independently migrated clean database; and
- produce aggregate counts, exact decimal totals and content digests as
  evidence.

Gate D may not:

- load `gl.journal_entry`, `gl.journal_entry_line`,
  `gl.posted_ledger_line` or `gl.posting_batch` history from legacy ERP;
- connect the clean application to the legacy database at runtime;
- use raw SQL to create accounting state;
- manufacture a retained-earnings, suspense or rounding plug;
- enable Accounting in production or repoint a production writer; or
- treat a passing rehearsal as Finance approval or deployment authority.

The former `scripts/backfill_accounting.py` extractor remains a read-only
forensic tool. Its period work list and digests explain the legacy record; they
are not clean-instance import material.

## Ordered work

### D0 — record the cutover data policy

ADR-0003 is the authority. Every data class is one of:

| Class | Clean-instance treatment | Owner of admission |
| --- | --- | --- |
| reconciled masters/configuration | typed idempotent bootstrap | owning module plus ERP adapter |
| open operational items | per-document import with control totals | owning business domain |
| accounting opening state | one approved opening chain plus subsidiary schedules | Finance and `dotmac-accounting` |
| continuity identities | explicit mapping and collision proof | identity/numbering owner |
| historical transactions | no import; legacy read-only archive | legacy ERP |

There is no fifth “copy the table” class.

### D1 — make the legacy extractor permanently read-only

Remove its module load mode and the unused module-side loader stub. Preserve the
typed extraction and digest code because it remains useful for forensic review
and for measuring the old record. Add an architecture canary proving no load
entry point is exported or accepted.

Acceptance:

- the CLI rejects `--load` as an unknown argument;
- `accounting_backfill` exports no loader;
- no module service is imported on the read-only extraction path; and
- existing master mapping and digest tests still pass.

### D2 — define the clean bootstrap manifest

The manifest is private operational input. Git contains its schema and empty
templates, never real party names, schedules or credentials. It binds:

- schema version and tenant identity;
- cutover instant and functional currency;
- hashes for every input file;
- expected row counts and exact decimal control totals;
- source owner, document kind, stable document identity and version;
- approval references and the owner responsible for each data class; and
- an explicit declaration that no historical transaction class is present.

Validation finishes before a transaction begins. Unknown fields, duplicate
identities, non-canonical decimals, missing evidence, mismatched hashes or an
unrecognized vocabulary member fail closed.

### D3 — bootstrap masters through module commands

Create in dependency order:

1. account categories, parents before children;
2. accounts, parents before children;
3. the cutover fiscal year and periods;
4. accounting dimensions; and
5. dimension values, parents before children.

The bootstrap adapter calls only `dotmac-accounting`'s public surface. It does
not insert module models directly. Each logical source has one stable identity
and fingerprint; rerunning the same pack is a no-op, while the same identity
with changed content is a conflict requiring a new reviewed pack.

Master data is not accepted merely because it exists in legacy ERP. The pack
contains the reviewed set intended to operate after cutover, which may exclude
duplicates and obsolete rows.

### D4 — admit one governed opening state

Finance supplies, for one named date:

- a signed final trial balance;
- signed AR and AP ageing schedules whose totals equal their control accounts;
- WHT, VAT, tax-payable, bank, inventory, fixed-asset and payroll-liability
  schedules where those controls are non-zero;
- the retained-earnings and current-tax close workpaper; and
- an approval reference naming the chart and functional currency.

The adapter builds one balanced `JournalKind.OPENING` journal through
`create_journal` and `post_journal`. Source identity is the approved opening
pack, not a legacy journal id. The journal fingerprint binds the ordered lines
and the evidence hashes. Subledger domains import their open items separately
and prove exact agreement with the corresponding controls before the opening
period may be closed.

The audited-opening evidence in
`docs/inventories/accounting-audited-opening-bridge-evidence.md` explains why
these approvals remain required. ADR-0003 removes historical correction from
the software composition path; it does not authorize Engineering to choose an
opening balance.

### D5 — behavioural rehearsal

Use a fresh isolated database migrated from empty, not a production restore.
Enable Accounting there only. Seed a synthetic second tenant before bootstrap
and prove it is byte-identical afterwards.

Run a versioned fixture corpus through ERP's business adapters covering:

- standard posting from AR, AP, expense, inventory, assets, tax and banking;
- residue allocation and exact six-decimal balance enforcement;
- idempotent replay and changed-fingerprint conflict;
- linked reversal;
- open, soft-close, reopen and lock transitions; and
- derived ERP balance-cache rebuild from module ledger evidence.

The expected result is the module's public contract and approved accounting
policy, not equality with dirty legacy rows. Each fixture records its expected
accounts, dimensions, exact amounts, source identity and lifecycle.

### D6 — prove bootstrap repeatability and recovery

Acceptance on two independently created databases:

- exact composed migration heads match;
- bootstrap pack count/amount/digest outputs match;
- every source identity is unique and every retry is a no-op;
- trial balance is exactly balanced and agrees to the approved opening pack;
- every subsidiary schedule agrees to its control account;
- all behavioural fixtures produce identical results;
- the synthetic tenant is unchanged and cross-tenant reads/writes are refused;
- an interrupted run resumes without duplicates; and
- restore and pack replay have written recovery evidence.

Only then does gate E begin repointing ERP callers.

## What happens to the known legacy defects

They remain documented in
`docs/inventories/accounting-finance-correction-memorandum.md` and the audited
opening evidence. They do not move into the clean instance and no longer block
module composition.

That is not erasure. Production ERP stays authoritative for its historical
period, and Finance may still correct it through append-only journals if a
statutory, audit or reporting need requires. The clean system records only the
approved cutover opening and later activity.

The APPROVED-but-unposted population is split by operational relevance:

- an item still requiring action after cutover must be completed before freeze
  or admitted as a typed open operational item; and
- an item with no continuing obligation stays in the archive with an explicit
  leave-in-legacy disposition.

No journal is mass-posted merely to empty the backlog, and no live obligation
is abandoned merely because its legacy journal is dirty.

## Exit criterion

Gate D is complete when the clean-instance bootstrap contract and operator are
implemented, both independent rehearsals pass, all required CI is green, and
Finance has supplied an approved opening pack for the eventual cutover. It is
not complete when a database has merely been created or when legacy history has
been copied successfully.
