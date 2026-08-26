# Platform Adoption Ledger — dotmac_erp

**Status:** Rebaselined 2026-08-02 (kernel-adoption slice E1). Supersedes the
Phase-0 draft (recon pin 318a6e0d, surveyed 2026-07-19), which predated this
repo's checked-in SOT map, the executable SOT registry, and the released kernel.
No code, schema, dependency, or runtime change is authorized by this document.

**Evidence pins:**

- `dotmac_erp` `origin/main` at `96928fa1774612ecd5cd28db1ab04b8e45425df4`.
- `dotmac-kernel==0.1.0a18` (source of record:
  `dotmac_starter_mt/packages/dotmac-kernel`, import name `dotmac_kernel`).
  The dependency **is installed** at this exact pin since slice E2 (below).
  Moved from `0.1.0a8` on 2026-08-07 — see "Pin history" below.

## Pin history

**2026-08-08 — `0.1.0a13` → `0.1.0a18`.** The releases carrying the settings
subsystem ERP is adopting: the port from ERP's own implementation (`a14`), open
value types with exact `Money` (`a15`), scope depth with isolation kept a stored
fact (`a16`), bulk reads and change events (`a17`), and per-scope requirements,
per-tenant encryption keys and history retention (`a18`). Note `a15`-`a17` were
intermediate versions in one merged stack and were never published separately —
`a18` is the artifact.

**2026-08-07 — `0.1.0a8` → `0.1.0a13`.** The kernel release carrying the
white-label foundation: the module registry and manifest declarations, D1's
per-module Postgres namespaces and migration lineages, tenant-entitlement
enforcement (`require_capability`), typed feature flags, and the platform
administration surface. (a13 is a single release covering five development
iterations; a9–a12 were published, a14–a17 never existed as artifacts.)

The upgrade changed **nothing in ERP but the pin, the lock entry, and this
ledger.** ERP consumes the kernel as CONTRACTS, not as a runtime: it has its own
`require_permission` and `_write_audit_event`, and `test_app_import_loads_no_
kernel_module` pins that the booted app loads no kernel module at all. The two
a9–a13 changes that enforce at runtime — `write_audit_event` rejecting
undeclared audit actions, and `require_permission` failing the boot on an
undeclared code — are kernel-side guards on kernel-side call paths ERP does not
use.

Compatibility held on every assertion E2 pins: bare-string `capabilities=(...)`
still resolves (a13 retyped the field as `str | CapabilitySpec` and coerces
strings), `ProductAssemblySpec`'s new fields all default, the consume-pure list
is a SUBSET check so a13's added modules (`cache`, `flags`) do not disturb it,
and `import dotmac_kernel` stays DB-free. No transitive dependency moved.

**Not delivered by this bump:** ERP declares no `ModuleManifest`, so D1's
namespace and migration-lineage rules govern nothing here yet. They become
relevant when ERP extracts stateful modules — its schema-bearing tables are much
of why D1 exists — and that is adoption work with its own slice.

**E2 status (2026-08-02): landed at pin `0.1.0a8` — install unblocked.** The
first E2 attempt targeted `0.1.0a7` and was blocked: the a7 wheel's floors
(`python >=3.12,<3.14`, `fastapi ^0.115`, `pydantic[email] ^2.9`) excluded
ERP's pins (`python >=3.11,<3.13`, `fastapi 0.111.0`, `pydantic 2.7.4`), so
the pin existed but poetry resolution failed and the wheel canaries had to
skip. The `0.1.0a8` release (kernel CHANGELOG, "0.1.0a8 — 2026-08-02")
resolved every blocker:

- **Floors widened** to `fastapi>=0.111,<0.116`, `pydantic>=2.7.4,<3.0`,
  `pydantic-settings>=2.2,<3.0`, `python>=3.11` — ERP's pins resolve cleanly
  with **no python marker** and no ERP pin loosened. Lock delta: adds only
  `dotmac-kernel 0.1.0a8`, `pydantic-settings 2.14.2`,
  `typing-inspection 0.4.2`.
- **Both a7 release defects fixed** (CHANGELOG "Fixed"):
  `dotmac_kernel.testing` no longer builds the DB engine at import (the deps
  import moved inside `assembly_test_client`), and `dotmac_kernel.profiles`
  is now in `SUPPORTED_MODULES`. The old red-sensitive defect pins are
  replaced by assertions of the FIXED behavior in
  `tests/architecture/test_kernel_compatibility.py`.
- **Extras split** (`[testing]` → httpx only; cryptography only via
  `[licensing]`): ERP installs the kernel with **no extras** — httpx 0.27.0
  and `cryptography>=44.0.1` are already ERP main dependencies, so the whole
  testing kit (including `FakeLicenceSigner`) works regardless.

Every E2 canary now RUNS — the skip machinery of the blocked attempt is
deleted; a missing or wrong-version kernel is a hard failure.

**E4 status (2026-08-02): exact Money/FX boundary adapter landed.** First
slice in which `app/` imports the kernel (`dotmac_kernel.money`, consume-pure
per the classification table below; the E1 import-boundary guard passes
unchanged). What shipped:

- **Adapter (one module):** `app/services/finance/money_boundary.py` —
  kernel `Money`/`Currency` ⇄ ERP `(Decimal, currency_code)` conversion
  (`to_boundary_money`/`from_money`), byte-exact connector serialization
  (`serialize_money`/`serialize_amount`), the slice's single centralized
  rounding decision (`BOUNDARY_ROUNDING` = half-up via
  `round_to_minor_units`), fail-closed validators (float/bool, missing or
  invalid currency, currency mismatch, excess minor-unit precision — a source
  FACT with more precision than the currency's minor units is rejected, never
  rounded), the WHT settlement identity (`check_settlement_identity`,
  net + WHT = gross, pre-existing one-minor-unit tolerance preserved), and
  FX ONLY from an immutable ERP-owned observation:
  `rate_snapshot_from_observation` builds a kernel `ExchangeRate` snapshot
  (pair, rate, effective time, `erp:core_fx:<source>[:<rate-type>]`,
  observation row id) from a persisted `core_fx.exchange_rate` row and
  refuses unpersisted/synthetic rows — `FXService` + `core_fx` remain the
  only FX owner; no posting path performs a live rate lookup.
- **Schemas converted (typed Money at the boundary):** the dotmac_sub
  connector records `InvoiceRecord`, `CreditNoteRecord`, `PaymentRecord`
  (`app/services/dotmac_sub/client.py`) each expose fail-closed
  `boundary_money()` (headers, WHT evidence, allocation amounts), enforced
  per-row inside the sync savepoints (`_invoices.py`, `_credit_notes.py`,
  `_payments.py` — a bad row fails ITS savepoint, never the run); the
  Sub/CRM payables command `CRMPurchaseInvoicePayload`
  (`app/schemas/sync/dotmac_crm.py`) validates header totals and line
  amounts through the adapter at parse time. The `_CENTS`/`ROUND_HALF_UP`
  scattering in `_invoices.py` and the flat `0.01` WHT tolerance in
  `_payments.py` were replaced by the adapter's currency-aware equivalents
  (behavior-identical for 2-minor-unit currencies; all live data is NGN).
- **Deliberately still Decimal-internal (boundary 6):** `Numeric(20,6)`
  posting/functional amounts, `Numeric(20,10)` FX rates, tax ratios,
  quantities, unit prices (line `quantity`/`unit_price` and
  `tax_rate_percent`/`wht_rate` are rates, not money), posted-line
  snapshots, `_functional_amount`'s 6-decimal functional conversion, and
  account mappings. Line-level `amount` inputs remain part of the derived
  rounding path (they may legitimately arrive as quantity×unit_price
  products), now centralized through `round_to_minor_units`. Outbound
  Decimal fields (e.g. `SubPurchaseInvoiceStatusResponse`) already serialize
  as exact JSON strings under pydantic v2 (verified); re-quantizing that
  existing wire contract to minor units was deliberately NOT done.
- **No money at the material-support boundary:** `CRMMaterialRequestPayload`
  carries quantities/serials only — valuation is ERP-internal at issue
  (moving-average cost). Nothing to convert; recorded here as the E4
  finding for that flow.
- **Float eliminations (finding 7 absorbed):**
  `app/models/fixed_assets/maintenance_work_order.py`
  `estimated_cost`/`actual_cost` are now `Mapped[Decimal]` (columns were
  already `Numeric(20,6)`; no schema change) and the two
  `float(updated_actual_cost)` writes in
  `app/services/people/assets/maintenance_service.py` are exact Decimal.
  The Sub-facing sync slice itself had no float money fields (verified:
  remaining floats there are HTTP timeouts/durations). `labor_hours` stays
  float (hours, not money).
- **Proof:** `tests/test_golden_money_pins.py` unchanged and green (row
  shapes identical); adapter unit tests in
  `tests/finance/test_money_boundary.py`; boundary/golden-serialization
  tests in `tests/services/test_dotmac_sub_money_boundary.py`. The E2
  app-unchanged canary evolved:
  `test_app_import_loads_only_pure_contract_kernel_modules` now snapshots
  the consume-pure import closure and asserts `app.main` loads nothing
  beyond it (kernel db/messaging/session surfaces still unimported).

**E4 review hardening (2026-08-02, PR #219 review):** the adapter's
fail-closed claims now hold at the real ingress, superseding two statements
above:

- **Strict wire parsing:** `DotmacSubClient._parse_invoice` /
  `_parse_payment` / `_parse_credit_note` no longer default malformed
  amounts to 0 or a missing currency to ERP's functional currency — money
  facts raise a typed `DotmacSubParseError` that fails the ONE source row
  (the feeds' `on_parse_error` collector keeps the pull alive; a failure
  parks the incremental watermark at the row, and an UNPOSITIONED failure —
  no usable `updated_at` — freezes the cursor for the run so the row can
  never be skipped past).
- **Admission on every pass:** `boundary_money()` runs BEFORE the
  unchanged-hash and status branches in all three sync mixins, so a legacy
  row with invalid money facts cannot ride the unchanged-skip forever.
- **Supplied line amounts are FACTS:** a Sub-supplied line `amount`
  validates through `to_boundary_money` (excess precision rejected, never
  rounded); only an ERP-derived quantity×unit_price (when Sub omits the
  amount) and derived tax splits round. The earlier "line-level amount
  inputs remain part of the derived rounding path" statement is obsolete.
- **Minor-unit authority:** `SUPPORTED_CURRENCIES` (an explicit
  `CurrencyRegistry`) ships **NGN and USD only**; `kernel_currency()`'s
  2-minor-unit default for unknown codes is unreachable. EUR/GBP arrive
  later only behind checked-in provisioning plus a database consistency
  test against `core_fx.currency`; zero/three-minor-unit contracts (JPY,
  BHD) stay covered via test-scoped registry instances.
- **Strings-only money wire rule (pending contract artifact):** external
  money crosses the wire as a canonical decimal STRING
  (`{"amount": "48375.00"}`); every JSON number token — int and float —
  plus booleans and non-finite values (NaN/±Infinity) is rejected at
  ingress (`mode="before"` validators on `CRMPurchaseInvoicePayload`, the
  strict Sub parsers). Per Michael's directive this rule is slated to
  become a checked-in cross-repository contract document when the E4 (ERP)
  and S5 (Sub) slices land — tracked here until that contract exists.

**E4 round-4 hardening (2026-08-03, PR #219 review):** what the boundary
*admits* is now typed and immutable in depth, not merely frozen at the top
level:

- **Admitted evidence is immutable IN DEPTH.** A frozen dataclass with a
  `list` field is only shallowly immutable — the collection can still be
  appended to or reassigned element-wise. Every collection field on an
  admitted wire record is a TUPLE (`InvoiceRecord.lines`,
  `InvoiceRecord.allocations`, `PaymentRecord.allocations`,
  `CreditNoteRecord.lines`); no admitted record carries a `list`, `dict` or
  `set`. A "changed" payload stays a NEW record (`dataclasses.replace`).
- **Zero has exactly ONE canonical spelling and it is positive.** Every
  negative-zero form (`"-0"`, `"-0.00"`, `"-0.0000"`) is rejected by the
  canonical grammar at both ingress paths, and `serialize_amount` normalizes
  a signed-zero result so it can never emit one. `Decimal("-0.00") ==
  Decimal("0.00")` is True, so this is enforced on the SIGN / literal text,
  never on equality.
- **Timestamps are admitted as `datetime`, not wire text.** `updated_at`,
  `issued_at`, `due_at`, `paid_at`, `wht_resolved_at` and `created_at` parse
  to tz-aware UTC instants in the client (`_parse_wire_instant`), which is
  the ONE owner of the wire-text→instant decision (`BaseSyncMixin._parse_date`
  / `_parse_datetime` are gone). A malformed timestamp is a typed row
  rejection routed through the SAME collector as a malformed money fact:
  `updated_at` parses first, so a bad non-position timestamp is POSITIONED
  (parks the cursor) while a bad `updated_at` is UNPOSITIONED (freezes it) —
  the round-2/3 cursor semantics are unchanged. Consumers needing the wire
  text (change hashes, `updated_since`) format it explicitly with
  `.isoformat()`.
- **Closed status sets are enums; open ones are documented `str`.**
  `tax_application` is a typed `TaxApplication` (`exclusive`/`inclusive`/
  `exempt`) — ERP already failed closed on anything else, and typing it also
  closes the hole where a bogus application rode through when the line
  carried no `tax_rate_id`. Invoice/credit-note/payment `status`,
  `wht_status`, subscriber `status`/`category` and billing-account `status`
  stay `str` **by decision**, documented in each record's docstring: Sub owns
  and extends those vocabularies, ERP's mappings carry documented catch-alls,
  and an unknown member must be DATA, never a failed financial row.
- **The last two copy-pasted watermark loops are gone.** The reseller and
  subscriber mixins now use the same `WatermarkProgress` owner (and carry
  `on_parse_error` collectors, so typing their timestamps cannot let one bad
  row terminate a feed generator). `WatermarkProgress.parse_error_collector`
  no longer takes a `parse_datetime` hook — `DotmacSubParseError.updated_at`
  is already the parsed instant.

**Authority order (highest wins):**

1. `app/services/sot_relationships.py` — the executable SOT registry
   (guarded by `tests/architecture/test_sot_registry_liveness.py`). It wins
   for current owners.
2. `docs/SOT_RELATIONSHIP_MAP.md` (prose map), `docs/gl_source_of_truth.md`,
   and the checked-in cross-product contracts
   (`docs/dotmac_sub_material_support_contract.md`,
   `docs/dotmac_sub_tax_accounting_contract.md`, `docs/oidc_identity_contract.md`,
   `docs/replaceable_application_boundary.md`).
3. This ledger — discovery facts and adoption classifications only.

**Enforcement:** the kernel import boundary declared below is enforced by
`tests/architecture/test_kernel_import_boundary.py` (static AST scan; proven
red-sensitive by a synthetic-tree negative control in the same file).

## Reconciliation with the SOT map and executable registry

The Phase-0 ledger's finding 10 ("no erp-wide SOT relationship map or
architecture-test suite") is **closed**: both now exist and win over this
ledger. Deltas the rebaseline absorbs:

| Phase-0 statement | Current state at 96928fa1 |
|---|---|
| No SOT map; creating one is a finding | `docs/SOT_RELATIONSHIP_MAP.md` + `app/services/sot_relationships.py` exist; 9 domains, liveness-guarded |
| `tests/architecture/` holds one metrics test | Full governance suite: SOT registry liveness, OpenAPI contract surface pin, identity protocol boundary, replaceable application boundary, webhook org attribution, metrics scrape safety, version-impact gate |
| Material flows CRM-only; #118 class open on that edge | `inventory.material_support` (module `app.services.inventory.material_support`) is the registered ERP owner of the Sub material-support slice; `sync.crm_procurement` is demoted to an explicit compatibility engine, still repair-first (registry notes) |
| Webhook org attribution in shadow retirement | Registry/`tests/architecture/test_webhook_org_attribution.py` govern it; per-org `IntegrationConfig(DOTMAC_SUB)` bindings are the authority |
| No OpenAPI pin (dual-mount aliasing risk) | `/api/v1` surface pinned in `tests/architecture/openapi_contract_surface.json` |

Registry owners this ledger defers to (do not restate or fork them here):
`tenancy.context`/`tenancy.orm_filter`/`tenancy.rls`, `auth.*`, `control.*`,
`audit.business_log` (fragmentation honestly recorded), `gl.*`,
`platform.sequences`, `fx.rates`, `tax.policy`, `events.outbox`,
`events.hooks`, `licensing.enforcement`, `sync.dotmac_sub`,
`inventory.material_support`, `sync.crm_procurement`, `platform.storage`,
`platform.secrets`, `platform.notifications`.

Still-open Phase-0 findings carried forward unchanged (verified present at
96928fa1): audit four-writers/three-tables (finding 1 — E6 prerequisite),
global-not-org-scoped RBAC tables (finding 2), licensing placeholder key
(finding 3 — `app/licensing/validator.py:32` still ships
`REPLACE_WITH_REAL_PUBLIC_KEY_BASE64`; E9 target), DomainSetting direct
writers (finding 5 — E6 prerequisite), plural approval engines (finding 6),
float annotations on money columns in
`app/models/fixed_assets/maintenance_work_order.py` (finding 7 — verified at
the E1 pin: `estimated_cost`/`actual_cost` were `Mapped[float]`; **RESOLVED
in E4** — annotations are now `Mapped[Decimal]` and the `float(...)` writes in
`app/services/people/assets/maintenance_service.py` are exact Decimal; see the
E4 status above).

## Non-negotiable adoption boundaries (from the accepted plan)

1. ERP stays authoritative for accounting, inventory, procurement, workforce,
   tax, asset, and backoffice records; the kernel never posts, moves stock, or
   decides employment outcomes.
2. `Organization` remains the ERP tenancy key; `organization_id` is never
   renamed. Kernel `Tenant` mapping is a later explicit adapter (E8 ADR).
3. Dual-layer tenancy (ORM listener + PostgreSQL RLS) must never be weakened
   or partially initialized (evidence section below).
4. ERP identity/OIDC/sessions/RBAC stay local; kernel Party/auth/RBAC is
   prohibited in this program.
5. The existing finance outbox is improved in place; kernel messaging tables
   are not introduced beside it before the E8 ADR.
6. Kernel `Money` is a boundary value only; ERP's six-decimal posting, FX,
   tax, and functional-currency internals are untouched.
7. ERP and Sub stay independent apps/databases (versioned APIs/events only).
8. Licence entitlement is separate from RBAC and from data integrity.

## Kernel public-module classification (0.1.0a8)

Classes: **consume-pure** (DB-free contract, importable once the pin lands in
E2) · **adapt-existing** (kernel contract adapted behind an existing ERP
owner, no kernel table) · **defer-db** (kernel persistence/session/migration
surface; gated on the E8 Organization→Tenant + migration ADR) ·
**prohibited** (out of scope for this program; never imported under `app/`).

Only **consume-pure** modules are in the architecture-test import allowlist
today. `adapt-existing` and `defer-db` modules join the allowlist only in the
slice that adopts them, in the same change that updates this table.

| Kernel module | Class | Timing | Rationale / constraints |
|---|---|---|---|
| `dotmac_kernel.money` | consume-pure | Early (E4) | `Money`/`Currency`/immutable `ExchangeRate` values at API/event boundaries only; ERP `Numeric(20,6)`, FX `(20,10)`, tax ratios, functional currency stay internal (boundary 6) |
| `dotmac_kernel.capabilities` | consume-pure | Early (E7) | `CapabilityCatalogue`; one domain owner per capability code |
| `dotmac_kernel.features` | consume-pure | Early (E7) | `FeatureManifest` as declared metadata ONLY — `mount_features` is never called; ERP routers keep mounting via `app/main.py` |
| `dotmac_kernel.profiles` | consume-pure | Early (E7) | `DeploymentProfileSpec`/registry for release preflight; never branch business logic on profile strings |
| `dotmac_kernel.assembly` | consume-pure | Early (E7) | `ProductAssemblySpec` as metadata/release validation; does not replace ERP app startup |
| `dotmac_kernel.providers` | consume-pure | Early (E7) | Provider seam interfaces consumed by profile preflight only |
| `dotmac_kernel.testing` | consume-pure | Early (E2+) | Pure fakes/clock/licence kit for compatibility tests. The a7 wheel defect (eager `harness` → `deps` → `db` import made the subtree DB-bound) is FIXED in 0.1.0a8; DB-free import of the full subtree is asserted by `tests/architecture/test_kernel_compatibility.py::test_every_consume_pure_module_imports_without_db`. `FakeLicenceSigner` works without kernel extras because cryptography is an ERP main dependency |
| `dotmac_kernel.licensing` | consume-pure (types/verifier); cutover deferred-but-required | E9 (after E8) | Value types + `verify_licence` are DB-free and importable; enforcement cutover replaces the placeholder-key path (`app/licensing/validator.py:32`) through one explicit shadow-compare + cutover. No second enforcement owner meanwhile |
| `dotmac_kernel.messaging` (behavior: envelope/outcome semantics) | adapt-existing | Early (E3) | Target semantics for the existing ERP outbox (`events.outbox` owner): claim/deliver/settle, fail-closed unknown events, no service-internal commits. Semantics are matched, not imported wholesale |
| `dotmac_kernel.messaging` (storage/relay/worker: `messaging.models`, `relay`, `worker`, `platform_*`, `inbox`) | defer-db | After E8 ADR | Kernel `outbox_events`/`inbox_records`/`platform_*` tables would stand beside `platform.event_outbox` — a prohibited second outbox until the ADR decides migration/tenancy compatibility |
| `dotmac_kernel.config` | adapt-existing | Mid-program (E6+) | Typed settings contract adapted behind ERP's canonical settings owner only after direct `DomainSetting` writers are removed. Env-name collision: both define `DATABASE_URL`; kernel additionally wants `PLATFORM_DATABASE_URL`. Kernel builds a module-level `settings` singleton on import |
| `dotmac_kernel.settings_resolver` | adapt-existing | Mid-program (E6+) | Spec registry / tenant→platform→default resolution adapted behind `control.settings`; ERP DB remains runtime-authoritative |
| `dotmac_kernel.settings_models` | defer-db | After E8 | Kernel `DomainSetting` table name `domain_settings` **collides exactly** with ERP `public.domain_settings` (different columns: `tenant_id` vs `organization_id`, no ERP history table on the kernel side) |
| `dotmac_kernel.settings_admin` | defer-db | After E6+E8 | Admin write path over kernel settings storage |
| `dotmac_kernel.audit` | defer-db | After E6 consolidation | `write_audit_event` targets table `audit_events` — **collides exactly** with ERP `public.audit_events` (`app/models/audit.py:26`). No kernel audit table beside ERP's four unconsolidated writers |
| `dotmac_kernel.entitlements` | defer-db | After E8 | `tenant_entitlement_grants` table; local grants only after Organization→Tenant mapping + module catalogue |
| `dotmac_kernel.db` | defer-db | After E8 | Import constructs TWO engines + `SessionLocal`/`PlatformSessionLocal` from env at import time and primes RLS via GUC `app.current_tenant` — a different GUC than ERP's `app.current_organization_id`. Importing it violates the no-second-session-factory exclusion and would half-initialize tenancy |
| `dotmac_kernel.migrations` | defer-db | After E8 | 12 revisions designed to compose into the consumer's Alembic `version_locations`; composing them into ERP's single-root, 372-revision graph (shared `public.alembic_version`) is an ADR-gated migration decision |
| `dotmac_kernel.models` | prohibited (identity/RBAC); Tenant subset defer-db via E8 | — | `Tenant`/`Party`/`Role`/`UserCredential`/`AuthSession` etc. Table collisions: `roles` (ERP `app/models/rbac.py:17`), `user_credentials` (ERP `app/models/auth.py:47`). Party never replaces `Person` in this program |
| `dotmac_kernel.models_platform` | prohibited | — | Platform actor identity (`platform_admins`/`platform_sessions`/`platform_audit_events`); ERP has no platform-actor concept and identity stays local |
| `dotmac_kernel.security` | prohibited | — | Kernel credential hashing/token machinery; ERP `auth.flow` owner stays |
| `dotmac_kernel.deps` | prohibited | — | Kernel route guards query kernel identity models |
| `dotmac_kernel.web_deps` | prohibited | — | Kernel portal auth (cookie + admin role) — ERP web auth stays local |
| `dotmac_kernel.platform_auth` | prohibited | — | Platform-admin auth surface |
| `dotmac_kernel.middleware` | prohibited | — | `TenantResolverMiddleware` resolves tenant from Host and pairs with kernel `get_db`'s `app.current_tenant` GUC — semantically collides with ERP's dependency-based org priming; CSRF/rate-limit/security-headers/observability duplicate existing ERP middleware owners |
| `dotmac_kernel.app_factory` | prohibited | — | `create_app` mounts kernel features, platform auth, and `/static`; ERP owns its FastAPI factory, `/admin`, and `/static` |
| `dotmac_kernel.crud` | prohibited | — | Reference-assembly CRUD services are not ERP domain services (plan matrix: out of scope) |
| `dotmac_kernel.templating` | prohibited | — | Kernel Jinja environment/brand globals; ERP has its own template system and UI standard |
| `dotmac_kernel.branding` | prohibited | — | Reference-assembly web surface |
| `dotmac_kernel.identity` | prohibited | — | Party-identity helpers |
| `dotmac_kernel.display` | prohibited | — | Kernel per-request display-settings seam tied to kernel settings + web auth |
| `dotmac_kernel.query` | prohibited (default-deny) | — | No documented ERP need; joins the allowlist only via a slice that documents one |
| `dotmac_kernel.errors` / `dotmac_kernel.exceptions` / `dotmac_kernel.logging` | prohibited (default-deny) | — | ERP has its own error taxonomy (`app/errors.py`) and logging owner; module-specific exceptions (e.g. `CurrencyMismatchError`) arrive via their allowed module |

Bare `import dotmac_kernel` (the curated top-level re-export surface) is also
disallowed under `app/`: it aggregates audit/entitlements/config/identity names
across classes, defeating per-module review. Import the classified submodule.

## Collision inventory (kernel 0.1.0a7 vs ERP at 96928fa1)

### Python packages

- Kernel installs as distribution `dotmac-kernel`, import package
  `dotmac_kernel`. No collision with ERP's `app` package; ERP `src/` contains
  CSS only. No namespace shadowing.

### Models and tables (kernel tables are all schema-less → `public`)

| Kernel table | ERP status | Severity |
|---|---|---|
| `domain_settings` | **EXACT COLLISION** — ERP `public.domain_settings` (`app/models/domain_settings.py:77`) with different columns (`organization_id`, spec-typed values, plus `domain_setting_history`) | Blocker for kernel settings storage until E8 |
| `audit_events` | **EXACT COLLISION** — ERP `public.audit_events` (`app/models/audit.py:26`), different shape | Blocker for kernel audit table until E6+E8 |
| `roles` | **EXACT COLLISION** — ERP `public.roles` (`app/models/rbac.py:17`, global RBAC) | Kernel RBAC prohibited anyway |
| `user_credentials` | **EXACT COLLISION** — ERP `public.user_credentials` (`app/models/auth.py:47`) | Kernel identity prohibited anyway |
| `auth_sessions` | Near-miss — ERP uses `public.sessions` (`app/models/auth.py:190`); no name clash but same concern | Prohibited surface |
| `tenants`, `tenant_domains` | No name clash; ERP tenancy authority is `core_org.organization` — a kernel `tenants` table would be a parallel tenancy authority | E8 ADR decides mapping |
| `parties`, `party_persons`, `party_organizations`, `party_roles` | No name clash with `public.people` (`app/models/person.py:50`) | Prohibited surface |
| `outbox_events`, `inbox_records`, `platform_outbox_events`, `platform_inbox_records` | No name clash — ERP outbox is `platform.event_outbox` (`app/models/finance/platform/event_outbox.py:42`, schema `platform`) — but a second outbox beside it is prohibited by boundary 5 | Defer-db |
| `platform_admins`, `platform_sessions`, `platform_audit_events` | No name clash; note ERP already uses a *schema* named `platform` — kernel `platform_*` tables in `public` would be confusable | Prohibited surface |
| `tenant_entitlement_grants` | No clash | Defer-db (E8) |

ERP schema inventory (for placement decisions): `public` plus domain schemas
incl. `hr`, `lease`, `core_org`, `tax`, `rpt`, `pm`, `fa`, `support`,
`payments`, `core_fx`, `ar`, `expense`, `payroll`, `recruit`, `training`,
`ipsas`, `audit`, `platform`, `procurement`.

### Alembic

- ERP: single `script_location = alembic` (`alembic.ini`), 372 revision files,
  one root (`down_revision = None` count: 1), version table
  `alembic_version` in schema `public`
  (`alembic/env.py` — `version_table_schema="public"`, `include_schemas=True`).
  Production migrates via `scripts/deploy.sh:93` →
  `poetry run alembic upgrade heads` (plural heads is a lived habit).
- Kernel: 12 revisions under `dotmac_kernel/migrations/versions/`
  (`…0001_initial_tenant_schema` … `…0012_platform_outbox`), designed to be
  composed via the consuming app's `version_locations`. Composition would put
  a **second root** into ERP's revision graph sharing the same
  `public.alembic_version` table → guaranteed extra head, and `upgrade heads`
  would then execute kernel DDL implicitly. This is exactly what the E1
  acceptance forbids; blocked until the E8 ADR (which also decides schema
  placement and `FORCE ROW LEVEL SECURITY` handling for any kernel table).

### Middleware

- ERP stack (`app/main.py`): `ObservabilityMiddleware` (`app/observability.py`,
  added at `app/main.py:275`), `rate_limit_middleware`
  (`app/middleware/rate_limit.py`, main:278), `csrf_middleware`
  (`app/web/csrf.py`, main:279), `csp_middleware` (main:365),
  `redirect_error_template_middleware` (main:403), `audit_middleware`
  (main:536, dispatches to Celery), plus `app/middleware/request_cache.py`
  and security headers.
- Kernel middleware package: `csrf`, `observability`, `rate_limit`,
  `security_headers`, `tenant`. Every one duplicates an existing ERP owner;
  `middleware/tenant.py` additionally resolves tenancy from the Host header
  (ERP resolves org from authenticated identity, not Host). Prohibited.

### Routes

- ERP mounts every API router twice (bare legacy alias + `/api/v1`,
  `app/main.py:694-695`; CRM webhook at `/api/v2`), owns `/admin`, `/static`,
  and ~40 web routers. Kernel `app_factory.create_app` mounts platform auth,
  kernel `/static`, and feature routers incl. `/admin`. Never mounted;
  `/api/v1` remains pinned by
  `tests/architecture/openapi_contract_surface.json`.

### Settings

- Env collisions: `DATABASE_URL` read by both ERP (`app/config.py:35`) and
  kernel `config.Settings`; kernel also expects `PLATFORM_DATABASE_URL` in
  production validation. Kernel `config` constructs a `settings` singleton at
  import.
- Storage collision: `domain_settings` table (above). Contract overlap:
  kernel spec-registry/resolver vs ERP `app/services/settings_spec.py` +
  `app/services/domain_settings.py` (`control.settings` owner, with known
  direct-writer fragmentation — finding 5). Adapt only behind the ERP owner
  after E6.

### Audit

- ERP as-built: four writers, three tables — HTTP `audit_middleware`
  (async via Celery) → `public.audit_events`; manual dispatcher + ORM flush
  listener → `audit.audit_log`
  (`app/models/finance/audit/audit_log.py`, schema `audit`); field tracker →
  `audit.field_change_log` (`app/models/audit_field_tracking.py`); plus
  `domain_setting_history`. Kernel `audit.write_audit_event` would be a fifth
  writer into a colliding table name. Blocked until E6 names one writer.

### Identity

- Kernel `Party`/`UserCredential`/`AuthSession`/`platform_auth` vs ERP
  `Person` (`public.people`) + `user_credentials`/`sessions`/`mfa_methods`/
  `api_keys`/`federated_identities` (`app/models/auth.py`) + OIDC adapter
  (`app/services/sso/oidc.py`). Prohibited for this program (boundary 4);
  guarded already by `tests/architecture/test_identity_protocol_boundary.py`.

### Outbox

- ERP owner (`events.outbox` in the registry):
  `app/services/finance/platform/outbox_publisher.py` (`OutboxPublisher`) over
  `platform.event_outbox`, relayed by `app/tasks/outbox_relay.py`, with
  saga/checkpoint companions (`app/models/finance/platform/…`). Kernel
  messaging BEHAVIOR is the E3 target semantics; kernel messaging STORAGE is
  deferred (see classification). Plan-claimed defects verified at this pin —
  see "Verified plan assumptions" below.

### Session factories

- ERP: one `SessionLocal` (`app/db/__init__.py`) plus the canonical context
  managers (`app/db/session_context.py`). Kernel `db.py`: `engine` +
  `platform_engine` created at import time, `SessionLocal` +
  `PlatformSessionLocal`, and a `get_db` that primes RLS with GUC
  `app.current_tenant` (transaction-scoped `set_config`). Two collisions:
  a second (and third) session factory, and a **different RLS GUC name** than
  ERP's `app.current_organization_id` — kernel sessions would satisfy neither
  ERP RLS policies nor the ORM listener. `dotmac_kernel.db` therefore stays
  defer-db behind E8.

## Organization context + PostgreSQL RLS initialization (as-built evidence)

ERP tenancy is dual-layer, and both layers must always be primed together
(registry rule for `organization_tenancy`):

- **Layer 1 — ORM listener:** `app/db/org_listener.py` (`do_orm_execute`
  handler `_add_org_filter`) reads `session.info["organization_id"]`,
  fail-closed via `MissingOrgContextError`
  (`app/db/multi_tenant.py`), gated by `ENFORCE_ORG_FILTER` (default on).
  SELECT-time only; UPDATE/DELETE isolation is RLS's job.
- **Layer 2 — PostgreSQL RLS:** policies created dynamically by migration
  `alembic/versions/add_rls_policies.py` (plus `add_hr_rls_policies.py` and
  schema-specific successors): per-table SELECT/INSERT/UPDATE/DELETE policies
  of the form `should_bypass_rls() OR organization_id =
  get_current_organization_id()`, with **`FORCE ROW LEVEL SECURITY`**
  (`add_rls_policies.py:108`), reading GUCs `app.current_organization_id` and
  `app.bypass_rls` set via `app/rls.py`
  (`set_current_organization_sync`, `bypass_rls_sync`; `SET LOCAL`, i.e.
  transaction-scoped — reset at COMMIT/ROLLBACK).

Per execution context:

| Context | Priming path | Both layers? |
|---|---|---|
| HTTP JSON API | `app/api/deps.py::get_db_with_org` — `prime_session(db, org)` + `set_current_organization_sync(db, org)` (deps.py:106-107), org from `require_tenant_auth`; auto-commit at edge. Cross-tenant admin: `get_db_admin_bypass` / auth bootstrap: `get_db_auth_bypass` — both bypass layers together (`enable_rls_bypass_sync` + `info["allow_cross_org"]`) | Yes — single dependency owns both |
| HTTP web (Jinja/HTMX) | `app/web/deps.py::get_db_for_org` (web/deps.py:1436; primes both at 1468-1475). Public-slug flows (careers/onboarding) open unprimed via `get_db` (web/deps.py:79), resolve the org under bypass, then `prime_tenant_context` (`app/db/session_context.py:119`) sets both layers retroactively | Yes — canonical dep or `prime_tenant_context` |
| **Not middleware** | ERP has **no tenant middleware**: org context is established by DB dependencies after authentication, never from Host headers. (Contrast: kernel `middleware/tenant.py` + kernel `get_db`.) | n/a |
| Celery tasks | `app/db/session_context.py::session_for_org` (primes both, session_context.py:191-196) / `cross_org_session` (bypasses both, 224-228). One-session-per-org rule because `SET LOCAL` dies at COMMIT. Enforced by `scripts/check_session_context.py` (CI + pre-commit + PostToolUse hook; scope `app/tasks/`). Worker bootstrap (`app/celery_app.py`): `worker_process_init` re-registers audit listeners per process; beat uses `app.celery_scheduler.DbScheduler` | Yes — canonical context managers |
| CLI / maintenance scripts | Same context managers (`session_for_org` / `cross_org_session`), e.g. `scripts/backfill_mailcow_offboarding.py`, `scripts/review_suspicious_bank_matches.py`. Note: `scripts/` is OUTSIDE `check_session_context.py`'s default scan scope — convention-held, a candidate gap for a later slice | Yes (by convention) |
| Reconciliation jobs | They are Celery tasks (e.g. `app/tasks/dotmac_sub.py` daily/full reconciliation, `app/tasks/outbox_relay.py`) → `session_for_org`/`cross_org_session` path above. The outbox relay processes per-event org context from the event row | Yes — same task path |
| Migrations | `alembic/env.py` builds its own engine from `settings.database_url` (no session-context helpers, no GUCs). DDL is unaffected by RLS; **data** migrations run subject to `FORCE ROW LEVEL SECURITY` unless they explicitly `SET app.bypass_rls = 'true'` (the policy functions honor it — `add_rls_policies.py:58,73-76`). Deploy path: `scripts/deploy.sh` → `alembic upgrade heads` inside the app container | RLS applies at the DB; no org context — bypass must be explicit per data migration |

Any kernel adoption that opens sessions (db/messaging/entitlements/audit)
must reach RLS through these exact seams or atomically extend them — a kernel
session primed with `app.current_tenant` primes **neither** ERP layer.

## Verified plan assumptions (evidence for later slices — no changes made)

E3 (outbox hardening) claims, all **confirmed** at 96928fa1:

1. Settlement methods commit inside the service: `OutboxPublisher.mark_published`
   (`app/services/finance/platform/outbox_publisher.py:180`), `handle_retry`
   (:222), `mark_dead` (:250), `retry_dead_event` (:304) each call
   `db.commit()`.
2. Unregistered event types are counted as skipped but marked `PUBLISHED`:
   `app/tasks/outbox_relay.py:195-203` ("No handler … — marking published").
3. A ledger handler can swallow per-line errors and return normally, letting
   the relay mark the whole event published:
   `handle_ledger_posting_completed` (`app/tasks/outbox_relay.py:65-136`) —
   the per-line `except Exception: logger.exception(...)` continues the loop,
   then the relay calls `mark_published` unconditionally on normal return.

**E3 update (implemented in this branch):** all three behaviors above are
repaired. `OutboxPublisher` settlement methods flush only (the relay task
owns commit/rollback); the relay is claim/deliver/settle
(`FOR UPDATE SKIP LOCKED` claim + token-gated settlement over new
`claim_token`/`claimed_at`/`lease_expires_at` columns, migration
`20260802_add_outbox_claim_lease_columns`); unknown events dead-letter as
`terminal_reason="unsupported_event"` unless declared no-consequence
(`DECLARED_NO_CONSEQUENCE*` in `app/tasks/outbox_relay.py`); the ledger
handler re-raises per-line failures so the whole delivery transaction rolls
back; replay of DEAD events is audited; and
`reconcile_outbox_balance_projection` verifies the GL balance projection
against posted ledger lines, repairing drift via
`rebuild_balances_for_period`. Canaries:
`tests/integration/platform/test_outbox_applied_result_canaries.py` (PG) and
`tests/tasks/test_outbox_relay.py`.

E9 claim confirmed: `app/licensing/validator.py:32` still ships
`_PUBLIC_KEY_B64 = "REPLACE_WITH_REAL_PUBLIC_KEY_BASE64"`.

No plan-assumption contradictions were found at this pin.

## E1 acceptance restated against this evidence

Adding the `dotmac-kernel==0.1.0a8` pin alone (E2) cannot:

- **mount routes** — ERP never calls `create_app`/`mount_features`
  (`app_factory`/`features` mounting are prohibited/metadata-only above);
- **run kernel migrations** — ERP's `alembic.ini` has no `version_locations`
  entry for the kernel and `deploy.sh` migrates only ERP's graph;
- **create a second session factory** — `dotmac_kernel.db` (import-time
  engines) is defer-db and outside the import allowlist;
- **change owner/transaction behavior** — no registry owner moves; the import
  boundary test fails any `app/` import outside the consume-pure allowlist.

## Prior Phase-0 content

The Phase-0 authority inventory (identity/money/async ledger tables), the
kernel *extraction* verdicts (Money/FX conventions, tax schema, outbox
state machine, SequenceService, secrets), and the findings list remain
historically accurate for pin 318a6e0d and are superseded here only where
the reconciliation table above says so. For current owners, always read
`app/services/sot_relationships.py` first.
