# ADR-0013: External effects are recorded by their owner before they run

**Status:** Proposed
**Date:** 2026-09-27
**Decider:** Michael Ayoade (decision pending)

## Context

ERP makes payment-provider calls, sends email, and delivers data to other
applications. A database rollback cannot undo an effect accepted outside ERP.
The failure to address is an accepted external effect with no committed local
record from which its owner can determine the outcome or repair a retry.

At this draft's base (`99e7fa52`, 2026-09-27), the code has several distinct
paths:

- `PaymentService.create_invoice_payment_intent` calls Paystack
  `initialize_transaction` before adding and committing its `PaymentIntent`.
  A failure between those steps can leave a provider transaction with no ERP
  intent. The resulting customer and provider outcome is unobserved by ERP;
  this ADR does not assume that no money can move.
- `PaymentService.create_expense_payment_intent` resolves an account and
  creates a Paystack transfer recipient before committing the intent. Its
  separate `initiate_expense_transfer` path uses that committed intent, but
  still calls Paystack directly. ADR-0005 makes `PaymentService` the sole
  writer of `PaymentIntent.status`, including reconciliation of uncertain
  transfers; the existing transfer sequence is not a general solution for
  external effects.
- `app/services/email.py::send_email` calls SMTP directly. The Celery
  `send_email_async` task retries some failures and `queue_email` submits a
  task, but neither is a transactional outbox record of the caller's decision.
- `OutboxPublisher.publish_event` adds an `EventOutbox` row and flushes in the
  **caller's transaction**. The row and its business change become durable
  together only if that transaction commits; a rollback removes both. The
  relay uses claim, lease, delivery and settlement, and can redeliver after a
  delivery succeeds but settlement fails.

ADR-0001 assigns the kernel idempotency ledger to transactional database
effects and assigns non-transactional effects to an outbox with at-least-once
delivery and receiver deduplication. The accepted external connector boundary
in `docs/external-connector-surface.md` moves provider packages, credentials,
ingress and egress behind Dotmac Integrator. ERP retains its payment decision
and intent owner; Integrator owns external transport and its delivery evidence.
The existing direct clients are transition debt, not the target execution
path. The Governance schema-9 ratchet already measures the broader connector
surface, but no ADR-specific gate proving record-before-effect has been added.

## Decision

1. **Record a committed owner decision before an external effect is eligible
   to run.** ERP's owning service commits the business state and a durable,
   correlated delivery intent in one transaction. If the caller rolls back,
   neither survives and no delivery is eligible. A task submission, log line,
   uncommitted `EventOutbox` flush, or provider response is not that record.
   For payments, `PaymentService` owns the payment decision and
   `PaymentIntent` transitions under ADR-0005. A transport result is an
   observation returned to this owner, not permission for a connector to
   assign ERP's status.

2. **Deliver through the contracted Integrator boundary.** ERP publishes a
   provider-neutral command or notification through a versioned ERP-to-
   Integrator contract; Integrator's bound connector performs provider or
   SMTP I/O, holds provider credentials, records attempts and outcomes, and
   returns typed observations. ERP reconciles those observations to its own
   intent. `EventOutbox` is ERP's existing transactional delivery owner and
   can carry an ERP-side command when its event, handler and contract are
   defined; this ADR does not claim that today's relay already delivers these
   payment or email commands. Direct ERP provider and SMTP callers are retired
   only after their replacement contract, delivery path and reconciliation
   have been proved.

3. **State the delivery guarantee honestly.** An outbox makes the local
   business change and delivery intent atomic, then uses at-least-once attempt
   semantics with bounded retries and dead-letter outcomes. It cannot
   guarantee remote acceptance or atomically commit with a provider or SMTP
   server. A crash after remote acceptance and before settlement can cause
   another delivery.
   The kernel idempotency ledger only guards effects in ERP's database
   transaction; it does not make a remote effect at most once. Each cutover
   must identify a stable operation reference, prove the receiver or provider
   deduplication contract where available, and reconcile unknown outcomes
   against authoritative remote evidence before allowing another money-moving
   attempt. No provider-side idempotency guarantee is presumed from the mere
   presence of a reference string.

4. **Treat email as at-least-once delivery.** A relay may retry after SMTP
   accepts a message but before the success settlement commits, so a recipient
   may receive a duplicate. The email slice must state which message classes
   permit automatic retry, which uncertain outcomes require review, and how
   delivery evidence is correlated. The acceptable duplicate policy is an
   open business decision; this ADR does not promise exactly-once email.

5. **Build a specific record-before-effect gate during migration.** The
   existing Governance schema-9 connector ratchet is retained and lowered as
   direct clients leave ERP. A separate architecture gate must inventory every
   direct external-effect entry point across services, tasks, routes, scripts
   and workers; classify each by owner, committed record, transport and
   cutover state; freeze the complete legacy inventory; fail on additions or
   unrecorded reductions; and lower its baseline only with deletion or a
   proved Integrator cutover. Its detector must show its scope and blind spots
   and pass planted-positive and known-negative sensitivity checks. It must
   exercise transaction rollback, remote-acceptance/settlement failure,
   duplicate delivery, and unknown-outcome reconciliation in focused tests.
   This gate is future work, not enforcement supplied by this ADR.

## Transition and cutover

Payment initialization, transfer initiation and recipient creation each need
an explicit migration slice; the invoice initialization gap is the first
record-before-call defect to close. Email and remaining direct clients follow
through named slices. A slice inventories its caller and provider operation,
defines the ERP intent and correlation reference, installs an Integrator
contract and connector, proves replay and reconciliation behavior, then
switches the caller and retires its direct client, credential use, ingress and
egress. The existing payment status owner remains `PaymentService` throughout.
No cutover is claimed by this document, and the broader schema-9 baseline must
be lowered only with the proved retirement.

The current synchronous invoice checkout returns a Paystack authorization
URL. Its replacement must define when and how a contracted Integrator result
becomes available to the customer without reintroducing provider I/O in ERP.
That user-facing response contract and the handling of provider operations
without demonstrable deduplication remain open for their migration slices.
Until then, direct paths remain measured legacy behavior, not compliant
implementations of this proposed target.

## Consequences

- A committed delivery intent gives the owning service a starting point for
  retry and reconciliation. It gives no evidence for a transaction that
  rolled back and caused no eligible delivery.
- Payment attempts with unknown external outcomes remain unresolved until
  `PaymentService` obtains a supported verdict. An expiry timestamp alone
  cannot prove that a provider operation did not succeed.
- Callers of migrated email paths receive a durable local acceptance result,
  not proof of SMTP acceptance or final delivery. Duplicate policy and
  outcome visibility must be settled before that slice's cutover.
- This ADR creates migration and gate work. It changes no runtime code or
  connector baseline by itself.

## Alternatives rejected

- **Wrap a provider call in `dotmac_kernel.idempotency.execute_once`.** The
  database ledger and remote effect cannot commit atomically. ADR-0001
  reserves that mechanism for transactional database effects.
- **Commit an intent, then keep direct ERP provider calls permanently.** A
  local row improves reconciliation but leaves transport, credentials and
  retries on the wrong side of the accepted Integrator boundary.
- **Treat a Celery task or an SMTP success log as the attempt record.** Neither
  commits atomically with the business decision, and neither closes the
  send-before-settle duplicate window.
- **Wait for a webhook as the first record.** A webhook is an observation that
  may be delayed or absent. It cannot replace ERP's prior decision and
  correlation reference.
