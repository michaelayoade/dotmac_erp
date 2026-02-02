"""
Admin web routes.

Provides admin dashboard and management pages with admin role requirement.
"""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.services.admin.settings_web import admin_settings_web_service
from app.services.admin.web import admin_web_service
from app.templates import templates
from app.web.deps import get_db, optional_web_auth, org_brand_context, WebAuthContext


router = APIRouter(prefix="/admin", tags=["admin-web"])


def _normalize_form(form: Any) -> dict[str, str]:
    if form is None:
        return {}
    return {key: value if isinstance(value, str) else "" for key, value in form.items()}


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def admin_dashboard(
    request: Request,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Admin dashboard page."""
    return admin_web_service.dashboard_response(request, db, auth)


@router.get("/users", response_class=HTMLResponse)
def admin_users(
    request: Request,
    page: int = Query(default=1, ge=1),
    search: str = Query(default=""),
    status: str = Query(default=""),
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Admin users list page."""
    return admin_web_service.users_response(request, db, auth, page, search, status)


@router.get("/users/new", response_class=HTMLResponse)
def admin_users_new(
    request: Request,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Show create user form."""
    return admin_web_service.users_new_response(request, db, auth)


@router.post("/users/new", response_class=HTMLResponse)
async def admin_users_create(
    request: Request,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Handle create user form submission."""
    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()
    roles = form.getlist("roles") if hasattr(form, "getlist") else []
    form = _normalize_form(form)

    first_name = (form.get("first_name") or "").strip()
    last_name = (form.get("last_name") or "").strip()
    email = (form.get("email") or "").strip()
    username = (form.get("username") or "").strip()
    organization_id = (form.get("organization_id") or "").strip()
    password = form.get("password") or ""
    password_confirm = form.get("password_confirm") or ""
    display_name = (form.get("display_name") or "").strip()
    phone = (form.get("phone") or "").strip()
    status = (form.get("status") or "active").strip()
    must_change_password = form.get("must_change_password") or ""

    return admin_web_service.users_create_response(
        request,
        db,
        auth,
        first_name,
        last_name,
        email,
        username,
        organization_id,
        password,
        password_confirm,
        display_name,
        phone,
        status,
        must_change_password,
        roles,
    )


@router.get("/users/{user_id}", response_class=HTMLResponse)
def admin_users_view(
    request: Request,
    user_id: str,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """View user details (same as edit)."""
    return admin_web_service.users_view_response(request, db, auth, user_id)


@router.get("/users/{user_id}/edit", response_class=HTMLResponse)
def admin_users_edit(
    request: Request,
    user_id: str,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Show edit user form."""
    return admin_web_service.users_edit_response(request, db, auth, user_id)


@router.post("/users/{user_id}/edit", response_class=HTMLResponse)
async def admin_users_update(
    request: Request,
    user_id: str,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Handle edit user form submission."""
    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()
    roles = form.getlist("roles") if hasattr(form, "getlist") else []
    form = _normalize_form(form)

    first_name = (form.get("first_name") or "").strip()
    last_name = (form.get("last_name") or "").strip()
    email = (form.get("email") or "").strip()
    username = (form.get("username") or "").strip()
    organization_id = (form.get("organization_id") or "").strip()
    password = form.get("password") or ""
    password_confirm = form.get("password_confirm") or ""
    display_name = (form.get("display_name") or "").strip()
    phone = (form.get("phone") or "").strip()
    status = (form.get("status") or "active").strip()
    must_change_password = form.get("must_change_password") or ""
    email_verified = form.get("email_verified") or ""

    return admin_web_service.users_update_response(
        request,
        db,
        auth,
        user_id,
        first_name,
        last_name,
        email,
        username,
        organization_id,
        password,
        password_confirm,
        display_name,
        phone,
        status,
        must_change_password,
        email_verified,
        roles,
    )


@router.post("/users/{user_id}/delete")
def admin_users_delete(
    request: Request,
    user_id: str,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Delete a user."""
    return admin_web_service.users_delete_response(request, db, auth, user_id)


@router.get("/roles", response_class=HTMLResponse)
def admin_roles(
    request: Request,
    page: int = Query(default=1, ge=1),
    search: str = Query(default=""),
    status: str = Query(default=""),
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Admin roles management page."""
    return admin_web_service.roles_response(request, db, auth, page, search, status)


@router.get("/roles/new", response_class=HTMLResponse)
def admin_roles_new(
    request: Request,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Show create role form."""
    return admin_web_service.roles_new_response(request, db, auth)


@router.post("/roles/new", response_class=HTMLResponse)
async def admin_roles_create(
    request: Request,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Handle create role form submission."""
    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()
    form = _normalize_form(form)

    name = (form.get("name") or "").strip()
    description = (form.get("description") or "").strip()
    is_active = form.get("is_active") or ""
    permissions = form.getlist("permissions") if hasattr(form, "getlist") else []

    return admin_web_service.roles_create_response(
        request,
        db,
        auth,
        name,
        description,
        is_active,
        permissions,
    )


@router.get("/roles/{role_id}", response_class=HTMLResponse)
def admin_roles_view(
    request: Request,
    role_id: str,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """View role details (same as edit)."""
    return admin_web_service.roles_view_response(request, db, auth, role_id)


@router.get("/roles/{role_id}/edit", response_class=HTMLResponse)
def admin_roles_edit(
    request: Request,
    role_id: str,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Show edit role form."""
    return admin_web_service.roles_edit_response(request, db, auth, role_id)


@router.post("/roles/{role_id}/edit", response_class=HTMLResponse)
async def admin_roles_update(
    request: Request,
    role_id: str,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Handle edit role form submission."""
    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()
    form = _normalize_form(form)

    name = (form.get("name") or "").strip()
    description = (form.get("description") or "").strip()
    is_active = form.get("is_active") or ""
    permissions = form.getlist("permissions") if hasattr(form, "getlist") else []

    return admin_web_service.roles_update_response(
        request,
        db,
        auth,
        role_id,
        name,
        description,
        is_active,
        permissions,
    )


@router.post("/roles/{role_id}/delete")
def admin_roles_delete(
    request: Request,
    role_id: str,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Delete a role."""
    return admin_web_service.roles_delete_response(request, db, auth, role_id)


@router.get("/permissions", response_class=HTMLResponse)
def admin_permissions(
    request: Request,
    page: int = Query(default=1, ge=1),
    search: str = Query(default=""),
    status: str = Query(default=""),
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Admin permissions management page."""
    return admin_web_service.permissions_response(request, db, auth, page, search, status)


@router.get("/permissions/new", response_class=HTMLResponse)
def admin_permissions_new(
    request: Request,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Show create permission form."""
    return admin_web_service.permissions_new_response(request, db, auth)


@router.post("/permissions/new", response_class=HTMLResponse)
async def admin_permissions_create(
    request: Request,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Handle create permission form submission."""
    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()
    form = _normalize_form(form)
    request.state.csrf_form = form

    key = (form.get("key") or "").strip()
    description = (form.get("description") or "").strip()
    is_active = form.get("is_active") or ""

    return admin_web_service.permissions_create_response(
        request,
        db,
        auth,
        key,
        description,
        is_active,
    )


@router.get("/permissions/{permission_id}", response_class=HTMLResponse)
def admin_permissions_view(
    request: Request,
    permission_id: str,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """View permission details (same as edit)."""
    return admin_web_service.permissions_view_response(request, db, auth, permission_id)


@router.get("/permissions/{permission_id}/edit", response_class=HTMLResponse)
def admin_permissions_edit(
    request: Request,
    permission_id: str,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Show edit permission form."""
    return admin_web_service.permissions_edit_response(request, db, auth, permission_id)


@router.post("/permissions/{permission_id}/edit", response_class=HTMLResponse)
async def admin_permissions_update(
    request: Request,
    permission_id: str,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Handle edit permission form submission."""
    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()
    form = _normalize_form(form)
    request.state.csrf_form = form

    key = (form.get("key") or "").strip()
    description = (form.get("description") or "").strip()
    is_active = form.get("is_active") or ""

    return admin_web_service.permissions_update_response(
        request,
        db,
        auth,
        permission_id,
        key,
        description,
        is_active,
    )


@router.post("/permissions/{permission_id}/delete")
def admin_permissions_delete(
    request: Request,
    permission_id: str,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Delete a permission."""
    return admin_web_service.permissions_delete_response(request, db, auth, permission_id)


@router.get("/organizations", response_class=HTMLResponse)
def admin_organizations(
    request: Request,
    page: int = Query(default=1, ge=1),
    search: str = Query(default=""),
    status: str = Query(default=""),
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Admin organizations management page."""
    return admin_web_service.organizations_response(request, db, auth, page, search, status)


@router.get("/organizations/new", response_class=HTMLResponse)
def admin_organizations_new(
    request: Request,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Show create organization form."""
    return admin_web_service.organizations_new_response(request, db, auth)


@router.post("/organizations/new", response_class=HTMLResponse)
def admin_organizations_create(
    request: Request,
    organization_code: str = Form(...),
    legal_name: str = Form(...),
    functional_currency_code: str = Form(...),
    presentation_currency_code: str = Form(...),
    fiscal_year_end_month: int = Form(...),
    fiscal_year_end_day: int = Form(...),
    trading_name: str = Form(default=""),
    registration_number: str = Form(default=""),
    tax_identification_number: str = Form(default=""),
    incorporation_date: str = Form(default=""),
    jurisdiction_country_code: str = Form(default=""),
    parent_organization_id: str = Form(default=""),
    consolidation_method: str = Form(default=""),
    ownership_percentage: str = Form(default=""),
    is_active: str = Form(default=""),
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Handle create organization form submission."""
    return admin_web_service.organizations_create_response(
        request,
        db,
        auth,
        organization_code,
        legal_name,
        functional_currency_code,
        presentation_currency_code,
        fiscal_year_end_month,
        fiscal_year_end_day,
        trading_name,
        registration_number,
        tax_identification_number,
        incorporation_date,
        jurisdiction_country_code,
        parent_organization_id,
        consolidation_method,
        ownership_percentage,
        is_active,
    )


@router.get("/organizations/{org_id}", response_class=HTMLResponse)
def admin_organizations_view(
    request: Request,
    org_id: str,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """View organization details (same as edit)."""
    return admin_web_service.organizations_view_response(request, db, auth, org_id)


@router.get("/organizations/{org_id}/edit", response_class=HTMLResponse)
def admin_organizations_edit(
    request: Request,
    org_id: str,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Show edit organization form."""
    return admin_web_service.organizations_edit_response(request, db, auth, org_id)


@router.post("/organizations/{org_id}/edit", response_class=HTMLResponse)
def admin_organizations_update(
    request: Request,
    org_id: str,
    organization_code: str = Form(...),
    legal_name: str = Form(...),
    functional_currency_code: str = Form(...),
    presentation_currency_code: str = Form(...),
    fiscal_year_end_month: int = Form(...),
    fiscal_year_end_day: int = Form(...),
    trading_name: str = Form(default=""),
    registration_number: str = Form(default=""),
    tax_identification_number: str = Form(default=""),
    incorporation_date: str = Form(default=""),
    jurisdiction_country_code: str = Form(default=""),
    parent_organization_id: str = Form(default=""),
    consolidation_method: str = Form(default=""),
    ownership_percentage: str = Form(default=""),
    is_active: str = Form(default=""),
    salaries_expense_account_id: str = Form(default=""),
    salary_payable_account_id: str = Form(default=""),
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Handle edit organization form submission."""
    return admin_web_service.organizations_update_response(
        request,
        db,
        auth,
        org_id,
        organization_code,
        legal_name,
        functional_currency_code,
        presentation_currency_code,
        fiscal_year_end_month,
        fiscal_year_end_day,
        trading_name,
        registration_number,
        tax_identification_number,
        incorporation_date,
        jurisdiction_country_code,
        parent_organization_id,
        consolidation_method,
        ownership_percentage,
        is_active,
        salaries_expense_account_id,
        salary_payable_account_id,
    )


@router.post("/organizations/{org_id}/delete")
def admin_organizations_delete(
    request: Request,
    org_id: str,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Delete an organization."""
    return admin_web_service.organizations_delete_response(request, db, auth, org_id)


@router.get("/settings", response_class=HTMLResponse)
def admin_settings(
    request: Request,
    page: int = Query(default=1, ge=1),
    search: str = Query(default=""),
    status: str = Query(default=""),
    domain: str = Query(default=""),
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Admin system settings page."""
    return admin_web_service.settings_response(request, db, auth, page, search, status, domain)


@router.get("/settings/new", response_class=HTMLResponse)
def admin_settings_new(
    request: Request,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Show create setting form."""
    return admin_web_service.settings_new_response(request, db, auth)


@router.post("/settings/new", response_class=HTMLResponse)
async def admin_settings_create(
    request: Request,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Handle create setting form submission."""
    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()
    form = _normalize_form(form)

    domain = (form.get("domain") or "").strip()
    key = (form.get("key") or "").strip()
    value_type = (form.get("value_type") or "").strip()
    value = form.get("value") or ""
    is_secret = form.get("is_secret") or ""
    is_active = form.get("is_active") or ""
    if not (domain and key and value_type):
        content_type = (request.headers.get("content-type") or "").lower()
        if content_type.startswith("application/json"):
            try:
                payload = await request.json()
            except Exception:
                payload = {}
            if isinstance(payload, dict):
                domain = domain or str(payload.get("domain") or "").strip()
                key = key or str(payload.get("key") or "").strip()
                value_type = value_type or str(payload.get("value_type") or "").strip()
                if not value:
                    value = payload.get("value") or ""
                if not is_secret:
                    is_secret = payload.get("is_secret") or ""
                if not is_active:
                    is_active = payload.get("is_active") or ""

    return admin_web_service.settings_create_response(
        request,
        db,
        auth,
        domain,
        key,
        value_type,
        value,
        is_secret,
        is_active,
    )




@router.get("/audit-logs", response_class=HTMLResponse)
def admin_audit_logs(
    request: Request,
    page: int = Query(default=1, ge=1),
    search: str = Query(default=""),
    status: str = Query(default=""),
    actor_type: str = Query(default=""),
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Admin audit logs page."""
    return admin_web_service.audit_logs_response(
        request,
        db,
        auth,
        page,
        search,
        status,
        actor_type,
    )


@router.get("/tasks", response_class=HTMLResponse)
def admin_tasks(
    request: Request,
    page: int = Query(default=1, ge=1),
    search: str = Query(default=""),
    status: str = Query(default=""),
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Admin scheduled tasks page."""
    return admin_web_service.tasks_response(request, db, auth, page, search, status)


@router.get("/tasks/new", response_class=HTMLResponse)
def admin_tasks_new(
    request: Request,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Show create task form."""
    return admin_web_service.tasks_new_response(request, db, auth)


@router.post("/tasks/new", response_class=HTMLResponse)
def admin_tasks_create(
    request: Request,
    name: str = Form(...),
    task_name: str = Form(...),
    schedule_type: str = Form(...),
    interval_seconds: int = Form(...),
    args_json: str = Form(default=""),
    kwargs_json: str = Form(default=""),
    enabled: str = Form(default=""),
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Handle create task form submission."""
    return admin_web_service.tasks_create_response(
        request,
        db,
        auth,
        name,
        task_name,
        schedule_type,
        interval_seconds,
        args_json,
        kwargs_json,
        enabled,
    )


@router.get("/tasks/{task_id}", response_class=HTMLResponse)
def admin_tasks_view(
    request: Request,
    task_id: str,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """View task details (same as edit)."""
    return admin_web_service.tasks_view_response(request, db, auth, task_id)


@router.get("/tasks/{task_id}/edit", response_class=HTMLResponse)
def admin_tasks_edit(
    request: Request,
    task_id: str,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Show edit task form."""
    return admin_web_service.tasks_edit_response(request, db, auth, task_id)


@router.post("/tasks/{task_id}/edit", response_class=HTMLResponse)
def admin_tasks_update(
    request: Request,
    task_id: str,
    name: str = Form(...),
    task_name: str = Form(...),
    schedule_type: str = Form(...),
    interval_seconds: int = Form(...),
    args_json: str = Form(default=""),
    kwargs_json: str = Form(default=""),
    enabled: str = Form(default=""),
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Handle edit task form submission."""
    return admin_web_service.tasks_update_response(
        request,
        db,
        auth,
        task_id,
        name,
        task_name,
        schedule_type,
        interval_seconds,
        args_json,
        kwargs_json,
        enabled,
    )


@router.post("/tasks/{task_id}/delete")
def admin_tasks_delete(
    request: Request,
    task_id: str,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Delete a task."""
    return admin_web_service.tasks_delete_response(request, db, auth, task_id)


# ========== Settings Hub ==========


def _admin_base_context(request: Request, auth: WebAuthContext, page_title: str, db: Session) -> dict:
    """Build base context for admin settings pages."""
    context = {
        "request": request,
        "user": {"name": "Admin", "initials": "AD"} if auth else {},
        "page_title": page_title,
        "active_page": "settings",
        "brand": org_brand_context(db, auth.organization_id if auth else None),
    }
    return context


@router.get("/settings/hub", response_class=HTMLResponse)
def admin_settings_hub(
    request: Request,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Admin settings hub page."""
    context = _admin_base_context(request, auth, "Settings", db)
    context.update(admin_settings_web_service.get_hub_context(
        auth.organization_id if auth else None
    ))
    return templates.TemplateResponse(request, "admin/settings/index.html", context)


@router.get("/settings/organization", response_class=HTMLResponse)
def admin_settings_organization(
    request: Request,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Organization profile settings page."""
    context = _admin_base_context(request, auth, "Organization Profile", db)
    if auth and auth.organization_id:
        context.update(admin_settings_web_service.get_organization_context(db, auth.organization_id))
    return templates.TemplateResponse(request, "admin/settings/organization.html", context)


@router.post("/settings/organization", response_class=HTMLResponse)
async def admin_settings_organization_update(
    request: Request,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Update organization profile."""
    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()
    data = _normalize_form(form)

    if auth and auth.organization_id:
        success, error = admin_settings_web_service.update_organization(db, auth.organization_id, data)
        if not success:
            context = _admin_base_context(request, auth, "Organization Profile", db)
            context.update(admin_settings_web_service.get_organization_context(db, auth.organization_id))
            context["error"] = error
            return templates.TemplateResponse(request, "admin/settings/organization.html", context)

    return RedirectResponse(url="/admin/settings/organization?saved=1", status_code=303)


@router.get("/settings/branding", response_class=HTMLResponse)
def admin_settings_branding(
    request: Request,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Branding settings page."""
    context = _admin_base_context(request, auth, "Branding", db)
    if auth and auth.organization_id:
        context.update(admin_settings_web_service.get_branding_context(db, auth.organization_id))
    return templates.TemplateResponse(request, "admin/settings/branding.html", context)


@router.post("/settings/branding", response_class=HTMLResponse)
async def admin_settings_branding_update(
    request: Request,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Update branding settings."""
    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()
    data = dict(form)

    if auth and auth.organization_id:
        success, error = admin_settings_web_service.update_branding(db, auth.organization_id, data)
        if not success:
            context = _admin_base_context(request, auth, "Branding", db)
            context.update(admin_settings_web_service.get_branding_context(db, auth.organization_id))
            context["error"] = error
            return templates.TemplateResponse(request, "admin/settings/branding.html", context)

    return RedirectResponse(url="/admin/settings/branding?saved=1", status_code=303)


@router.get("/settings/email", response_class=HTMLResponse)
def admin_settings_email(
    request: Request,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Email configuration page."""
    context = _admin_base_context(request, auth, "Email Configuration", db)
    if auth and auth.organization_id:
        context.update(admin_settings_web_service.get_email_context(db, auth.organization_id))
    return templates.TemplateResponse(request, "admin/settings/email.html", context)


@router.post("/settings/email", response_class=HTMLResponse)
async def admin_settings_email_update(
    request: Request,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Update email settings."""
    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()
    data = dict(form)

    if auth and auth.organization_id:
        success, error = admin_settings_web_service.update_email(db, auth.organization_id, data)
        if not success:
            context = _admin_base_context(request, auth, "Email Configuration", db)
            context.update(admin_settings_web_service.get_email_context(db, auth.organization_id))
            context["error"] = error
            return templates.TemplateResponse(request, "admin/settings/email.html", context)

    return RedirectResponse(url="/admin/settings/email?saved=1", status_code=303)


@router.get("/settings/features", response_class=HTMLResponse)
def admin_settings_features(
    request: Request,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Feature flags page."""
    context = _admin_base_context(request, auth, "Feature Flags", db)
    if auth and auth.organization_id:
        context.update(admin_settings_web_service.get_features_context(db, auth.organization_id))
    return templates.TemplateResponse(request, "admin/settings/features.html", context)


@router.post("/settings/features/{feature_key}/toggle", response_class=HTMLResponse)
async def admin_settings_feature_toggle(
    request: Request,
    feature_key: str,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Toggle a feature flag."""
    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()
    enabled = str(form.get("enabled", "false")).lower() == "true"

    if auth and auth.organization_id:
        success, error = admin_settings_web_service.toggle_feature(
            db, auth.organization_id, feature_key, enabled
        )
        if not success:
            context = _admin_base_context(request, auth, "Feature Flags", db)
            context.update(admin_settings_web_service.get_features_context(db, auth.organization_id))
            context["error"] = error
            return templates.TemplateResponse(request, "admin/settings/features.html", context)

    return RedirectResponse(url="/admin/settings/features?saved=1", status_code=303)


@router.get("/settings/payments", response_class=HTMLResponse)
def admin_settings_payments(
    request: Request,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Payments settings hub page."""
    context = _admin_base_context(request, auth, "Payment Providers", db)
    if auth and auth.organization_id:
        context.update(admin_settings_web_service.get_payments_hub_context(db, auth.organization_id))
    return templates.TemplateResponse(request, "admin/settings/payments_index.html", context)


@router.get("/settings/payments/paystack", response_class=HTMLResponse)
def admin_settings_paystack(
    request: Request,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Paystack settings page."""
    context = _admin_base_context(request, auth, "Paystack Settings", db)
    if auth and auth.organization_id:
        context.update(admin_settings_web_service.get_paystack_context(db, auth.organization_id))
    return templates.TemplateResponse(request, "admin/settings/paystack.html", context)


@router.post("/settings/payments/paystack", response_class=HTMLResponse)
async def admin_settings_paystack_update(
    request: Request,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Update Paystack settings."""
    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()
    data = dict(form)

    if auth and auth.organization_id:
        success, error = admin_settings_web_service.update_paystack(db, auth.organization_id, data)
        if not success:
            context = _admin_base_context(request, auth, "Paystack Settings", db)
            context.update(admin_settings_web_service.get_paystack_context(db, auth.organization_id))
            context["error"] = error
            return templates.TemplateResponse(request, "admin/settings/paystack.html", context)

    return RedirectResponse(url="/admin/settings/payments/paystack?saved=1", status_code=303)


@router.get("/settings/advanced", response_class=HTMLResponse)
def admin_settings_advanced(
    request: Request,
    page: int = Query(default=1, ge=1),
    search: str = Query(default=""),
    status: str = Query(default=""),
    domain: str = Query(default=""),
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Advanced settings (raw DomainSettings CRUD)."""
    return admin_web_service.settings_response(request, db, auth, page, search, status, domain)


@router.get("/settings/{setting_id}", response_class=HTMLResponse)
def admin_settings_view(
    request: Request,
    setting_id: UUID,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """View setting details (same as edit)."""
    return admin_web_service.settings_view_response(request, db, auth, str(setting_id))


@router.get("/settings/{setting_id}/edit", response_class=HTMLResponse)
def admin_settings_edit(
    request: Request,
    setting_id: UUID,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Show edit setting form."""
    return admin_web_service.settings_edit_response(request, db, auth, str(setting_id))


@router.post("/settings/{setting_id}/edit", response_class=HTMLResponse)
async def admin_settings_update(
    request: Request,
    setting_id: UUID,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Handle edit setting form submission."""
    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()
    form = _normalize_form(form)

    domain = (form.get("domain") or "").strip()
    key = (form.get("key") or "").strip()
    value_type = (form.get("value_type") or "").strip()
    value = form.get("value") or ""
    is_secret = form.get("is_secret") or ""
    is_active = form.get("is_active") or ""
    if not (domain and key and value_type):
        content_type = (request.headers.get("content-type") or "").lower()
        if content_type.startswith("application/json"):
            try:
                payload = await request.json()
            except Exception:
                payload = {}
            if isinstance(payload, dict):
                domain = domain or str(payload.get("domain") or "").strip()
                key = key or str(payload.get("key") or "").strip()
                value_type = value_type or str(payload.get("value_type") or "").strip()
                if not value:
                    value = payload.get("value") or ""
                if not is_secret:
                    is_secret = payload.get("is_secret") or ""
                if not is_active:
                    is_active = payload.get("is_active") or ""

    return admin_web_service.settings_update_response(
        request,
        db,
        auth,
        str(setting_id),
        domain,
        key,
        value_type,
        value,
        is_secret,
        is_active,
    )


@router.post("/settings/{setting_id}/delete")
def admin_settings_delete(
    request: Request,
    setting_id: UUID,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Delete a setting."""
    return admin_web_service.settings_delete_response(request, db, auth, str(setting_id))
