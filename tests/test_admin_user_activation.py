from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from datetime import UTC  # type: ignore
except ImportError:  # pragma: no cover
    UTC = timezone.utc

from app.models.person import PersonStatus
from app.services.admin.web import admin_web_service


ROOT = Path(__file__).resolve().parents[1]


def test_activate_user_account_reactivates_profile_and_local_credential(
    db_session,
    person,
    user_credential,
    monkeypatch,
):
    monkeypatch.setattr("app.services.admin.web.fire_audit_event", lambda **_: None)
    person.status = PersonStatus.inactive
    person.is_active = False
    user_credential.is_active = False
    user_credential.failed_login_attempts = 5
    user_credential.locked_until = datetime.now(UTC) + timedelta(minutes=15)
    db_session.commit()

    error = admin_web_service.activate_user_account(db_session, str(person.id))

    assert error is None
    db_session.refresh(person)
    db_session.refresh(user_credential)
    assert person.status == PersonStatus.active
    assert person.is_active is True
    assert user_credential.is_active is True
    assert user_credential.failed_login_attempts == 0
    assert user_credential.locked_until is None


def test_users_context_filters_by_visible_profile_status(
    db_session,
    person,
    user_credential,
):
    person.status = PersonStatus.active
    person.is_active = True
    user_credential.failed_login_attempts = 5
    user_credential.locked_until = datetime.now(UTC) + timedelta(minutes=15)
    db_session.commit()

    active_context = admin_web_service.users_context(
        db_session,
        search=person.email,
        status="active",
        page=1,
    )
    assert len(active_context["users"]) == 1
    assert active_context["users"][0]["status"] == "active"
    assert active_context["users"][0]["account_is_active"] is False

    inactive_context = admin_web_service.users_context(
        db_session,
        search=person.email,
        status="inactive",
        page=1,
    )
    assert inactive_context["users"] == []


def test_admin_users_activate_route_is_registered():
    source = (ROOT / "app" / "web" / "admin.py").read_text()

    assert '@router.post("/users/{user_id}/activate")' in source
    assert "users_activate_response" in source


def test_admin_user_form_template_has_activation_card():
    template = (ROOT / "templates" / "admin" / "user_form.html").read_text()

    assert "User account activated successfully." in template
    assert "/admin/users/{{ user_data.id }}/activate" in template
    assert "setTimeout(() => show = false, 5000)" in template
    assert 'disabled aria-disabled="true"' in template
    assert "Account Status" in template
    assert "Failed attempts" in template


def test_admin_users_template_keeps_list_design_without_activation_cards():
    template = (ROOT / "templates" / "admin" / "users.html").read_text()

    assert 'class="space-y-3 p-4 md:hidden"' in template
    assert "grid grid-cols-1 gap-4 lg:grid-cols-2 xl:grid-cols-3" not in template
    assert "/admin/users/{{ user.id }}/activate" not in template
