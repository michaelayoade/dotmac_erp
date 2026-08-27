# Paystack notification forwarding to Selfcare

ERP receives and validates the Paystack webhook because the merchant account's
webhook destination points to ERP. For a successful charge whose reference is
not an ERP payment intent, ERP sends only the provider type and reference to
Selfcare's authenticated reconciliation endpoint.

The forwarded notice is not trusted as proof of money. Selfcare independently
verifies the reference with Paystack, then its canonical financial owners alone
decide payment creation, allocation, account credit, subscription, and access.

Requirements:

- ERP's Paystack configuration must use the same live merchant account.
- The ERP-to-Selfcare API key must have `billing:provider:write` and must remain
  in the approved secret store.
- Delivery failures return HTTP 503 so Paystack retries the original event.
- Repeated delivery is safe because the Paystack reference and provider
  transaction identity are idempotent in Selfcare.

Deploy Selfcare before ERP so the reconciliation endpoint exists before ERP
begins forwarding notifications.
