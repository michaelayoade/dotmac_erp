# ERP designation projection into Mailcow and SOGo

## Ownership

ERP is the source of truth for an employee's designation. The authoritative
value is `Employee.designation.designation_name`.

The following values are derived projections and are never authoritative:

- Mailcow `mailbox.custom_attributes.designation`.
- The `<span data-dotmac-erp-designation="true">...</span>` block in a SOGo
  identity signature.

The existing `EmployeeMailboxProvisioningService` is the sole ERP writer of
the Mailcow projection. The Mailcow-local `sogo_designation_sync.py` process is
the sole writer of the managed SOGo signature block. It preserves all other
signature content and SOGo preferences.

## Flow

1. An employee designation assignment or designation name changes in ERP.
2. ERP queues the existing grandfathered Mailcow provisioning task in an
   idempotent profile-only mode after the transaction is expected to commit.
3. The existing direct Mailcow API integration reads the complete mailbox
   custom-attribute map, merges `designation`, writes the complete map, and
   reads it back to verify the result.
4. Every five minutes, a process local to the Mailcow host reads the Mailcow
   designation attribute and reconciles the managed block in SOGo.
5. A daily ERP reconciliation queues every eligible employee again so broker,
   API, or timing failures do not cause permanent drift.

Mailbox provisioning also writes the designation before it completes, so a
new mailbox enters the same reconciliation flow. A SOGo profile is created by
SOGo when the user first uses webmail; the next Mailcow-local run then applies
the managed block.

## Failure and repair behavior

- Missing and unmanaged-domain mailboxes are skipped without inventing state.
- Mailcow updates preserve unrelated custom attributes and fail if read-back
  verification differs from the intended complete map.
- Removing an ERP designation removes only the managed SOGo block.
- Renaming a designation queues every active employee assigned to it.
- The ERP daily sweep and Mailcow five-minute sweep are both idempotent.

## Architecture status

This implementation intentionally uses the production system's grandfathered
direct ERP-to-Mailcow API path. Michael approved that legacy approach for this
production change on 2026-11-23. It does not authorize additional direct
external connectors. Moving this projection behind Dotmac Integrator remains
migration debt under the cross-repository connector boundary.
