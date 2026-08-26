"""Which scope each live `/sync/crm` route requires.

## Why this file exists before anything enforces it

`ApiKey.has_scope` is called NOWHERE in this application. Every service key —
scoped or not — can reach every route, because no route declares what it needs
and no guard asks. The scope vocabulary exists as data on the key and is inert.

That is the state a fail-closed machine-credential facility replaces, and the
replacement cannot be done safely from the key side alone. Deriving a key's
minimum scopes from what it is *permitted* to do is circular when it is
permitted everything; deriving them from observed traffic is only as complete as
the log window. Both were tried while assembling this map, and both were wrong:

* three scopes, derived from 84k lines of retained access logs;
* six, after reading the caller's code and finding quarterly NCC and purchase
  paths that no retained log contained;
* six again, after finding a third of the routes already retired.

So the mapping is written down HERE, next to the routes, where a new route is
added in the same file and the guard below notices immediately.

## What the map is not

It is not enforcement. Adding an entry changes no behaviour today. It is the
reviewable artefact a reissue is made against, so the scopes a credential gets
are a decision someone can check rather than an inference in a chat log.

## Retired routes are deliberately absent

Eight `/sync/crm` routes carry `require_crm_material_sync_retired` and answer
410 Gone: material and item authority moved to `/sync/sub`. A retired route
needs no scope, and giving one an entry would quietly imply a credential should
still be able to reach it.
"""

from __future__ import annotations

from typing import Final

#: Scope required by each live route, keyed by `"{METHOD} {path}"` exactly as
#: the router declares it — path template, not a resolved id.
CRM_SYNC_ROUTE_SCOPES: Final[dict[str, str]] = {
    # Regulatory reads. The NCC pack is quarterly, which is precisely why it
    # was invisible in the access logs and had to come from the caller's code.
    "GET /ncc/financials": "crm:ncc:read",
    "GET /ncc/staff-headcount": "crm:ncc:read",
    "GET /contacts/people": "crm:ncc:read",
    "GET /contacts/companies": "crm:ncc:read",
    # Workforce reads.
    "GET /workforce/employees": "crm:workforce:read",
    "GET /workforce/departments": "crm:workforce:read",
    # Expense capture.
    "GET /expense-categories": "crm:expense:write",
    "GET /expense-claims/{omni_id}": "crm:expense:write",
    "POST /expense-claims": "crm:expense:write",
    "POST /expense-totals": "crm:expense:write",
    # Purchasing.
    "POST /purchase-orders": "crm:po:write",
    "POST /purchase-orders/variations": "crm:po:write",
    "POST /purchase-invoices": "crm:ap:write",
    "POST /purchase-invoices/{purchase_invoice_id}/attachments": "crm:ap:write",
    # Generic sync surface.
    "POST /bulk": "crm:sync:write",
    "POST /webhook/{entity_type}": "crm:sync:write",
    "POST /reconcile-orphans": "crm:sync:write",
}

#: NOT here, deliberately: `GET /projects`, `GET /tickets` and
#: `GET /work-orders`. Those three carry `require_tenant_auth` — they are ERP's
#: own UI reading CRM data as a signed-in user, not a service credential
#: calling in. A scope entry for them would put ERP's interactive surface into
#: a machine credential's grant, which is the opposite of what this map is for.

#: The minimum set a credential needs to serve the whole live CRM surface.
#: `crm:write` is deliberately NOT here: it is the catch-all, and no route
#: justifies it.
CRM_SERVICE_MINIMUM_SCOPES: Final[frozenset[str]] = frozenset(
    CRM_SYNC_ROUTE_SCOPES.values()
)
