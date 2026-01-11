"""
Admin web routes.

Provides admin dashboard and management pages with admin role requirement.
"""

from datetime import datetime
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.services.admin.web import admin_web_service
from app.templates import templates
from app.web.deps import brand_context, WebAuthContext, optional_web_auth


router = APIRouter(prefix="/admin", tags=["admin-web"])


def get_db():
    """Get database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _request_path_with_query(request: Request) -> str:
    if request.url.query:
        return f"{request.url.path}?{request.url.query}"
    return request.url.path


def _admin_login_redirect(next_path: str) -> RedirectResponse:
    return RedirectResponse(
        url=f"/admin/login?{urlencode({'next': next_path})}",
        status_code=302,
    )


def require_admin_web_auth(
    request: Request,
    auth: WebAuthContext = Depends(optional_web_auth),
) -> WebAuthContext | RedirectResponse:
    """
    Require admin role for web routes.

    Redirects to admin login if not authenticated or not admin.
    """
    if not auth.is_authenticated:
        return _admin_login_redirect(_request_path_with_query(request))

    if "admin" not in auth.roles:
        raise HTTPException(
            status_code=403,
            detail="Admin access required",
        )

    return auth


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def admin_dashboard(
    request: Request,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Admin dashboard page."""
    auth_or_redirect = require_admin_web_auth(request, auth)
    if isinstance(auth_or_redirect, RedirectResponse):
        return auth_or_redirect
    context = admin_web_service.dashboard_context(db)

    return templates.TemplateResponse(
        request,
        "admin/dashboard.html",
        {
            "title": "Admin Dashboard",
            "page_title": "Dashboard",
            "brand": brand_context(),
            "user": auth.user,
            "active_page": "dashboard",
            **context,
        },
    )


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
    auth_or_redirect = require_admin_web_auth(request, auth)
    if isinstance(auth_or_redirect, RedirectResponse):
        return auth_or_redirect
    context = admin_web_service.users_context(db, search, status, page)

    return templates.TemplateResponse(
        request,
        "admin/users.html",
        {
            "title": "Users",
            "page_title": "Users",
            "brand": brand_context(),
            "user": auth.user,
            "active_page": "users",
            **context,
        },
    )


@router.get("/users/new", response_class=HTMLResponse)
def admin_users_new(
    request: Request,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Show create user form."""
    auth_or_redirect = require_admin_web_auth(request, auth)
    if isinstance(auth_or_redirect, RedirectResponse):
        return auth_or_redirect
    context = admin_web_service.user_form_context(db)

    return templates.TemplateResponse(
        request,
        "admin/user_form.html",
        {
            "title": "Add New User",
            "page_title": "Add New User",
            "brand": brand_context(),
            "user": auth.user,
            "active_page": "users",
            "error": None,
            "success": None,
            **context,
        },
    )


@router.post("/users/new", response_class=HTMLResponse)
def admin_users_create(
    request: Request,
    first_name: str = Form(...),
    last_name: str = Form(...),
    email: str = Form(...),
    username: str = Form(...),
    organization_id: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
    display_name: str = Form(default=""),
    phone: str = Form(default=""),
    status: str = Form(default="active"),
    must_change_password: str = Form(default=""),
    roles: list[str] = Form(default=[]),
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Handle create user form submission."""
    auth_or_redirect = require_admin_web_auth(request, auth)
    if isinstance(auth_or_redirect, RedirectResponse):
        return auth_or_redirect
    person, error = admin_web_service.create_user(
        db=db,
        first_name=first_name,
        last_name=last_name,
        email=email,
        username=username,
        organization_id=organization_id,
        password=password,
        password_confirm=password_confirm,
        display_name=display_name,
        phone=phone,
        status=status,
        must_change_password=must_change_password,
        role_ids=roles,
    )

    if error:
        context = admin_web_service.user_form_context(db)
        context["user_data"] = admin_web_service.user_data_from_payload(
            {
                "first_name": first_name,
                "last_name": last_name,
                "display_name": display_name,
                "email": email,
                "phone": phone,
                "status": status,
                "organization_id": organization_id,
                "username": username,
                "must_change_password": must_change_password,
                "roles": roles,
            }
        )
        return templates.TemplateResponse(
            request,
            "admin/user_form.html",
            {
                "title": "Add New User",
                "page_title": "Add New User",
                "brand": brand_context(),
                "user": auth.user,
                "active_page": "users",
                "error": error,
                "success": None,
                **context,
            },
            status_code=400,
        )

    return RedirectResponse(url="/admin/users?created=1", status_code=302)


@router.get("/users/{user_id}", response_class=HTMLResponse)
def admin_users_view(
    request: Request,
    user_id: str,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """View user details (same as edit)."""
    return admin_users_edit(request, user_id, db, auth)


@router.get("/users/{user_id}/edit", response_class=HTMLResponse)
def admin_users_edit(
    request: Request,
    user_id: str,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Show edit user form."""
    auth_or_redirect = require_admin_web_auth(request, auth)
    if isinstance(auth_or_redirect, RedirectResponse):
        return auth_or_redirect
    context = admin_web_service.user_form_context(db, user_id)

    return templates.TemplateResponse(
        request,
        "admin/user_form.html",
        {
            "title": f"Edit User - {context['user_data']['first_name']} {context['user_data']['last_name']}",
            "page_title": "Edit User",
            "brand": brand_context(),
            "user": auth.user,
            "active_page": "users",
            "error": None,
            "success": None,
            **context,
        },
    )


@router.post("/users/{user_id}/edit", response_class=HTMLResponse)
def admin_users_update(
    request: Request,
    user_id: str,
    first_name: str = Form(...),
    last_name: str = Form(...),
    email: str = Form(...),
    username: str = Form(...),
    organization_id: str = Form(...),
    password: str = Form(default=""),
    password_confirm: str = Form(default=""),
    display_name: str = Form(default=""),
    phone: str = Form(default=""),
    status: str = Form(default="active"),
    must_change_password: str = Form(default=""),
    email_verified: str = Form(default=""),
    roles: list[str] = Form(default=[]),
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Handle edit user form submission."""
    auth_or_redirect = require_admin_web_auth(request, auth)
    if isinstance(auth_or_redirect, RedirectResponse):
        return auth_or_redirect
    person, error = admin_web_service.update_user(
        db=db,
        user_id=user_id,
        first_name=first_name,
        last_name=last_name,
        email=email,
        username=username,
        organization_id=organization_id,
        password=password,
        password_confirm=password_confirm,
        display_name=display_name,
        phone=phone,
        status=status,
        must_change_password=must_change_password,
        email_verified=email_verified,
        role_ids=roles,
    )

    context = admin_web_service.user_form_context(db, user_id)

    if error:
        context["user_data"] = admin_web_service.user_data_from_payload(
            {
                "first_name": first_name,
                "last_name": last_name,
                "display_name": display_name,
                "email": email,
                "phone": phone,
                "status": status,
                "organization_id": organization_id,
                "username": username,
                "must_change_password": must_change_password,
                "email_verified": email_verified,
                "roles": roles,
            },
            user_id=user_id,
        )
        return templates.TemplateResponse(
            request,
            "admin/user_form.html",
            {
                "title": f"Edit User - {first_name} {last_name}",
                "page_title": "Edit User",
                "brand": brand_context(),
                "user": auth.user,
                "active_page": "users",
                "error": error,
                "success": None,
                **context,
            },
            status_code=400,
        )

    return templates.TemplateResponse(
        request,
        "admin/user_form.html",
        {
            "title": f"Edit User - {first_name} {last_name}",
            "page_title": "Edit User",
            "brand": brand_context(),
            "user": auth.user,
            "active_page": "users",
            "error": None,
            "success": "User updated successfully",
            **context,
        },
    )


@router.post("/users/{user_id}/delete")
def admin_users_delete(
    request: Request,
    user_id: str,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Delete a user."""
    auth_or_redirect = require_admin_web_auth(request, auth)
    if isinstance(auth_or_redirect, RedirectResponse):
        return auth_or_redirect
    error = admin_web_service.delete_user(db, user_id)

    if error:
        raise HTTPException(status_code=400, detail=error)

    return RedirectResponse(url="/admin/users?deleted=1", status_code=302)


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
    auth_or_redirect = require_admin_web_auth(request, auth)
    if isinstance(auth_or_redirect, RedirectResponse):
        return auth_or_redirect
    context = admin_web_service.roles_context(
        db=db,
        search=search,
        status=status,
        page=page,
    )

    return templates.TemplateResponse(
        request,
        "admin/roles.html",
        {
            "title": "Roles",
            "page_title": "Roles & Permissions",
            "brand": brand_context(),
            "user": auth.user,
            "active_page": "roles",
            **context,
        },
    )


@router.get("/roles/new", response_class=HTMLResponse)
def admin_roles_new(
    request: Request,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Show create role form."""
    auth_or_redirect = require_admin_web_auth(request, auth)
    if isinstance(auth_or_redirect, RedirectResponse):
        return auth_or_redirect
    context = admin_web_service.role_form_context(db)

    return templates.TemplateResponse(
        request,
        "admin/role_form.html",
        {
            "title": "Create Role",
            "page_title": "Create Role",
            "brand": brand_context(),
            "user": auth.user,
            "active_page": "roles",
            "error": None,
            "success": None,
            **context,
        },
    )


@router.post("/roles/new", response_class=HTMLResponse)
def admin_roles_create(
    request: Request,
    name: str = Form(...),
    description: str = Form(default=""),
    is_active: str = Form(default=""),
    permissions: list[str] = Form(default=[]),
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Handle create role form submission."""
    auth_or_redirect = require_admin_web_auth(request, auth)
    if isinstance(auth_or_redirect, RedirectResponse):
        return auth_or_redirect
    role, error = admin_web_service.create_role(
        db=db,
        name=name,
        description=description,
        is_active=is_active == "1",
        permission_ids=permissions,
    )

    if error:
        context = admin_web_service.role_form_context(db)
        return templates.TemplateResponse(
            request,
            "admin/role_form.html",
            {
                "title": "Create Role",
                "page_title": "Create Role",
                "brand": brand_context(),
                "user": auth.user,
                "active_page": "roles",
                "error": error,
                "success": None,
                **context,
            },
            status_code=400,
        )

    return RedirectResponse(url="/admin/roles?created=1", status_code=302)


@router.get("/roles/{role_id}", response_class=HTMLResponse)
def admin_roles_view(
    request: Request,
    role_id: str,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """View role details (same as edit)."""
    return admin_roles_edit(request, role_id, db, auth)


@router.get("/roles/{role_id}/edit", response_class=HTMLResponse)
def admin_roles_edit(
    request: Request,
    role_id: str,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Show edit role form."""
    auth_or_redirect = require_admin_web_auth(request, auth)
    if isinstance(auth_or_redirect, RedirectResponse):
        return auth_or_redirect
    context = admin_web_service.role_form_context(db, role_id)

    if not context.get("role_data"):
        raise HTTPException(status_code=404, detail="Role not found")

    return templates.TemplateResponse(
        request,
        "admin/role_form.html",
        {
            "title": f"Edit Role - {context['role_data']['name']}",
            "page_title": "Edit Role",
            "brand": brand_context(),
            "user": auth.user,
            "active_page": "roles",
            "error": None,
            "success": None,
            **context,
        },
    )


@router.post("/roles/{role_id}/edit", response_class=HTMLResponse)
def admin_roles_update(
    request: Request,
    role_id: str,
    name: str = Form(...),
    description: str = Form(default=""),
    is_active: str = Form(default=""),
    permissions: list[str] = Form(default=[]),
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Handle edit role form submission."""
    auth_or_redirect = require_admin_web_auth(request, auth)
    if isinstance(auth_or_redirect, RedirectResponse):
        return auth_or_redirect
    role, error = admin_web_service.update_role(
        db=db,
        role_id=role_id,
        name=name,
        description=description,
        is_active=is_active == "1",
        permission_ids=permissions,
    )

    context = admin_web_service.role_form_context(db, role_id)

    if error:
        return templates.TemplateResponse(
            request,
            "admin/role_form.html",
            {
                "title": f"Edit Role - {name}",
                "page_title": "Edit Role",
                "brand": brand_context(),
                "user": auth.user,
                "active_page": "roles",
                "error": error,
                "success": None,
                **context,
            },
            status_code=400,
        )

    return templates.TemplateResponse(
        request,
        "admin/role_form.html",
        {
            "title": f"Edit Role - {name}",
            "page_title": "Edit Role",
            "brand": brand_context(),
            "user": auth.user,
            "active_page": "roles",
            "error": None,
            "success": "Role updated successfully",
            **context,
        },
    )


@router.post("/roles/{role_id}/delete")
def admin_roles_delete(
    request: Request,
    role_id: str,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Delete a role."""
    auth_or_redirect = require_admin_web_auth(request, auth)
    if isinstance(auth_or_redirect, RedirectResponse):
        return auth_or_redirect
    error = admin_web_service.delete_role(db, role_id)

    if error:
        raise HTTPException(status_code=400, detail=error)

    return RedirectResponse(url="/admin/roles?deleted=1", status_code=302)


# --- Permissions Routes ---


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
    auth_or_redirect = require_admin_web_auth(request, auth)
    if isinstance(auth_or_redirect, RedirectResponse):
        return auth_or_redirect
    context = admin_web_service.permissions_context(
        db=db,
        search=search,
        status=status,
        page=page,
    )

    return templates.TemplateResponse(
        request,
        "admin/permissions.html",
        {
            "title": "Permissions",
            "page_title": "Permissions",
            "brand": brand_context(),
            "user": auth.user,
            "active_page": "permissions",
            **context,
        },
    )


@router.get("/permissions/new", response_class=HTMLResponse)
def admin_permissions_new(
    request: Request,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Show create permission form."""
    auth_or_redirect = require_admin_web_auth(request, auth)
    if isinstance(auth_or_redirect, RedirectResponse):
        return auth_or_redirect
    context = admin_web_service.permission_form_context(db)

    return templates.TemplateResponse(
        request,
        "admin/permission_form.html",
        {
            "title": "Create Permission",
            "page_title": "Create Permission",
            "brand": brand_context(),
            "user": auth.user,
            "active_page": "permissions",
            "error": None,
            "success": None,
            **context,
        },
    )


@router.post("/permissions/new", response_class=HTMLResponse)
def admin_permissions_create(
    request: Request,
    key: str = Form(...),
    description: str = Form(default=""),
    is_active: str = Form(default=""),
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Handle create permission form submission."""
    auth_or_redirect = require_admin_web_auth(request, auth)
    if isinstance(auth_or_redirect, RedirectResponse):
        return auth_or_redirect
    permission, error = admin_web_service.create_permission(
        db=db,
        key=key,
        description=description,
        is_active=is_active == "1",
    )

    if error:
        context = admin_web_service.permission_form_context(db)
        return templates.TemplateResponse(
            request,
            "admin/permission_form.html",
            {
                "title": "Create Permission",
                "page_title": "Create Permission",
                "brand": brand_context(),
                "user": auth.user,
                "active_page": "permissions",
                "error": error,
                "success": None,
                **context,
            },
            status_code=400,
        )

    return RedirectResponse(url="/admin/permissions?created=1", status_code=302)


@router.get("/permissions/{permission_id}", response_class=HTMLResponse)
def admin_permissions_view(
    request: Request,
    permission_id: str,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """View permission details (same as edit)."""
    return admin_permissions_edit(request, permission_id, db, auth)


@router.get("/permissions/{permission_id}/edit", response_class=HTMLResponse)
def admin_permissions_edit(
    request: Request,
    permission_id: str,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Show edit permission form."""
    auth_or_redirect = require_admin_web_auth(request, auth)
    if isinstance(auth_or_redirect, RedirectResponse):
        return auth_or_redirect
    context = admin_web_service.permission_form_context(db, permission_id)

    if not context.get("permission_data"):
        raise HTTPException(status_code=404, detail="Permission not found")

    return templates.TemplateResponse(
        request,
        "admin/permission_form.html",
        {
            "title": f"Edit Permission - {context['permission_data']['key']}",
            "page_title": "Edit Permission",
            "brand": brand_context(),
            "user": auth.user,
            "active_page": "permissions",
            "error": None,
            "success": None,
            **context,
        },
    )


@router.post("/permissions/{permission_id}/edit", response_class=HTMLResponse)
def admin_permissions_update(
    request: Request,
    permission_id: str,
    key: str = Form(...),
    description: str = Form(default=""),
    is_active: str = Form(default=""),
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Handle edit permission form submission."""
    auth_or_redirect = require_admin_web_auth(request, auth)
    if isinstance(auth_or_redirect, RedirectResponse):
        return auth_or_redirect
    permission, error = admin_web_service.update_permission(
        db=db,
        permission_id=permission_id,
        key=key,
        description=description,
        is_active=is_active == "1",
    )

    context = admin_web_service.permission_form_context(db, permission_id)

    if error:
        return templates.TemplateResponse(
            request,
            "admin/permission_form.html",
            {
                "title": f"Edit Permission - {key}",
                "page_title": "Edit Permission",
                "brand": brand_context(),
                "user": auth.user,
                "active_page": "permissions",
                "error": error,
                "success": None,
                **context,
            },
            status_code=400,
        )

    return templates.TemplateResponse(
        request,
        "admin/permission_form.html",
        {
            "title": f"Edit Permission - {key}",
            "page_title": "Edit Permission",
            "brand": brand_context(),
            "user": auth.user,
            "active_page": "permissions",
            "error": None,
            "success": "Permission updated successfully",
            **context,
        },
    )


@router.post("/permissions/{permission_id}/delete")
def admin_permissions_delete(
    request: Request,
    permission_id: str,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Delete a permission."""
    auth_or_redirect = require_admin_web_auth(request, auth)
    if isinstance(auth_or_redirect, RedirectResponse):
        return auth_or_redirect
    error = admin_web_service.delete_permission(db, permission_id)

    if error:
        raise HTTPException(status_code=400, detail=error)

    return RedirectResponse(url="/admin/permissions?deleted=1", status_code=302)


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
    auth_or_redirect = require_admin_web_auth(request, auth)
    if isinstance(auth_or_redirect, RedirectResponse):
        return auth_or_redirect
    context = admin_web_service.organizations_context(
        db=db,
        search=search,
        status=status,
        page=page,
    )

    return templates.TemplateResponse(
        request,
        "admin/organizations.html",
        {
            "title": "Organizations",
            "page_title": "Organizations",
            "brand": brand_context(),
            "user": auth.user,
            "active_page": "organizations",
            **context,
        },
    )


@router.get("/organizations/new", response_class=HTMLResponse)
def admin_organizations_new(
    request: Request,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Show create organization form."""
    auth_or_redirect = require_admin_web_auth(request, auth)
    if isinstance(auth_or_redirect, RedirectResponse):
        return auth_or_redirect
    context = admin_web_service.organization_form_context(
        db,
        default_currency_org_id=str(auth.organization_id) if auth.organization_id else None,
    )

    return templates.TemplateResponse(
        request,
        "admin/organization_form.html",
        {
            "title": "Create Organization",
            "page_title": "Create Organization",
            "brand": brand_context(),
            "user": auth.user,
            "active_page": "organizations",
            "error": None,
            "success": None,
            **context,
        },
    )


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
    auth_or_redirect = require_admin_web_auth(request, auth)
    if isinstance(auth_or_redirect, RedirectResponse):
        return auth_or_redirect
    organization, error = admin_web_service.create_organization(
        db=db,
        organization_code=organization_code,
        legal_name=legal_name,
        functional_currency_code=functional_currency_code,
        presentation_currency_code=presentation_currency_code,
        fiscal_year_end_month=fiscal_year_end_month,
        fiscal_year_end_day=fiscal_year_end_day,
        trading_name=trading_name or None,
        registration_number=registration_number or None,
        tax_identification_number=tax_identification_number or None,
        incorporation_date=incorporation_date or None,
        jurisdiction_country_code=jurisdiction_country_code or None,
        parent_organization_id=parent_organization_id or None,
        consolidation_method=consolidation_method or None,
        ownership_percentage=ownership_percentage or None,
        is_active=is_active == "1",
    )

    if error:
        context = admin_web_service.organization_form_context(
            db,
            default_currency_org_id=str(auth.organization_id) if auth.organization_id else None,
        )
        return templates.TemplateResponse(
            request,
            "admin/organization_form.html",
            {
                "title": "Create Organization",
                "page_title": "Create Organization",
                "brand": brand_context(),
                "user": auth.user,
                "active_page": "organizations",
                "error": error,
                "success": None,
                **context,
            },
            status_code=400,
        )

    return RedirectResponse(url="/admin/organizations?created=1", status_code=302)


@router.get("/organizations/{org_id}", response_class=HTMLResponse)
def admin_organizations_view(
    request: Request,
    org_id: str,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """View organization details (same as edit)."""
    return admin_organizations_edit(request, org_id, db, auth)


@router.get("/organizations/{org_id}/edit", response_class=HTMLResponse)
def admin_organizations_edit(
    request: Request,
    org_id: str,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Show edit organization form."""
    auth_or_redirect = require_admin_web_auth(request, auth)
    if isinstance(auth_or_redirect, RedirectResponse):
        return auth_or_redirect
    context = admin_web_service.organization_form_context(db, org_id)

    if not context.get("organization_data"):
        raise HTTPException(status_code=404, detail="Organization not found")

    return templates.TemplateResponse(
        request,
        "admin/organization_form.html",
        {
            "title": f"Edit Organization - {context['organization_data']['legal_name']}",
            "page_title": "Edit Organization",
            "brand": brand_context(),
            "user": auth.user,
            "active_page": "organizations",
            "error": None,
            "success": None,
            **context,
        },
    )


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
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Handle edit organization form submission."""
    auth_or_redirect = require_admin_web_auth(request, auth)
    if isinstance(auth_or_redirect, RedirectResponse):
        return auth_or_redirect
    organization, error = admin_web_service.update_organization(
        db=db,
        organization_id=org_id,
        organization_code=organization_code,
        legal_name=legal_name,
        functional_currency_code=functional_currency_code,
        presentation_currency_code=presentation_currency_code,
        fiscal_year_end_month=fiscal_year_end_month,
        fiscal_year_end_day=fiscal_year_end_day,
        trading_name=trading_name or None,
        registration_number=registration_number or None,
        tax_identification_number=tax_identification_number or None,
        incorporation_date=incorporation_date or None,
        jurisdiction_country_code=jurisdiction_country_code or None,
        parent_organization_id=parent_organization_id or None,
        consolidation_method=consolidation_method or None,
        ownership_percentage=ownership_percentage or None,
        is_active=is_active == "1",
    )

    context = admin_web_service.organization_form_context(db, org_id)

    if error:
        return templates.TemplateResponse(
            request,
            "admin/organization_form.html",
            {
                "title": f"Edit Organization - {legal_name}",
                "page_title": "Edit Organization",
                "brand": brand_context(),
                "user": auth.user,
                "active_page": "organizations",
                "error": error,
                "success": None,
                **context,
            },
            status_code=400,
        )

    return templates.TemplateResponse(
        request,
        "admin/organization_form.html",
        {
            "title": f"Edit Organization - {legal_name}",
            "page_title": "Edit Organization",
            "brand": brand_context(),
            "user": auth.user,
            "active_page": "organizations",
            "error": None,
            "success": "Organization updated successfully",
            **context,
        },
    )


@router.post("/organizations/{org_id}/delete")
def admin_organizations_delete(
    request: Request,
    org_id: str,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Delete an organization."""
    auth_or_redirect = require_admin_web_auth(request, auth)
    if isinstance(auth_or_redirect, RedirectResponse):
        return auth_or_redirect
    error = admin_web_service.delete_organization(db, org_id)

    if error:
        raise HTTPException(status_code=400, detail=error)

    return RedirectResponse(url="/admin/organizations?deleted=1", status_code=302)


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
    auth_or_redirect = require_admin_web_auth(request, auth)
    if isinstance(auth_or_redirect, RedirectResponse):
        return auth_or_redirect
    context = admin_web_service.settings_context(
        db=db,
        search=search,
        domain=domain,
        status=status,
        page=page,
    )

    return templates.TemplateResponse(
        request,
        "admin/settings.html",
        {
            "title": "Settings",
            "page_title": "System Settings",
            "brand": brand_context(),
            "user": auth.user,
            "active_page": "settings",
            **context,
        },
    )


@router.get("/settings/new", response_class=HTMLResponse)
def admin_settings_new(
    request: Request,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Show create setting form."""
    auth_or_redirect = require_admin_web_auth(request, auth)
    if isinstance(auth_or_redirect, RedirectResponse):
        return auth_or_redirect
    context = admin_web_service.setting_form_context(db)

    return templates.TemplateResponse(
        request,
        "admin/setting_form.html",
        {
            "title": "Create Setting",
            "page_title": "Create Setting",
            "brand": brand_context(),
            "user": auth.user,
            "active_page": "settings",
            "error": None,
            "success": None,
            **context,
        },
    )


@router.post("/settings/new", response_class=HTMLResponse)
def admin_settings_create(
    request: Request,
    domain: str = Form(...),
    key: str = Form(...),
    value_type: str = Form(...),
    value: str = Form(default=""),
    is_secret: str = Form(default=""),
    is_active: str = Form(default=""),
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Handle create setting form submission."""
    auth_or_redirect = require_admin_web_auth(request, auth)
    if isinstance(auth_or_redirect, RedirectResponse):
        return auth_or_redirect
    setting, error = admin_web_service.create_setting(
        db=db,
        domain=domain,
        key=key,
        value_type=value_type,
        value=value,
        is_secret=is_secret == "1",
        is_active=is_active == "1",
    )

    if error:
        context = admin_web_service.setting_form_context(db)
        return templates.TemplateResponse(
            request,
            "admin/setting_form.html",
            {
                "title": "Create Setting",
                "page_title": "Create Setting",
                "brand": brand_context(),
                "user": auth.user,
                "active_page": "settings",
                "error": error,
                "success": None,
                **context,
            },
            status_code=400,
        )

    return RedirectResponse(url="/admin/settings?created=1", status_code=302)


@router.get("/settings/{setting_id}", response_class=HTMLResponse)
def admin_settings_view(
    request: Request,
    setting_id: str,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """View setting details (same as edit)."""
    return admin_settings_edit(request, setting_id, db, auth)


@router.get("/settings/{setting_id}/edit", response_class=HTMLResponse)
def admin_settings_edit(
    request: Request,
    setting_id: str,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Show edit setting form."""
    auth_or_redirect = require_admin_web_auth(request, auth)
    if isinstance(auth_or_redirect, RedirectResponse):
        return auth_or_redirect
    context = admin_web_service.setting_form_context(db, setting_id)

    if not context.get("setting_data"):
        raise HTTPException(status_code=404, detail="Setting not found")

    return templates.TemplateResponse(
        request,
        "admin/setting_form.html",
        {
            "title": f"Edit Setting - {context['setting_data']['key']}",
            "page_title": "Edit Setting",
            "brand": brand_context(),
            "user": auth.user,
            "active_page": "settings",
            "error": None,
            "success": None,
            **context,
        },
    )


@router.post("/settings/{setting_id}/edit", response_class=HTMLResponse)
def admin_settings_update(
    request: Request,
    setting_id: str,
    domain: str = Form(...),
    key: str = Form(...),
    value_type: str = Form(...),
    value: str = Form(default=""),
    is_secret: str = Form(default=""),
    is_active: str = Form(default=""),
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Handle edit setting form submission."""
    auth_or_redirect = require_admin_web_auth(request, auth)
    if isinstance(auth_or_redirect, RedirectResponse):
        return auth_or_redirect
    setting, error = admin_web_service.update_setting(
        db=db,
        setting_id=setting_id,
        domain=domain,
        key=key,
        value_type=value_type,
        value=value,
        is_secret=is_secret == "1",
        is_active=is_active == "1",
    )

    context = admin_web_service.setting_form_context(db, setting_id)

    if error:
        return templates.TemplateResponse(
            request,
            "admin/setting_form.html",
            {
                "title": f"Edit Setting - {key}",
                "page_title": "Edit Setting",
                "brand": brand_context(),
                "user": auth.user,
                "active_page": "settings",
                "error": error,
                "success": None,
                **context,
            },
            status_code=400,
        )

    return templates.TemplateResponse(
        request,
        "admin/setting_form.html",
        {
            "title": f"Edit Setting - {key}",
            "page_title": "Edit Setting",
            "brand": brand_context(),
            "user": auth.user,
            "active_page": "settings",
            "error": None,
            "success": "Setting updated successfully",
            **context,
        },
    )


@router.post("/settings/{setting_id}/delete")
def admin_settings_delete(
    request: Request,
    setting_id: str,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Delete a setting."""
    auth_or_redirect = require_admin_web_auth(request, auth)
    if isinstance(auth_or_redirect, RedirectResponse):
        return auth_or_redirect
    error = admin_web_service.delete_setting(db, setting_id)

    if error:
        raise HTTPException(status_code=400, detail=error)

    return RedirectResponse(url="/admin/settings?deleted=1", status_code=302)


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
    auth_or_redirect = require_admin_web_auth(request, auth)
    if isinstance(auth_or_redirect, RedirectResponse):
        return auth_or_redirect
    context = admin_web_service.audit_logs_context(
        db=db,
        search=search,
        actor_type=actor_type,
        status=status,
        page=page,
    )

    return templates.TemplateResponse(
        request,
        "admin/audit_logs.html",
        {
            "title": "Audit Logs",
            "page_title": "Audit Logs",
            "brand": brand_context(),
            "user": auth.user,
            "active_page": "audit",
            **context,
        },
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
    auth_or_redirect = require_admin_web_auth(request, auth)
    if isinstance(auth_or_redirect, RedirectResponse):
        return auth_or_redirect
    context = admin_web_service.tasks_context(
        db=db,
        search=search,
        status=status,
        page=page,
    )

    return templates.TemplateResponse(
        request,
        "admin/tasks.html",
        {
            "title": "Scheduled Tasks",
            "page_title": "Scheduled Tasks",
            "brand": brand_context(),
            "user": auth.user,
            "active_page": "tasks",
            **context,
        },
    )


@router.get("/tasks/new", response_class=HTMLResponse)
def admin_tasks_new(
    request: Request,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Show create task form."""
    auth_or_redirect = require_admin_web_auth(request, auth)
    if isinstance(auth_or_redirect, RedirectResponse):
        return auth_or_redirect

    context = admin_web_service.task_form_context(db)

    return templates.TemplateResponse(
        request,
        "admin/task_form.html",
        {
            "title": "Create Task",
            "page_title": "Create Task",
            "brand": brand_context(),
            "user": auth.user,
            "active_page": "tasks",
            "error": None,
            "success": None,
            **context,
        },
    )


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
    auth_or_redirect = require_admin_web_auth(request, auth)
    if isinstance(auth_or_redirect, RedirectResponse):
        return auth_or_redirect

    task, error = admin_web_service.create_task(
        db=db,
        name=name,
        task_name=task_name,
        schedule_type=schedule_type,
        interval_seconds=interval_seconds,
        args_json=args_json,
        kwargs_json=kwargs_json,
        enabled=enabled == "1",
    )

    if error:
        context = admin_web_service.task_form_context(db)
        return templates.TemplateResponse(
            request,
            "admin/task_form.html",
            {
                "title": "Create Task",
                "page_title": "Create Task",
                "brand": brand_context(),
                "user": auth.user,
                "active_page": "tasks",
                "error": error,
                "success": None,
                **context,
            },
            status_code=400,
        )

    return RedirectResponse(url="/admin/tasks?created=1", status_code=302)


@router.get("/tasks/{task_id}", response_class=HTMLResponse)
def admin_tasks_view(
    request: Request,
    task_id: str,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """View task details (same as edit)."""
    return admin_tasks_edit(request, task_id, db, auth)


@router.get("/tasks/{task_id}/edit", response_class=HTMLResponse)
def admin_tasks_edit(
    request: Request,
    task_id: str,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Show edit task form."""
    auth_or_redirect = require_admin_web_auth(request, auth)
    if isinstance(auth_or_redirect, RedirectResponse):
        return auth_or_redirect

    context = admin_web_service.task_form_context(db, task_id)

    if not context.get("task_data"):
        raise HTTPException(status_code=404, detail="Task not found")

    return templates.TemplateResponse(
        request,
        "admin/task_form.html",
        {
            "title": f"Edit Task - {context['task_data']['name']}",
            "page_title": "Edit Task",
            "brand": brand_context(),
            "user": auth.user,
            "active_page": "tasks",
            "error": None,
            "success": None,
            **context,
        },
    )


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
    auth_or_redirect = require_admin_web_auth(request, auth)
    if isinstance(auth_or_redirect, RedirectResponse):
        return auth_or_redirect

    task, error = admin_web_service.update_task(
        db=db,
        task_id=task_id,
        name=name,
        task_name=task_name,
        schedule_type=schedule_type,
        interval_seconds=interval_seconds,
        args_json=args_json,
        kwargs_json=kwargs_json,
        enabled=enabled == "1",
    )

    context = admin_web_service.task_form_context(db, task_id)

    if error:
        return templates.TemplateResponse(
            request,
            "admin/task_form.html",
            {
                "title": f"Edit Task - {name}",
                "page_title": "Edit Task",
                "brand": brand_context(),
                "user": auth.user,
                "active_page": "tasks",
                "error": error,
                "success": None,
                **context,
            },
            status_code=400,
        )

    return templates.TemplateResponse(
        request,
        "admin/task_form.html",
        {
            "title": f"Edit Task - {name}",
            "page_title": "Edit Task",
            "brand": brand_context(),
            "user": auth.user,
            "active_page": "tasks",
            "error": None,
            "success": "Task updated successfully",
            **context,
        },
    )


@router.post("/tasks/{task_id}/delete")
def admin_tasks_delete(
    request: Request,
    task_id: str,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Delete a task."""
    auth_or_redirect = require_admin_web_auth(request, auth)
    if isinstance(auth_or_redirect, RedirectResponse):
        return auth_or_redirect

    error = admin_web_service.delete_task(db, task_id)

    if error:
        raise HTTPException(status_code=400, detail=error)

    return RedirectResponse(url="/admin/tasks?deleted=1", status_code=302)
