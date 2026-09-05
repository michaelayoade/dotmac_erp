# Codex Instructions for Dotmac ERP

These are the repo-level instructions Codex should follow for this workspace.

## Primary Stack
- FastAPI + SQLAlchemy + Alembic
- Redis/Celery
- Jinja2 + Tailwind (PostCSS) + Alpine/HTMX

## Testing and QA
- Unit/integration: `pytest tests/ --ignore=tests/e2e/`
- Coverage: `pytest --cov=app --cov-report=html`
- E2E: `pytest tests/e2e/ -v`
- Lint: `make lint` (`poetry run ruff check`) — never a bare `ruff`, which
  runs whatever is on `PATH` rather than the version `poetry.lock` pins.
- Formatting: `make format-check` (verify) / `make format` (write). Both are
  part of `make check`; ruff is pinned exactly and the pins are guarded by
  `tests/architecture/test_toolchain_coherence.py`.
- Typing: `mypy`
- ERP identity cutover: `make privilege-manifest-check` byte-compares the
  generated `dotmac_erp_app` -> `app_user` privilege manifest and SQL against
  the frozen production census. It is offline and needs no database. The
  ENFORCING gate is `tests/architecture/test_privilege_manifest.py` (which
  regenerates and compares in the test job); the Make target is the
  convenience form. `make privilege-manifest` rewrites the artefacts — never
  hand-edit them. Every row carries one of three dispositions: `grant`
  (goes in `scripts/erp_identity_cutover_grants.sql`), `review_required`
  (needs a sign-off; EMPTY today — both open items were ruled on 2026-09-04)
  and `denied_by_architecture` (never applied). A boolean cannot tell "grant
  after review" from "never grant"; the split between the two files is
  permanent, not a staging step.
- ERP identity cutover, the two rulings (2026-09-04, Michael, both Change-1
  BLOCKERS). **The persistence plane is resolved from a DECLARATION**
  (`app/persistence_planes.py`: a module's `tables`/`platform_tables` read
  from `app.runtime_admission.COMPOSED_MODULES`, plus the host assembly's own
  declaration) and NEVER inferred from the `mod_` prefix, the `public`
  schema, a `tenant_id` column, RLS state or current ACLs — those are
  evidence to validate a declaration, not sources of ownership. An
  unclassified relation REFUSES generation; it never defaults to the tenant
  plane. And **no denied item renders SQL**: the denials live in
  `scripts/erp_identity_cutover_denied.sql`, which has no `BEGIN`, no
  `COMMIT` and no statement at all, so it is a no-op by construction rather
  than by convention. A denied relation's absence is proved at table AND
  column level (`denial_violations`); a denied function's is proved with an
  EFFECTIVE `has_function_privilege` question asked of `app_user`, of
  `PUBLIC` and of the declared permitted executor
  (`function_denial_violations`) — PostgreSQL grants EXECUTE to `PUBLIC` by
  default, so `REVOKE … FROM app_user` alone does not neutralize an inherited
  grant, and a surviving default is reported as remediation owed.
- CSS build: `npm run dev` or `npm run watch:css` (outputs `static/css/app.css`)

## Priorities
- Prefer correctness, security, and maintainability over speed.
- Keep responses concise and actionable.
- Ask clarifying questions when requirements are ambiguous.

## Architecture + Safety Defaults
- API changes must preserve RBAC and tenant scoping.
- Web changes must respect CSRF; avoid exposing tokens to JS.
- Prefer service-layer changes over route logic changes.
- Use Alembic for schema changes.
- Call out security or data loss risks.
- Do not weaken security controls without explicit approval.

## Codebase Rules (Source of Truth)
- `docs/architecture/erp-runtime-identity-cutover.md` for the
  `dotmac_erp_app` -> `app_user` runtime identity cutover: the ruling
  (identity migration first, least-privilege reduction second), the frozen
  census, the manifest's OID-independent identity scheme, and the guard's
  refusals. No SQL from that programme has been applied anywhere.
- `docs/adr/` for accepted architecture decisions. An ADR outranks a plan
  document; where they disagree, fix the plan. Added 2026-08-15 — ERP had no
  decision record directory, which is how a gate ("After E8 ADR") came to point
  at a document that did not exist.
- `docs/adr/reservations.toml` allocates ADR numbers, and it is the only thing
  that does. Take `next_free`, land that row alone on `main`, and write the ADR
  afterwards. Never read the directory and take the next gap: that is a
  read-then-write with no lock across branches that cannot see each other, and
  it is how 0003, 0004, 0006 and 0008 each came to name more than one decision.
  A number is never reused, including after withdrawal — it survives in commit
  messages, review threads and `match="ADR-00NN"` assertions that removing a
  row does not update. Claim with `python tools/adr/allocate.py --slug <slug>`;
  it refuses an absent register outright, because a tool that creates one on
  first run is indistinguishable from a branch that lost the file and
  re-allocates 0001. Enforced by
  `tests/architecture/test_adr_number_allocation.py` (the register),
  `test_adr_allocator.py` (the writer's logic),
  `test_adr_allocator_cli.py` (the entry point, run for real) and
  `.github/workflows/adr-allocation.yml` (a claim changes the register alone).
  `claimed` is always the git AUTHOR date and never a merge time; `landed_at`
  is separate and exists only for rows on `main`; every off-`main` row carries
  `visibility` = `pr`/`remote_ref`/`local_only`, because a claim only one
  workstation can see does not support a count anyone else is asked to check.
- `CLAUDE.md` for critical coding rules, workflow, verification steps, and module map.
- `.claude/rules/` for design system, templates, security, services, and web routes standards.
- `UI_CONVENTIONS.md` and `CONSISTENCY_CHECKLIST.md` for UI consistency checks.

Follow those files as the authoritative guidance when implementing changes or reviewing code.

## Cross-repository engineering governance

- `.dotmac/standards-profile.json` declares each enrolled authority and fully
  typed contract surface against one exact accepted Governance commit.
- The `Dotmac engineering standards` CI job must execute that same immutable
  revision and be required on protected `main`.
- Mutable tags/branches, copied rules, candidate mode, or a missing required
  check are not substitutes for the Governance-owned enforcement path.
- The schema-9 external-connector ratchet is transitional migration evidence,
  not runtime isolation. Its six baselines and conservation ledger match
  `docs/external-connector-surface.md`; they only shrink with deletion or a
  proved cutover behind Dotmac Integrator. The permanent boundary is
  Integrator-only connector packages, secrets, ingress, and egress.
