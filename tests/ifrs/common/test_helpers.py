"""
Unit tests for common helper functions.

Tests for entity validation, retrieval, and status management.

Note: Tests for validate_unique_code and get_org_scoped_entity_by_field
require actual SQLAlchemy models and are better suited for integration tests.
These unit tests focus on the functions that can be tested with simple mocks.
"""

import uuid
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.services.finance.common.helpers import (
    get_org_scoped_entity,
    toggle_entity_status,
    activate_entity,
    deactivate_entity,
    get_entity_display_name,
)


# Test fixtures
@pytest.fixture
def organization_id() -> uuid.UUID:
    """Generate a test organization ID."""
    return uuid.uuid4()


@pytest.fixture
def entity_id() -> uuid.UUID:
    """Generate a test entity ID."""
    return uuid.uuid4()


@pytest.fixture
def mock_db_session():
    """Create a mock database session."""
    session = MagicMock()
    session.query = MagicMock(return_value=session)
    session.filter = MagicMock(return_value=session)
    session.first = MagicMock(return_value=None)
    session.all = MagicMock(return_value=[])
    session.add = MagicMock()
    session.commit = MagicMock()
    session.flush = MagicMock()
    session.refresh = MagicMock()
    session.delete = MagicMock()
    session.get = MagicMock(return_value=None)
    return session


class MockSupplier:
    """Mock Supplier model for testing."""

    __tablename__ = "suppliers"

    def __init__(
        self,
        supplier_id: uuid.UUID = None,
        organization_id: uuid.UUID = None,
        supplier_code: str = "SUP001",
        legal_name: str = "Test Supplier",
        is_active: bool = True,
    ):
        self.supplier_id = supplier_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.supplier_code = supplier_code
        self.legal_name = legal_name
        self.is_active = is_active


class MockCustomer:
    """Mock Customer model for testing."""

    __tablename__ = "customers"

    def __init__(
        self,
        customer_id: uuid.UUID = None,
        organization_id: uuid.UUID = None,
        customer_code: str = "CUST001",
        legal_name: str = "Test Customer",
        is_active: bool = True,
    ):
        self.customer_id = customer_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.customer_code = customer_code
        self.legal_name = legal_name
        self.is_active = is_active


# Tests for get_entity_display_name
class TestGetEntityDisplayName:
    """Tests for get_entity_display_name function."""

    def test_simple_name(self):
        """Test simple class name conversion."""
        assert get_entity_display_name(MockSupplier) == "Mock Supplier"

    def test_camel_case_name(self):
        """Test CamelCase class name conversion."""
        assert get_entity_display_name(MockCustomer) == "Mock Customer"


# Tests for get_org_scoped_entity
class TestGetOrgScopedEntity:
    """Tests for get_org_scoped_entity function."""

    def test_found_entity(self, mock_db_session, organization_id, entity_id):
        """Test successful entity retrieval."""
        supplier = MockSupplier(
            supplier_id=entity_id,
            organization_id=organization_id,
        )
        mock_db_session.get.return_value = supplier

        result = get_org_scoped_entity(
            db=mock_db_session,
            model_class=MockSupplier,
            entity_id=entity_id,
            org_id=organization_id,
        )

        assert result == supplier
        mock_db_session.get.assert_called_once()

    def test_not_found_raises(self, mock_db_session, organization_id, entity_id):
        """Test that HTTPException is raised when entity not found."""
        mock_db_session.get.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            get_org_scoped_entity(
                db=mock_db_session,
                model_class=MockSupplier,
                entity_id=entity_id,
                org_id=organization_id,
            )

        assert exc_info.value.status_code == 404

    def test_wrong_org_raises(self, mock_db_session, organization_id, entity_id):
        """Test that HTTPException is raised when entity belongs to different org."""
        different_org = uuid.uuid4()
        supplier = MockSupplier(
            supplier_id=entity_id,
            organization_id=different_org,
        )
        mock_db_session.get.return_value = supplier

        with pytest.raises(HTTPException) as exc_info:
            get_org_scoped_entity(
                db=mock_db_session,
                model_class=MockSupplier,
                entity_id=entity_id,
                org_id=organization_id,
            )

        assert exc_info.value.status_code == 404

    def test_not_found_returns_none(self, mock_db_session, organization_id, entity_id):
        """Test that None is returned when raise_on_missing=False."""
        mock_db_session.get.return_value = None

        result = get_org_scoped_entity(
            db=mock_db_session,
            model_class=MockSupplier,
            entity_id=entity_id,
            org_id=organization_id,
            raise_on_missing=False,
        )

        assert result is None


# Tests for toggle_entity_status
class TestToggleEntityStatus:
    """Tests for toggle_entity_status function."""

    def test_deactivate_entity(self, mock_db_session, organization_id, entity_id):
        """Test deactivating an entity."""
        supplier = MockSupplier(
            supplier_id=entity_id,
            organization_id=organization_id,
            is_active=True,
        )
        mock_db_session.get.return_value = supplier

        result = toggle_entity_status(
            db=mock_db_session,
            model_class=MockSupplier,
            entity_id=entity_id,
            org_id=organization_id,
            is_active=False,
        )

        assert result.is_active is False
        mock_db_session.commit.assert_called_once()
        mock_db_session.refresh.assert_called_once()

    def test_activate_entity(self, mock_db_session, organization_id, entity_id):
        """Test activating an entity."""
        supplier = MockSupplier(
            supplier_id=entity_id,
            organization_id=organization_id,
            is_active=False,
        )
        mock_db_session.get.return_value = supplier

        result = toggle_entity_status(
            db=mock_db_session,
            model_class=MockSupplier,
            entity_id=entity_id,
            org_id=organization_id,
            is_active=True,
        )

        assert result.is_active is True

    def test_pre_check_validation(self, mock_db_session, organization_id, entity_id):
        """Test that pre_check callback is called and can raise."""
        supplier = MockSupplier(
            supplier_id=entity_id,
            organization_id=organization_id,
        )
        mock_db_session.get.return_value = supplier

        def pre_check(db, entity):
            raise HTTPException(status_code=400, detail="Cannot deactivate")

        with pytest.raises(HTTPException) as exc_info:
            toggle_entity_status(
                db=mock_db_session,
                model_class=MockSupplier,
                entity_id=entity_id,
                org_id=organization_id,
                is_active=False,
                pre_check=pre_check,
            )

        assert exc_info.value.status_code == 400


# Tests for activate_entity and deactivate_entity convenience functions
class TestActivateDeactivateEntity:
    """Tests for activate_entity and deactivate_entity functions."""

    def test_activate_entity_convenience(
        self, mock_db_session, organization_id, entity_id
    ):
        """Test activate_entity convenience function."""
        supplier = MockSupplier(
            supplier_id=entity_id,
            organization_id=organization_id,
            is_active=False,
        )
        mock_db_session.get.return_value = supplier

        result = activate_entity(
            db=mock_db_session,
            model_class=MockSupplier,
            entity_id=entity_id,
            org_id=organization_id,
        )

        assert result.is_active is True

    def test_deactivate_entity_convenience(
        self, mock_db_session, organization_id, entity_id
    ):
        """Test deactivate_entity convenience function."""
        supplier = MockSupplier(
            supplier_id=entity_id,
            organization_id=organization_id,
            is_active=True,
        )
        mock_db_session.get.return_value = supplier

        result = deactivate_entity(
            db=mock_db_session,
            model_class=MockSupplier,
            entity_id=entity_id,
            org_id=organization_id,
        )

        assert result.is_active is False

    def test_deactivate_with_pre_check(
        self, mock_db_session, organization_id, entity_id
    ):
        """Test deactivate_entity with pre_check callback."""
        supplier = MockSupplier(
            supplier_id=entity_id,
            organization_id=organization_id,
        )
        mock_db_session.get.return_value = supplier

        def check_balance(db, entity):
            raise HTTPException(status_code=400, detail="Has outstanding balance")

        with pytest.raises(HTTPException) as exc_info:
            deactivate_entity(
                db=mock_db_session,
                model_class=MockSupplier,
                entity_id=entity_id,
                org_id=organization_id,
                pre_check=check_balance,
            )

        assert "outstanding balance" in str(exc_info.value.detail)
