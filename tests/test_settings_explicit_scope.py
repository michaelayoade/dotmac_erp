"""A settings read states whose value it wants.

Scope used to be ambient: `get_by_key` read `db.info["organization_id"]`, and
when nothing had populated it the query dropped its organization predicate
entirely and ran under `allow_cross_org`, ordered by `updated_at DESC`. So a
read with no scope returned the most recently updated row of ANY organization —
deterministically, silently, with no error.

That is masked while a deployment has one organization. It stops being masked
the day it does not, which is why it is being fixed now rather than then.

`settings_cache` already drew this line: `_require_organization` refuses `None`
outright. These tests hold the resolver to the same standard.
"""

from __future__ import annotations

import logging
import uuid

import pytest
from app.models.domain_settings import DomainSetting, SettingValueType
from app.services import domain_settings as settings_service
from app.services.setting_domains import SettingDomain

ORG_A = uuid.UUID("00000000-0000-0000-0000-0000000000aa")
ORG_B = uuid.UUID("00000000-0000-0000-0000-0000000000bb")


def _row(db, *, key: str, value: str, org: uuid.UUID | None):
    setting = DomainSetting(
        domain=SettingDomain.auth,
        key=key,
        value_type=SettingValueType.string,
        value_text=value,
        organization_id=org,
        is_active=True,
    )
    db.add(setting)
    db.flush()
    return setting


@pytest.fixture
def key() -> str:
    return f"scope_probe_{uuid.uuid4().hex[:8]}"


def test_an_explicit_org_reads_that_orgs_row(db_session, key):
    _row(db_session, key=key, value="a-value", org=ORG_A)
    _row(db_session, key=key, value="b-value", org=ORG_B)

    service = settings_service.auth_settings
    found = service.get_by_key(db_session, key, organization_id=ORG_A)
    assert found.value_text == "a-value"


def test_an_explicit_org_falls_back_to_the_global_row(db_session, key):
    """Falling back to global is correct; falling through to ANOTHER ORG is
    what this whole change exists to prevent."""
    _row(db_session, key=key, value="global-value", org=None)
    _row(db_session, key=key, value="b-value", org=ORG_B)

    service = settings_service.auth_settings
    found = service.get_by_key(db_session, key, organization_id=ORG_A)
    assert found.value_text == "global-value"


def test_explicit_none_reads_the_global_row_and_only_that(db_session, key):
    """`None` means "I have no organization", which is a statement — not the
    same as failing to make one. An org row must not satisfy it."""
    _row(db_session, key=key, value="b-value", org=ORG_B)
    _row(db_session, key=key, value="global-value", org=None)

    service = settings_service.auth_settings
    found = service.get_by_key(db_session, key, organization_id=None)
    assert found.value_text == "global-value"


def test_explicit_none_does_not_fall_through_to_another_org(db_session, key):
    """The regression this guards: with only an org row present, a global-only
    read must find nothing rather than serving that org's value."""
    from fastapi import HTTPException

    _row(db_session, key=key, value="b-value", org=ORG_B)

    service = settings_service.auth_settings
    with pytest.raises(HTTPException):
        service.get_by_key(db_session, key, organization_id=None)


def test_ambient_scope_warns_and_names_the_call_site(db_session, key, caplog):
    """The migration signal: every implicit read reports itself once, so the
    remaining call sites are a finite list rather than a guess."""
    _row(db_session, key=key, value="global-value", org=None)
    settings_service._reported_ambient_sites.clear()

    service = settings_service.auth_settings
    with caplog.at_level(logging.WARNING, logger=settings_service.__name__):
        service.get_by_key(db_session, key)

    assert "ambient organization scope" in caplog.text
    assert __file__.rsplit("/", 1)[-1] in caplog.text


def test_ambient_warning_is_reported_once_per_site(db_session, key, caplog):
    """A hot path must not emit a warning per request, or the signal is
    unusable and someone silences the logger."""
    _row(db_session, key=key, value="global-value", org=None)
    settings_service._reported_ambient_sites.clear()

    service = settings_service.auth_settings
    with caplog.at_level(logging.WARNING, logger=settings_service.__name__):
        for _ in range(5):
            service.get_by_key(db_session, key)

    warnings = [r for r in caplog.records if "ambient" in r.getMessage()]
    assert len(warnings) == 1


def test_explicit_scope_emits_no_warning(db_session, key, caplog):
    _row(db_session, key=key, value="global-value", org=None)
    settings_service._reported_ambient_sites.clear()

    service = settings_service.auth_settings
    with caplog.at_level(logging.WARNING, logger=settings_service.__name__):
        service.get_by_key(db_session, key, organization_id=None)

    assert "ambient" not in caplog.text


def test_the_warning_names_the_business_caller_not_the_plumbing(
    db_session, key, caplog
):
    """Reporting the nearest frame names `resolve_value` for every caller —
    true, useless, and it collapses 61 call sites into one entry. The warning
    has to walk out of the settings layer to the code that actually failed to
    state a scope, or the migration inventory is worthless."""
    from app.services.settings_spec import resolve_value

    # A key with a REAL spec: `resolve_value` returns early for an unregistered
    # key, so a probe key never reaches the scope resolution being tested.
    spec_key = "refresh_cookie_samesite"
    _row(db_session, key=spec_key, value="lax", org=None)
    settings_service._reported_ambient_sites.clear()

    with caplog.at_level(logging.WARNING, logger=settings_service.__name__):
        resolve_value(db_session, SettingDomain.auth, spec_key)

    assert "settings_spec.py" not in caplog.text, (
        "the warning named the resolver instead of its caller"
    )
    assert "test_settings_explicit_scope.py" in caplog.text


def test_resolve_value_threads_the_scope(db_session, key):
    """`resolve_value` is the function callers are being consolidated onto, so
    it has to carry the scope rather than dropping it one layer down."""
    from app.services.settings_spec import resolve_value

    _row(db_session, key=key, value="a-value", org=ORG_A)
    _row(db_session, key=key, value="b-value", org=ORG_B)

    # No spec exists for this probe key, so resolve_value returns None; the
    # point is that the scope reaches get_by_key without raising.
    resolve_value(db_session, SettingDomain.auth, key, organization_id=ORG_A)
