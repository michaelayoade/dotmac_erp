"""Explicit email routes must not silently select unusable profiles."""

from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.models.email_profile import EmailModule
from app.services.email_profile import EmailProfileService


def profile(org, active=True, default=False):
    return SimpleNamespace(
        profile_id=uuid4(),
        organization_id=org,
        is_active=active,
        is_default=default,
        name="Synthetic SMTP",
    )


@pytest.mark.parametrize("invalid", ["inactive", "foreign", "missing"])
def test_invalid_route_is_rejected_before_any_mutation(invalid):
    org = uuid4()
    item = profile(org)
    if invalid == "inactive":
        item.is_active = False
    elif invalid == "foreign":
        item.organization_id = uuid4()
    db = MagicMock()
    db.get.return_value = None if invalid == "missing" else item
    with pytest.raises(ValueError):
        EmailProfileService(db).set_module_routing(
            org, EmailModule.FINANCE, item.profile_id
        )
    db.scalar.assert_not_called()
    db.add.assert_not_called()
    db.flush.assert_not_called()


@pytest.mark.parametrize("shared", [False, True])
def test_active_owned_and_system_profiles_remain_routeable(shared):
    org = uuid4()
    item = profile(None if shared else org)
    route = SimpleNamespace(email_profile_id=None, use_default=True)
    db = MagicMock()
    db.get.return_value = item
    db.scalar.return_value = route
    result = EmailProfileService(db).set_module_routing(
        org, EmailModule.FINANCE, item.profile_id
    )
    assert result.email_profile_id == item.profile_id
    assert result.use_default is False
    db.get.assert_called_once()
    assert db.get.call_args.kwargs["with_for_update"] is True
    db.commit.assert_not_called()


def test_explicit_default_selection_does_not_require_a_profile():
    db = MagicMock()
    db.scalar.return_value = SimpleNamespace(
        email_profile_id=uuid4(), use_default=False
    )
    result = EmailProfileService(db).set_module_routing(
        uuid4(), EmailModule.EXPENSE, None, use_default=True
    )
    assert result.use_default is True
    assert result.email_profile_id is None
    db.get.assert_not_called()


@pytest.mark.parametrize("reason", ["default", "linked"])
def test_deactivation_refuses_profiles_still_in_use(reason):
    org = uuid4()
    item = profile(org, default=reason == "default")
    db = MagicMock()
    db.scalar.side_effect = [item, SimpleNamespace(module=EmailModule.FINANCE)]
    with pytest.raises(ValueError):
        EmailProfileService(db).set_profile_active(
            org, item.profile_id, is_active=False
        )
    assert item.is_active is True
    db.flush.assert_not_called()


def test_deactivation_is_scoped_and_allowed_after_routes_are_reassigned():
    org = uuid4()
    item = profile(org)
    db = MagicMock()
    db.scalar.side_effect = [item, None]
    result = EmailProfileService(db).set_profile_active(
        org, item.profile_id, is_active=False
    )
    assert result.is_active is False
    statements = [str(call.args[0]) for call in db.scalar.call_args_list]
    assert all("organization_id" in statement for statement in statements)
    assert "FOR UPDATE" in statements[0]
    db.flush.assert_called_once_with()
    db.commit.assert_not_called()


def test_legacy_inactive_route_fallback_is_explicit_and_preserved(caplog):
    org = uuid4()
    inactive = profile(org, active=False)
    default = profile(org, default=True)
    db = MagicMock()
    db.get.return_value = inactive
    db.scalar.side_effect = [
        SimpleNamespace(use_default=False, email_profile_id=inactive.profile_id),
        default,
    ]
    assert (
        EmailProfileService(db).get_profile_for_module(org, EmailModule.FINANCE)
        is default
    )
    event = next(
        row
        for row in caplog.records
        if getattr(row, "event", None) == "email_profile_route_fallback"
    )
    assert event.organization_id == str(org)
    assert event.email_module == "FINANCE"
    assert event.fallback_reason == "missing_or_inactive_profile"
