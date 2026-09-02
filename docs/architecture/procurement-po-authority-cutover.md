# Purchase-order authority cutover

Status: **census complete; column ownership decided; storage separation
designed and NOT yet applied; no authority has moved.**

ERP source revision: `4af9ba14ee2d750f63d86ac50f190f47c21f0ca2`
Target owner: `dotmac-procurement 0.1.0a1`
(peeled tag `dotmac-procurement-v0.1.0a1` ->
`20d24703e70e4d361de2f406165df4b36cbee507`)

## The rule this cutover is governed by

> **Authority transfers at the fact/column level. A row containing facts owned
> by different authorities cannot be transferred as a unit. Separate the
> storage or projection first; two owners writing different columns of one row
> is split authority.**

This is why the purchase order cannot simply be repointed. `ap.purchase_order`
today carries procurement commitment facts and AP/GL-derived facts on one
physical row.

## Intended ownership after cutover

| Concern | Owner after cutover |
| --- | --- |
| PO creation, approval, award, supplier selection, commitment, status | `dotmac-procurement` |
| Supplier invoices, receipts, payments, accounting consequences | ERP AP (unchanged) |
| `amount_received` | Derived from procurement-owned line quantities |
| `amount_invoiced` | ERP AP — see the open decision below |

## Column-ownership decision (sequence step 2)

Measured against `app/models/finance/ap/purchase_order.py`.

| Column | Writers today | Decision |
| --- | --- | --- |
| supplier_id, po_number, po_date, expected_delivery_date, currency_code, exchange_rate, subtotal, tax_amount, total_amount, shipping_address, terms_and_conditions, status, approved_by_user_id, approved_at, created_by_user_id, correlation_id | `PurchaseOrderService` | Move to `dotmac-procurement` |
| `amount_received` | `goods_receipt.py:632` (absolute recompute), `purchase_order.py:859` (`+=`, dead) | **DERIVE.** It is already a cached aggregate of `sum(line.quantity_received * line.unit_price)` (`goods_receipt.py:625-632`). Deriving preserves behaviour exactly and removes the mixed column without a projection table. |
| `amount_invoiced` | **NONE — zero writers repo-wide** | **OPEN DECISION.** See below. |
| `commitment_journal_entry_id` | **NONE — zero writers, zero readers** | Orphaned DDL. Drop, or leave unmapped and unreferenced. |
| `budget_id` | `purchase_order.py:371,493` | Written, never read anywhere. Product-owned; keep in ERP or retire. |
| `is_amendment`, `original_po_id`, `amendment_version`, `amendment_reason`, `variation_id` | **NONE** | DDL-only, together with `POStatus.SUPERSEDED`. No writer on either side. |

### `amount_invoiced` is not derivable today

`app/models/finance/ap/supplier_invoice.py` has **no `po_id` or
`purchase_order` column** — there is no PO-to-invoice link to derive from.
The column is created `NOT NULL DEFAULT 0`, never written, and rendered to
users at `templates/finance/ap/purchase_order_detail.html:339` and
`app/services/finance/ap/web/purchase_order_web.py:267`. It therefore displays
a permanently-zero monetary figure. The sibling
`purchase_order_line.quantity_invoiced` has the same shape.

Deriving it requires first introducing an invoice-to-PO link, which is new
behaviour, not a cutover step. The three options, none of which should be
chosen silently because all touch a displayed financial figure:

1. Narrow ERP-owned projection table keyed by PO id, written only by AP.
   Preserves current behaviour (stays zero) and unmixes the row.
2. Add the invoice-to-PO link and derive. Fixes the display; a behaviour change.
3. Remove the dead column and its UI element.

## Census (sequence step 1)

### Writers of PO / PO line state

- `app/services/finance/ap/purchase_order.py` — the only module constructing
  rows. `create_po` (357), `update_po` (483, and `db.delete(line)` at 497 —
  delete-and-recreate of all lines), `delete_po` (567), `submit_for_approval`
  (611), `approve_po` (683), `cancel_po` (760), `close_po` (823, **no
  production caller**), `update_received_amount` (859, **no production
  caller**).
- `app/services/finance/ap/goods_receipt.py` — **a second, independent
  writer that bypasses `PurchaseOrderService`**: `purchase_order_line
  .quantity_received` at 294, 651, 676; `amount_received` at 632; `status` at
  635/637.
- `app/services/finance/automation/workflow.py:909` — a generic
  `setattr(entity, field_name, value)` reachable for `PURCHASE_ORDER` via
  `app/services/finance/automation/entity_registry.py:64`. It can set **any**
  PO column, `status` included, with no guard, and is reachable from the very
  events PO transitions fire (`purchase_order.py:618,692,767`).
- `app/services/sync/sub/procurement.py:1169` — inbound writer, delegates to
  `create_po`; always lands in DRAFT.

`amount_received` and `status` therefore have **two live authorities with
different semantics** (`+=` versus absolute recompute). Consolidating them is
part of this cutover, not a follow-up.

### Entrypoints that must delegate (sequence step 6)

- **API** — `app/api/finance/ap_routes/purchase_orders.py`: 6 routes
  (create/read/list/submit/approve/cancel).
- **Web** — `app/web/finance/ap.py` lines 986-1256: 12 routes, implemented by
  `app/services/finance/ap/web/purchase_order_web.py`.
- **Sync** — `app/api/sync/dotmac_sub.py:452` -> `sync/sub/procurement.py:1042`.
- **Jobs** — **none exist.** No Celery task or Beat entry touches purchase
  orders. Nothing to convert; nothing to invent.
- **Scripts/CLI** — **no runtime entrypoint exists.** `scripts/seed_rbac.py`
  carries permission strings only. `scripts/add_active_filters.py:82` is a
  one-off codemod that names `list_purchase_orders_context`; it writes no
  purchase-order state and is not a runtime path. It is worth noting for one
  reason: it targets `app/services/finance/ap/web.py`, the shadowed dead
  module below, so running it would patch unreachable code. Retire it with
  that file.
- **Templates** — `templates/finance/ap/purchase_orders.html`,
  `purchase_order_detail.html`, `purchase_order_form.html`,
  `purchase_order_pdf.html`.

### Audit evidence that must be preserved explicitly (step 7)

`app/services/audit_listener.py:429-430` registers `before_flush`/`after_flush`
on the SQLAlchemy `Session` **class**, not on a declarative `Base`. Coverage is
by table name and requires an `organization_id` attribute.

- `ap.purchase_order` **is** audited today (not in `_SKIP_TABLES`, has
  `organization_id`).
- `ap.purchase_order_line` is **silently unaudited** — it has no
  `organization_id`, so `_collect_changes` skips it at 174, 202 and 231. Every
  line write, including the `update_po` delete-and-recreate and all
  `quantity_received` mutations, is invisible today.
- The PO path makes **zero** explicit `fire_audit_event` calls.

Because the listener keys on ERP-mapped models, writes performed through
`dotmac_procurement` models would **not** be audited by inheritance. The
audited header path must therefore get explicit audit evidence at cutover.
The unaudited line path is a pre-existing gap and is recorded here, not
silently widened.

## Dead code found during the census

`app/services/finance/ap/web.py` (4651 lines) is **unreachable**: the
`app/services/finance/ap/web/` package shadows the module.

Verified in three parts, because the claim is load-bearing for deletion:
both `web.py` and `web/__init__.py` exist in the same package directory; a
clean-room `importlib.util.find_spec` reproduction of that exact layout
resolves the name to the package's `__init__.py`, not the module; and nothing
under `app/services/finance/ap/` uses `importlib` or `sys.path` manipulation
that could load the shadowed file by another route.

It contains a stale duplicate of the PO web surface, missing the
`update`/`delete` handlers the live package has. It must not be "converted" —
it must be deleted.

## Cutover gates

Authority may move only when all of these hold:

1. Exact released pin composed and its `pc` lineage migrated.
2. Storage separation applied so no PO row carries columns owned by two
   authorities.
3. Backfill on restored production data preserving source ids.
4. Old and composed reads compared against identical restored data with no
   unexplained differences.
5. Every entrypoint above delegates; authorization, audit and transactional
   behaviour preserved.
6. The writer switches exactly once.
7. A planted call to the legacy write path is proven to refuse.

## Blocked

Gates 3 and 4 require the isolated restored-production rehearsal target. That
work is currently blocked; see the programme report. No pin has been added and
no writer has been repointed, so ERP remains the sole PO authority and this
document describes intent, not applied state.
