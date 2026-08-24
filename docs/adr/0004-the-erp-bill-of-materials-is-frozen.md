# ADR-0004 — The ERP bill of materials is frozen before composition starts

- **Status:** Proposed
- **Date:** 2026-08-24
- **Decider:** Michael
- **Scope:** ERP module selection, capability ownership, composition order
- **Related:** `docs/adr/0003-clean-install-starts-from-governed-opening-state.md`,
  `app/bill_of_materials.py`,
  `tests/architecture/test_bill_of_materials.py`

## Context

The cutover programme runs in one order: **all code cutovers → synthetic proof
→ approved real data → one production switch.** Every software and ownership
cutover completes first on clean disposable instances; real data loads only
after synthetic proof; `erp.dotmac.io` then switches once, with the entire
composed ERP becoming authoritative together, after which the legacy ERP is
frozen permanently as a read-only archive.

That last property is what makes selection urgent rather than incidental. Under
a staged, per-domain production cutover, an undecided capability can be left
running on legacy for another quarter. Under a single atomic switch it cannot:
legacy stops accepting business writes on the same day, so anything without a
named owner in the new system simply stops working. **Nothing gets left
behind.**

ADR-0003 already requires that "each business decision and state transition has
one named module or retained ERP owner", and that the legacy writer and caller
ratchets reach their declared terminal states. It does not say which modules
those are. Without that list, step 3 (repoint every route, job, webhook, command
and report) has no target for each caller, and step 7 (reach zero unresolved
writer/caller rows) has no denominator: a row can be "unresolved" only against a
set of owners someone declared.

There is also a measured reason to write the set down rather than infer it.
Starter currently holds ninety module distributions, and their extraction
dossiers were written over months, several while "Backoffice" was the generic
name for whatever would eventually replace this application. Some name the
Dotmac ERP product as first cutover for capabilities an ERP has no writer for at
all. Inferring the product from those dossiers would compose a marketing CMS,
an ISP network suite and a platform control plane into a back-office system.

Finally, omission is the failure mode a list cannot detect from its own
contents. In the ISP programme matrix, `dotmac-work-orders` — a built,
ledger-allocated module with a package on `main` — appeared in no cohort at all,
and every check passed, because nothing declared the set it was missing from.

## Decision

**The ERP bill of materials is frozen at `app/bill_of_materials.py` before any
module is composed. It declares two closures, and both are enforced.**

### Module closure

Every distribution in the Starter package census — pinned at
`bd8d2262c26f62041cc22a813916066b9af85c7f`, ninety of them — is either
`SELECTED` (the composed ERP installs it) or `EXCLUDED` (it does not, and the
row names who owns that capability instead). Never both, never neither. A new
Starter package cannot drift into or past this product silently; it forces a
diff here.

Thirty-five distributions are selected. Five are composed today
(`dotmac-kernel`, `dotmac-ui`, `dotmac-files`, `dotmac-imports`,
`dotmac-accounting`); thirty are selected and not yet pinned. Fifty-five are
excluded: the ISP estate to `dotmac_sub`, the platform plane to the vendor
control plane, connector runtime to Integrator, and four marketing capabilities
to an open decision below.

### Capability closure

Every capability in `ERP_CAPABILITY_CENSUS` is carried by exactly one selected
module or is explicitly `RETAINED` as ERP-owned assembly code. Ninety-eight
capabilities: fifty-five module-owned, forty-three retained.

A retained row is a **named owner**, which is what step 7 needs. ADR-0003
admits retained ERP owners — "'composable' does not mean that every line of
application code moves into a package; it means that ownership is singular,
declared and enforceable" — and this ADR uses that admission deliberately, not
as a loophole. It does not claim a retained capability should never become a
module; it claims that turning it into one is not on the critical path of this
cutover.

### What is frozen, and what is not

**Membership and ownership are frozen. Version pins are not.** Steps 2 through 7
will change module code — that is what those steps are — so freezing versions
now would freeze the wrong thing. Pins move under composition and are frozen at
step 10, when the final clean production database is created. Changing the
*membership* of the set, by contrast, is an amendment to this ADR.

### Enforcement

`tests/architecture/test_bill_of_materials.py` fails the build when:

- a Starter distribution has no disposition, or has two;
- a capability has no owner, or has two;
- a module is pinned in `pyproject.toml` without a selected row, or a row claims
  to be composed without a pin;
- a pin is a range rather than an exact version;
- a row claims to be composed while unreleased or unbuilt;
- a disposition states no reason.

Each detector carries a sensitivity proof, because a closure check over a set
that happens to be closed passes for the wrong reason.

## Open decisions

These two are named here rather than answered, because both are Michael's and
neither blocks steps 2 through 4.

**1. The four marketing capabilities.** `dotmac-content`, `dotmac-publishing`,
`dotmac-sites` and `dotmac-web-analytics` are recorded `EXCLUDED` with owner
`pending-decision`. Their dossiers name the Dotmac ERP product as cutover 1;
that wording predates the correction that there is no separate Backoffice
product. Including them widens the single atomic switch by a content management
system with no ERP writer to retire, which is why the proposed disposition is
exclusion. If they belong in this product, the dossiers are right and this ADR
must be amended before step 2; if they belong to a separate marketing assembly,
the dossiers should be repointed. `dotmac-media-observations` is excluded on
different grounds — Michael paused it on 2026-08-18, and a paused capability may
not enter a frozen bill of materials.

**2. Trade accounts receivable.** Retained, and it is the largest retained
domain by far. `dotmac-billing` is operational receivables and is explicitly not
statutory trade AR (Starter ADR-0020), so no module in the census can carry it.
Creating one now would place an unbuilt module on the critical path of every
subsequent step, so the proposal is that ERP keeps it and a shared owner becomes
a later product decision. Treasury and cash positioning, consolidation and
intercompany, budgeting, lease accounting and financial-statement presentation
are retained on the same reasoning and are listed individually in `RETAINED`.

## What composition then costs (measured, step 2)

`COMPOSITION_PLAN` in the same module records, per selected module, the kernel
floor, schema, lineage branch and head, and the database effects its manifest
requires. It is measured from the module manifests at the census revision, not
estimated, and it turns composition into a four-file atomic change — the pin in
`pyproject.toml`, the lineage in `alembic.ini`, the expected head in
`app/migration_bindings.py`, and the plan row — with the build comparing all
four against each other.

Three obligations fall out of it, and none of them is a version bump:

- **The kernel must be repinned before anything in tranche 1 composes.** The
  selection floors at `0.1.0a91` (`dotmac-payments`); ERP pinned `0.1.0a85` when
  this was written. Every other selected module floors at or below `0.1.0a88`.
  Closed on 2026-08-24 by the repin to `0.1.0a94` — the latest published kernel,
  chosen over the minimum `0.1.0a91` so steps 3 through 7 do not force a second
  repin. The check became "the pin satisfies every SELECTED module's floor",
  which is the stronger question the gap had been hiding.
- **ERP must supply `party_person_catalog.v1`.** `dotmac-people`,
  `dotmac-party` and `dotmac-expenses` require it and no assembly migration
  provides it. That is the identity seam, and it gates the whole HR and expense
  column of the product.
- **ERP must supply `outbox_relay.v1`.** `dotmac-approvals` and
  `dotmac-durable-timers` require it. Durable Timers in turn underpins
  reminders, escalations and scheduled runs.

The assembly supplies exactly three effects today — `tenant_scope_catalog.v1`,
`module_database_roles.v1` and `idempotency_ledger.v1` — and the plan's blocked
set is derived from that fact and compared against a declared one, so supplying
an effect must delete its row in the same change that adds the migration.

Twenty-one modules are otherwise unblocked. Four have no installable artifact at
all.

## Consequences

- Step 2 has a definite work list: thirty selected modules to compose, of which
  four (`dotmac-app-sync`, `dotmac-fx-policy`, `dotmac-template-studio`,
  `dotmac-workforce`) cannot be composed at all yet — they are unreleased and
  absent from the Starter release allowlist, so releasing them becomes an
  explicit prerequisite rather than a surprise found mid-composition.
- Step 3 has a target for every caller: each route, job, webhook, command and
  report repoints to a selected module or to a retained owner named here.
- Step 7 has a denominator. "Zero unresolved writer/caller rows" means every
  writer maps to exactly one row in this file.
- The retained set is large, and that is the honest shape of a first cutover.
  Forty-three retained capabilities is the measured distance between "ERP is a
  thin assembly" as an ambition and as a claim; shrinking it is later product
  work, not a precondition for the switch.
- Steps 11 through 14 remain blocked on the unresolved cross-database seal
  protocol. This ADR does not touch that, and freezing the bill of materials
  does not authorize any deployment, pin, migration or data movement.

## Alternatives rejected

- **Infer the product from the extraction dossiers.** Rejected: several dossiers
  name the Dotmac ERP product as first cutover for capabilities this application
  has no writer for, because they were written when "Backoffice" was the generic
  destination. Inference would compose a CMS and an ISP network suite into a
  back-office product.
- **Decide each module when its turn comes.** Rejected: it is exactly how a
  capability reaches the switch date unowned. Under one atomic cutover the cost
  of a late discovery is not a delayed domain, it is a missing one.
- **Freeze the version pins too.** Rejected: steps 2 through 7 change module
  code by design, so pinned versions would be amended continuously and the
  freeze would come to mean nothing. Membership is the durable claim; versions
  are frozen at step 10.
- **Build a module for every unowned capability first.** Rejected: it puts eight
  unbuilt modules on the critical path of a programme whose first seven steps are
  otherwise unblocked. A retained ERP owner satisfies ADR-0003's singular
  ownership requirement today.
- **Keep the list in a document instead of code.** Rejected: a prose list cannot
  be compared to `pyproject.toml`, and the failure this file exists to prevent is
  drift between what the product claims to install and what it installs.
