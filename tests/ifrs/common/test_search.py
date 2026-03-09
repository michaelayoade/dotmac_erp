"""
Unit tests for common search and filter utilities.

Tests for search filter functions used in list queries.

Note: Tests that involve SQLAlchemy's or_() function require actual SQLAlchemy
columns and are better suited for integration tests. These unit tests focus on
the helper functions that can be tested with simple mocks.
"""

from datetime import date
from decimal import Decimal
from enum import Enum
from unittest.mock import MagicMock

import pytest

from app.services.finance.common.search import (
    apply_amount_range_filter,
    apply_date_range_filter,
    apply_status_filter,
    build_search_pattern,
)


class SampleStatus(Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    PENDING = "pending"


# Mock field class to simulate SQLAlchemy columns
class MockField:
    """Mock SQLAlchemy field for testing."""

    def __init__(self, name: str):
        self.name = name

    def ilike(self, pattern: str):
        result = MagicMock()
        result.__repr__ = lambda: f"{self.name} ILIKE '{pattern}'"
        return result

    def __eq__(self, other):
        result = MagicMock()
        result.__repr__ = lambda: f"{self.name} == {other}"
        return result

    def __ge__(self, other):
        result = MagicMock()
        result.__repr__ = lambda: f"{self.name} >= {other}"
        return result

    def __le__(self, other):
        result = MagicMock()
        result.__repr__ = lambda: f"{self.name} <= {other}"
        return result

    def in_(self, values):
        result = MagicMock()
        result.__repr__ = lambda: f"{self.name} IN {values}"
        return result

    def __hash__(self):
        return hash(self.name)


@pytest.fixture
def mock_query():
    """Create a mock SQLAlchemy query."""
    query = MagicMock()
    query.filter = MagicMock(return_value=query)
    query.where = query.filter
    return query


@pytest.fixture
def mock_date_field():
    """Create a mock date field."""
    return MockField("created_date")


@pytest.fixture
def mock_amount_field():
    """Create a mock amount field."""
    return MockField("amount")


@pytest.fixture
def mock_status_field():
    """Create a mock status field."""
    return MockField("status")


# Tests for build_search_pattern
class TestBuildSearchPattern:
    """Tests for build_search_pattern function."""

    def test_contains_pattern(self):
        """Test building contains pattern."""
        result = build_search_pattern("test")
        assert result == "%test%"

    def test_starts_with_pattern(self):
        """Test building starts_with pattern."""
        result = build_search_pattern("test", match_type="starts_with")
        assert result == "test%"

    def test_ends_with_pattern(self):
        """Test building ends_with pattern."""
        result = build_search_pattern("test", match_type="ends_with")
        assert result == "%test"

    def test_exact_pattern(self):
        """Test building exact pattern."""
        result = build_search_pattern("test", match_type="exact")
        assert result == "test"

    def test_escapes_special_chars(self):
        """Test that special SQL characters are escaped."""
        result = build_search_pattern("test%value")
        assert r"\%" in result

        result = build_search_pattern("test_value")
        assert r"\_" in result


# Tests for apply_date_range_filter
class TestApplyDateRangeFilter:
    """Tests for apply_date_range_filter function."""

    def test_no_dates_returns_unchanged(self, mock_query, mock_date_field):
        """Test that no dates returns query unchanged."""
        apply_date_range_filter(mock_query, mock_date_field)
        mock_query.filter.assert_not_called()

    def test_start_date_only(self, mock_query, mock_date_field):
        """Test applying only start date."""
        apply_date_range_filter(
            mock_query, mock_date_field, start_date=date(2024, 1, 1)
        )
        mock_query.filter.assert_called_once()

    def test_end_date_only(self, mock_query, mock_date_field):
        """Test applying only end date."""
        apply_date_range_filter(
            mock_query, mock_date_field, end_date=date(2024, 12, 31)
        )
        mock_query.filter.assert_called_once()

    def test_both_dates(self, mock_query, mock_date_field):
        """Test applying both start and end dates."""
        apply_date_range_filter(
            mock_query,
            mock_date_field,
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
        )
        assert mock_query.filter.call_count == 2


# Tests for apply_amount_range_filter
class TestApplyAmountRangeFilter:
    """Tests for apply_amount_range_filter function."""

    def test_no_amounts_returns_unchanged(self, mock_query, mock_amount_field):
        """Test that no amounts returns query unchanged."""
        apply_amount_range_filter(mock_query, mock_amount_field)
        mock_query.filter.assert_not_called()

    def test_min_amount_only(self, mock_query, mock_amount_field):
        """Test applying only min amount."""
        apply_amount_range_filter(
            mock_query, mock_amount_field, min_amount=Decimal("100")
        )
        mock_query.filter.assert_called_once()

    def test_max_amount_only(self, mock_query, mock_amount_field):
        """Test applying only max amount."""
        apply_amount_range_filter(
            mock_query, mock_amount_field, max_amount=Decimal("1000")
        )
        mock_query.filter.assert_called_once()

    def test_both_amounts(self, mock_query, mock_amount_field):
        """Test applying both min and max amounts."""
        apply_amount_range_filter(
            mock_query,
            mock_amount_field,
            min_amount=Decimal("100"),
            max_amount=Decimal("1000"),
        )
        assert mock_query.filter.call_count == 2


# Tests for apply_status_filter
class TestApplyStatusFilter:
    """Tests for apply_status_filter function."""

    def test_empty_statuses_returns_unchanged(self, mock_query, mock_status_field):
        """Test that empty statuses returns query unchanged."""
        apply_status_filter(mock_query, mock_status_field, None)
        mock_query.filter.assert_not_called()

        apply_status_filter(mock_query, mock_status_field, [])
        mock_query.filter.assert_not_called()

    def test_applies_status_filter(self, mock_query, mock_status_field):
        """Test applying status filter with values."""
        apply_status_filter(
            mock_query, mock_status_field, [SampleStatus.ACTIVE, SampleStatus.PENDING]
        )
        mock_query.filter.assert_called_once()
