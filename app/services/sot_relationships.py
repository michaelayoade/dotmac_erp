"""System-wide single-source-of-truth relationship registry.

Names the service boundaries that own domain decisions in dotmac_erp,
mirroring the dotmac_sub registry pattern. Declarative: routes, tasks, and
event handlers use it as the architectural map; the liveness architecture
test keeps every named owner real. Seeded from the Phase-0 platform adoption
ledger (docs/PLATFORM_ADOPTION_LEDGER.md) — entries record the AS-BUILT
owner; where ownership is fragmented today, the notes say so instead of
pretending otherwise. Every future slice that adds or moves an owner must
update this registry in the same change.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SOTService:
    name: str
    module: str
    owns: tuple[str, ...]
    depends_on: tuple[str, ...] = ()
    notes: str | None = None


@dataclass(frozen=True)
class DomainSOT:
    domain: str
    services: tuple[SOTService, ...]
    entrypoints: tuple[str, ...]
    rule: str


DOMAIN_SOT_RELATIONSHIPS: tuple[DomainSOT, ...] = (
    DomainSOT(
        domain="organization_tenancy",
        services=(
            SOTService(
                name="tenancy.mapping",
                module="app.tenancy",
                owns=("identity-preserving Organization-to-Tenant scope mapping",),
                notes=(
                    "core_org.organization remains authoritative; tenant_id is "
                    "the same UUID and the hosted tenants row is a projection."
                ),
            ),
            SOTService(
                name="tenancy.projection",
                module="app.services.tenant_projection",
                owns=(
                    "Organization-to-kernel-Tenant projection writes and repair",
                    "stable ERP tenant slug derivation",
                    "Organization deletion tenant tombstones",
                ),
                depends_on=("tenancy.mapping",),
                notes=(
                    "core_org.organization remains authoritative; the projection "
                    "mutates in the caller's ERP-owned transaction."
                ),
            ),
            SOTService(
                name="tenancy.context",
                module="app.db.session_context",
                owns=(
                    "organization and module-tenant request/task context priming",
                    "one-session-per-org rule for SET LOCAL lifetimes",
                    "cross-org session escape hatch",
                ),
                depends_on=(
                    "tenancy.mapping",
                    "tenancy.projection",
                    "tenancy.orm_filter",
                    "tenancy.rls",
                ),
                notes=(
                    "All enforcement inputs must be primed together; the "
                    "canonical deps (get_db_with_org, get_db_for_org, "
                    "session_for_org) are the only sanctioned entry points."
                ),
            ),
            SOTService(
                name="tenancy.orm_filter",
                module="app.db.org_listener",
                owns=("fail-closed SELECT-time organization filtering",),
                notes="Layer 1; UPDATE/DELETE isolation is RLS's job.",
            ),
            SOTService(
                name="tenancy.rls",
                module="app.rls",
                owns=(
                    "PostgreSQL tenant-scope GUC context "
                    "(app.current_organization_id, app.current_tenant)",
                ),
                notes=(
                    "ERP and shared-module scope GUCs are set atomically; "
                    "runtime code has no user-settable RLS bypass."
                ),
            ),
        ),
        entrypoints=("app.api.deps", "app.web.deps", "app.db.session_context"),
        rule=(
            "Organization scoping is one identity enforced by the ORM listener, "
            "ERP RLS and shared-module RLS. Canonical entry points set every "
            "input together or not at all."
        ),
    ),
    DomainSOT(
        domain="identity_access",
        services=(
            SOTService(
                name="auth.flow",
                module="app.services.auth_flow",
                owns=(
                    "login/refresh/logout and MFA flows",
                    "session token issue, rotation, and hashing",
                    "RBAC claim baking into tokens",
                ),
            ),
            SOTService(
                name="auth.guards",
                module="app.services.auth_dependencies",
                owns=(
                    "request authentication and org resolution",
                    "role/permission guard evaluation",
                ),
                depends_on=("auth.rbac",),
                notes=(
                    "The permission-check join is currently duplicated across "
                    "the api and web guard families (ledger finding 2) — a "
                    "single has_permission owner is the consolidation target."
                ),
            ),
            SOTService(
                name="auth.application_access",
                module="app.services.application_lifecycle",
                owns=(
                    "Person login-eligibility transitions",
                    "local credential activation/deactivation orchestration",
                    "managed application lifecycle plans and execution receipts",
                ),
                depends_on=("auth.flow",),
                notes=(
                    "Activation delegates exact binding changes to the ERP "
                    "external-identity authority; Integrator remains transport."
                ),
            ),
            SOTService(
                name="auth.external_identity",
                module="app.services.external_identity",
                owns=(
                    "exact external-subject to Person bindings",
                    "locked external-login finalization",
                    "binding disable and selective session revocation",
                ),
                depends_on=("auth.flow",),
                notes=(
                    "dotmac-auth-oidc verifies protocol assertions; ERP alone "
                    "writes bindings and ERP session provenance."
                ),
            ),
            SOTService(
                name="auth.rbac",
                module="app.services.rbac",
                owns=(
                    "role/permission/grant CRUD",
                    "permission-code catalogue (seeded)",
                ),
                notes=(
                    "Tables are GLOBAL, not org-scoped; safety rests on the "
                    "one-person-one-org invariant (ledger finding 2). The "
                    "scope decision is explicit kernel-adoption work."
                ),
            ),
        ),
        entrypoints=(
            "app.api.auth_flow",
            "app.api.application_lifecycle",
            "app.web.auth",
            "app.api.rbac",
        ),
        rule=(
            "Person is the single ERP login identity; credentials, sessions, "
            "MFA, API keys and application access resolve to person_id. ERP "
            "accepts only a shared-adapter verified exact external subject. "
            "External authorization claims and "
            "counterparties (Customer, Supplier) are not ERP identities or "
            "permission owners."
        ),
    ),
    DomainSOT(
        domain="configuration_control",
        services=(
            SOTService(
                name="control.settings",
                module="app.services.domain_settings",
                owns=(
                    "domain_settings writes with history",
                    "org-override/global/default resolution",
                ),
                depends_on=("control.settings_spec",),
                notes=(
                    "Several modules still construct DomainSetting rows "
                    "directly, bypassing history (ledger finding 5) — those "
                    "callers are migration targets, not co-owners."
                ),
            ),
            SOTService(
                name="control.settings_spec",
                module="app.services.settings_spec",
                owns=("setting specs, validation, and coercion",),
            ),
            SOTService(
                name="control.feature_flags",
                module="app.services.feature_flag_service",
                owns=(
                    "flag resolution (org row, global row, registry default)",
                    "flag-gated router/module availability",
                ),
                depends_on=("control.settings",),
            ),
        ),
        entrypoints=("app.api.settings", "app.services.module_settings_web"),
        rule=(
            "Settings are data with one canonical writer and recorded "
            "history; flags never substitute for authorization."
        ),
    ),
    DomainSOT(
        domain="audit_trail",
        services=(
            SOTService(
                name="audit.business_log",
                module="app.services.audit_dispatcher",
                owns=("manual business audit events into audit.audit_log",),
                notes=(
                    "AS-BUILT FRAGMENTATION (ledger finding 1): the HTTP "
                    "middleware writes audit_events via Celery, the ORM flush "
                    "listener and the field tracker write independently. One "
                    "writer interface must be named before kernel audit "
                    "adoption; this entry records today's manual-path owner, "
                    "it does not bless the fragmentation."
                ),
            ),
        ),
        entrypoints=("app.main", "app.services.audit_listener"),
        rule=(
            "Target: one audit writer interface. Until consolidation, no NEW "
            "audit writer may be added outside the four existing mechanisms."
        ),
    ),
    DomainSOT(
        domain="general_ledger",
        services=(
            SOTService(
                name="gl.poster",
                module="app.services.finance.gl.ledger_posting",
                owns=(
                    "posted ledger line construction (single site)",
                    "balance and functional-amount validation at posting",
                ),
                depends_on=("gl.period_guard", "fx.rates"),
                notes="docs/gl_source_of_truth.md is the domain contract.",
            ),
            SOTService(
                name="gl.period_guard",
                module="app.services.finance.gl.period_guard",
                owns=("open/soft-close/hard-close posting gates",),
            ),
            SOTService(
                name="platform.sequences",
                module="app.services.finance.platform.sequence",
                owns=("document numbering for all finance documents",),
            ),
            SOTService(
                name="fx.rates",
                module="app.services.finance.platform.fx",
                owns=(
                    "exchange-rate lookup and conversion",
                    "functional-currency resolution",
                ),
            ),
            SOTService(
                name="tax.policy",
                module="app.services.finance.tax.tax_calculation",
                owns=("tax computation from effective-dated TaxCode policy",),
                notes=(
                    "Rate stored as ratio, not percentage — the sub tax "
                    "contract doc governs the sub-facing boundary."
                ),
            ),
        ),
        entrypoints=("app.services.finance.posting.base",),
        rule=(
            "Documents reach the GL only through posting adapters with "
            "deterministic idempotency keys; posted lines are immutable and "
            "corrections are new journals; balances are derived cache."
        ),
    ),
    DomainSOT(
        domain="platform_events",
        services=(
            SOTService(
                name="events.outbox",
                module="app.services.finance.platform.outbox_publisher",
                owns=(
                    "transactional event publication and claim/lease, "
                    "retry, dead-letter, and replay state",
                ),
                notes=(
                    "Applied-result semantics: the relay task owns "
                    "commit/rollback (publisher methods flush only); "
                    "delivery is claim/deliver/settle with token-gated "
                    "settlement; unknown events dead-letter as unsupported "
                    "unless declared no-consequence; replay of DEAD events "
                    "is audited; the outbox reconciler repairs GL balance "
                    "projection drift via the canonical rebuild writer."
                ),
            ),
            SOTService(
                name="events.hooks",
                module="app.services.hooks.registry",
                owns=("outbound webhook registration and emission",),
            ),
        ),
        entrypoints=("app.tasks.outbox_relay", "app.tasks.hooks"),
        rule=(
            "Cross-module consequences ride the outbox inside the business "
            "transaction; handlers never commit; external delivery goes "
            "through service hooks with signed, retried delivery."
        ),
    ),
    DomainSOT(
        domain="commercial_licensing",
        services=(
            SOTService(
                name="licensing.enforcement",
                module="app.licensing.enforcement",
                owns=(
                    "startup and periodic license gates",
                    "licensed-module and limit checks",
                ),
                notes=(
                    "The embedded verification key is a placeholder at the "
                    "ledger's recon pin (finding 3) — a real key or an "
                    "explicit gate-off decision is required."
                ),
            ),
        ),
        entrypoints=("app.startup", "app.tasks.license"),
        rule="License checks gate module availability, never data integrity.",
    ),
    DomainSOT(
        domain="external_sync",
        services=(
            SOTService(
                name="sync.dotmac_sub",
                module="app.services.dotmac_sub.sync",
                owns=(
                    "Sub billing-fact ingestion (AR) and GL projection",
                    "reverse-and-repost on posted-invoice changes",
                ),
                notes=(
                    "Pull-based per the checked-in contract: Sub owns ISP "
                    "billing facts, ERP owns accounting; webhook ingress "
                    "feeds the same service — no second decision path. The "
                    "adapter never promotes Sub's customer tax identifier into "
                    "ERP's locally governed customer tax-identity field."
                ),
            ),
            SOTService(
                name="sync.sub_operational_context",
                module="app.services.sync.crm.projects",
                owns=(
                    "version-2 Sub operational-context intake",
                    "ERP project, ticket, project-task, and work-order projections",
                    "organization-scoped source-ID upsert mappings",
                ),
                notes=(
                    "Sub owns operational lifecycle decisions. ERP projections are "
                    "rebuildable context for finance and employee-expense links; "
                    "the neutral /sync/sub/bulk route reuses the established "
                    "idempotent projection service during compatibility migration."
                ),
            ),
            SOTService(
                name="inventory.material_support",
                module="app.services.inventory.material_support",
                owns=(
                    "Sub material-support intake and immutable replay contract",
                    "ERP material-support outcome lookup",
                    "routing accepted support needs through ERP inventory policy",
                ),
                depends_on=("sync.crm_procurement",),
                notes=(
                    "Sub owns the service work order and material need; ERP owns "
                    "warehouse, stock, serial, fiscal-period, and issue decisions. "
                    "The crm_id/omni_id names are temporary compatibility storage "
                    "for the immutable Sub request UUID, not CRM authority. See "
                    "docs/dotmac_sub_material_support_contract.md."
                ),
            ),
            SOTService(
                name="sync.crm_procurement",
                module="app.services.sync.crm.procurement",
                owns=(
                    "legacy CRM material/PO/purchase-invoice sync mappings",
                    "compatibility material-request inventory policy engine",
                ),
                notes=(
                    "REPAIR-FIRST (ledger finding 9): the #118 money-bug "
                    "class lives on this edge; no extraction or convergence "
                    "until closed. New Sub material requests enter through "
                    "inventory.material_support; this service remains its "
                    "compatibility engine until the CRM path is retired."
                ),
            ),
        ),
        entrypoints=(
            "app.tasks.dotmac_sub",
            "app.api.dotmac_sub",
            "app.api.sync.dotmac_sub",
            "app.tasks.expense",
        ),
        rule=(
            "External systems are transports or explicitly contracted "
            "authorities; local mirrors are rebuildable projections with "
            "reconciliation paths. ERP remains independently replaceable: no "
            "direct external database access, cross-system foreign keys, shared "
            "transactions, or shared business-domain runtime."
        ),
    ),
    DomainSOT(
        domain="platform_services",
        services=(
            SOTService(
                name="platform.storage",
                module="app.services.storage",
                owns=("object storage access (single owner)",),
            ),
            SOTService(
                name="platform.secrets",
                module="app.services.secrets",
                owns=("OpenBao pointer resolution for settings/secrets",),
                notes="Settings hold pointers, never secret values.",
            ),
            SOTService(
                name="platform.notifications",
                module="app.services.notification",
                owns=("in-app and email notification dispatch",),
            ),
        ),
        entrypoints=("app.api.files",),
        rule="One owner per platform capability; callers never re-implement.",
    ),
)


def all_services() -> tuple[SOTService, ...]:
    return tuple(
        service for domain in DOMAIN_SOT_RELATIONSHIPS for service in domain.services
    )
