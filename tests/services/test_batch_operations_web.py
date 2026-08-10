"""Authorization on the batch-operations screen.

The interesting case is the third one. `WebAuthContext.organization_id` is
`UUID | None`, so an authenticated admin whose session carries no organization
would have passed `None` into a scoped query — producing an UNSCOPED read that
lists every tenant's runs. That is exactly the fail-silent shape this screen
was built to make visible, so it fails loudly instead.

mypy found it, not a test; the test is here so it stays found.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from fastapi.responses import RedirectResponse

from app.services.admin.batch_operations_web import batch_operations_web_service

ORG = uuid.uuid4()


def _request(path: str = "/admin/batch-operations", query: str = ""):
    request = MagicMock()
    request.url.path = path
    request.url.query = query
    return request


def _auth(*, authenticated=True, admin=True, org=ORG):
    auth = MagicMock()
    auth.is_authenticated = authenticated
    auth.is_admin = admin
    auth.organization_id = org
    return auth


def _scope(auth, request=None):
    return batch_operations_web_service._authorized_scope(request or _request(), auth)


def test_an_admin_gets_their_organization_back():
    assert _scope(_auth()) == ORG


def test_an_anonymous_visitor_is_redirected_to_login():
    result = _scope(None)
    assert isinstance(result, RedirectResponse)
    assert result.status_code == 302
    assert "/admin/login" in result.headers["location"]


def test_the_redirect_preserves_where_they_were_going():
    result = _scope(
        _auth(authenticated=False), _request("/admin/batch-operations", "page=2")
    )
    assert "next=" in result.headers["location"]
    assert "page%3D2" in result.headers["location"]


def test_a_non_admin_is_forbidden():
    with pytest.raises(HTTPException) as caught:
        _scope(_auth(admin=False))
    assert caught.value.status_code == 403


def test_an_admin_with_no_organization_fails_loudly():
    """The one mypy caught. Returning None here would have reached
    `recent(db, organization_id=None)` — an unscoped read across every tenant,
    silently succeeding."""
    with pytest.raises(HTTPException) as caught:
        _scope(_auth(org=None))
    assert caught.value.status_code == 409


def test_the_scope_is_returned_not_merely_validated():
    """Shape matters: the guard hands back the organization, so a caller
    cannot reach a query without one. A pass/fail guard leaves the caller free
    to read `auth.organization_id` itself and get None."""
    import inspect

    sig = inspect.signature(batch_operations_web_service._authorized_scope)
    assert "uuid.UUID" in str(sig.return_annotation) or "UUID" in str(
        sig.return_annotation
    )
