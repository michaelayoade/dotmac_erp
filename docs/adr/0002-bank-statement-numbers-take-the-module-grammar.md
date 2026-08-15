# ADR-0002 — Bank statement numbers take the module grammar (`STMT-2026-00001`)

- **Status:** Accepted
- **Date:** 2026-08-15
- **Decider:** Michael
- **Scope:** `SequenceType.BANK_STATEMENT` only. No other series.
- **Related:** [ADR-0001](0001-kernel-idempotency-is-erps-only-at-most-once-owner.md)
  (the composition prerequisite), `dotmac_starter_mt:docs/inventories/numbering-erp-adoption-slice.md`
  (the adoption analysis this decides)

## Context

`dotmac-numbering` is the fleet owner of concurrency-safe document series
(ADR-0030 build step 5, published as `0.1.0a2`). ERP's first adoption slice is
`SequenceType.BANK_STATEMENT`, chosen because it has exactly one allocation
site in `app/` (`app/services/finance/banking/bank_statement.py:874-878`) and
already passes a real business date.

The two formatters do not agree, and no configuration bridges them:

- **ERP** (`app/services/finance/common/sequence_utils.py`, `format_number`)
  concatenates prefix + year + month with NO separator, then places exactly one
  separator before the sequence segment: `STMT` + `2026` → `STMT2026`, then
  `-00001` → **`STMT2026-00001`**.
- **The module** joins every segment with the separator:
  **`STMT-2026-00001`**.

No `SeriesConfiguration` reproduces ERP's string. `separator=""` yields
`STMT202600001` (loses the separator ERP prints); `separator="-"` yields the
module's own grammar. This is a real fork in the road, not a formatting
preference: the adoption dossier's shadow assertion ("compare the formatted
string") is unsatisfiable as written.

## Decision

**ERP accepts the module's grammar. Bank statement numbers allocated after
cutover read `STMT-2026-00001`.**

The shared formatter is NOT extended to reproduce ERP's legacy grammar. Doing
so would mean a new identity-shaping field on `number_series`, which is a
migration, a new column in the freeze trigger's identity set, a new check
constraint, a version bump and a new kernel floor on a package that just
reached its completion gate — to serve one adopter's legacy cosmetics.

### Cutover terms

1. **Historical numbers are unchanged.** Nothing is reformatted, reissued or
   backfilled into the new shape. `STMT2026-00001` stays exactly as issued.
2. **The counter continues.** There is no reset at cutover, so the shape
   changes mid-series and the sequence keeps counting. A reset would be the
   more visible change, not the safer one: it would put two statements with the
   same number in one organization's history.
3. **ERP readers accept both grammars permanently.** Not a transition window.
   The old shape stays in the data forever, so every parser, search, export and
   reconciliation path must accept both for good. A tolerance introduced as
   "temporary" becomes a defect the day someone removes it.
4. **This record is the customer-visible history.** The cutover date is
   recorded here when the shadow slice flips, alongside the format change, so
   an operator asking "why do our statement numbers change shape in the middle
   of 2026" has an answer that is not archaeology.

### Why this is safe

The two grammars are **disjoint strings** — `STMT2026-00001` can never equal
`STMT-2026-00001` — so a new-shape number cannot collide with a historical one.
Uniqueness is enforced per `(organization, bank_account, statement_number)`
(`app/services/finance/banking/bank_statement.py:843-857`), and that check is
unaffected by the shape.

## Consequences

### The shadow comparison changes shape

String equality is the wrong assertion. Under this decision the shadow harness
compares:

```
legacy.replace(separator, "") == module.replace(separator, "")
    and legacy_counter_delta == module_counter_delta == 1
    and legacy_period == module_period
```

A normalized-string comparison plus two structural comparisons. That IS the
content of the check: a separator difference is expected; a digit, period or
value difference is not. A harness asserting raw equality would fail on every
row and prove nothing.

### What actually reads a statement number

Surveyed at `origin/main`: the readers are display, search, sort and export —
`app/services/finance/banking/web_parts/statements.py` (table cells, CSV,
`ilike('%search%')`), `bulk.py` (search field and delete message),
`suspicious_matches.py` (carried through a match DTO), `app/api/finance/banking.py`
and the schemas. **No reader decomposes the number** — nothing parses the year
or sequence back out of the string. That is what makes clause 3 cheap to honour
in code and the reason it must be stated anyway: the absence of a parser today
is not a guarantee about tomorrow.

One user-visible consequence follows directly from substring search: an
operator who searches `STMT2026` will match historical statements and not new
ones. That is inherent to the shape change, is not a defect, and is the concrete
thing to say in the release note.

### Two writers that never allocate

`app/services/finance/banking/mono_sync.py:1503` writes
`MONO-<YYYYMM>` and `app/services/finance/payments/paystack_sync.py:571`
writes a `PSK-` number. Neither goes through the sequence at all. They are
untouched by this decision, must be excluded from any backfill, and are the
reason a backfill derives `period_key` from the NUMBER rather than from the
mutable `statement_date`.

### Sequencing

This decision does not authorize the cutover. It removes the last open question
from the shadow slice, which still requires: ADR-0001's composition slice
(tables, binding, passing `require_prerequisites`, PostgreSQL proofs); a shadow
run where the legacy allocator stays authoritative and divergence is PERSISTED
rather than logged; and — for production — a runtime that is not the `postgres`
superuser, per ADR-0001 § 8.

## Alternatives rejected

- **Teach the module ERP's grammar** (a `fuse_date_segments`-style
  identity-shaping field). Rejected as stated above: it reopens a closed
  package's identity contract, and it makes one adopter's legacy the shared
  formatter's permanent complexity. Every future adopter would then have to
  understand a field that exists for a grammar nobody would choose today.
- **Reset the counter at cutover so the new shape starts at 1.** Rejected:
  clause 2's reasoning — a reset reissues numbers that already exist in
  customer-facing documents.
- **Reformat historical rows to the new grammar.** Rejected: it rewrites
  numbers that appear on statements customers already hold, to make a database
  column look tidy.
- **Keep ERP's own allocator for bank statements and adopt the module
  elsewhere.** Rejected: it preserves the very split-brain the numbering owner
  exists to end, and bank statements are the one series with a single allocator
  — the easiest honest cutover in the codebase. Declining it would mean
  declining all of them.
