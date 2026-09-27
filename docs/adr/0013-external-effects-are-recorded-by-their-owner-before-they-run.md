# ADR-0013: External effects are recorded by their owner before they run

**Status:** Accepted
**Date:** 2026-09-27
**Decider:** Michael Ayoade (approved 2026-09-27)

## Context

ERP performs external, irreversible effects: payment-provider calls
(Paystack, Mono), outbound deliveries to other Dotmac applications, and email.
Once such a call succeeds, ERP cannot take it back by rolling back a database
transaction. What ERP *can* control is whether a durable record of the attempt
exists when the surrounding transaction does not survive.

A read-only survey of `main` at `a2b7d9cd` (2026-09-27) found the platform
already owns the right mechanisms, but not every effect uses them:

- `EventOutbox` (`app/models/finance/platform/event_outbox.py`) is a durable
  delivery owner with retry, claim/lease and terminal states. Effects routed
  through it survive the caller's rollback.
- ADR-0001 names `dotmac_kernel.idempotency` as ERP's sole future durable
  at-most-once owner. The retiring `platform.idempotency_record` ledger is
  frozen by `tests/architecture/test_idempotency_legacy_ratchet.py`.
- `PaymentIntent` status has one writer (ADR-0005). The Paystack **transfer**
  path already commits its intent *before* calling the provider.
- The Paystack **initialize** path does not. `payment_service.py` builds the
  intent, calls `initialize_transaction`, and only then adds and commits it. If
  the request dies between the provider call and the commit, Paystack holds a
  pending transaction that ERP never recorded. No money moves, because the
  customer never receives the authorization URL, but the provider and ERP
  disagree.
- `app/services/email.py` sends through `smtplib` directly. A successful send
  leaves a log line and no durable record.
- Direct provider clients are constructed in eight modules, and `smtplib` is
  used in two, with nothing preventing a ninth or a third.

The same failure class was fixed in Sub by ADR 0017, where the absence of a
durable record let a week of failed router enforcement go unseen. Dotmac's
standard is to reuse the pattern, not the code.

## Decision

1. **Every external, irreversible effect is recorded by its owner before it
   runs.** No service calls a payment provider, another Dotmac application, or
   an SMTP server directly from inside a request or service transaction.
2. **Asynchronous deliveries go through `EventOutbox`.** Email and
   application-to-application pushes are enqueued in the caller's transaction
   and delivered by the relay, which owns attempts, retries and terminal
   outcomes.
3. **Synchronous provider calls use an intent-first protocol inside their
   owner.** The owner commits the intent (for payments, `PaymentIntent`) in a
   pending state, then calls the provider with an idempotency key derived from
   that intent, then settles the intent with the result in a separate step. A
   provider success whose settlement fails leaves a committed pending intent
   that reconciliation can resolve against the provider by reference. The
   durable at-most-once key comes from `dotmac_kernel.idempotency`, per
   ADR-0001.
4. **A two-directional ratchet enforces the rule.** An architecture test
   inventories every module that constructs a provider client or uses
   `smtplib`, and freezes today's set as grandfathered. A new direct caller
   fails CI. A migrated caller must lower the inventory in the same change.
   The detector states its blind spots and carries a planted sensitivity
   proof.
5. Callers migrate in risk order, each as its own change that shrinks the
   ratchet: Paystack initialize first (following the transfer path's existing
   intent-first shape), then email through the outbox, then the remaining
   provider modules.

This ADR records the decision. The ratchet and each migration land in their
own changes, which cite it.

## Consequences

- A provider or SMTP failure after the effect can no longer erase the
  evidence that the effect was attempted; reconciliation always has a row to
  start from.
- Payment initialization gains one commit before the provider call. A pending
  intent whose provider call never completes is expired by its existing
  `expires_at`, as abandoned checkouts already are.
- Email delivery becomes asynchronous. Callers that need to know a message
  was accepted read the outbox outcome instead of a return value.
- New integrations must choose an owner (outbox or intent-first) at design
  time; the ratchet makes a direct call a reviewed exception rather than a
  default.
- The ratchet's grandfathered set is migration debt, not an accepted
  exemption. It is recorded in the debt register and may only shrink.

## Alternatives rejected

- **Patch each call site individually** (reorder the Paystack call, add an
  email-attempt table). Rejected: it fixes today's two sites, leaves the class
  open for the next integration, and adds a second attempt-recording
  mechanism beside `EventOutbox`.
- **Record attempts on an independent session, as Sub's ADR 0017 does.**
  Rejected for ERP: Sub needed an out-of-band writer because its enforcement
  path had no durable owner. ERP already has `EventOutbox` and a named
  at-most-once owner, and a second write path would violate the one-writer
  rule for delivery state.
- **Rely on provider webhooks to reconcile.** Rejected: a webhook arrives only
  for effects the customer completed. It cannot report an initialized but
  unrecorded transaction, and email has no webhook at all.
- **Accept the gap because no money moves.** Rejected: a provider-side
  transaction ERP does not know about is a reconciliation discrepancy, and
  the same missing-record shape on a transfer or refund path would move
  money.
