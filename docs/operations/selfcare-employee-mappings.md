# Selfcare employee mapping ownership

A non-null `hr.employee.dotmac_sub_account_id` has one employee owner within
an organization, regardless of employee status. Null mappings may occur on
any number of employees. Different organizations may use the same account ID.
An employee may reuse its own mapping; sync never transfers ownership from an
inactive employee. Attendance continues to reject ambiguous mappings with 409.

## Required administrator review before migration

Migration `20260905_selfcare_mapping_unique` refuses existing duplicates without
changing data. Run this read-only query through an authorized administrator
connection, replacing the organization UUID with the organization under review:

```sql
BEGIN READ ONLY;
WITH mappings AS (
    SELECT employee_id, employee_code, person_id, organization_id, status,
           dotmac_sub_account_id,
           count(*) OVER (
               PARTITION BY organization_id, dotmac_sub_account_id
           ) AS owners
    FROM hr.employee
    WHERE organization_id = '00000000-0000-0000-0000-000000000001'::uuid
      AND dotmac_sub_account_id IS NOT NULL
)
SELECT employee_id, employee_code, person_id, organization_id, status,
       dotmac_sub_account_id, owners
FROM mappings
WHERE owners > 1
ORDER BY dotmac_sub_account_id, employee_code;
ROLLBACK;
```

The query deliberately includes every employment status. Tenant-scoped
connections must also have the normal tenant context set. An empty result from
a connection without authorized visibility is not proof that duplicates are absent.
Review every affected organization, including the reported account
`661db492-bb60-4b17-a191-46b3b2129101` in the organization above.

Administrators must verify the account's identity against the employee/person
records and review attendance dependencies. Record the approved correction in
the normal audit/change process. Do not automatically pick an active employee,
delete employee records, or erase attendance history. This change supplies no
automatic data repair and must not be deployed until that review is complete.
Rerun the diagnostic after approved corrections, then schedule the migration
through the normal deployment process. It locks employee writes while checking
all organizations and installing uniqueness; plan a suitable maintenance window.
The migration requires full visibility and refuses an RLS-filtered census.

## Forward workforce provisioning

New employees follow one ordered relay: Mailcow mailbox, Nextcloud account,
Selfcare account, then Selfcare-to-Nextcloud Talk mapping. This is a going-
forward flow and performs no employee backfill. The Mailcow task enqueues
Selfcare sync only after Nextcloud has persisted its exact user ID. Staff sync
also refuses Selfcare creation when that binding is absent, so an independently
queued task cannot race ahead of Nextcloud.

ERP calls Selfcare's create endpoint with `existing_account_policy=reject` and
a stable employee idempotency key. A true existing-email collision returns 409
without mutation. If ERP loses the first success response, replay returns the
original Selfcare UUID without changing roles or identity. ERP claims and
flushes that UUID locally before any role, department, or Talk mapping call.

The Selfcare API key requires `sub:staff_access:read`, `rbac:assign`,
`rbac:roles:read`, `operations:service_team:membership`, and
`communications:nextcloud_talk_staff:manage`. The last scope authorizes both mapping
and its explicit disable command. Missing Talk scope is a permanent
configuration error;
temporary Selfcare or binding failures remain retryable.

Do not enable `MAILCOW_PROVISIONING_ENABLED`,
`NEXTCLOUD_PROVISIONING_ENABLED`, or `DOTMAC_SUB_STAFF_SYNC_ENABLED` until the
corresponding ERP and Selfcare changes are deployed and the API key carries all
five scopes. Enable the complete chain for one controlled new employee first.

## Transactions and external effects

Staff sync requires a database session. A PostgreSQL transaction advisory lock
serializes syncs within the organization, including create-only admission. A row lock
and refresh protect existing ownership and eliminate stale
employee mappings after waiting. All assignment branches use the same ownership
check, including inactive employees, then flush the mapping before changing
roles, department membership, active status, or publishing access projections.
Account IDs must be canonical UUID strings. A conflicting email lookup for an
already-bound employee cannot override its existing account identity.
The immediate database unique constraint also arbitrates writers outside the
sync lock protocol. A losing claim raises an explicit permanent sync error and
never reaches those remote mutations. Locks last until the caller commits or
rolls back; the service does not commit the caller's transaction.

A savepoint rolls back the mapping, successful-sync timestamp and projection
changes if synchronization fails. Other integrity errors propagate unchanged.
Reconciliation counts success only after commit; failed claims are errors.

PostgreSQL and Selfcare do not share a transaction. A remote operation may
succeed before a later transport error or database commit failure. Such an
attempt remains failed locally and needs retry/reconciliation; rollback cannot
undo a remote request. The tenant lock cannot serialize independent Selfcare
writers. Stable idempotency keys and Selfcare's create-only reservation make
account creation replay-safe; reconciliation repairs later role, department,
and Talk mapping steps. No production data or remote account status is
changed by the tests, which use synthetic databases and fake Selfcare clients.
