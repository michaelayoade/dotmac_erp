# ERP direct external-connector surface

ERP adopts the Governance-owned schema-9 ratchet from accepted ADR 0011 at
immutable canonical-main commit
`4f6fbf98c25f7cfbb3dacc4f3d2f5fd7e473f193`.

The ratchet freezes measured legacy connector surface while providers move
behind Dotmac Integrator. It is transitional defence in depth, not runtime
isolation. The permanent boundary is Integrator-only connector packages and
provider secrets, default-deny product egress, provider ingress terminating at
Integrator, and versioned inbox/outbox contracts between independent apps.

ERP declares no measurement roots and copies no detector. The Governance
engine derives scope from Git-tracked Python, proves test-only reachability
centrally, and reports every untracked Python source as an error.

## Accepted baseline

Measured on 2026-08-17 against current `origin/main` with the accepted schema-9
engine: 2,147 tracked Python sources measured, 591 centrally proven test-only
or unreachable sources excluded, zero untracked Python, nine conserved
findings, and no syntax errors.

| Category | Baseline |
| --- | ---: |
| `outbound_transport` | 22 |
| `webhook_surface` | 8 |
| `provider_credential` | 6 |
| `connector_task` | 12 |
| `sync_checkpoint` | 18 |
| `delivery_retry` | 6 |

### `outbound_transport` — 22 files

`app/dependency_health.py`, `app/monitoring.py`,
`app/services/careers/captcha.py`, `app/services/crm/client.py`,
`app/services/dotmac_sub/client.py`, `app/services/email.py`,
`app/services/finance/automation/workflow.py`,
`app/services/finance/banking/mono_client.py`,
`app/services/finance/payments/paystack_client.py`,
`app/services/finance/platform/ecb_rate_fetcher.py`,
`app/services/hooks/registry.py`, `app/services/mailcow/cleanup_queue.py`,
`app/services/mailcow/client.py`, `app/services/nextcloud/client.py`,
`app/services/push.py`, `app/services/remita/client.py`,
`app/services/secrets.py`, `app/services/storage.py`,
`app/services/sync/inventory_push_service.py`, `app/tasks/email.py`,
`app/tasks/hooks.py`, and `tests/e2e/conftest.py`.

### `webhook_surface` — 8 files

`app/api/crm.py`, `app/api/dotmac_academy.py`, `app/api/dotmac_sub.py`,
`app/api/finance/banking.py`, `app/api/finance/payments.py`,
`app/api/sync/dotmac_crm.py`,
`app/services/finance/banking/mono_client.py`, and
`app/services/finance/payments/paystack_client.py`.

### `provider_credential` — 6 files

`app/config.py`, `app/dependency_health.py`, `app/models/email_profile.py`,
`app/services/finance/settings_web.py`, `app/services/storage.py`, and
`tests/conftest.py`.

### `connector_task` — 12 files

`app/api/dotmac_sub.py`, `app/services/finance/banking/mono_sync.py`,
`app/services/people/hr/employees.py`, `app/tasks/crm.py`,
`app/tasks/dotmac_sub.py`, `app/tasks/exchange_rates.py`,
`app/tasks/expense.py`, `app/tasks/finance.py`, `app/tasks/hr.py`,
`app/tasks/payments_sync.py`, `app/tasks/performance.py`, and
`app/tasks/staff_sync.py`.

### `sync_checkpoint` — 18 files

`app/models/finance/ar/customer_payment.py`,
`app/models/finance/ar/invoice.py`,
`app/models/finance/platform/event_handler_checkpoint.py`,
`app/models/inventory/material_request.py`, `app/models/mixins.py`,
`app/models/people/base.py`, `app/models/people/training/academy.py`,
`app/models/pm/time_entry.py`, `app/schemas/support.py`,
`app/services/dotmac_sub/sync/_credit_notes.py`,
`app/services/dotmac_sub/sync/_invoices.py`,
`app/services/dotmac_sub/sync/_payments.py`,
`app/services/dotmac_sub/sync/_progress.py`,
`app/services/dotmac_sub/sync/_resellers.py`,
`app/services/dotmac_sub/sync/_subscribers.py`,
`app/services/people/training/academy.py`,
`app/services/sync/crm/expenses.py`, and
`app/services/sync/crm/inventory.py`.

The `event_handler_checkpoint.py` path is retained in this measured inventory
even though its persistence model is a local ERP-outbox receipt rather than an
external feed cursor. The two-directional ratchet favours recall: that known
false positive inflates the floor instead of creating an adopter-controlled
exemption that could hide a real connector.

### `delivery_retry` — 6 files

`app/dependency_health.py`, `app/services/crm/client.py`,
`app/services/dotmac_sub/client.py`,
`app/services/sync/inventory_push_service.py`, `app/tasks/email.py`, and
`app/tasks/hooks.py`.

## Conserved findings

These are connector-shaped symbols removed by the central test-only
reachability proof. Recording them suppresses nothing and claims no file is
safe; it makes every subtraction from the measured universe reviewable.
`InsightEngine` is application code reached only by its excluded test at this
revision, so its two entries also preserve that stronger dead-code signal.

| Path | Symbol | Category | Fingerprint |
| --- | --- | --- | --- |
| `app/services/coach/insight_engine.py` | `InsightEngine` | `delivery_retry` | `f7f150d9fa6c5e2d3675f1d041c8b2bcc65068b1924504f577f6205207108ed0` |
| `app/services/coach/insight_engine.py` | `InsightEngine` | `outbound_transport` | `f7f150d9fa6c5e2d3675f1d041c8b2bcc65068b1924504f577f6205207108ed0` |
| `tests/services/test_dotmac_sub_incremental_sync.py` | `test_customer_feeds_forward_their_watermarks` | `sync_checkpoint` | `c60a65c1214a478fd7909fe746b7c3b6d607b4d074ae09ceddaa9cbd1e301c41` |
| `tests/services/test_dotmac_sub_sync.py` | `test_verify_webhook_signature` | `webhook_surface` | `7a87e6ab27b39d6e01e49ccac47818926bce03ed12bd764665023a16a583c57c` |
| `tests/services/test_dotmac_sub_sync.py` | `test_verify_webhook_signature_unconfigured` | `webhook_surface` | `7a87e6ab27b39d6e01e49ccac47818926bce03ed12bd764665023a16a583c57c` |
| `tests/services/test_hook_registry.py` | `TestHookRegistry` | `webhook_surface` | `3241007e58c4c3fd6974f234bc622472ac6ce8d84dfeb0235ae38bca818678aa` |
| `tests/services/test_mono_sync.py` | `test_verify_webhook_rejects_empty_secrets` | `webhook_surface` | `3db300e89674052e47d62060f10e90da27fa084ee070ba39df0a9a577d6bf628` |
| `tests/tasks/test_hooks_tasks.py` | `TestExecuteAsyncHook` | `delivery_retry` | `ced43bd99de9a2af6ac3d9d4be8e3bb3a254f17d4b7435bb8eedb1c29a399ff6` |
| `tests/test_email_services.py` | `TestSendEmail` | `outbound_transport` | `fdcce130fa315a92dd3689dd38d97db4b8b258668a1780a6d9a010adde921b23` |

## Review rule

A count rising fails. A count falling also fails until the profile and this
record are lowered in the same change. Every reduction must show deletion or a
cutover to a named connector distribution behind Dotmac Integrator.

The ratchet reaches its sunset only when all baselines and conserved findings
are zero and ADR 0011's runtime package, secret, egress, ingress, and contract
conditions hold simultaneously.
