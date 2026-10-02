# ERP direct external-connector surface

ERP adopts the Governance-owned schema-9 ratchet from accepted ADR 0011 at
immutable canonical-main commit
`a19259b10568d29dc0a9617347498fea7f1e7a97`.

The ratchet freezes measured legacy connector surface while providers move
behind Dotmac Integrator. It is transitional defence in depth, not runtime
isolation. The permanent boundary is Integrator-only connector packages and
provider secrets, default-deny product egress, provider ingress terminating at
Integrator, and versioned inbox/outbox contracts between independent apps.

ERP declares no measurement roots and copies no detector. The Governance
engine derives scope from Git-tracked Python, proves test-only reachability
centrally, and reports every untracked Python source as an error.

## Accepted baseline

Remeasured on 2026-08-25 in the direct-CRM retirement change with the pinned
schema-9 engine. That review left nine conserved findings at the time. The
current profile and ledger below contain twelve after later reviewed additions.

Reviewed again on 2026-08-29 for PR #416 after connector failure coverage
changed the source files containing three test-only conserved symbols. Their
fingerprints are re-declared below; no baseline or conserved finding was added
or removed.

Reviewed again on 2026-09-13 for PR #562. The ordered workforce provisioning
relay makes `app/tasks/email.py` a connector task because it hands off to the
Selfcare synchronization task only after Mailcow and Nextcloud succeed. The
connector-task baseline and exact inventory increase by that one reviewed file.

The finance consolidation in PR #687 moves payment-channel loading from
`BankMappingMixin` into the existing provider-calling `PaymentSyncMixin`.
Its bounded channel-feed regression harness in
`tests/services/test_dotmac_sub_sync.py` follows that ownership change.
Both webhook-signature tests in the same file are unchanged. The pinned
Governance engine fingerprints the containing source file, so their two
conserved fingerprints are re-declared to match its measured result. All
six category baselines and all twelve conserved findings remain unchanged.

| Category | Baseline |
| --- | ---: |
| `outbound_transport` | 20 |
| `webhook_surface` | 6 |
| `provider_credential` | 6 |
| `connector_task` | 12 |
| `sync_checkpoint` | 17 |
| `delivery_retry` | 4 |

### `outbound_transport` — 20 files

`app/dependency_health.py`, `app/monitoring.py`,
`app/services/careers/captcha.py`, `app/services/dotmac_sub/client.py`,
`app/services/email.py`,
`app/services/finance/automation/workflow.py`,
`app/services/finance/banking/mono_client.py`,
`app/services/finance/payments/paystack_client.py`,
`app/services/finance/platform/ecb_rate_fetcher.py`,
`app/services/hooks/registry.py`, `app/services/mailcow/cleanup_queue.py`,
`app/services/mailcow/client.py`, `app/services/nextcloud/client.py`,
`app/services/push.py`, `app/services/remita/client.py`,
`app/services/secrets.py`, `app/services/storage.py`, `app/tasks/email.py`,
`app/tasks/hooks.py`, and `tests/e2e/conftest.py`.

### `webhook_surface` — 6 files

`app/api/dotmac_academy.py`, `app/api/dotmac_sub.py`,
`app/api/finance/banking.py`, `app/api/finance/payments.py`,
`app/services/finance/banking/mono_client.py`, and
`app/services/finance/payments/paystack_client.py`.

### `provider_credential` — 6 files

`app/config.py`, `app/dependency_health.py`, `app/models/email_profile.py`,
`app/services/finance/settings_web.py`, `app/services/storage.py`, and
`tests/conftest.py`.

### `connector_task` — 12 files

`app/api/dotmac_sub.py`, `app/services/finance/banking/mono_sync.py`,
`app/services/people/hr/employees.py`, `app/tasks/dotmac_sub.py`,
`app/tasks/email.py`, `app/tasks/exchange_rates.py`,
`app/tasks/expense.py`, `app/tasks/finance.py`, `app/tasks/hr.py`,
`app/tasks/payments_sync.py`, `app/tasks/performance.py`, and
`app/tasks/staff_sync.py`.

### `sync_checkpoint` — 17 files

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
`app/services/people/training/academy.py`, and
`app/services/sync/sub/expenses.py`.

### `delivery_retry` — 4 files

`app/dependency_health.py`, `app/services/dotmac_sub/client.py`,
`app/tasks/email.py`, and
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
| `tests/services/test_dotmac_sub_incremental_sync.py` | `test_customer_feeds_forward_their_watermarks` | `sync_checkpoint` | `ced5e1214b2e8731e79f877ec620f2d3333f80244fc07284dabdf7aa91b99ef3` |
| `tests/services/test_dotmac_sub_sync.py` | `test_verify_webhook_signature` | `webhook_surface` | `e16d6b004f355a8128d08a6c297327a58d02154315ba627d5b564f9f757cf598` |
| `tests/services/test_dotmac_sub_sync.py` | `test_verify_webhook_signature_unconfigured` | `webhook_surface` | `e16d6b004f355a8128d08a6c297327a58d02154315ba627d5b564f9f757cf598` |
| `tests/services/test_hook_registry.py` | `TestHookRegistry` | `webhook_surface` | `30d9cf711d1444dd4d20b815ca18b894ef283058e856b1311b252bef5282bac0` |
| `tests/services/test_mono_sync.py` | `test_verify_webhook_rejects_empty_secrets` | `webhook_surface` | `4f907fc550796c1b212c1cc8481d78488f55f6345669a1ecd7fe45a7b9c92349` |
| `tests/services/test_paystack_relay_resilience.py` | `test_relay_circuit_opens_after_transport_failure` | `delivery_retry` | `900e8c9e8d8f0b56ea27f97233219a06e311fa829455bc0e2f8f82c3ec030912` |
| `tests/services/test_paystack_relay_resilience.py` | `test_relay_retries_transient_server_error_with_exact_body` | `delivery_retry` | `900e8c9e8d8f0b56ea27f97233219a06e311fa829455bc0e2f8f82c3ec030912` |
| `tests/tasks/test_hooks_tasks.py` | `TestExecuteAsyncHook` | `delivery_retry` | `2b429df85fe39fe006f8bf5c79a2038cbddefd5ff91c2388910fa6f17eba1597` |
| `tests/tasks/test_outbox_relay.py` | `test_email_handler_dead_letters_permanent_smtp_error` | `outbound_transport` | `6bc63b552c27b8334cdb4d63382292eb5ec464dc07e7fb63d800a88bd275f63d` |
| `tests/test_email_services.py` | `TestSendEmail` | `outbound_transport` | `940f495c96637f0a97e7e96ac826447d79914a2ad09998132aeb81d0142499d3` |

## Review rule

A count rising fails. A count falling also fails until the profile and this
record are lowered in the same change. Every reduction must show deletion or a
cutover to a named connector distribution behind Dotmac Integrator.

The ratchet reaches its sunset only when all baselines and conserved findings
are zero and ADR 0011's runtime package, secret, egress, ingress, and contract
conditions hold simultaneously.
