# Dotmac Sub vendor to ERP supplier

The `/api/v1/sync/sub/suppliers` endpoint accepts a version 1 vendor identity
under the `sub:suppliers:write` service scope. The authenticated service key
sets the ERP organization; the request cannot select one. The source vendor
UUID is unique within that organization. Repeated submissions return the
existing supplier without changing its accounting data.

Supplier creation uses `SupplierService.create_supplier`, including its normal
supplier code and AP account validation. Name maps to legal and trading name;
contact name, email and phone map to primary contact. A Sub code over the ERP
30 character limit, or an empty code, gets a deterministic `SUB-` code derived
from the source UUID. An unrelated supplier code collision is rejected for
review; the integration does not merge identities by name or code. Tax,
payment, and bank fields remain ERP owned.

This follows ERP's current AP supplier owner. The accounting adoption boundary
places supplier liabilities in a later `dotmac-payables` slice; this integration
does not preempt that Starter cutover or create a second accounting owner.

Configure `gl.sub_supplier_ap_control_account_id` for each ERP organization
with the UUID of its active AP liability account. For the current chart,
Finance expects to select **2000 – Trade Payables**. The account code is not
hardcoded: ERP validates the chosen account against its organization, active
status, AP subledger type, and liability category. A missing or invalid setting
rejects creation.

Deploy the ERP migration and endpoint first, configure the AP account and
service key scope, then deploy the Sub vendor flow. Sub stores requests in its
outbox and retries transient delivery failures. Inspect dead letters for
configuration errors and code conflicts. After correcting the cause, replay
the same source vendor UUID; ERP will return the same supplier if creation
previously succeeded. Sub can reconstruct a missing reference from its stored
accepted response.

Acceptance requires creating a vendor in Sub, running the outbox relay,
confirming one supplier in ERP with the configured AP account, and confirming
Sub's supplier reference. Repeat delivery and verify the same supplier ID.
Local execution has not performed this live test because the development
environment lacks the full application dependencies and migrated databases.
