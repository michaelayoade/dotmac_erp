# People replacement boundary

Status: **target storage and legacy source read projection installed;
bootstrap/reconciler absent; authority remains in ERP**.

> **Destination naming.** The replacement target is the commercial **Dotmac
> ERP** product assembly, not an internally framed `dotmac_backoffice`
> application — Michael corrected that on 2026-08-19
> (`dotmac-erp-recomposition-into-domain-modules`; the earlier
> `erp-hardening-is-containment-backoffice-is-the-destination` entry is
> superseded on naming and retained only for its cutover mechanics).
>
> **Four names, and only four** — do not introduce a fifth:
>
> | Role | Name |
> | --- | --- |
> | Product / repository slug | `dotmac-erp` |
> | Authority / assembly id | `asm-dotmac-erp` |
> | Historical source repository | `dotmac_erp` |
> | Human name | Dotmac ERP |
>
> The writer ledger's `next_owner` is an AUTHORITY field, so it reads
> `asm-dotmac-erp/dotmac-people`. Starter dossier `candidate_consumers` name
> the product slug `dotmac-erp`. The legacy `dotmac_erp` repository is the
> extraction SOURCE and is archived or renamed at final retirement.
>
> The `backoffice:people:read` scope, the
> `/api/v1/sync/backoffice/people/projection` route and the
> `backoffice.people.projection.v1` contract keep their names deliberately.
> They are shipped wire identifiers held by a live consumer; renaming them is a
> breaking API change, not a documentation correction, and it is out of scope
> here.

## Ownership

| Concern | Current owner | Intended owner after an authorized cutover |
| --- | --- | --- |
| Person identity used by employment | `public.people` in Dotmac ERP | kernel `Party` + `PartyPerson` in the composed Dotmac ERP product |
| Employment directory, positions and assignments | Dotmac ERP HR services and `hr.*` tables | released `dotmac-people`, composed by the Dotmac ERP product |
| Credentials, sessions, roles and permissions | Dotmac ERP | Not part of this slice |
| Payroll, bank, compensation, attendance and location data | Their existing ERP domains | Not part of `dotmac-people` |
| Personal profile data that supplements `public.people` | `hr.employee` | **Unresolved**; it is neither a `dotmac-people` field nor approved kernel `PartyPerson` data |
| Employee documents, qualifications, certifications and dependants | `hr.employee_*` tables | **Unresolved**; file storage alone is not business ownership |
| Skill catalogue and employee proficiency | `hr.skill` and `hr.employee_skill` | selected `dotmac-workforce` capability, but blocked until that distribution is released and its contract is accepted |

ERP now pins `dotmac-people==0.1.0a1` and composes its independent `people`
lineage at `pe_0001_people_directory`. This installs the six tenant tables in
`mod_people`; it does not import the module runtime under `app/`, bootstrap any
row, project compatibility state, or move a writer. ERP remains the sole
authority until a separately authorized domain cutover has completed backfill,
shadow comparison and reconciliation and has disabled the corresponding ERP
writer paths.

The complete disposition of the legacy `hr.employee` columns and its extended
tables is recorded in
`docs/inventories/people-employee-field-ownership.tsv`. An `unresolved` row is
a cutover blocker, not permission to place the field in a JSON escape hatch or
to copy it into `dotmac-people`.
`tests/integration/test_people_employee_ownership_catalog.py` proves that the
ledger covers every migrated `hr.employee` column and that every classified
extended entity exists after `alembic upgrade heads`; the architecture test
separately keeps the ledger synchronized with mapped model intent.

## Versioned legacy source read projection

The composed product may read one tenant at a time through:

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

This endpoint is a one-way extraction seam from the historical `dotmac_erp`
source into the clean Dotmac ERP assembly before the authorized switch. It is
not the target bootstrap itself: no target-side importer, fingerprint ledger or
reconciler is installed yet. After the single product switch, the historical
source is fenced and archived; the new authority never reverse-feeds it.

The shipped v1 projection is also **not deterministic enough for cutover**:
position fingerprints include `is_department_head`, which is evaluated against
the process date rather than an explicit `as_of`. A scan crossing an assignment
date boundary can therefore change without a source write. A successor
versioned projection must accept an explicit effective date before bootstrap or
fixed-point reconciliation is admissible; the published v1 contract must not be
redefined in place.

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

## Dependency evidence

`docs/inventories/people-dependent-references.tsv` is the canonical static
**model-intent** ledger. One row is one unique intended-FK identity
`(source_schema, source_table, source_column, target_schema, target_table,
target_column)`. Repeated model declarations collapse into that identity;
`declaration_kind` and `declaration_paths` preserve whether it was explicit or
expanded from a reusable mixin and where it came from. Consequently, a count
of source declarations and a count of ledger rows answer different questions
and must never be presented as the same number. A model-intent row is evidence
that checked-in ORM code declares a dependency; it is not evidence that a
migration installed the corresponding PostgreSQL constraint.

`tests/integration/people_hub_fk_catalog.tsv` is the separately checked-in
baseline of constraints observed in the fully migrated PostgreSQL catalogue.
Each row records the six-column identity plus update/delete actions, match type
and deferrability. That is the migrated FK contract truth for this boundary.
`tests/integration/test_people_hub_fk_catalog.py` queries PostgreSQL and compares
it with the physical baseline without inferring the expected answer from
whatever ORM models happen to import during the test.

The current differences between model intent and migrated constraints are
explicit **model-only FK drift** and **physical-only FK drift**. They are
control debt under separate baselines in one two-directional ratchet, not an
assertion that either evidence source is a subset of the other. A change to
model intent must update the static ledger deliberately; a change to migrated
constraints must update the physical baseline deliberately; and the drift
ratchet prevents either gap changing silently in either direction. The tests
own all numeric baselines. Neither evidence source nor the debt baselines may
be replaced by copied prose counts in this document or the BOM.

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

1. The composed product pins the released kernel and `dotmac-people`
   distributions and migrates their lineages successfully. **Satisfied for
   storage only** by `dotmac-people==0.1.0a1`; this is not runtime adoption.
2. Backfill preserves source IDs and records source fingerprints.
3. A successor projection with an explicit effective date removes v1's
   process-date-dependent fingerprint, and the target pins that date for each
   complete reconciliation pass.
4. Shadow reads and a repeatable reconciler show no unexplained row or field
   differences for every entity and tenant.
5. Every runtime row in the writer ledger is replaced by a composed-product
   command path or explicitly retired; every operator-script row is disabled.
6. The composed product becomes the sole writer in one cutover. Any transitional
   `hr.employee` compatibility surface is a **local, rebuildable, read-only
   projection inside the clean assembly**, written only by its reconciler from
   kernel Party and `dotmac-people` authority. It is not the historical ERP
   table and creates no reverse feed to the fenced source.
7. ERP tables remain until downstream foreign-key consumers have moved; table
   deletion is a later, separately authorized production operation.
