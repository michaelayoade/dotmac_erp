# ERP designation projection into the Mailcow footer

ERP is the source of truth for `Employee.designation.designation_name`. The
existing `EmployeeMailboxProvisioningService` projects that value to the
lowercase `mailbox.custom_attributes.designation` key. Mailcow's domain-wide
HTML footer renders `{= designation =}` for authenticated outgoing mail. The
plain-text footer is empty. The old Mailcow-local SOGo designation writer is
retired; do not reinstall or enable its service or timer, and do not add a
designation to SOGo signatures.

## Independent controls

- `MAILCOW_PROVISIONING_ENABLED` controls mailbox creation and activation.
- `MAILCOW_PROFILE_SYNC_ENABLED` controls designation writes to existing
  mailboxes, including writes during authorized mailbox creation. Both default
  to `false`; enabling profile sync does not enable account provisioning.
- `MAILCOW_OFFBOARDING_ENABLED` remains independent.

Set the profile flag in the deployment's canonical environment configuration,
not only `.env.example`. Host Compose uses `.env` as an `env_file` for app,
worker, and Beat. Verify the effective setting in all three runtimes before
activation. Keep workforce provisioning settings disabled when only profile
sync is intended. The separate `deploy/product.toml` and `deploy/rendered/`
topology is not the live runtime yet. Before adopting it, bind this flag for
all three roles in the descriptor and regenerate the rendered assets with
`dotmac-deploy`; never hand-edit generated Compose files.

## Flow and safety

An employee designation change or shared designation rename queues the
existing `run_employee_mailcow_provisioning(..., profile_only=True)` task. The
daily `employee-mailbox-profile-reconcile` schedule requeues eligible employees
to repair missed dispatches. The worker rereads committed ERP state and checks
the profile flag at execution. A ten-second enqueue countdown is not a commit
guarantee. A new employee may retry before its row is committed; an existing
employee may briefly project old state. The daily sweep repairs missed events.

Only ACTIVE employees with an active person, unended employment, same-tenant
references, a managed-domain work email, and an existing active Mailcow mailbox
qualify. ON_LEAVE, draft, suspended, and exited employees are excluded. A
missing mailbox is skipped, never created by profile-only work. A removed
designation clears only the lowercase managed attribute; missing or invalid
assigned designation data is skipped. The Mailcow client reads the complete
custom-attribute map, merges the change, writes the complete map, and verifies
the read-back. Unrelated attributes are preserved. An unsupported map fails
without writing.

The profile-only task returns `updated`, `unchanged`, or `skipped`. Mailcow API
errors and read-back mismatches fail the task for bounded retries. It never
changes mailbox lifecycle settings, workforce stages, activation, Nextcloud,
Selfcare, Talk, or SOGo state.

## Rollout and rollback

After a separately authorized deployment, verify `MAILCOW_PROVISIONING_ENABLED`
remains false. Preview eligible ERP-to-mailbox mappings without writes, then
enable `MAILCOW_PROFILE_SYNC_ENABLED` in the canonical deployment
configuration and verify app, worker, and Beat. Run a bounded projection and
read back each expected attribute map. Confirm the designation appears once in
the existing HTML company footer. To roll back, disable only profile sync.
Do not enable provisioning or the retired SOGo designation timer.

This uses the already grandfathered direct ERP-to-Mailcow API path. Moving it
behind Dotmac Integrator remains migration debt under the cross-repository
connector boundary.
