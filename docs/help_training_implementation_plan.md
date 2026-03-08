# Help And Training Implementation Plan

## Goal
Replace the current redirect-heavy `/help` experience with a real in-app help and product training system that:
- teaches users how to complete tasks
- provides searchable answers and troubleshooting
- supports role-based onboarding and learning tracks
- integrates with existing module pages and support workflows

This plan treats the current implementation as a starting point, not the final product.

## Current State
- `/help` exists and renders a unified help center.
- Content is generated from static structures in [app/services/help_center.py](/home/dotmac/projects/dotmac_erp/app/services/help_center.py).
- The current help UI in [templates/help_center.html](/home/dotmac/projects/dotmac_erp/templates/help_center.html) is mostly a launcher for module pages.
- Product training does not exist as a first-class system.
- HR training operations under `/people/training/*` are separate from product training/help and should remain separate.

## Principles
- Keep HR training management separate from product education.
- Preserve RBAC and module visibility in all help/training surfaces.
- Default to service-layer content retrieval and thin routes.
- Avoid embedding large help bodies directly in templates.
- Make help content updateable without code deploys once the authoring layer lands.

## Delivery Phases

### Phase 0: Foundation And Direction
Objective: lock architecture, content model, and IA before building more screens.

Tickets:
1. Define the content model for help and training.
2. Define page taxonomy and URL structure.
3. Define authoring workflow and content ownership.
4. Define analytics and success measures.
5. Separate product-training concepts from HR training concepts in naming and navigation.

File targets:
- [app/services/help_center.py](/home/dotmac/projects/dotmac_erp/app/services/help_center.py)
- [app/web_home.py](/home/dotmac/projects/dotmac_erp/app/web_home.py)
- [docs/help_training_implementation_plan.md](/home/dotmac/projects/dotmac_erp/docs/help_training_implementation_plan.md)
- new schema/service files under `app/services/help/` or `app/services/knowledge/`
- optional new models under `app/models/help/`

Acceptance criteria:
- Approved content types and fields exist on paper.
- URL map is documented.
- Decision is made on storage: DB-backed recommended.

### Phase 1: Help Content Domain Model
Objective: introduce first-class help content instead of hardcoded launcher data.

Tickets:
1. Add database models for help articles, playbooks, troubleshooting entries, learning tracks, and track steps.
2. Add Alembic migration(s).
3. Add service layer for content retrieval, filtering, and visibility.
4. Seed initial content from the current hardcoded payload.
5. Add read-only APIs/services for article listing and detail retrieval.

Recommended entities:
- `HelpArticle`
- `HelpCategory`
- `HelpTrack`
- `HelpTrackStep`
- `HelpFeedback`
- `HelpSearchEvent`
- `HelpProgress`

Suggested fields:
- `slug`
- `title`
- `summary`
- `body_markdown` or sanitized rich text
- `content_type`
- `module_key`
- `role_keys`
- `tags`
- `difficulty`
- `estimated_minutes`
- `prerequisites`
- `related_article_ids`
- `status`
- `published_at`
- `last_reviewed_at`

File targets:
- `app/models/help/*.py`
- `alembic/versions/*help*.py`
- `app/services/help/*.py`
- `tests/services/test_help_*.py`

Acceptance criteria:
- Help content is retrievable without relying on hardcoded dicts.
- Role/module filtering is service-driven and tested.
- Tenant-safe analytics/progress tables exist if analytics is DB-backed.

### Phase 2: Real Help Center Information Architecture
Objective: convert `/help` from a launcher into a true help destination.

Tickets:
1. Redesign the `/help` landing page around real content.
2. Add section pages:
   - Getting Started
   - By Module
   - By Role
   - Troubleshooting
   - Training Tracks
   - What’s New
3. Add article detail pages.
4. Add related-content and next-step navigation.
5. Add role-aware featured content.

Routes to add:
- `/help`
- `/help/articles/{slug}`
- `/help/module/{module_key}`
- `/help/role/{role_key}`
- `/help/troubleshooting`
- `/help/tracks`
- `/help/tracks/{slug}`

File targets:
- [app/web_home.py](/home/dotmac/projects/dotmac_erp/app/web_home.py)
- new route module such as `app/web/help.py`
- [templates/help_center.html](/home/dotmac/projects/dotmac_erp/templates/help_center.html)
- new templates under `templates/help/`
- shared macros in `templates/components/macros.html`

Acceptance criteria:
- `/help` shows actual content, not mostly operational links.
- Users can open a full article page and stay oriented inside the help area.
- Each article can show metadata, related articles, and next actions.

### Phase 3: Search And Discovery
Objective: allow users to find answers, not only browse links.

Tickets:
1. Add full-text-ish search over title, summary, body, tags, module, and role.
2. Add ranked search results with snippets.
3. Add filter chips for module, role, type, difficulty.
4. Add no-result recovery suggestions.
5. Track search analytics and zero-result queries.

Routes to add:
- `/help/search`

File targets:
- `app/services/help/search.py`
- `app/web/help.py`
- `templates/help/search_results.html`
- optional search index helpers
- `tests/services/test_help_search.py`

Acceptance criteria:
- Search returns articles and troubleshooting entries.
- Results include matched-context snippets.
- Zero-result events are captured.

### Phase 4: Content Coverage Build-Out
Objective: cover the highest-value workflows with real instructional content.

Tickets:
1. Inventory top workflows per module.
2. Create quick-start guides by role.
3. Create module hub content for Finance, People, Inventory, Procurement, Support, Projects, Expense, Public Sector, Fleet, Self Service.
4. Create cross-module workflow guides:
   - Procure-to-Pay
   - Hire-to-Payroll
   - Expense-to-Reimbursement
   - Order-to-Cash
5. Create troubleshooting content for common errors and access issues.

Content requirements per guide:
- objective
- audience
- prerequisites
- step-by-step flow
- expected result
- common mistakes
- troubleshooting links
- related reports/pages

File targets:
- seeded content files or admin-entered content
- optional markdown import utilities under `scripts/`
- screenshots in `docs/screenshots/`

Acceptance criteria:
- Every major enabled module has a usable hub.
- At least one quick start and one troubleshooting guide exist per major module.
- Cross-module flows exist as real articles.

### Phase 5: Product Training Tracks
Objective: build guided learning experiences distinct from operational HR training.

Tickets:
1. Add “training tracks” for product learning.
2. Add track detail pages with ordered lessons.
3. Add user progress tracking.
4. Add completion indicators and “continue learning”.
5. Add optional quizzes/checkpoints later if needed.

Suggested starter tracks:
- Admin onboarding
- HR operations onboarding
- Finance operations onboarding
- Employee self-service onboarding
- Support desk onboarding

Routes to add:
- `/help/tracks`
- `/help/tracks/{slug}`
- `/help/tracks/{slug}/lesson/{article_slug}`

File targets:
- `app/services/help/tracks.py`
- `templates/help/tracks.html`
- `templates/help/track_detail.html`
- `templates/help/article_detail.html`
- `tests/services/test_help_tracks.py`

Acceptance criteria:
- Users can start a track, resume it, and see progress.
- Role-specific tracks are filtered correctly.

### Phase 6: Contextual In-App Help
Objective: bring help to the workflow instead of forcing users back to `/help`.

Tickets:
1. Add a reusable page-help panel component.
2. Add contextual help links on major module pages.
3. Add field-level help for complex forms.
4. Add “learn this workflow” side panels on critical flows.
5. Add page-to-article mappings by route/module.

Priority pages:
- finance invoice entry
- payroll run pages
- people employee setup
- support tickets dashboard
- procurement requisition/RFQ flows
- inventory transactions

File targets:
- `templates/components/` new help drawer/panel macros
- selected module templates under `templates/finance/`, `templates/people/`, `templates/support/`, `templates/procurement/`, `templates/inventory/`
- `app/services/help/contextual.py`
- tests for route-to-article mapping

Acceptance criteria:
- Critical pages expose relevant help without leaving the workflow.
- Contextual help respects RBAC/module access.

### Phase 7: Troubleshooting System
Objective: turn bullet lists into actionable diagnosis and fix guidance.

Tickets:
1. Add structured troubleshooting article type.
2. Add filters by module and symptom.
3. Add sections for:
   - likely cause
   - diagnosis steps
   - fix steps
   - escalation path
4. Add route-error and validation-message linking where practical.
5. Add permissions/session/access troubleshooting articles.

File targets:
- `app/services/help/troubleshooting.py`
- `templates/help/troubleshooting*.html`
- mappings in service layer for route/module/error relationships

Acceptance criteria:
- Troubleshooting content is more than symptom bullets.
- Users can reach issue-specific content from search and contextual help.

### Phase 8: Support Escalation And Feedback
Objective: connect help outcomes to support when self-service fails.

Tickets:
1. Add article feedback widgets.
2. Add “still need help?” CTA with context.
3. Pre-fill support escalation context from current page/article/search.
4. Track unresolved-help events.
5. Optionally route feedback to admins/content owners.

File targets:
- help feedback models/services
- support integration points in [app/web/support.py](/home/dotmac/projects/dotmac_erp/app/web/support.py)
- help templates

Acceptance criteria:
- Article usefulness can be measured.
- Escalation can carry article/page context.

### Phase 9: Authoring And Content Operations
Objective: let non-developers maintain help content safely.

Tickets:
1. Build admin content management screens.
2. Add draft/review/publish/archive states.
3. Add preview mode.
4. Add link validation and content linting.
5. Add stale-content review workflow.

File targets:
- admin settings/content routes
- `app/services/admin/web.py`
- new admin templates
- `tests/*admin*help*`

Acceptance criteria:
- Content can be updated without code edits.
- Publishing workflow is role-restricted.

### Phase 10: Analytics, QA, And Rollout
Objective: verify adoption and quality after release.

Tickets:
1. Add dashboards for article usage, track completion, and zero-result searches.
2. Add smoke tests for `/help` routes and search.
3. Add e2e coverage for article navigation and progress.
4. Add performance checks for search and article rendering.
5. Roll out by phase with fallback to current `/help`.

File targets:
- analytics services and reporting
- `tests/e2e/`
- route tests in `tests/test_web_routes.py`

Acceptance criteria:
- Help experience is measurable.
- Core user journeys are covered by tests.

## Prioritized Ticket Backlog

### Epic A: Help Domain And Routing
- A1: Add help models and Alembic migration.
- A2: Add help service facade and content retrieval.
- A3: Add dedicated help router.
- A4: Move `/help` rendering out of hardcoded-only mode.
- A5: Add article detail route and template.

### Epic B: Search
- B1: Add search service.
- B2: Add search route and results template.
- B3: Add filters and empty states.
- B4: Add search analytics.

### Epic C: Content Migration
- C1: Convert current `MODULE_GUIDES` into seed content.
- C2: Convert current role playbooks into track/playbook records.
- C3: Add first 20 real guides.
- C4: Add first troubleshooting pack.

### Epic D: Product Training
- D1: Add training track model and progress tracking.
- D2: Add role-specific onboarding tracks.
- D3: Add track pages and resume behavior.

### Epic E: Contextual Help
- E1: Add reusable contextual help panel component.
- E2: Add page mappings for top 10 screens.
- E3: Add inline field help for high-friction forms.

### Epic F: Content Admin
- F1: Add admin list/create/edit/publish UI.
- F2: Add review workflow and validation.
- F3: Add stale-content reporting.

## File-Level Change Targets

### Must Change
- [app/services/help_center.py](/home/dotmac/projects/dotmac_erp/app/services/help_center.py)
  Current hardcoded payload should become fallback/seed source, not the long-term source of truth.
- [app/web_home.py](/home/dotmac/projects/dotmac_erp/app/web_home.py)
  `/help` route should eventually delegate to a dedicated help service/router.
- [templates/help_center.html](/home/dotmac/projects/dotmac_erp/templates/help_center.html)
  Replace launcher layout with richer IA and article-first rendering.

### Likely New
- `app/web/help.py`
- `app/models/help/`
- `app/services/help/`
- `templates/help/`
- `tests/services/test_help_*.py`
- `tests/e2e/test_help_*.py`
- `alembic/versions/*help*.py`

### Should Stay Separate
- [app/web/people/training.py](/home/dotmac/projects/dotmac_erp/app/web/people/training.py)
- [app/services/people/training/training_service.py](/home/dotmac/projects/dotmac_erp/app/services/people/training/training_service.py)

These are HR training operations, not product-learning content.

## Recommended Sequence For Actual Implementation
1. Phase 0 architecture decisions
2. Phase 1 data model and services
3. Phase 2 article pages and IA
4. Phase 3 search
5. Phase 4 content migration and first content wave
6. Phase 5 product training tracks
7. Phase 6 contextual help
8. Phase 8 support escalation and feedback
9. Phase 9 authoring/admin tools
10. Phase 10 analytics and rollout hardening

## Suggested Definition Of Done
- `/help` provides direct answers, not mostly redirects.
- Every major module has a real help hub.
- Role-based onboarding tracks exist for top user types.
- Search returns relevant content with snippets.
- Contextual help is present on key screens.
- Troubleshooting content includes diagnosis and fixes.
- Help content is maintainable without code deployment.
- RBAC/module filtering is enforced and tested.

## Immediate Next Sprint Recommendation
Start with the smallest slice that creates real end-user value:

1. Add dedicated article pages and help router.
2. Keep current hardcoded content as seed/fallback.
3. Convert the top 10 most important workflows into full articles.
4. Add basic search across those articles.
5. Add module hubs for Finance, People, Support, and Procurement.

That gives the team a working help product without waiting for the full CMS/authoring layer.
