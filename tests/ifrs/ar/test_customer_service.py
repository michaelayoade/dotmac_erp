"""
Tests for CustomerService.
"""

from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.services.finance.ar.customer import (
    CustomerInput,
    CustomerService,
)
from tests.ifrs.ar.conftest import (
    MockCustomer,
    MockInvoice,
    MockInvoiceStatus,
    MockRiskCategory,
)


@pytest.fixture
def mock_db():
    """Create mock database session."""
    return MagicMock()


@pytest.fixture
def org_id():
    """Create test organization ID."""
    return uuid4()


@pytest.fixture
def user_id():
    """Create test user ID."""
    return uuid4()


@pytest.fixture
def sample_customer_input():
    """Create sample customer input."""
    from app.models.finance.ar.customer import CustomerType, RiskCategory

    return CustomerInput(
        customer_code="CUS-001",
        customer_type=CustomerType.COMPANY,
        customer_name="Acme Corporation",
        default_receivable_account_id=uuid4(),
        trading_name="Acme Corp",
        tax_id="12-3456789",
        credit_limit=Decimal("50000.00"),
        payment_terms_days=30,
        currency_code="USD",
        risk_category=RiskCategory.MEDIUM,
    )


class TestCreateCustomer:
    """Tests for create_customer method."""

    def test_create_customer_success(self, mock_db, org_id, sample_customer_input):
        """Test successful customer creation."""
        # validate_unique_code uses db.query().filter().first() -> falls back to db.scalar()
        mock_db.scalar.return_value = None  # No duplicate

        with (
            patch(
                "app.services.finance.ar.customer.validate_unique_code",
            ),
            patch.object(
                CustomerService, "_generate_customer_code", return_value="CUS-001"
            ),
        ):
            CustomerService.create_customer(mock_db, org_id, sample_customer_input)

            mock_db.add.assert_called_once()
            mock_db.flush.assert_called()
            mock_db.refresh.assert_called_once()

    def test_create_duplicate_customer_code_fails(
        self, mock_db, org_id, sample_customer_input
    ):
        """Test that duplicate customer code fails."""
        from fastapi import HTTPException

        # Existing customer with same code
        existing = MockCustomer(
            organization_id=org_id,
            customer_code=sample_customer_input.customer_code,
        )

        # validate_unique_code tries db.query first, falls back to db.scalar
        mock_db.scalar.return_value = existing

        with patch(
            "app.services.finance.ar.customer.validate_unique_code",
            side_effect=HTTPException(
                status_code=400,
                detail="Customer code 'CUS-001' already exists",
            ),
        ):
            with pytest.raises(HTTPException) as exc:
                CustomerService.create_customer(mock_db, org_id, sample_customer_input)

        assert exc.value.status_code == 400
        assert "already exists" in exc.value.detail


class TestUpdateCustomer:
    """Tests for update_customer method."""

    def test_update_customer_success(self, mock_db, org_id, sample_customer_input):
        """Test successful customer update."""
        customer = MockCustomer(
            organization_id=org_id,
            customer_code="OLD-CODE",
        )
        mock_db.get.return_value = customer

        with (
            patch(
                "app.services.finance.ar.customer.get_org_scoped_entity",
                return_value=customer,
            ),
            patch(
                "app.services.finance.ar.customer.validate_unique_code",
            ),
        ):
            result = CustomerService.update_customer(
                mock_db, org_id, customer.customer_id, sample_customer_input
            )

        mock_db.flush.assert_called()
        assert result.customer_code == sample_customer_input.customer_code

    def test_update_nonexistent_customer_fails(
        self, mock_db, org_id, sample_customer_input
    ):
        """Test updating non-existent customer fails."""
        from fastapi import HTTPException

        mock_db.get.return_value = None

        with patch(
            "app.services.finance.ar.customer.get_org_scoped_entity",
            return_value=None,
        ):
            with pytest.raises(HTTPException) as exc:
                CustomerService.update_customer(
                    mock_db, org_id, uuid4(), sample_customer_input
                )

        assert exc.value.status_code == 404

    def test_update_wrong_organization_fails(
        self, mock_db, org_id, sample_customer_input
    ):
        """Test updating customer from wrong organization fails."""
        from fastapi import HTTPException

        customer = MockCustomer(
            organization_id=uuid4(),  # Different org
            customer_code="OLD-CODE",
        )
        mock_db.get.return_value = customer

        # get_org_scoped_entity checks organization_id match, returns None for wrong org
        with patch(
            "app.services.finance.ar.customer.get_org_scoped_entity",
            return_value=None,
        ):
            with pytest.raises(HTTPException) as exc:
                CustomerService.update_customer(
                    mock_db, org_id, customer.customer_id, sample_customer_input
                )

        assert exc.value.status_code == 404


class TestUpdateCreditLimit:
    """Tests for update_credit_limit method."""

    def test_update_credit_limit_success(self, mock_db, org_id):
        """Test successful credit limit update."""
        customer = MockCustomer(
            organization_id=org_id,
            credit_limit=Decimal("10000.00"),
        )
        mock_db.get.return_value = customer

        new_limit = Decimal("50000.00")

        with patch(
            "app.services.finance.ar.customer.get_org_scoped_entity",
            return_value=customer,
        ):
            result = CustomerService.update_credit_limit(
                mock_db, org_id, customer.customer_id, new_limit
            )

        assert result.credit_limit == new_limit
        mock_db.flush.assert_called()


class TestUpdateRiskCategory:
    """Tests for update_risk_category method."""

    def test_update_risk_category_success(self, mock_db, org_id):
        """Test successful risk category update."""
        from app.models.finance.ar.customer import RiskCategory

        customer = MockCustomer(
            organization_id=org_id,
            risk_category=MockRiskCategory.MEDIUM,
        )
        mock_db.get.return_value = customer

        with patch(
            "app.services.finance.ar.customer.get_org_scoped_entity",
            return_value=customer,
        ):
            result = CustomerService.update_risk_category(
                mock_db, org_id, customer.customer_id, RiskCategory.HIGH
            )

        assert result.risk_category == RiskCategory.HIGH
        mock_db.flush.assert_called()


class TestDeactivateCustomer:
    """Tests for deactivate_customer method."""

    def test_deactivate_customer_success(self, mock_db, org_id):
        """Test successful customer deactivation."""
        customer = MockCustomer(organization_id=org_id, is_active=True)
        mock_db.get.return_value = customer

        # toggle_entity_status calls get_org_scoped_entity which uses db.get
        result = CustomerService.deactivate_customer(
            mock_db, org_id, customer.customer_id
        )

        assert result.is_active is False
        mock_db.commit.assert_called()

    def test_deactivate_nonexistent_customer_fails(self, mock_db, org_id):
        """Test deactivating non-existent customer fails."""
        from fastapi import HTTPException

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc:
            CustomerService.deactivate_customer(mock_db, org_id, uuid4())

        assert exc.value.status_code == 404


class TestActivateCustomer:
    """Tests for activate_customer method."""

    def test_activate_customer_success(self, mock_db, org_id):
        """Test successful customer activation."""
        customer = MockCustomer(organization_id=org_id, is_active=False)
        mock_db.get.return_value = customer

        result = CustomerService.activate_customer(
            mock_db, org_id, customer.customer_id
        )

        assert result.is_active is True
        mock_db.commit.assert_called()


class TestCheckCreditLimit:
    """Tests for check_credit_limit method."""

    def test_check_credit_within_limit(self, mock_db, org_id):
        """Test credit check within limit."""
        customer = MockCustomer(
            organization_id=org_id,
            credit_limit=Decimal("10000.00"),
        )
        mock_db.get.return_value = customer

        # Mock outstanding invoices via db.scalars().all()
        invoice = MockInvoice(
            customer_id=customer.customer_id,
            total_amount=Decimal("2000.00"),
            amount_paid=Decimal("0"),
            status=MockInvoiceStatus.POSTED,
        )

        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [invoice]
        mock_db.scalars.return_value = mock_scalars

        with patch(
            "app.services.finance.ar.customer.get_org_scoped_entity",
            return_value=customer,
        ):
            is_within, current_balance, available = CustomerService.check_credit_limit(
                mock_db, org_id, customer.customer_id, Decimal("5000.00")
            )

        assert is_within is True
        assert current_balance == Decimal("2000.00")
        assert available == Decimal("8000.00")

    def test_check_credit_exceeds_limit(self, mock_db, org_id):
        """Test credit check exceeding limit."""
        customer = MockCustomer(
            organization_id=org_id,
            credit_limit=Decimal("5000.00"),
        )
        mock_db.get.return_value = customer

        # Mock outstanding invoices via db.scalars().all()
        invoice = MockInvoice(
            customer_id=customer.customer_id,
            total_amount=Decimal("4000.00"),
            amount_paid=Decimal("0"),
            status=MockInvoiceStatus.POSTED,
        )

        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [invoice]
        mock_db.scalars.return_value = mock_scalars

        with patch(
            "app.services.finance.ar.customer.get_org_scoped_entity",
            return_value=customer,
        ):
            is_within, current_balance, available = CustomerService.check_credit_limit(
                mock_db, org_id, customer.customer_id, Decimal("2000.00")
            )

        assert is_within is False
        assert current_balance == Decimal("4000.00")
        assert available == Decimal("1000.00")

    def test_check_credit_no_limit(self, mock_db, org_id):
        """Test credit check with no limit (unlimited)."""
        customer = MockCustomer(
            organization_id=org_id,
            credit_limit=None,
        )
        mock_db.get.return_value = customer

        with patch(
            "app.services.finance.ar.customer.get_org_scoped_entity",
            return_value=customer,
        ):
            is_within, current_balance, available = CustomerService.check_credit_limit(
                mock_db, org_id, customer.customer_id, Decimal("1000000.00")
            )

        assert is_within is True
        assert available == Decimal("999999999")


class TestGetCustomer:
    """Tests for get method."""

    def test_get_existing_customer(self, mock_db, org_id):
        """Test getting existing customer."""
        customer = MockCustomer(organization_id=org_id)
        mock_db.get.return_value = customer

        with patch(
            "app.services.finance.ar.customer.get_org_scoped_entity",
            return_value=customer,
        ):
            result = CustomerService.get(mock_db, org_id, str(customer.customer_id))

        assert result == customer

    def test_get_nonexistent_customer_raises(self, mock_db, org_id):
        """Test getting non-existent customer raises exception."""
        from fastapi import HTTPException

        mock_db.get.return_value = None

        with patch(
            "app.services.finance.ar.customer.get_org_scoped_entity",
            side_effect=HTTPException(status_code=404, detail="Customer not found"),
        ):
            with pytest.raises(HTTPException) as exc:
                CustomerService.get(mock_db, org_id, str(uuid4()))

        assert exc.value.status_code == 404


class TestGetCustomerByCode:
    """Tests for get_by_code method."""

    def test_get_customer_by_code(self, mock_db, org_id):
        """Test getting customer by code."""
        customer = MockCustomer(
            organization_id=org_id,
            customer_code="CUS-001",
        )
        # get_by_code uses db.scalar(select(...))
        mock_db.scalar.return_value = customer

        result = CustomerService.get_by_code(mock_db, org_id, "CUS-001")

        assert result == customer

    def test_get_customer_by_code_not_found(self, mock_db, org_id):
        """Test getting non-existent customer by code returns None."""
        mock_db.scalar.return_value = None

        result = CustomerService.get_by_code(mock_db, org_id, "NOTFOUND")

        assert result is None


class TestListCustomers:
    """Tests for list method."""

    def test_list_with_filters(self, mock_db, org_id):
        """Test listing customers with filters."""
        customers = [MockCustomer(organization_id=org_id)]
        # list() uses db.scalars(stmt).all()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = customers
        mock_db.scalars.return_value = mock_scalars

        result = CustomerService.list(
            mock_db,
            organization_id=str(org_id),
            is_active=True,
        )

        assert result == customers

    def test_list_with_search(self, mock_db, org_id):
        """Test listing customers with search."""
        customers = [MockCustomer(organization_id=org_id, legal_name="Acme Corp")]
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = customers
        mock_db.scalars.return_value = mock_scalars

        result = CustomerService.list(
            mock_db,
            organization_id=str(org_id),
            search="Acme",
        )

        assert result == customers


class TestGetCustomerSummary:
    """Tests for get_customer_summary method."""

    def test_get_customer_summary(self, mock_db, org_id):
        """Test getting customer summary with balance info."""
        customer = MockCustomer(
            organization_id=org_id,
            credit_limit=Decimal("10000.00"),
        )
        mock_db.get.return_value = customer

        # Mock outstanding invoices via db.scalars().all()
        invoice1 = MockInvoice(
            customer_id=customer.customer_id,
            total_amount=Decimal("1000.00"),
            amount_paid=Decimal("0"),
            status=MockInvoiceStatus.POSTED,
        )
        invoice2 = MockInvoice(
            customer_id=customer.customer_id,
            total_amount=Decimal("500.00"),
            amount_paid=Decimal("200.00"),
            status=MockInvoiceStatus.PARTIALLY_PAID,
        )

        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [invoice1, invoice2]
        mock_db.scalars.return_value = mock_scalars

        with patch(
            "app.services.finance.ar.customer.get_org_scoped_entity",
            return_value=customer,
        ):
            result = CustomerService.get_customer_summary(
                mock_db, org_id, customer.customer_id
            )

        assert result["customer_id"] == customer.customer_id
        assert result["outstanding_invoice_count"] == 2
        # balance_due = 1000 + 300 = 1300
        assert result["outstanding_balance"] == Decimal("1300.00")
        assert result["available_credit"] == Decimal("8700.00")

    def test_get_customer_summary_not_found(self, mock_db, org_id):
        """Test getting summary for non-existent customer."""
        from fastapi import HTTPException

        mock_db.get.return_value = None

        with patch(
            "app.services.finance.ar.customer.get_org_scoped_entity",
            return_value=None,
        ):
            with pytest.raises(HTTPException) as exc:
                CustomerService.get_customer_summary(mock_db, org_id, uuid4())

        assert exc.value.status_code == 404


# ===========================================================================
# Parent-Child Customer Hierarchy
# ===========================================================================


class TestParentChildCustomer:
    """Tests for parent-child customer hierarchy (ISP reseller model)."""

    def test_create_child_with_valid_parent(self, mock_db, org_id):
        """Creating a child customer with a valid parent succeeds."""
        parent = MockCustomer(organization_id=org_id, customer_code="PARENT-001")
        parent_id = parent.customer_id

        with (
            patch(
                "app.services.finance.ar.customer.get_org_scoped_entity",
                return_value=parent,
            ),
            patch(
                "app.services.finance.ar.customer.validate_unique_code",
            ),
            patch.object(
                CustomerService, "_generate_customer_code", return_value="CUST-99999"
            ),
        ):
            inp = CustomerInput(
                customer_type=MockCustomer().customer_type,
                customer_name="Child Customer",
                default_receivable_account_id=uuid4(),
                parent_customer_id=parent_id,
            )
            result = CustomerService.create_customer(mock_db, org_id, inp)

        assert result.parent_customer_id == parent_id

    def test_create_child_with_nonexistent_parent(self, mock_db, org_id):
        """Creating a child with a non-existent parent raises ValueError."""
        with (
            patch(
                "app.services.finance.ar.customer.get_org_scoped_entity",
                return_value=None,
            ),
            patch(
                "app.services.finance.ar.customer.validate_unique_code",
            ),
            patch.object(
                CustomerService, "_generate_customer_code", return_value="CUST-99999"
            ),
        ):
            inp = CustomerInput(
                customer_type=MockCustomer().customer_type,
                customer_name="Orphan Customer",
                default_receivable_account_id=uuid4(),
                parent_customer_id=uuid4(),
            )
            with pytest.raises(ValueError, match="Parent customer not found"):
                CustomerService.create_customer(mock_db, org_id, inp)

    def test_update_self_reference_raises_error(self, mock_db, org_id):
        """Setting parent_customer_id to self raises ValueError."""
        customer = MockCustomer(organization_id=org_id)
        cust_id = customer.customer_id

        with patch(
            "app.services.finance.ar.customer.get_org_scoped_entity",
            return_value=customer,
        ):
            inp = CustomerInput(
                customer_type=customer.customer_type,
                customer_name=customer.legal_name,
                customer_code=customer.customer_code,
                default_receivable_account_id=customer.ar_control_account_id,
                parent_customer_id=cust_id,  # Self-reference
            )
            with pytest.raises(ValueError, match="cannot be its own parent"):
                CustomerService.update_customer(mock_db, org_id, cust_id, inp)

    def test_update_set_parent(self, mock_db, org_id):
        """Updating a customer to set a valid parent works."""
        parent = MockCustomer(organization_id=org_id, customer_code="PARENT-002")
        child = MockCustomer(organization_id=org_id, customer_code="CHILD-001")

        # First call returns child (for the update target), second returns parent (for validation)
        def side_effect(**kwargs):
            entity_id = kwargs.get("entity_id")
            if str(entity_id) == str(child.customer_id):
                return child
            if str(entity_id) == str(parent.customer_id):
                return parent
            return None

        with patch(
            "app.services.finance.ar.customer.get_org_scoped_entity",
            side_effect=side_effect,
        ):
            inp = CustomerInput(
                customer_type=child.customer_type,
                customer_name=child.legal_name,
                customer_code=child.customer_code,
                default_receivable_account_id=child.ar_control_account_id,
                parent_customer_id=parent.customer_id,
            )
            result = CustomerService.update_customer(
                mock_db, org_id, child.customer_id, inp
            )

        assert result.parent_customer_id == parent.customer_id

    def test_list_children(self, mock_db, org_id):
        """list_children returns children for a given parent."""
        parent_id = uuid4()
        child1 = MockCustomer(
            organization_id=org_id,
            parent_customer_id=parent_id,
            customer_code="CHILD-1",
        )
        child2 = MockCustomer(
            organization_id=org_id,
            parent_customer_id=parent_id,
            customer_code="CHILD-2",
        )

        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [child1, child2]
        mock_db.scalars.return_value = mock_scalars

        result = CustomerService.list_children(mock_db, org_id, parent_id)

        assert len(result) == 2
        assert result[0].customer_code == "CHILD-1"
        assert result[1].customer_code == "CHILD-2"

    def test_create_without_parent(self, mock_db, org_id):
        """Creating a customer without parent_customer_id works (default case)."""
        with (
            patch(
                "app.services.finance.ar.customer.get_org_scoped_entity",
            ),
            patch(
                "app.services.finance.ar.customer.validate_unique_code",
            ),
            patch.object(
                CustomerService, "_generate_customer_code", return_value="CUST-99999"
            ),
        ):
            inp = CustomerInput(
                customer_type=MockCustomer().customer_type,
                customer_name="Standalone Customer",
                default_receivable_account_id=uuid4(),
                # parent_customer_id defaults to None
            )
            result = CustomerService.create_customer(mock_db, org_id, inp)

        assert result.parent_customer_id is None
