"""
Tests for app/services/common.py

Tests for core utility functions:
- coerce_uuid: Converts string/UUID values to UUID objects
- apply_ordering: Adds ORDER BY clause to queries with validation
- apply_pagination: Adds LIMIT/OFFSET clause to queries
"""

import uuid
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.services.common import coerce_uuid, apply_ordering, apply_pagination


# ============ TestCoerceUuid ============

class TestCoerceUuid:
    """Tests for the coerce_uuid function."""

    def test_coerce_uuid_none_returns_none(self):
        """Passing None should return None."""
        result = coerce_uuid(None)
        assert result is None

    def test_coerce_uuid_string_converts(self):
        """A valid UUID string should be converted to a UUID object."""
        uuid_str = "12345678-1234-5678-1234-567812345678"
        result = coerce_uuid(uuid_str)
        assert isinstance(result, uuid.UUID)
        assert str(result) == uuid_str

    def test_coerce_uuid_instance_passthrough(self):
        """A UUID instance should be returned as-is."""
        original = uuid.UUID("12345678-1234-5678-1234-567812345678")
        result = coerce_uuid(original)
        assert result is original
        assert isinstance(result, uuid.UUID)

    def test_coerce_uuid_invalid_raises_valueerror(self):
        """An invalid UUID string should raise ValueError when raise_http=False."""
        with pytest.raises(ValueError):
            coerce_uuid("not-a-uuid", raise_http=False)

    def test_coerce_uuid_with_dashes(self):
        """A UUID string with dashes should be converted correctly."""
        uuid_str = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        result = coerce_uuid(uuid_str)
        assert isinstance(result, uuid.UUID)
        assert str(result) == uuid_str

    def test_coerce_uuid_without_dashes(self):
        """A UUID string without dashes should be converted correctly."""
        uuid_str = "12345678123456781234567812345678"
        result = coerce_uuid(uuid_str)
        assert isinstance(result, uuid.UUID)
        # Python's UUID adds dashes when converting to string
        assert str(result) == "12345678-1234-5678-1234-567812345678"


# ============ TestApplyOrdering ============

class TestApplyOrdering:
    """Tests for the apply_ordering function."""

    def test_apply_ordering_asc(self):
        """Ascending ordering should call asc() on the column."""
        mock_query = MagicMock()
        mock_column = MagicMock()
        mock_column.asc.return_value = "asc_column"
        mock_query.order_by.return_value = mock_query

        allowed_columns = {"name": mock_column}

        result = apply_ordering(mock_query, "name", "asc", allowed_columns)

        mock_column.asc.assert_called_once()
        mock_query.order_by.assert_called_once_with("asc_column")
        assert result == mock_query

    def test_apply_ordering_desc(self):
        """Descending ordering should call desc() on the column."""
        mock_query = MagicMock()
        mock_column = MagicMock()
        mock_column.desc.return_value = "desc_column"
        mock_query.order_by.return_value = mock_query

        allowed_columns = {"name": mock_column}

        result = apply_ordering(mock_query, "name", "desc", allowed_columns)

        mock_column.desc.assert_called_once()
        mock_query.order_by.assert_called_once_with("desc_column")
        assert result == mock_query

    def test_apply_ordering_invalid_column_raises_400(self):
        """An invalid order_by column should raise HTTPException with 400."""
        mock_query = MagicMock()
        mock_column = MagicMock()
        allowed_columns = {"name": mock_column, "created_at": mock_column}

        with pytest.raises(HTTPException) as exc_info:
            apply_ordering(mock_query, "invalid_column", "asc", allowed_columns)

        assert exc_info.value.status_code == 400
        assert "Invalid order_by" in exc_info.value.detail
        assert "created_at" in exc_info.value.detail
        assert "name" in exc_info.value.detail

    def test_apply_ordering_returns_query(self):
        """The function should return the modified query object."""
        mock_query = MagicMock()
        mock_column = MagicMock()
        mock_column.asc.return_value = "asc_column"
        modified_query = MagicMock()
        mock_query.order_by.return_value = modified_query

        allowed_columns = {"name": mock_column}

        result = apply_ordering(mock_query, "name", "asc", allowed_columns)

        assert result is modified_query


# ============ TestApplyPagination ============

class TestApplyPagination:
    """Tests for the apply_pagination function."""

    def test_apply_pagination_limit_offset(self):
        """Pagination should apply both limit and offset."""
        mock_query = MagicMock()
        limited_query = MagicMock()
        offset_query = MagicMock()

        mock_query.limit.return_value = limited_query
        limited_query.offset.return_value = offset_query

        result = apply_pagination(mock_query, limit=10, offset=20)

        mock_query.limit.assert_called_once_with(10)
        limited_query.offset.assert_called_once_with(20)
        assert result is offset_query

    def test_apply_pagination_zero_limit(self):
        """Zero limit should be passed through to the query."""
        mock_query = MagicMock()
        limited_query = MagicMock()
        offset_query = MagicMock()

        mock_query.limit.return_value = limited_query
        limited_query.offset.return_value = offset_query

        result = apply_pagination(mock_query, limit=0, offset=0)

        mock_query.limit.assert_called_once_with(0)
        limited_query.offset.assert_called_once_with(0)

    def test_apply_pagination_large_offset(self):
        """Large offset values should be passed through correctly."""
        mock_query = MagicMock()
        limited_query = MagicMock()
        offset_query = MagicMock()

        mock_query.limit.return_value = limited_query
        limited_query.offset.return_value = offset_query

        result = apply_pagination(mock_query, limit=50, offset=10000)

        mock_query.limit.assert_called_once_with(50)
        limited_query.offset.assert_called_once_with(10000)

    def test_apply_pagination_returns_query(self):
        """The function should return the modified query object."""
        mock_query = MagicMock()
        limited_query = MagicMock()
        final_query = MagicMock()

        mock_query.limit.return_value = limited_query
        limited_query.offset.return_value = final_query

        result = apply_pagination(mock_query, limit=10, offset=5)

        assert result is final_query

    def test_apply_pagination_chaining(self):
        """Pagination should allow query chaining."""
        mock_query = MagicMock()
        limited_query = MagicMock()
        offset_query = MagicMock()

        mock_query.limit.return_value = limited_query
        limited_query.offset.return_value = offset_query
        offset_query.all.return_value = ["item1", "item2"]

        result = apply_pagination(mock_query, limit=10, offset=0)
        items = result.all()

        assert items == ["item1", "item2"]
