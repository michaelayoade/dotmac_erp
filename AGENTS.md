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
- Lint: `ruff`
- Typing: `mypy`
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
- `docs/adr/` for accepted architecture decisions. An ADR outranks a plan
  document; where they disagree, fix the plan. Added 2026-08-15 — ERP had no
  decision record directory, which is how a gate ("After E8 ADR") came to point
  at a document that did not exist.
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
