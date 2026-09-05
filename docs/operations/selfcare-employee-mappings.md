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

## Transactions and external effects

**New-account provisioning now requires administrator action.** Selfcare's
`POST /staff-accounts` is a create-or-update operation: it can replace an
existing account's roles before returning the account ID. A preceding GET and
an ERP database lock cannot make that remote pair atomic. ERP therefore does
not call that endpoint during staff sync. If lookup finds no account, sync
raises a permanent error without remote writes or a success timestamp. An
administrator must provision and verify the account through the normal
Selfcare process, then retry ERP sync. Automatic provisioning can return only
after a create-only or ownership-conditional remote contract is available.

Staff sync requires a database session. A PostgreSQL transaction advisory lock
serializes syncs within the organization, including email lookup. A row lock
and refresh protect existing ownership and eliminate stale
employee mappings after waiting. All assignment branches use the same ownership
check, including inactive employees, then flush the mapping before changing
roles, department membership, active status, or publishing access projections.
Account IDs must be canonical UUID strings. A conflicting email lookup cannot
override an employee's existing account identity.
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
writers. No production data or remote account status is
changed by the tests, which use synthetic databases and fake Selfcare clients.
