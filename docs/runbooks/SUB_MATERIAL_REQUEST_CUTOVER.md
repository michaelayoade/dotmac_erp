# Dotmac Sub material-support activation

This procedure activates the canonical Sub-to-ERP material-support contract.
It does not preserve a CRM compatibility path. Complete the ERP retirement
cutover in `CRM_RETIREMENT_CUTOVER.md` before enabling Sub delivery.

## Preconditions

- Compatible ERP and Sub revisions are deployed and their CI evidence is
  attached to the change record.
- ERP exposes only the canonical `/api/v1/sync/sub/*` routes for this flow.
- Historical CRM material evidence is sealed in
  `archive.retired_crm_records`; it is not live input and is never relabelled.
- The service credential and signing material exist only through their
  approved OpenBao pointers. Do not paste or print secret values into a shell
  transcript, ticket, pull request, or deployment log.

## Configure the binding

1. Create a non-human ERP API key with only `sub:inventory:read`,
   `sub:material:write`, and `sub:material:read`.
2. Install the key in Sub through its approved secret-source configuration.
3. Configure the signed ERP outcome binding to the approved Sub callback URL.
4. Install the signing material in both applications through the corresponding
   OpenBao-backed secret sources.
5. Keep the Sub sender disabled while the read-only catalogue and signature
   checks run.

## Prove the authority boundary

With the sender still disabled, verify:

- `/sync/sub/inventory*` is the read-only catalogue surface;
- `/sync/sub/material-requests` is the only external material-request writer;
- a submitted request stores `source_system="sub"` and the Sub UUID in
  `source_id`;
- an identical replay returns the same ERP request and a changed-body replay
  fails closed;
- the request can be issued only by ERP inventory policy;
- the signed outcome and polling repair identify the request by
  `source_request_id`; and
- no CRM route, scope, task, setting, client, service key, or live mapping is
  present.

## Enable and observe

1. Enable one organization and one controlled material request.
2. Prove `submitted -> pending_stock` and `submitted -> issued` outcomes as
   applicable, including the Sub projection.
3. Verify retry, dead-letter, identity-conflict, and reconciliation alerts.
4. Reconcile the ERP request, signed outcome evidence, and Sub material
   dependency one to one.
5. Expand enablement only after the agreed observation window has no
   unexplained drift.

There is no CRM rollback mode. To stop an activation, disable new Sub delivery,
repair forward, and reconcile every request ERP already accepted.
