import logging
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.models.domain_settings import SettingDomain
from app.services.help_center import (
    GLOSSARY,
    RELEASE_NOTES,
    build_help_experience_payload,
    build_help_module_hub,
    get_help_article_by_slug,
    get_help_track_by_slug,
    search_help_articles,
)
from app.services.settings_spec import resolve_value
from app.templates import templates
from app.web.deps import WebAuthContext, base_context, get_db, require_web_auth

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/help", tags=["help-web"])


def _help_overrides(db: Session) -> dict | None:
    value = resolve_value(db, SettingDomain.settings, "help_center_content_json")
    return value if isinstance(value, dict) else None


def _help_experience(auth: WebAuthContext, db: Session) -> dict:
    """Build the help experience payload and add sidebar tracks."""
    payload = build_help_experience_payload(
        accessible_modules=auth.accessible_modules,
        roles=auth.roles,
        scopes=auth.scopes,
        is_admin=auth.is_admin,
        overrides=_help_overrides(db),
    )
    payload["help_tracks"] = payload.get("tracks", [])
    return payload


# ── Progress & Feedback API (HTMX) ──────────────────────────────────


@router.post("/progress/{slug}", response_class=HTMLResponse)
def toggle_progress(
    request: Request,
    slug: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Toggle article completion. Returns an HTMX snippet for the button."""
    from app.services.help.progress_service import HelpProgressService

    svc = HelpProgressService(db)
    is_completed = svc.toggle_completion(
        organization_id=auth.organization_id,
        person_id=auth.person_id,
        slug=slug,
    )
    db.commit()
    if is_completed:
        return HTMLResponse(
            '<button type="button" hx-post="/help/progress/{slug}" hx-swap="outerHTML" '
            'class="btn btn-secondary btn-sm border-teal-200 text-teal-700 dark:text-teal-300">'
            "Article Completed</button>".replace("{slug}", slug)
        )
    return HTMLResponse(
        '<button type="button" hx-post="/help/progress/{slug}" hx-swap="outerHTML" '
        'class="btn btn-secondary btn-sm">'
        "Mark Article Complete</button>".replace("{slug}", slug)
    )


@router.post("/feedback/{slug}", response_class=HTMLResponse)
def submit_feedback(
    request: Request,
    slug: str,
    rating: str = Form(...),
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Submit article feedback. Returns an HTMX snippet for the buttons."""
    from app.services.help.feedback_service import HelpFeedbackService

    svc = HelpFeedbackService(db)
    try:
        saved_rating = svc.submit_feedback(
            organization_id=auth.organization_id,
            person_id=auth.person_id,
            slug=slug,
            rating=rating,
        )
    except ValueError:
        return HTMLResponse("Invalid rating", status_code=400)
    db.commit()

    helpful_cls = "border-teal-200 text-teal-700 dark:text-teal-300" if saved_rating == "helpful" else ""
    not_helpful_cls = "border-rose-200 text-rose-700 dark:text-rose-300" if saved_rating == "not_helpful" else ""

    html = (
        '<div id="feedback-buttons" class="flex gap-2">'
        '<button type="button" hx-post="/help/feedback/{slug}" hx-target="#feedback-buttons" '
        'hx-swap="outerHTML" hx-vals=\'{{ "rating": "helpful" }}\' '
        'class="btn btn-secondary btn-sm {helpful_cls}">Helpful</button>'
        '<button type="button" hx-post="/help/feedback/{slug}" hx-target="#feedback-buttons" '
        'hx-swap="outerHTML" hx-vals=\'{{ "rating": "not_helpful" }}\' '
        'class="btn btn-secondary btn-sm {not_helpful_cls}">Not Helpful</button>'
        "</div>"
    ).replace("{slug}", slug).replace("{helpful_cls}", helpful_cls).replace("{not_helpful_cls}", not_helpful_cls)
    return HTMLResponse(html)


@router.get("/api/progress", response_class=JSONResponse)
def get_progress(
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Return user's completed article slugs and feedback map as JSON."""
    from app.services.help.feedback_service import HelpFeedbackService
    from app.services.help.progress_service import HelpProgressService

    progress_svc = HelpProgressService(db)
    feedback_svc = HelpFeedbackService(db)

    completed = progress_svc.get_completed_slugs(
        organization_id=auth.organization_id,
        person_id=auth.person_id,
    )
    feedback_map = feedback_svc.get_user_feedback_map(
        organization_id=auth.organization_id,
        person_id=auth.person_id,
    )
    return {"completed": completed, "feedback": feedback_map}


# ── Page Routes ──────────────────────────────────────────────────────


@router.get("/search", response_class=HTMLResponse)
def search_page(
    request: Request,
    q: str | None = Query(default=None),
    module: str | None = Query(default=None),
    content_type: str | None = Query(default=None),
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    context = base_context(request, auth, "Help Search", "help", db=db)
    search_results = search_help_articles(
        accessible_modules=auth.accessible_modules,
        roles=auth.roles,
        scopes=auth.scopes,
        is_admin=auth.is_admin,
        query=q,
        module_key=module,
        content_type=content_type,
        overrides=_help_overrides(db),
    )
    context.update(search_results)
    context["help_tracks"] = search_results.get("tracks", [])

    # Record search analytics
    if q and q.strip():
        try:
            from app.services.help.search_analytics_service import (
                HelpSearchAnalyticsService,
            )

            analytics = HelpSearchAnalyticsService(db)
            filters_dict = {}
            if module:
                filters_dict["module"] = module
            if content_type:
                filters_dict["content_type"] = content_type
            analytics.record_search(
                organization_id=auth.organization_id,
                query=q,
                result_count=len(search_results.get("articles", [])),
                person_id=auth.person_id,
                filters=filters_dict if filters_dict else None,
            )
            db.commit()
        except Exception:
            logger.exception("Failed to record search analytics")

    return templates.TemplateResponse(request, "help/search_results.html", context)


@router.get("/articles/{slug}", response_class=HTMLResponse)
def article_detail(
    request: Request,
    slug: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    article = get_help_article_by_slug(
        accessible_modules=auth.accessible_modules,
        roles=auth.roles,
        scopes=auth.scopes,
        is_admin=auth.is_admin,
        slug=slug,
        overrides=_help_overrides(db),
    )
    if not article:
        return RedirectResponse(url="/help?error=Article+not+found", status_code=303)

    payload = _help_experience(auth, db)
    all_articles = payload["articles"]
    module_articles = [
        item for item in all_articles if item["module_key"] == article["module_key"]
    ]
    related_articles = [
        item for item in module_articles if item["slug"] != article["slug"]
    ][:4]

    # Previous/next navigation within the same module
    prev_article = None
    next_article = None
    for idx, item in enumerate(module_articles):
        if item["slug"] == article["slug"]:
            if idx > 0:
                prev_article = module_articles[idx - 1]
            if idx < len(module_articles) - 1:
                next_article = module_articles[idx + 1]
            break

    # Load server-side progress/feedback for this article
    from app.services.help.feedback_service import HelpFeedbackService
    from app.services.help.progress_service import HelpProgressService

    progress_svc = HelpProgressService(db)
    feedback_svc = HelpFeedbackService(db)

    article_completed = progress_svc.is_completed(
        organization_id=auth.organization_id,
        person_id=auth.person_id,
        slug=slug,
    )
    article_feedback = feedback_svc.get_user_feedback(
        organization_id=auth.organization_id,
        person_id=auth.person_id,
        slug=slug,
    )

    context = base_context(request, auth, article["title"], "help", db=db)
    context.update(payload)
    context.update(
        {
            "article": article,
            "related_articles": related_articles,
            "prev_article": prev_article,
            "next_article": next_article,
            "article_completed": article_completed,
            "article_feedback": article_feedback or "",
        }
    )
    return templates.TemplateResponse(request, "help/article_detail.html", context)


@router.get("/module/{module_key}", response_class=HTMLResponse)
def module_hub(
    request: Request,
    module_key: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    payload = build_help_module_hub(
        accessible_modules=auth.accessible_modules,
        roles=auth.roles,
        scopes=auth.scopes,
        is_admin=auth.is_admin,
        module_key=module_key,
        overrides=_help_overrides(db),
    )
    if not payload:
        return RedirectResponse(url="/help?error=Module+help+not+available", status_code=303)

    context = base_context(
        request,
        auth,
        f"{payload['module_title']} Help",
        "help",
        db=db,
    )
    context.update(payload)
    context["help_tracks"] = payload.get("tracks", [])
    return templates.TemplateResponse(request, "help/module_hub.html", context)


@router.get("/tracks", response_class=HTMLResponse)
def tracks_page(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    from app.services.help.progress_service import HelpProgressService

    context = base_context(request, auth, "Training Tracks", "help", db=db)
    payload = _help_experience(auth, db)
    context.update(payload)

    progress_svc = HelpProgressService(db)
    completed_slugs = set(
        progress_svc.get_completed_slugs(
            organization_id=auth.organization_id,
            person_id=auth.person_id,
        )
    )
    context["completed_slugs"] = completed_slugs
    return templates.TemplateResponse(request, "help/tracks.html", context)


@router.get("/tracks/{slug}", response_class=HTMLResponse)
def track_detail(
    request: Request,
    slug: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    from app.services.help.progress_service import HelpProgressService

    track = get_help_track_by_slug(
        accessible_modules=auth.accessible_modules,
        roles=auth.roles,
        scopes=auth.scopes,
        is_admin=auth.is_admin,
        slug=slug,
        overrides=_help_overrides(db),
    )
    if not track:
        return RedirectResponse(url="/help/tracks?error=Track+not+found", status_code=303)

    progress_svc = HelpProgressService(db)
    completed_slugs = set(
        progress_svc.get_completed_slugs(
            organization_id=auth.organization_id,
            person_id=auth.person_id,
        )
    )

    context = base_context(request, auth, track["title"], "help", db=db)
    payload = _help_experience(auth, db)
    context.update(payload)
    context["track"] = track
    context["completed_slugs"] = completed_slugs
    return templates.TemplateResponse(request, "help/track_detail.html", context)


@router.get("/glossary", response_class=HTMLResponse)
def glossary_page(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    context = base_context(request, auth, "Glossary", "help", db=db)
    payload = _help_experience(auth, db)
    context.update(payload)
    context["glossary_terms"] = GLOSSARY
    context["glossary_categories"] = sorted({term["category"] for term in GLOSSARY})
    return templates.TemplateResponse(request, "help/glossary.html", context)


@router.get("/release-notes", response_class=HTMLResponse)
def release_notes_page(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    context = base_context(request, auth, "Release Notes", "help", db=db)
    payload = _help_experience(auth, db)
    context.update(payload)
    context["release_notes"] = RELEASE_NOTES
    return templates.TemplateResponse(request, "help/release_notes.html", context)


# ── Admin Routes ─────────────────────────────────────────────────────


def _require_admin(auth: WebAuthContext) -> None:
    """Guard for admin-only help routes."""
    if not auth.is_admin:
        from fastapi import HTTPException

        raise HTTPException(status_code=403, detail="Admin access required")


@router.get("/admin/articles", response_class=HTMLResponse)
def admin_article_list(
    request: Request,
    search: str | None = Query(default=None),
    status: str | None = Query(default=None),
    module: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    _require_admin(auth)
    from app.services.help.admin_web import HelpAdminWebService

    ws = HelpAdminWebService(db)
    ctx = ws.list_articles_context(
        organization_id=auth.organization_id,
        status=status,
        module_key=module,
        search=search,
        page=page,
    )
    context = base_context(request, auth, "Manage Help Articles", "help", db=db)
    payload = _help_experience(auth, db)
    context.update(payload)
    context.update(ctx)
    return templates.TemplateResponse(request, "help/admin/article_list.html", context)


@router.get("/admin/articles/new", response_class=HTMLResponse)
def admin_article_new(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    _require_admin(auth)
    from app.services.help.admin_web import HelpAdminWebService

    ws = HelpAdminWebService(db)
    ctx = ws.article_form_context(organization_id=auth.organization_id)
    context = base_context(request, auth, "New Help Article", "help", db=db)
    payload = _help_experience(auth, db)
    context.update(payload)
    context.update(ctx)
    return templates.TemplateResponse(request, "help/admin/article_form.html", context)


@router.post("/admin/articles/new", response_class=HTMLResponse)
def admin_article_create(
    request: Request,
    title: str = Form(...),
    slug: str = Form(...),
    module_key: str = Form(...),
    content_type: str = Form("workflow"),
    summary: str = Form(""),
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    _require_admin(auth)
    from app.services.help.admin_web import HelpAdminWebService

    ws = HelpAdminWebService(db)
    data = {
        "title": title,
        "slug": slug,
        "module_key": module_key,
        "content_type": content_type,
        "summary": summary,
    }
    ws.create_article(organization_id=auth.organization_id, data=data)
    db.commit()
    return RedirectResponse(url="/help/admin/articles?saved=1", status_code=303)


@router.get("/admin/articles/{article_id}/edit", response_class=HTMLResponse)
def admin_article_edit(
    request: Request,
    article_id: UUID,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    _require_admin(auth)
    from app.services.help.admin_web import HelpAdminWebService

    ws = HelpAdminWebService(db)
    ctx = ws.article_form_context(
        organization_id=auth.organization_id, article_id=article_id
    )
    if not ctx["article"]:
        return RedirectResponse(
            url="/help/admin/articles?error=Article+not+found", status_code=303
        )
    context = base_context(request, auth, "Edit Help Article", "help", db=db)
    payload = _help_experience(auth, db)
    context.update(payload)
    context.update(ctx)
    return templates.TemplateResponse(request, "help/admin/article_form.html", context)


@router.post("/admin/articles/{article_id}/edit", response_class=HTMLResponse)
def admin_article_update(
    request: Request,
    article_id: UUID,
    title: str = Form(...),
    slug: str = Form(...),
    module_key: str = Form(...),
    content_type: str = Form("workflow"),
    summary: str = Form(""),
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    _require_admin(auth)
    from app.services.help.admin_web import HelpAdminWebService

    ws = HelpAdminWebService(db)
    data = {
        "title": title,
        "slug": slug,
        "module_key": module_key,
        "content_type": content_type,
        "summary": summary,
    }
    result = ws.update_article(
        organization_id=auth.organization_id, article_id=article_id, data=data
    )
    if not result:
        return RedirectResponse(
            url="/help/admin/articles?error=Article+not+found", status_code=303
        )
    db.commit()
    return RedirectResponse(url="/help/admin/articles?saved=1", status_code=303)


@router.post("/admin/articles/{article_id}/publish")
def admin_article_publish(
    request: Request,
    article_id: UUID,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    _require_admin(auth)
    from app.services.help.admin_web import HelpAdminWebService

    ws = HelpAdminWebService(db)
    result = ws.publish_article(
        organization_id=auth.organization_id, article_id=article_id
    )
    if not result:
        return RedirectResponse(
            url="/help/admin/articles?error=Article+not+found", status_code=303
        )
    db.commit()
    return RedirectResponse(url="/help/admin/articles?saved=1", status_code=303)


@router.post("/admin/articles/{article_id}/archive")
def admin_article_archive(
    request: Request,
    article_id: UUID,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    _require_admin(auth)
    from app.services.help.admin_web import HelpAdminWebService

    ws = HelpAdminWebService(db)
    result = ws.archive_article(
        organization_id=auth.organization_id, article_id=article_id
    )
    if not result:
        return RedirectResponse(
            url="/help/admin/articles?error=Article+not+found", status_code=303
        )
    db.commit()
    return RedirectResponse(url="/help/admin/articles?saved=1", status_code=303)


@router.get("/admin/dashboard", response_class=HTMLResponse)
def admin_dashboard(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    _require_admin(auth)
    from app.services.help.admin_web import HelpAdminWebService

    ws = HelpAdminWebService(db)
    ctx = ws.content_health_context(organization_id=auth.organization_id)
    context = base_context(request, auth, "Help Content Health", "help", db=db)
    payload = _help_experience(auth, db)
    context.update(payload)
    context.update(ctx)
    return templates.TemplateResponse(request, "help/admin/dashboard.html", context)
