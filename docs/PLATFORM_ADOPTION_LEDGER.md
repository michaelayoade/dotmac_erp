# Platform Adoption Ledger — dotmac_erp (Phase 0)

**Status:** Draft for review — Phase 0 of the platform adoption program. No code or
schema changes are authorized by this document.
**Decision authority:** `dotmac_starter_mt` `docs/adr/0003-unified-deployment-profiles.md`
and `docs/superpowers/plans/2026-07-18-existing-product-adoption.md` (this repo's track).
**Companion:** the dotmac_sub ledger (`dotmac_sub/docs/PLATFORM_ADOPTION_LEDGER.md`).
Unlike sub, this repo has no SOT relationship map; `docs/gl_source_of_truth.md` covers
the GL only. Creating an erp-wide map is itself a finding (below).
**Recon basis:** repo state at `origin/main` 318a6e0d (the adoption plan's pinned recon
revision), surveyed 2026-07-19.

## The adoption frame

```text
dotmac_erp = platform kernel + ERP product assembly + ERP domain modules
```

- ERP `Organization` maps to the kernel `Tenant` **via adapter** — `organization_id`
  is the wire-level tenant key across ~240 models, the RLS policies, and the ORM
  listener; renaming is prohibited by the adoption plan.
- The org **hierarchy** (parent org, consolidation method, ownership %) is ERP
  domain on top of flat kernel tenancy — product-owned.
- ERP is additionally the adoption plan's designated **source candidate for kernel
  global-commercial primitives** (Money/FX, tax policy, outbox, licensing) — the
  extraction verdicts are a first-class section below.

Classifications: **reuse** (adopt kernel contract nearly as-is / lift into kernel),
**adapt** (kernel contract behind an adapter seam), **product-owned**,
**migrate-later**, **retire**.

## Ledger

### Identity, tenancy, authorization

| Concern | Current authority | Class | Notes |
|---|---|---|---|
| Organization/tenancy | `core_org.organization` + dual-layer enforcement: ORM listener (`app/db/org_listener.py`, fail-closed `MissingOrgContextError`, gated by `ENFORCE_ORG_FILTER` default true) AND native PostgreSQL RLS (`app/rls.py` GUCs `app.current_organization_id`/`app.bypass_rls`; dynamic policy migration over every `organization_id` table) | adapt | Stronger than the recon assumption of app-only scoping. The GUC convention is directly **reusable** by the kernel RLS contract. Both layers must be primed together — canonical deps (`get_db_with_org`, `get_db_for_org`, `session_for_org`) + a CI guard enforce this; kernel adoption must preserve or atomically unify the invariant |
| Person/credentials/sessions | `Person` (single login identity, org-scoped, globally-unique email) + `UserCredential`/`MFAMethod`/`Session`/`ApiKey` (colon-namespaced key scopes) | migrate-later | Maps cleanly to kernel Party/identity; Person is load-bearing across HR/RBAC/audit FKs — adapter first, migrate when kernel auth is proven |
| External identity | OIDC Authorization Code + PKCE adapter (`app/services/sso/oidc.py`) + ERP-owned `federated_identities` bindings and local sessions | adapt | Shared auth DB, cross-app JWT secret, and shared-cookie paths retired. The provider proves identity only; ERP authorization remains local and the provider is replaceable by configuration and binding migration |
| Employee / Customer / Supplier | `hr.employee` (1:1 Person), `ar.customer`, `ap.supplier` — counterparties carry no Person link | product-owned | Whether kernel Party absorbs counterparties is a later explicit decision; today there is no link to confuse |
| RBAC | Global `roles`/`permissions`/`role_permissions`/`person_roles` + dependency guards; colon-namespaced read/manage codes seeded by `scripts/seed_rbac.py`; admin bypass; fail-closed unseeded keys | reuse (pattern) / adapt (storage) | This is the fleet-pattern source. **Decision needed:** tables are global, not org-scoped — safe only via the implicit one-org-per-person invariant. Also: the permission-check join is duplicated inline 4× across guard families (no `has_permission` owner) |
| Settings + feature flags | `settings_spec.py` registry + `domain_settings` (org override → global → default, typed, history, cache, secrets crypto); flags = `feature_flag_registry` metadata + `domain='features'` rows; flags gate router inclusion | adapt | The most kernel-shaped subsystem in the repo. Findings: 7+ modules construct `DomainSetting` directly (history only on the canonical service path); flag authority split across spec/seed/registry |
| Audit | **Four writers, three tables**: HTTP audit → `audit_events` (Celery async), manual `fire_audit_event` + automatic ORM flush listener → `audit.audit_log`, field tracker → `audit.field_change_log`; plus `DomainSettingHistory` | adapt (consolidate first) | The worst single-writer violation in the repo; divergent durability semantics (async vs same-transaction). Name one writer before adopting the kernel audit contract |
| Session/transaction | Commit-at-the-edge: org-scoped deps auto-commit; services flush only; Celery/CLI commit inside `session_for_org` (SET LOCAL dies at COMMIT — one session per org); CI guard bans raw `SessionLocal()` in tasks | adapt | Matches the kernel one-transaction-owner direction. Caveat: commit semantics are chosen by dependency choice (`get_db` never commits; org deps do) — convention-enforced, not typed |
| Surfaces | Single FastAPI app; every API router mounted twice (bare legacy alias + `/api/v1`; CRM webhook at `/api/v2`); Jinja2/HTMX web modules behind ~40 access wrappers + feature flags; careers/onboarding public-slug flows | product-owned | Thin adapters per the SOT standard. The dual-mount aliasing complicates a future OpenAPI contract pin — scope the pin to `/api/v1` |

### Money, commercial, and finance core

| Concern | Current authority | Class | Notes |
|---|---|---|---|
| Money/currency/FX | No Money value object — bare `Decimal` + `currency_code`; `MoneyType=Numeric(20,6)`, `ExchangeRateType=Numeric(20,10)`; per-currency `decimal_places`; `ExchangeRate` (org, pair, rate_type, effective_date, source) with FX snapshotted into posted ledger lines; `FXService.convert`; ECB daily ingestion; FX revaluation service | adapt (see extraction verdicts) | Core finance (`gl/ap/ar/tax`) is Decimal-clean. Flags: `fixed_assets/maintenance_work_order.py` annotates money columns as Python `float`; ROUND_HALF_UP is scattered per-site (68+ quantize calls) rather than policy-owned |
| Tax/jurisdiction | `TaxCode` (rate **as ratio**, effective-dated, inclusive/compound/recoverable flags, GL account mapping on the code) + full tax subledger incl. deferred VAT (lived rollout w/ runbook + reconciliation evidence) | adapt (schema) / product-owned (accounting) | The sub↔erp tax boundary is a checked-in, fail-closed contract (`docs/dotmac_sub_tax_accounting_contract.md`) — a model asymmetric-integration artifact |
| Document numbering | `SequenceService` — single numbering authority for invoices, payments, POs, receipts | **reuse** | Self-contained, org-scoped; immediately liftable |
| GL/AP/AR core | GL-first, **single poster verified**: `PostedLedgerLine` constructed only in `LedgerPostingService.post_journal_entry`; all documents post via adapters with deterministic idempotency keys; period guards; balance table explicitly "derived cache, never authoritative" (`docs/gl_source_of_truth.md`) | product-owned | Extracting this would be extracting the product. The kernel takes the *pattern*: single-poster + posting-adapter + source-document idempotency + period-guard interface; `gl_source_of_truth.md` is the SoT-doc template |
| Approvals | Finance `ApprovalWorkflow` (document_type + amount threshold + JSONB levels + request lifecycle + SoD mixin) — but HR runs **several independent approval surfaces** (leave, loans, salary review, info changes, shift swaps) | adapt (finance engine) | A kernel approvals contract must resolve the multiple-engines problem erp itself has not unified |
| Licensing | `app/licensing/` — Ed25519-signed `.lic`, grace ladder, feature/module/user/org gates, machine fingerprint, startup + periodic enforcement | **reuse** (kernel seed) | **Security flag:** the embedded verification key is `REPLACE_WITH_REAL_PUBLIC_KEY_BASE64` at this pin — signature enforcement cannot pass outside dev mode. Kernel work = key management, revocation; fix the placeholder before anything relies on it |

### Async, integrations, delivery

| Concern | Current authority | Class | Notes |
|---|---|---|---|
| Outbox/events | `platform.event_outbox` (event_version, causation_id, idempotency_key, PENDING/PUBLISHED/FAILED/DEAD, stepped retries, dead-letter requeue) + relay task + saga/checkpoint/idempotency companions | **reuse** (table+publisher) / adapt (relay) | Contract-grade schema. Caveats for kernel-grade: unhandled events are silently marked PUBLISHED; effectively single-producer today (only ledger posting publishes); in-process handlers only — fold the service-hook webhook deliverer in as the external transport |
| Outbound webhooks | Service hooks (`HookRegistry.emit`, per-hook exponential backoff, retryable-error classification) | adapt | The natural external transport for the kernel outbox |
| Async infra | Celery + **DB-driven beat** (operator-editable schedules) + session-context CI guard + per-process audit-listener registration (worker bootstrap lesson) | adapt | Conventions portable to the kernel task contract; implementation is ordinary Celery |
| sub↔erp sync (AR) | Pull-based per the checked-in contract ("Sub owns ISP billing facts; ERP owns accounting; no second push path"): watermarked incremental client w/ retries + circuit cooldown; cents-quantize; content-hash change detection; reverse-and-repost on posted-invoice changes; webhook ingress (HMAC, delivery-id dedup) feeding the SAME sync service; daily + full reconciliation tasks | reuse (contract + client pattern) / product-owned (services) | Exemplary. The integration-audit "shared-client keystone" lives here. **Webhook org attribution (audit D2):** the authority is per-org `IntegrationConfig(DOTMAC_SUB)` bindings — the credential that verifies the signature IS the identity, ambiguous bindings (one secret, two orgs) fail closed; the env-secret + `DEFAULT_ORGANIZATION_ID` path is in shadow retirement behind `DOTMAC_SUB_WEBHOOK_ORG_RESOLUTION` (legacy\|shadow\|strict, default shadow logs divergence as cutover evidence; a startup seed migrates the env binding into the default org's config row). **strict is the target**: after the observation window, the retirement PR deletes the legacy path and flips the default |
| CRM↔erp sync (procurement) | `app/services/sync/crm/procurement.py` + `CRMSyncMapping` + monolith remnant `dotmac_crm_sync_service.py`; expense money movement in `app/tasks/expense.py` (incl. stuck-transfer repair polling) | product-owned, **repair-first** | **This — not the sub AR pull — is where the #118 money-bug class (dead webhook, double-cash) lives.** Do not extract or converge until that class is closed |
| Files/notifications/search | Single S3/MinIO storage owner; conventional notification service; LIKE-based suggestions (no engine) | product-owned | Storage is reuse-shaped |
| Observability/secrets | Sentry→GlitchTip, OTel, Prometheus + scrape-safety doc; `app/services/secrets.py` OpenBao **pointer** resolution + settings crypto | reuse (secrets) / adapt (obs) | Secrets module matches the fleet OpenBao-pointer rule exactly — clean kernel candidate |
| Migrations/deploy/CI | 367 alembic revisions, `upgrade heads` (multi-head lived reality); hardened `deploy.sh` (backup → pin-by-sha → migrate → health gate → auto-rollback); CI: lint/type/test/security/pre-commit/docker + session-context guard | product-owned; multi-head habit → **retire** | Deploy pattern is starter-kit material; kernel should mandate single-head linear history |

## Findings (the Phase-0 actionable list)

1. **Audit: four writers into three tables** — HTTP (async Celery), manual dispatcher,
   ORM flush listener, field tracker. One writer interface must be named before the
   kernel audit contract adopts. The worst single-writer violation in this repo.
2. **RBAC scope decision** — roles/permissions/person_roles are global, not
   org-scoped; safety rests on the implicit one-person-one-org invariant. Kernel
   adoption must make global-vs-tenant scope an explicit decision. Also: the
   permission-check query is duplicated inline 4× (no `has_permission` owner).
3. **Licensing placeholder public key** — `validator.py` ships
   `REPLACE_WITH_REAL_PUBLIC_KEY_BASE64`; production enforcement hard-fails at
   startup, but signature verification cannot genuinely pass. Decide: wire a real
   key (build-time, per-product) or gate the module off until the kernel licensing
   contract lands.
4. **External identity cutover** — shared-auth-DB SSO is retired. Production
   cutover still requires provisioning issuer/subject bindings and OIDC client
   configuration before enabling `OIDC_ENABLED`; local login remains the
   fallback until that operational gate is complete.
5. **DomainSetting multi-writer** — 7+ modules construct rows directly; setting
   history is only guaranteed through the canonical service. Plus flag authority
   split across spec list, seed list, and registry table.
6. **Approval engines are plural** — finance ApprovalWorkflow + independent HR
   approval surfaces; unify or explicitly charter both before a kernel approvals
   contract.
7. **Float annotations on money columns** in `fixed_assets/maintenance_work_order.py`
   (DB precision is Numeric; the Python type invites float arithmetic) — small,
   worth fixing independently of adoption. Rounding policy is scattered
   (ROUND_HALF_UP per site) — absorbed by a future kernel Money type.
8. **External-projection residue in identity rows** — `ERPNextSyncMixin` on
   Employee/Supplier, `nextcloud_user_id` on Person (cache-in-source-row pattern).
9. **CRM procurement sync is the #118 money-bug surface** (dead webhook,
   double-cash) — repair-first; the sub AR pull is healthy and contractually clean.
10. **No erp-wide SOT relationship map or architecture-test suite** —
    `tests/architecture/` holds one metrics test; tenancy tests live in `tests/db/`.
    Creating the map + governance suite is the natural first post-ledger slice
    (mirror sub's `sot_relationships.py` + registry-liveness pattern).

## Kernel extraction candidates (verdicts for the deployment-profiles plan)

- **Money/FX** — *extract the conventions, author the primitive.* No Money class
  exists to lift; take `Numeric(20,6)`/`(20,10)`, `Currency.decimal_places`, the
  `ExchangeRate` model (rate-type + effective-date + source + snapshot-into-document),
  pure `FXService.convert`. Functional-currency plumbing stays with the GL. The
  kernel Money type absorbs the scattered rounding policy.
- **Tax policy** — *schema yes, accounting no.* TaxCode rate-as-ratio + effective
  dating + flags + jurisdiction is kernel-worthy; account mapping, subledger, and
  deferred VAT are ERP-owned. The sub tax contract doc is the reusable template.
- **Outbox** — *reuse table + publisher state machine; adapt relay* (no silent
  PUBLISHED for unhandled events, pluggable transports, keep event_version +
  causation_id).
- **Licensing** — *reuse as the signed-licensing seed* after the placeholder-key
  fix; kernel adds key custody + revocation.
- **Small, immediately liftable:** `SequenceService` (document numbering),
  `app/services/secrets.py` (OpenBao pointer resolution).
- **Pattern templates:** `docs/gl_source_of_truth.md`, `deploy.sh`
  (pin-by-sha/health-gate/rollback), the asymmetric pull-sync contract doc.

## Phase 1 next steps (separate slices, after this ledger is accepted)

1. Create the erp SOT relationship map + executable registry and a
   `tests/architecture/` governance suite (sub's pattern); fold findings 1, 2, 5, 6
   into it as tracked owners.
2. Pin the `/api/v1` OpenAPI contract surface (scope: `/api/v1` only — the bare
   legacy aliases are duplicates; note the `/api/v2` CRM webhook separately).
3. Golden characterization pins for the money paths: posting-adapter idempotency,
   AR invoice settlement via the sub sync (reverse-and-repost), expense posting.
4. Decide the licensing key question (finding 3).
5. Declare the `erp` `ProductAssemblySpec` once the kernel publishes that contract.

## Explicitly out of scope for Phase 0

- Renaming `organization_id`, merging Person into kernel Party tables, or touching
  the dual-layer tenancy mechanics.
- Any dual-write, schema change, or writer replacement.
- Extracting Money/outbox/licensing into the kernel (verdicts above inform the
  kernel plan; execution follows kernel contract publication).
- Shared databases or ORM imports with dotmac_sub or the vendor control plane.
