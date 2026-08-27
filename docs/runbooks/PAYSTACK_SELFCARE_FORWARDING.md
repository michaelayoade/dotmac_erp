# Paystack notification forwarding to Selfcare

ERP receives and validates the Paystack webhook because the merchant account's
webhook destination points to ERP. For a successful charge whose reference is
not an ERP payment intent and begins with Selfcare's canonical `DMAC-` prefix,
ERP relays the exact raw body and original Paystack signature to Selfcare's
existing `POST /api/v1/payment-events/paystack` endpoint.

Selfcare verifies Paystack's signature over the unchanged bytes, records the
verified receipt idempotently, and its canonical financial owners alone decide
payment creation, allocation, account credit, subscription, and access.

Requirements:

- ERP's Paystack configuration must use the same live merchant account.
- Selfcare and ERP must resolve the same live Paystack signing credential from
  their approved secret stores.
- Delivery failures return HTTP 503 so Paystack retries the original event.
- Repeated delivery is safe because Selfcare's existing provider inbox is
  idempotent by Paystack event identity.

No Selfcare API deployment is required; the signed webhook endpoint already
owns this flow.
