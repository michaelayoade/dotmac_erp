# ERP external-connector surface — measurement and adjudication

- Date: 2026-08-15
- Measured at: `381eb7b16d5b1fcaba1ebac621a41ef8eba3b1da` (working tree, plus the
  rename recorded below)
- Detector: the Dotmac Governance engine's classifier, proposed ADR 0010, run
  read-only. ERP owns no copy of it.
- Machine-readable companion: `external-connector-surface.json`

This is a dated characterization of what ERP's application runtime does today.
It is a fact sheet, not a mandate, and it does not authorize retiring anything.

## Measured scope

`app` is ERP's only application runtime root. `src/` holds CSS and no Python.
Inside `app`, the engine's structural rules skip `tests`, `test`, `__pycache__`,
`migrations`, `alembic`, `node_modules`, `.venv`, and `test_*.py`. That leaves
**1457 runtime files**, all of which parsed — the sweep measured zero files by
failing closed.

## Counts

| Category | Count |
| --- | --- |
| `http_client` | 21 |
| `webhook_surface` | 11 |
| `provider_credential` | 3 |
| `connector_task` | 12 |
| `sync_checkpoint` | 7 |
| `delivery_retry` | 6 |

The per-category file lists are in the JSON. These are the numbers the staged
schema-6 profile declares as baselines.

## The `sync_checkpoint` adjudication

At the committed revision `sync_checkpoint` measures **8**, not the 7 above. The
eighth was `EventHandlerCheckpoint`
(`app/models/finance/platform/event_handler_checkpoint.py`). Its docstring
claimed idempotency over ERP's own outbox, which — if true — would make it
at-most-once machinery rather than a position in an external feed. A docstring
is not evidence, so the table, its columns, its readers, its writers and its
state transitions were inspected directly.

### Evidence

1. **Every column is local.** `checkpoint_id` (PK), `event_id`, `handler_name`,
   `processed_at`, `status`, `attempts`, `last_error`, `created_at`. There is no
   external system identifier, no provider column, no remote or upstream id, no
   `last_synced_at`, no cursor, and no high-water mark of any kind.
2. **The referenced feed is ERP's own.** `event_id` is a foreign key to
   `platform.event_outbox`, ERP's transactional outbox, written atomically with
   ERP business transactions. Nothing external writes it.
3. **The cardinality is decisive.** `UNIQUE (event_id, handler_name)` means one
   row per event per handler. A feed checkpoint is ONE row whose stored position
   ADVANCES as a stream is consumed. This table advances nothing; it accumulates
   one immutable record per `(event, handler)` pair. That is an execution
   receipt, not a position.
4. **State transitions are execution outcomes.** `PENDING → SUCCESS | FAILED`,
   with `attempts` and `last_error`. That is at-most-once bookkeeping in the
   shape ADR-0014 describes.
5. **Readers and writers: there are none.** Across the whole repository the only
   references were the model definition, two `__init__` re-exports, and the
   `CREATE TABLE` in `alembic/versions/create_ifrs_schemas.py`. No service,
   task, API or web route reads or writes it. ERP's real outbox de-duplication
   is `EventOutbox.idempotency_key`, used in
   `app/services/finance/platform/outbox_publisher.py`.

### Conclusion

It stores no external-feed position. It was a misnamed local receipt table, and
under proposed ADR 0010 the remedy for a misnaming is to fix the name, never to
declare an exclusion — ERP declares none.

The code concept was renamed to `EventHandlerReceipt` /
`HandlerReceiptStatus`, and the `sync_checkpoint` baseline was lowered from 8 to
7 **in the same change**.

### Persistence identity is deliberately unchanged

A changed table would be a migration masquerading as a rename. Frozen, and
enforced by `scripts/check_connector_adoption.py --persistence`:

| Kept as-is | Value |
| --- | --- |
| table | `event_handler_checkpoint` |
| schema | `platform` |
| primary key column | `checkpoint_id` |
| unique constraint | `uq_checkpoint_event_handler` |
| PostgreSQL enum type | `checkpoint_status` |
| columns | all eight, unchanged and in order |
| migration lineage | `alembic/versions/create_ifrs_schemas.py`, untouched |

## The corrected cursor rule, checked against ERP

Proposed ADR 0010 narrows the NAME net: `checkpoint`, `syncstate` and
`synccursor` count alone, while a bare `*Cursor` must also name a feed. Against
ERP's real classes:

- **ERP has no `*Cursor` classes at all**, so the narrowing costs ERP nothing.
  There is no ERP analogue of `dotmac_sub`'s `InboxTeamRoundRobinCursor`.
- Before the rename, the ONLY two name-net matches in the entire runtime were
  `EventHandlerCheckpoint` and `CheckpointStatus`, both in the one file above,
  and both matched on the standalone `checkpoint` hint rather than the ambiguous
  cursor branch. ERP's adjudication therefore tests the `checkpoint` clause, not
  the cursor clause.
- After the rename, **all 7** remaining `sync_checkpoint` counts come from the
  COLUMN net (`last_synced_at`) and none from the NAME net:
  `app/models/finance/ar/customer_payment.py`,
  `app/models/finance/ar/invoice.py`,
  `app/models/inventory/material_request.py`, `app/models/mixins.py`,
  `app/models/people/base.py`, `app/models/pm/time_entry.py`,
  `app/schemas/support.py`.

That last point is the practical demonstration of the ADR's claim that
narrowing the name rule costs no recall: everything ERP still counts is caught
because it genuinely stores a watermark.

## What is NOT resolved here

- **The receipt table is dead code.** It has no reader and no writer. Renaming
  it was the minimal correct action for the classification question; whether to
  keep, wire up or drop the table is a separate decision with a migration
  attached, and it is deliberately not taken here.
- **`sync_checkpoint` is still 7.** Those seven `last_synced_at` columns are
  real feed watermarks on ERP business models. Retiring them is Integrator work,
  not a naming question.
