# ADR-0002 — Bank statement numbers take the module grammar (`STMT-2026-00001`)

- **Status:** Accepted — **amended 2026-09-04**, see
  [§ Amendment](#amendment--2026-09-04-quote-is-the-first-family-and-the-shadow-is-withdrawn).
  One clause is withdrawn (family selection); every other clause stands.
- **Date:** 2026-08-15 (accepted) / 2026-09-04 (amended)
- **Decider:** Michael, both times
- **Scope, as accepted 2026-08-15:** `SequenceType.BANK_STATEMENT` only. No
  other series.
- **Scope, as amended 2026-09-04:** the grammar decision below is
  family-independent and binds whichever family moves. `SequenceType.QUOTE` is
  the first family. Bank statements are not first; ADR-0002's terms still
  govern them for whenever they move.
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

## Amendment — 2026-09-04: `QUOTE` is the first family, and the shadow is withdrawn

Michael ruled that ERP must not begin its numbering adoption with a bank
statement or any statutory number, and that the verification must not shadow
two live allocators. This amendment carries out that ruling against this
record, which is the accepted decision the ruling contradicts.

**Exactly one clause is superseded: the family selection.** The Scope line
"`SequenceType.BANK_STATEMENT` only. No other series", and the Context sentence
naming bank statements as "ERP's first adoption slice", are withdrawn. Nothing
else in this record is withdrawn, weakened or reopened. The list below is the
full disposition, so that a later reader does not have to infer which half
survived.

### What is withdrawn

- **The family-selection clause only.** Bank statements were chosen on
  2026-08-15 because theirs is the one allocation site in `app/` already
  passing a real business date. That property was real and is not disputed. It
  is no longer sufficient, because the ruling adds a constraint the original
  decision never weighed: a first cutover must not be attempted on a number a
  regulator or a customer may audit, and `statement_number` is a bank record.

  The independent reason to agree with the ruling is a defect in the *proof*,
  not in the family: `statement_number` is shared with `MONO-<YYYYMM>` and
  `PSK-` strings that no allocator ever produced (see "Two writers that never
  allocate" above, which this amendment retains and now reads as evidence
  against its own family). A historical-duplicate proof over that column must
  first filter the population — and a filter is exactly the mechanism by which
  such a proof passes for the wrong reason.

### What stands, unchanged

Every one of these keeps full force:

1. **The grammar decision.** ERP accepts the module's grammar; the shared
   formatter is not extended to reproduce ERP's concatenated shape. This was
   never a statement about bank statements — the two formatters disagree for
   every family — so it survives the family change intact and now binds
   `QUOTE`: `QT-2026-09-0001`, not `QT202609-0001`.
2. **Cutover term 1 — historical numbers are unchanged.** Nothing is
   reformatted, reissued or backfilled.
3. **Cutover term 2 — the counter continues.** No reset at cutover; the shape
   changes mid-series.
4. **Cutover term 3 — ERP readers accept both grammars permanently.** Not a
   transition window.
5. **Cutover term 4 — this record is the customer-visible history.** The
   cutover date is recorded here when the writer flips.
6. **The disjointness argument and the uniqueness backstop.** The two grammars
   can never produce equal strings, so a new-shape number cannot collide with a
   historical one, and a database uniqueness constraint refuses one if it
   somehow did. For quotes that constraint is
   `uq_quote_number (organization_id, quote_number)`
   (`app/models/finance/ar/quote.py:58`), already present — the direct
   counterpart of the per-`(organization, bank_account, statement_number)`
   check this record relied on.
7. **The comparison content.** "A separator difference is expected; a digit,
   period or value difference is not" is retained verbatim as the content of
   the check. Only the machinery that was to evaluate it moves — see
   "The shadow is withdrawn" below.
8. **"What actually reads a statement number."** Readers are display, search,
   sort and export; none decomposes the number; substring search over the old
   shape stops matching new numbers. The same survey must be redone for
   `quote_number` before its flip — the finding is retained as a *method*, not
   inherited as a result about a different column.
9. **"Two writers that never allocate."** `MONO-` and `PSK-` are excluded from
   any backfill, and `period_key` is derived from the number rather than from a
   mutable date.
10. **The prerequisite list.** This record still does not authorize a cutover.
    ADR-0001's composition slice and a runtime that is not the `postgres`
    superuser remain conditions.
11. **All four rejected alternatives.** Teaching the module ERP's grammar,
    resetting the counter, reformatting historical rows, and keeping ERP's own
    allocator for one family while adopting the module elsewhere all stay
    rejected, on their original reasoning.

### Why `QUOTE`

Evidence: the family inventory
`docs/inventories/2026-09-03-erp-document-series-families.md` and the selection
analysis, both of which live on `docs/numbering-first-series-family` at
`7926400f` and **arrive on `main` only when that branch merges** — this
amendment deliberately does not copy them, because a duplicated inventory is a
second copy of a fact. Until that merge the two citations resolve on that
branch and nowhere else. Each fact below, by contrast, was re-read at the line
in the tree this change was written against.

- **One writer, one allocation site — strictly narrower than bank statements.**
  `quote_number` is allocated at `app/services/finance/ar/quote.py:111`, through
  `QuoteService.generate_quote_number` (same file, lines 63-69, the only
  `SequenceType.QUOTE` allocation in `app/`). There is no JSON API route, no
  importer, no `dotmac_sub` sync and no scheduled automation that creates a
  quote. The single entry point is one interactive web form, which reaches the
  service through `create_from_payload`
  (`app/services/finance/ar/quote.py:168`). Every other `quote_number`
  reference in `app/` is display, search, sort or export. ADR-0002 chose bank
  statements for having one allocation site; quotes have one allocation site
  **and** one writer, and their column is not shared with strings no allocator
  produced.
- **No accounting or statutory consequence.** A quote posts nothing and is not
  a tax document; the sales order and the invoice carry anything a regulator
  reads. It is the only low-volume family in the inventory with that property,
  and it is the property the ruling requires.
- **The business date is present, required, and demonstrably unused.**
  `quote_date: date` is a required parameter of `QuoteService.create`
  (`app/services/finance/ar/quote.py:76`). It is in scope at the allocation on
  line 111 and is not passed to it; it is assigned to the model two lines
  later, at line 113. The defect is one line wide, and the proof that it is
  fixed is an assertion rather than a topic.
- **Monthly reset makes the date proof non-vacuous.** Year, month and a period
  boundary are all observable in the number, so a business-date assertion has
  something to be wrong about. `PROJECT` and `CONTRACT` would pass a
  business-date gate while exercising nothing, because their numbers contain no
  date at all.
- **A duplicate would be refused by the database.** `uq_quote_number` already
  exists, so after cutover a collision aborts a transaction instead of writing
  a second row.

### Why `PAYROLL_ENTRY` was rejected, though it was the strongest competitor

`PAYROLL_ENTRY` is genuinely the lowest-volume family in ERP — one per
organization per pay period — and, with bank statements excluded, the only
remaining family already passing a real business date
(`reference_date=posting_date`). It was rejected on two counts.

**The decisive one: `payroll.payroll_entry` has no unique constraint on
`entry_number`.** Its `__table_args__` declares one index,
`idx_payroll_entry_period (organization_id, start_date, end_date)`, and a
schema; `entry_number` is a plain non-null `String(30)`
(`app/models/people/payroll/payroll_entry.py:73-95`). So the
historical-duplicate proof would have **nothing to detect a collision with**:
the database would accept a duplicate silently, and what this record calls a
proof would be a query someone remembered to run. Retained clause 6 above is
not decoration — it is the backstop the whole cutover leans on, and payroll
does not have it.

Second, and smaller: a payroll run that cannot allocate a number is a payroll
run that does not happen. Low volume and low blast radius are different
properties, and only the second makes a first cutover cheap.

### The shadow is withdrawn, because it was never coherent

A number is allocated once, and the document carries exactly one. Running the
legacy allocator and the module allocator over the same document produces two
numbers, and there are only two ways to spend them:

- **print the legacy number** — the module's counter has then advanced for a
  number nobody holds. The drift grows by one per document, forever, and the
  continuity assertion is being compared against a counter that has been
  deliberately falsified. The proof measures its own contamination.
- **print the module number** — that is not a shadow. That is the cutover, with
  a second allocator running beside it for decoration.

**This record's own harness requires exactly that double-advance, and never
names it.** "Compare the formatted string … the legacy allocator stays
authoritative and divergence is PERSISTED" is only executable if both numbers
were allocated; `legacy_counter_delta == module_counter_delta == 1` is a
comparison of two counters that both moved. The flaw is in the mechanism, not
in the comparison: retained clause 7 keeps the comparison and this amendment
gives it somewhere honest to run.

### The verification method Michael ruled — three parts, in order

1. **Offline replay parity, allocating nothing.** Against a restored copy,
   replay every historical quote's `(organization, quote_date, issued value)`
   through the module's **pure** functions — `period_for` and `format_number`
   — with no `allocate()` call, and assert agreement with the legacy number
   after normalizing the separator, which is retained clause 7's comparison
   used verbatim. This proves the arithmetic over the whole history without
   spending a single number.
2. **Read-only `preview()` parity in production.** With the series configured
   and counters seeded but the allocator **not** repointed, assert that
   `preview(scope, "quote", reference_date=quote_date)` equals what the module
   would issue, and that its value is exactly legacy `current_number + 1` for
   the same period. `preview` takes no lock and writes nothing. This is the
   honest analogue of a shadow: it compares the next number without issuing
   one, so it can run for as long as anyone wants without drifting anything.
3. **One sealed writer flip, with a stated inverse — never a dual write.** The
   repoint is a single edit at `app/services/finance/ar/quote.py:111`. The
   inverse is stated before the flip, not discovered after it: point back at
   the legacy allocator, then advance the legacy
   `numbering_sequence.current_number` to the maximum value the module issued
   in that period, so it cannot reissue a module-issued number. **The inverse
   is writable only because of retained clause 6** — the two grammars are
   disjoint, so a module-issued quote number is identifiable on sight, which is
   what makes the reconciliation query expressible at all. Disjointness was
   argued in 2026-08-15 as collision safety; it turns out to be what makes the
   rollback possible, and that is the second reason it is retained rather than
   restated.

One trap the flip must close in the same change: the legacy sequence admin
screen (`app/services/finance/settings_web.py`) keeps rendering an editable
prefix, separator and padding for Quote, and `NumberingService.update_sequence`
keeps accepting them, while the module refuses identity-shaping changes once a
series has allocated. Leaving both live means an operator edits a form that
changes nothing and is told it worked.

### Disposition of ADR-0008 on `docs/numbering-first-series-family`

That record proposed `QUOTE` and is explicitly self-limited: its own Status
says it "cannot be accepted on its own" because it contradicts this ADR, and it
deliberately declines to supersede or edit it. **It is not superseded by this
amendment. It stands as the supporting analysis this amendment cites** — the
family inventory, the six proofs stated as failable assertions, and the twelve
rejected alternatives are evidence, and "Superseded by NNNN" is a status for a
decision that was accepted and then replaced. ADR-0008 was never accepted, so
there is nothing to supersede.

Two things must nonetheless be true when that branch merges, and neither is
done here because editing another branch's record is not this change's to do:

- Its **Status must stop reading `Proposed`**. A live proposal to select a
  first family, standing beside an accepted amendment that has selected one, is
  the same split-brain the numbering owner exists to end. The honest status is
  analysis supporting an accepted decision, with the decision named as this
  ADR.
- Its **number is not settled**. `0008` was claimed concurrently by more than
  one branch. See `docs/adr/reservations.toml` and the reconciliation this
  change reports; the register, not the branch, decides which record keeps
  which number.

### A note on this record's title

The filename and title still say "Bank statement numbers". Neither is changed:
the convention is that an ADR is never renumbered or renamed, and a link that
worked yesterday keeps working. Read the title as the decision's origin, not
its scope — the scope is the two Scope lines in the header.
