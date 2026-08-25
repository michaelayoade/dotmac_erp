# ADR-0008 — Refunds have one owner (two, at one named boundary)

- **Status:** Accepted
- **Date:** 2026-08-25
- **Decider:** Michael
- **Domain:** `customer_refund` (new in `app/services/sot_relationships.py` and
  `docs/SOT_RELATIONSHIP_MAP.md`), plus an extension of `payment_execution`
  (ADR-0005).
- **Supersedes:** nothing. Extends ADR-0005.

## Context

"Was this refunded, by whom, against what, and is it posted?" had no answer in
ERP, because *refund* was not a thing the system knew about. There is no
`Refund` model, no `refund` table, and no `/refund` route. What exists instead
is a refund-shaped side effect stamped onto five unrelated aggregates by
eleven writers, none of which is named as the owner and none of which can see
the others:

| # | Writer | What it decides |
|---|---|---|
| 1 | `payments/payment_service.py::process_transfer_reversal` | `PaymentIntent.status = REVERSED`, `ExpenseClaim` PAID→APPROVED, batch item FAILED, two GL reversals |
| 2 | `payments/webhook_service.py::_handle_transfer_reversed` | delegates to #1 — and `charge.refund` / `refund.processed` were **not handled at all**, falling into `logger.info("Unhandled event type: ...")` |
| 3 | `ar/invoice.py` credit-note lifecycle | SUBMITTED / APPROVED / POSTED / VOID / delete |
| 4 | `ar/ar_posting_saga.py` | credit-note sign flip |
| 5 | `ar/ar_inventory_integration.py` | credit-note stock effects |
| 6 | `ar/web/credit_note_web.py` | a second orchestration surface over #3 |
| 7 | `ar/customer_payment.py::void_payment` / `::mark_bounced` | allocation reversal, GL reversal, terminal status |
| 8 | `gl/reversal.py::ReversalService.create_reversal` | the GL **mechanism**, 15 direct callers, no notion of *why* |
| 9 | `ap/posting/reversal.py::reverse_invoice_posting` | AP invoice posting reversal |
| 10 | `dotmac_sub/sync/_payments.py::_handle_unsettled_payment` | `CustomerPayment.status = REVERSED` — ERP state decided because *Sub* refunded |
| 11 | `dotmac_sub/sync/_credit_notes.py` | `Invoice.status = VOID` stamped by an adapter |

Plus two byte-identical, caller-less, test-less cash-advance refund writers
(`expense/service_advances.py::record_refund` and its twin in
`people/expense/expense_service.py`), each incrementing
`CashAdvance.amount_refunded` and deciding `FULLY_SETTLED`.

`docs/SOT_RELATIONSHIP_MAP.md` named no refund, credit-note or reversal
domain, and `app/services/sot_relationships.py` had no entry: `grep -n
"refund\|reversal\|credit"` returned nothing. Under the fleet standard that is
not an omission in the documentation — it is the finding. A decision with no
named owner is a decision eleven places take differently.

Three concrete consequences of the fragmentation, all live before this change:

- **A refund Paystack actually paid out was silently dropped.** `charge.refund`
  and `refund.processed` reached `process_webhook`, matched no branch, and were
  logged at INFO as unhandled while the webhook row was marked PROCESSED. The
  cash left the bank and ERP's books never moved.
  `docs/paystack_chargebacks_investigation.md` is eight instances of exactly
  this shape (₦282,538.13 of settled-then-reversed transactions absent from the
  collections database).
- **Failure left the GL and the subledger disagreeing.** `void_payment` and
  `mark_bounced` reversed the allocations *first*, then attempted the GL
  reversal inside `try/except Exception: logger.exception(...)`, then stamped
  the terminal status regardless. A GL reversal that failed produced an invoice
  with its balance given back, a payment marked VOID, and a ledger still
  carrying the cash.
- **A reversal could not be told apart from any other reversal.** Every refund
  path funnels into `ReversalService.create_reversal`, which is handed a free-text
  `reason` and, at several sites, no `idempotency_key` at all. A refund
  reversal, an FX revaluation reversal and a data-health correction land in the
  ledger as the same kind of row.

## Decision

### 1. Two owners, one boundary: customer money in vs. company money out

Refund is not one decision. Money coming back to a **customer** and money
coming back from a failed **payout** are different aggregates, different
ledgers and different triggers. They get one owner each, and the boundary
between them is named rather than left to whoever gets there first.

**`app.services.finance.ar.customer_payment.CustomerPaymentService` owns the
customer refund decision.** It was already doing the complete refund shape end
to end, for two triggers, and doing it the way the standard asks:

- it reverses the allocations and hands the derived verdict back to the
  paid-status owner (`apply_payment_status`, ADR-protected by
  `tests/architecture/test_paid_status_single_owner.py`) rather than
  recomputing coverage itself;
- it delegates the ledger to the GL owner instead of posting journal lines;
- it settles the subledger status;
- and it sits on the aggregate a customer refund is *about*. A refund of a
  customer's money is a fact about the receipt that money arrived on.

**`PaymentService` (the `payment_execution` domain, ADR-0005) owns
expense/outbound payout reversals.** Its refund surface is exclusively
`source_type == "EXPENSE_CLAIM"`, and it already owns the expense-claim
revert, the batch-item consequence and the two reimbursement/fee reversal
postings.

The two owners meet at exactly one place: `PaymentIntent.status`. ADR-0005
makes `PaymentService` the sole writer of that column, and that does not bend
for an inbound refund. So a Paystack refund of a *collection* is settled by
`CustomerPaymentService.refund_payment` (allocations, GL, subledger) and the
intent is then moved by `PaymentService.record_inbound_refund` — the gateway
artefact recorded by the gateway's owner. The intent is not the refund record;
it is the transport's receipt.

### 2. One explicit entry point, and `void`/`bounce` become its callers

`CustomerPaymentService.refund_payment(db, organization_id, payment_id,
amount, reason, refunded_by_user_id, ...)` is the single way a customer refund
is decided. It reverses the allocations, calls `apply_payment_status`, calls
`ReversalService.create_reversal` with an explicit refund reason and
idempotency suffix, records reason/amount/actor, and settles the payment's
terminal status.

`void_payment` and `mark_bounced` are now thin callers with different reasons
and different terminal statuses — not parallel implementations of the same
shape. One behaviour, three reasons:

| caller | reason | terminal status | idempotency suffix |
|---|---|---|---|
| `void_payment` | `Payment voided: …` | `VOID` | `void-reversal:v1` |
| `mark_bounced` | `Payment bounced: …` | `BOUNCED` | `bounce-reversal:v1` |
| `refund_payment` (direct, sync, webhook) | `…` | `REVERSED` | `refund-reversal:v1` |

The two pre-existing idempotency keys are preserved **byte for byte**, so no
already-reversed production journal changes identity under this change.

`refund_payment` is idempotent: a second call against a payment already in the
requested terminal status returns it unchanged and touches neither the ledger
nor the subledger.

### 3. The ledger leg goes first, and a refund that cannot reach the ledger changes nothing

The order is now GL reversal → allocation reversal → terminal status, and a
failed GL reversal raises `RefundReversalError` having mutated nothing. This
**is a deliberate behaviour change** to `void_payment` and `mark_bounced`, not
a tidy-up: they previously logged the failure and completed the void anyway.
Recording it as a change rather than smuggling it through is the point of this
section. The new behaviour is the one `dotmac_sub/sync/_payments.py` had
already arrived at independently ("left unchanged to avoid GL/subledger
divergence") — this change makes it the rule for every trigger instead of one.

### 4. Sync adapters observe; they do not decide

`dotmac_sub/sync/_payments.py::_handle_unsettled_payment` no longer assigns
`PaymentStatus.REVERSED`. It reports what Sub said and calls
`refund_payment`. `dotmac_sub/sync/_credit_notes.py` no longer stamps
`InvoiceStatus.VOID` onto an existing credit note; it calls
`ARInvoiceService.void_from_external_source`, a new entry point on the
credit-note lifecycle owner.

That entry point exists because `void_invoice` deliberately refuses `POSTED`,
`PARTIALLY_PAID` and `PAID` documents — a human may not void a document that
is already in the ledger — while an upstream void of an already-posted credit
note is a different premise: the document was never ERP's to decide, and the
sync has already reversed its GL leg. Rather than teach the adapter to bypass
the owner, the owner gained the second premise, so `void_reason`,
`voided_by_user_id`, `voided_at` and the audit event are written by the
lifecycle owner in both cases.

A credit note *born* void (the create path) still takes its status at
construction. That is birth, not a transition, and there is nothing to
reconcile.

### 5. The gateway's refund events are handled

`charge.refund`, `refund.processed` and `refund.failed` are dispatched instead
of falling through to "Unhandled event type". `refund.failed` deliberately
changes no ERP state — the refund did not happen — but is recorded rather than
ignored.

Refund payloads carry `transaction_reference`, not `reference`, so both the
intent lookup **and** `_build_event_id` were taught the alternate key. Without
the second half, every refund event in a deployment would collapse onto the
event id `refund.processed:` and the second refund would be silently marked
DUPLICATE.

### 6. `ReversalService` stays the mechanism, and refund reversals say so

`ReversalService` is not the refund owner and is not moved: it owns *how* a
journal is reversed and cannot know *why*. What changes is that a refund
reversal is now distinguishable from an FX revaluation or a data-health
correction, because every `create_reversal` call site must pass an explicit
`reason=`, and every call site in a module that decides a refund must
additionally pass an explicit `idempotency_key=`.

That second rule is scoped to the refund deciders rather than applied to all
eighteen call sites on purpose: dragging payroll, the AP/AR sagas, FX
revaluation and the data-health task into an idempotency-key requirement is a
separate slice with its own replay semantics, and mixing it in here would hide
this decision inside a repo-wide refactor. The `reason=` half is a ratchet over
a set that is already complete today.

### 7. The two dead `record_refund` twins are deleted

`app/services/expense/service_advances.py::record_refund` and
`app/services/people/expense/expense_service.py::record_refund` are removed.
They were byte-identical, had zero callers across `app/`, `tests/`, `scripts/`
and `tools/`, and had no tests. Two uncalled writers of
`CashAdvance.amount_refunded`, each also deciding `FULLY_SETTLED`, is precisely
the fragmentation this ADR exists to stop — and an exemption for them would
have rested on the premise "nobody calls them", which nothing enforces.

The `CashAdvance.amount_refunded` column, its migration and the two report
queries that read it **stay**. Deleting dead service code is not a destructive
migration, and this ADR does not authorize one.

### 8. The rule is enforced, not stated

`tests/architecture/test_refund_has_one_owner.py` fails the build on any
assignment of `PaymentStatus.REVERSED` or `PaymentIntentStatus.REVERSED` to an
attribute outside the two owners, on any write to `amount_refunded` outside
them, and on any `create_reversal` call site that omits `reason=` (or omits
`idempotency_key=` inside a refund decider). It carries a two-sided sensitivity
proof: a planted violation must be detected, **and** both owners' own
legitimate writes must remain visible to the detector, so a renamed enum, a
moved module or a mis-globbed root fails the build instead of passing over
nothing.

## Consequences

- **No new table, no migration, no new aggregate — deliberately.** See
  "Alternatives rejected"; the follow-up is stated below rather than built here.
- **The refund record today is three durable facts, not one row**: the GL
  reversal journal (amount, date, actor, refund-marked reason and idempotency
  key), an `audit.audit_log` row written through the existing
  `fire_audit_event` path (actor, reason, refunded amount, old→new status, and
  the credit note id when there is one), and the payment's terminal status.
  This is enough to answer "was this refunded, by whom, against what, is it
  posted". It is **not** enough to answer "how much of it, in how many
  instalments" — see the next point.
- **Partial customer refunds are refused loudly.** `refund_payment` raises a
  `ValidationError` naming this ADR when the requested amount is materially
  less than the receipt. The repository has no way to represent one:
  `create_reversal` reverses a whole journal, and there is no aggregate to hold
  a second, smaller refund against the same receipt. Refusing is honest;
  silently reversing the whole receipt would be a cash error. Sub's own
  `partially_refunded` status is unaffected — it is in `_SETTLED_STATUSES` and
  goes down the existing reverse-and-re-post-at-the-new-amount path, which this
  change does not touch.
- **A Paystack partial refund therefore lands as a FAILED `PaymentWebhook`
  row** carrying the refusal message, instead of the silence it produced
  before. Webhook redrive is already disabled repo-wide (2026-08-14, fail
  closed), so that row is a durable "a human must look at this" record rather
  than a retry storm.
- **Follow-up: a first-class `Refund` aggregate.** The thing this change
  deliberately does not build. It is what partial refunds, multiple refunds
  against one receipt, refund-to-credit-note linkage as data rather than as an
  audit field, and a `/refund` API surface all need. It is a migration, a new
  table with tenant scoping and RLS, and a blast radius across AR, payments and
  both sync adapters. Naming the owner first is what makes that table
  designable; building it inside an ownership fix would have meant designing it
  with the owner still undecided.
- **The AR credit-note lifecycle (writers 3, 4, 5) is untouched.** It is a
  large surface, and this change is about naming the owner, not rewriting AR.
  Writer 6 (`credit_note_web.py`) gained one delegate call and no logic; it
  remains a thin adapter over `ar_invoice_service`.
- **Writer 9 (`ap/posting/reversal.py`) is deliberately left where it is.** It
  reverses an AP *invoice posting* — a supplier-side correction, not a refund
  of anyone's money — and folding it into a refund owner would assert a
  relationship that does not exist. It is in scope for the `create_reversal`
  `reason=` ratchet and nothing more.
- Test coverage arrives with the owner: `tests/finance/test_refund_owner.py`
  drives the same refund through all three triggers (direct, Sub sync, Paystack
  webhook) and asserts identical ledger and subledger state — the property the
  eleven-writer arrangement could not have.

## Alternatives rejected

- **Build the `Refund` model now.** The tempting answer, and the wrong order.
  A durable record designed while the owning service is still unnamed encodes
  whichever writer happens to fill it — that is how `amount_refunded` ended up
  with two writers and no readers. The owner and its single entry point are
  what a `Refund` table would hang off; they are cheap, reversible and
  immediately enforceable, and they make the table's shape an obvious question
  rather than a guess. Recorded above as a stated follow-up, not dropped.
- **Make the credit note the refund record.** It is the closest existing
  object and it does not fit. A credit note carries no link to the payment and
  no cash leg: it adjusts what the customer *owes*, which is a different fact
  from money going *back*. Credit notes are issued with no refund at all
  (billing corrections, goodwill), and refunds happen with no credit note at
  all (a duplicate payment returned). Treating one as the other would make
  every "was this refunded?" query wrong in both directions.
- **Make `ReversalService` the owner.** It is the single GL mechanism and
  therefore looks like the chokepoint every refund passes through. But it is
  handed a journal id and a free-text string; it cannot know whether it is
  undoing a refund, an FX revaluation, a duplicate posting or a data-health
  correction, and giving it that knowledge would mean teaching the GL
  mechanism about AR receipts, expense claims and Paystack. The mechanism stays
  a mechanism; the callers say why.
- **Give `PaymentService` both directions, since it already owns the intent.**
  It would make the Paystack webhook simpler and everything else worse. The
  intent exists only for gateway-originated money; a refund of a cheque, a bank
  transfer or a manually keyed receipt has no intent at all, and those are the
  majority of AR receipts. An owner that can only decide about the subset that
  came through one gateway is not the owner.
- **Leave `void_payment`/`mark_bounced` failure semantics alone and only
  extract the shared code.** That preserves, in the new single owner, the
  behaviour that a failed GL reversal still voids the payment — the exact
  divergence the sync adapter had already refused to accept. Consolidating two
  implementations and keeping the worse of their two failure modes is not a
  consolidation.
- **Route the two dead `record_refund` twins through the new owner instead of
  deleting them.** A cash advance refunded by an employee is company money
  coming back in, not a customer refund; wiring it to the customer owner would
  create the second writer this ADR removes. With zero callers and zero tests,
  the choice is delete or design the cash-advance refund decision properly —
  and the latter is not this slice.
