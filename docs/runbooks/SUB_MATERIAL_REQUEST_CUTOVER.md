# DotMac Sub material-request cutover

This change is compatible with `dotmac_sub` PR #2252 and must not be deployed independently as an active flow.

## Automated bootstrap

After both applications are deployed but before enabling traffic:

1. Generate one shared webhook secret and expose it to both applications as `ERP_SUB_WEBHOOK_SECRET`.
2. Run Sub's bootstrap script with `--prepare` to create disabled bindings and obtain its callback URL.
3. Run `python scripts/one_off/bootstrap_sub_material_integration.py --organization-id <org-uuid> --callback-url <sub-callback-url> --apply`.
4. Capture the one-time service token and expose it to Sub as `ERP_SUB_SERVICE_TOKEN`.
5. Re-run the Sub bootstrap with `--apply` to validate bindings and perform the first catalogue import.

The ERP script creates a non-human API key with only `sub:inventory:read`, `sub:material:write`, and `sub:material:read`. It also configures the asynchronous signed status callback. No secret is committed to Git or stored in the service-hook row.

## Authority cutover

- CRM item writes and CRM inventory/material-request routes return HTTP 410.
- `/sync/sub/inventory*` is the catalogue read path for Sub.
- `/sync/sub/material-requests` is the external material-request writer.
- Existing CRM material rows retain `source_system=crm` and never emit callbacks to Sub.
- New Sub rows use `source_system=sub`; only these rows emit signed Sub callbacks.

Confirm that a submitted Sub request is created once, can be issued only in ERP, and produces an HMAC-signed callback whose `omni_id` is the Sub UUID. Confirm CRM item/material endpoints return 410 while unrelated CRM expense and procurement endpoints remain available.
