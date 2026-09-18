"""Keep explicit module routes bound to active, eligible email profiles."""

BRANCH = "fix/erp-email-profile-routing-validation"
TITLE = "fix(email): validate active scoped routes and guard profile deactivation"
TESTS = ["tests/services/test_email_profile_routing_validation.py", "tests/finance/test_settings_web_email_profiles.py"]


def apply(change, write, rewrite):
    path = "app/services/email_profile.py"
    change(path, '''            profile = self.db.get(EmailProfile, profile_id)
            if not profile:
                raise ValueError(f"Email profile {profile_id} not found")''', '''            profile = self.db.get(EmailProfile, profile_id, with_for_update=True)
            if not profile:
                raise ValueError(f"Email profile {profile_id} not found")
            if profile.organization_id not in (None, organization_id):
                raise ValueError("Email profile is not available to this organization")
            if not profile.is_active:
                raise ValueError("Cannot route module email to an inactive profile")''')
    change(path, '''                        routing.email_profile_id,
                    )

        # Step 2:''', '''                        routing.email_profile_id,
                        extra={
                            "event": "email_profile_route_fallback",
                            "organization_id": str(organization_id),
                            "email_module": module.value,
                            "profile_id": str(routing.email_profile_id),
                            "fallback_reason": "missing_or_inactive_profile",
                        },
                    )

        # Step 2:''')
    change(path, "    def list_profiles(\n", '''    def set_profile_active(
        self,
        organization_id: UUID,
        profile_id: UUID,
        *,
        is_active: bool,
    ) -> EmailProfile:
        """Change a tenant-owned profile's state without silently breaking routes.

        Shared system profiles are not tenant-owned and cannot be modified
        through this method. Callers must first reassign explicit routes and
        the default profile before deactivation. The caller owns the commit.
        """
        profile = self.db.scalar(
            select(EmailProfile)
            .where(
                EmailProfile.profile_id == profile_id,
                EmailProfile.organization_id == organization_id,
            )
            .with_for_update()
        )
        if profile is None:
            raise ValueError("Email profile is not owned by this organization")
        if not is_active:
            if profile.is_default:
                raise ValueError("Choose another default email profile before deactivation")
            linked = self.db.scalar(
                select(ModuleEmailRouting)
                .where(
                    ModuleEmailRouting.organization_id == organization_id,
                    ModuleEmailRouting.email_profile_id == profile_id,
                    ModuleEmailRouting.use_default.is_(False),
                )
                .limit(1)
            )
            if linked is not None:
                raise ValueError("Reassign module email routes before deactivating this profile")
        profile.is_active = is_active
        self.db.flush()
        return profile

    def list_profiles(
''')
    web = "app/services/finance/settings_web.py"
    change(web, '''            if profile is None:
                profile = EmailProfile(
                    name=f"{module_def['label']} SMTP",''', '''            if profile is not None and profile.organization_id != organization_id:
                return False, "A shared or other organization's email profile cannot be edited here."

            if profile is None:
                profile = EmailProfile(
                    name=f"{module_def['label']} SMTP",''')
    change(web, '''            if routing:
                routing.email_profile_id = profile.profile_id
                routing.use_default = False
            else:
                routing = ModuleEmailRouting(
                    organization_id=organization_id,
                    module=module_def["module"],
                    email_profile_id=profile.profile_id,
                    use_default=False,
                )
                db.add(routing)''', '''            from app.services.email_profile import EmailProfileService

            EmailProfileService(db).set_module_routing(
                organization_id,
                module_def["module"],
                profile.profile_id,
                use_default=False,
            )''')
    write("tests/services/test_email_profile_routing_validation.py", '''"""Explicit email routes must not silently select unusable profiles."""

from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.models.email_profile import EmailModule
from app.services.email_profile import EmailProfileService


def profile(org, active=True, default=False):
    return SimpleNamespace(
        profile_id=uuid4(), organization_id=org, is_active=active,
        is_default=default, name="Synthetic SMTP",
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
        EmailProfileService(db).set_module_routing(org, EmailModule.FINANCE, item.profile_id)
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
    result = EmailProfileService(db).set_module_routing(org, EmailModule.FINANCE, item.profile_id)
    assert result.email_profile_id == item.profile_id
    assert result.use_default is False
    db.get.assert_called_once()
    assert db.get.call_args.kwargs["with_for_update"] is True
    db.commit.assert_not_called()


def test_explicit_default_selection_does_not_require_a_profile():
    db = MagicMock()
    db.scalar.return_value = SimpleNamespace(email_profile_id=uuid4(), use_default=False)
    result = EmailProfileService(db).set_module_routing(uuid4(), EmailModule.EXPENSE, None, use_default=True)
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
        EmailProfileService(db).set_profile_active(org, item.profile_id, is_active=False)
    assert item.is_active is True
    db.flush.assert_not_called()


def test_deactivation_is_scoped_and_allowed_after_routes_are_reassigned():
    org = uuid4()
    item = profile(org)
    db = MagicMock()
    db.scalar.side_effect = [item, None]
    result = EmailProfileService(db).set_profile_active(org, item.profile_id, is_active=False)
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
    assert EmailProfileService(db).get_profile_for_module(org, EmailModule.FINANCE) is default
    event = next(row for row in caplog.records if getattr(row, "event", None) == "email_profile_route_fallback")
    assert event.organization_id == str(org)
    assert event.email_module == "FINANCE"
    assert event.fallback_reason == "missing_or_inactive_profile"
''')
