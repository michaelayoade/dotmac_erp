# People replacement boundary

Status: **backfill contract installed; authority remains in ERP**.

## Ownership

| Concern | Current owner | Intended owner after an authorized cutover |
| --- | --- | --- |
| Person identity used by employment | `public.people` in Dotmac ERP | kernel `Party` + `PartyPerson` in Dotmac Backoffice |
| Employment directory, positions and assignments | Dotmac ERP HR services and `hr.*` tables | released `dotmac-people`, composed by Dotmac Backoffice |
| Credentials, sessions, roles and permissions | Dotmac ERP | Not part of this slice |
| Payroll, bank, compensation, attendance and location data | Their existing ERP domains | Not part of `dotmac-people` |

This change does not move any writer. ERP remains the sole authority until a
separately authorized cutover has completed backfill, shadow comparison and
reconciliation and has disabled the corresponding ERP writer paths.

## Versioned source projection

Backoffice may read one tenant at a time through:

`GET /api/v1/sync/backoffice/people/projection`

The API requires an API key with the explicit
`backoffice:people:read` scope. Legacy keys with an empty scope set are
rejected. The organization comes from the authenticated service principal;
the request cannot supply or override it. The database session is tenant
primed and every projection query also carries an explicit
`organization_id` predicate.

Required parameters and continuation:

- `entity` is one of `party_person`, `department`, `designation`,
  `employment_type`, `employee`, `position`, `position_assignment`.
- `after` is the last source UUID from the previous page.
- `limit` is 1–500 and defaults to 200.
- `next_after` is present only when another page exists.

The response contract is `backoffice.people.projection.v1`. Every item carries
the preserved ERP UUID, the source timestamp when one exists, and a SHA-256
fingerprint over exactly the projected target fields. The fingerprint—not an
ERP `updated_at` value—is the comparison authority because legacy bulk writers
do not all advance timestamps.

UUID keyset order prevents offset drift. A scan is not a database snapshot
across HTTP requests: the backfill/reconciler must repeat an entity scan until
the source-ID/fingerprint set reaches a fixed point. Absence is meaningful only
after a complete scan; this contract makes no incremental tombstone claim.

## Field boundary

The projection is shaped to the released kernel and `dotmac-people` storage
contracts:

- ERP `Person` becomes kernel `Party(type=person)` + `PartyPerson`.
- Department, designation and employment-type codes/names map directly.
- Employee carries only party identity, directory references, employment
  dates and employment lifecycle status.
- Position carries its hierarchy, directory references, routing policy and
  active state.
- Position assignment carries employee, position, type and date interval.

ERP `Department.head_id` is not exported as a second head authority. The
projection derives `Position.is_department_head=true` only for the department's
position occupied by its named head through an active primary assignment on
the request date. ERP `Employee.reports_to_id` and cached `Position.is_vacant`
are not exported; hierarchy and vacancy are derived from positions and
assignments in the target.

Bank details, compensation, payroll state, cost centres, locations, shifts,
credentials, roles, permissions, Sub account metadata and all other wide ERP
employee fields are outside this contract.

## Writer retirement evidence

`docs/inventories/people-authority-writers.tsv` is the exact current census of
ERP mutations to projected source fields. Runtime writers are marked
`retire_with_domain_cutover`; operator/seed scripts are marked
`disable_before_cutover`. The architecture test scans application code,
tasks/workers, scripts and tools and compares in both directions: a new writer
fails, and a removed writer also fails until the ledger is lowered in the same
change. Its planted cases prove constructor, ORM assignment, `setattr`,
session add/delete, SQLAlchemy DML and raw SQL detection; a read-only negative
case prevents model references from being classified as writes.

## Cutover gates

Authority may move only when all of these are evidenced in checked-in cutover
artifacts and the irreversible switch is explicitly authorized:

1. Backoffice pins the released kernel and `dotmac-people` distributions and
   migrates their lineages successfully.
2. Backfill preserves source IDs and records source fingerprints.
3. Shadow reads and a repeatable reconciler show no unexplained row or field
   differences for every entity and tenant.
4. Every runtime row in the writer ledger is replaced by a Backoffice command
   path or explicitly retired; every operator-script row is disabled.
5. Backoffice becomes the sole writer in one cutover and ERP compatibility is
   read-only.
6. ERP tables remain until downstream foreign-key consumers have moved; table
   deletion is a later, separately authorized production operation.
