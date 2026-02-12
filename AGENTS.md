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
- `CLAUDE.md` for critical coding rules, workflow, verification steps, and module map.
- `.claude/rules/` for design system, templates, security, services, and web routes standards.
- `UI_CONVENTIONS.md` and `CONSISTENCY_CHECKLIST.md` for UI consistency checks.

Follow those files as the authoritative guidance when implementing changes or reviewing code.
