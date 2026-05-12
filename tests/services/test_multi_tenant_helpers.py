"""Tests for get_or_404_for_org — sugar over db.get + 404."""

from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

import pytest


def test_returns_object_when_found():
    from app.services.common.multi_tenant import get_or_404_for_org

    expected = MagicMock()
    db = MagicMock()
    db.get.return_value = expected

    result = get_or_404_for_org(db, MagicMock, uuid4())

    assert result is expected


def test_raises_not_found_when_db_returns_none():
    """The listener returns None when the PK exists in another org. The
    helper translates None → NotFoundError so callers don't have to."""
    from app.errors import NotFoundError  # adapt path per your verification
    from app.services.common.multi_tenant import get_or_404_for_org

    db = MagicMock()
    db.get.return_value = None

    class FakeModel:
        __name__ = "FakeModel"

    with pytest.raises(NotFoundError) as exc:
        get_or_404_for_org(db, FakeModel, uuid4())

    assert "FakeModel" in str(exc.value)


def test_does_not_leak_existence_on_cross_org():
    """The error message is the same regardless of whether the PK doesn't
    exist OR exists in another org. The helper just translates None to
    404; both cases pass through identically."""
    from app.errors import NotFoundError
    from app.services.common.multi_tenant import get_or_404_for_org

    db = MagicMock()
    db.get.return_value = None

    class FakeModel:
        __name__ = "Invoice"

    with pytest.raises(NotFoundError) as exc:
        get_or_404_for_org(db, FakeModel, uuid4())

    # Message must NOT differ between "not found" and "wrong org".
    assert "wrong org" not in str(exc.value).lower()
    assert "tenant" not in str(exc.value).lower()
