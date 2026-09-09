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
        mock_db.flush.assert_called()

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
        mock_db.flush.assert_called()


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


class TestPrepareCustomer:
    """``prepare_customer`` -- the ONE real preparation step ``create_customer``
    also consumes (Michael's ruled shape, 2026-09-09): no SAVEPOINT, no
    fabricated ``customer_code``, and ``create_customer`` calls this method
    rather than duplicating its decisions
    (``tests/architecture/test_imports_adoption.py::
    test_customer_adapter_owns_no_transaction_or_session_factory`` forbids
    the import adapter from owning a transaction; an earlier version of
    this repair relocated that violation into a SAVEPOINT INSIDE this
    service instead of removing it, which Michael explicitly rejected).
    These tests are the sensitivity proof for the property that actually
    has to hold: the guard is a static scan of one adapter file, so a
    stray ``db.add``/``flush`` introduced HERE would be invisible to it.
    """

    def test_never_touches_the_session(self, mock_db, org_id):
        """Zero session mutation, ever -- the property the guard cannot see.

        If ``prepare_customer`` ever gained an ``add``/``flush``/``commit``/
        ``refresh``/``begin_nested``/``rollback`` call, this is what would
        catch it.
        """
        inp = CustomerInput(
            customer_type=MockCustomer().customer_type,
            customer_name="Preview Only Ltd",
            default_receivable_account_id=uuid4(),
        )

        result = CustomerService.prepare_customer(mock_db, org_id, inp)

        mock_db.add.assert_not_called()
        mock_db.flush.assert_not_called()
        mock_db.commit.assert_not_called()
        mock_db.refresh.assert_not_called()
        mock_db.begin_nested.assert_not_called()
        mock_db.rollback.assert_not_called()
        assert result.legal_name == "Preview Only Ltd"

    def test_never_allocates_a_customer_code(self, mock_db, org_id):
        """UNMEASURABLE FIELD, proven rather than merely documented.

        ``customer_code`` can only be known by ``_generate_customer_code``
        actually allocating one -- a real, row-locked, sequence-mutating
        write. This asserts the allocator is never even reached from
        preparation, and that the numbering state a real allocation would
        touch stays exactly as it was: nothing here can have advanced it.
        """
        with patch.object(CustomerService, "_generate_customer_code") as generate_code:
            inp = CustomerInput(
                customer_type=MockCustomer().customer_type,
                customer_name="No Code Yet Ltd",
                default_receivable_account_id=uuid4(),
            )

            result = CustomerService.prepare_customer(mock_db, org_id, inp)

            generate_code.assert_not_called()
        assert result.customer_code == ""

    def test_the_prepared_entity_is_transient(self, mock_db, org_id):
        """The prepared entity must never have been added to a session.

        This is what the retiring importer's ``construct_only`` entities are
        also captured at (``test_legacy_construct_only.py``) -- a shadow
        comparison refuses to compare two entities at different persistence
        stages, so this has to hold for the comparison to mean anything.
        """
        from sqlalchemy import inspect as sa_inspect

        inp = CustomerInput(
            customer_type=MockCustomer().customer_type,
            customer_name="Transient Co",
            default_receivable_account_id=uuid4(),
        )

        result = CustomerService.prepare_customer(mock_db, org_id, inp)

        state = sa_inspect(result)
        assert state.transient is True
        assert state.session is None

    def test_an_explicit_customer_code_is_observed_for_real(self, mock_db, org_id):
        """An explicit code is a REAL duplicate observation, not a preview.

        Unlike the blank-code case, a caller-supplied code CAN be checked
        for real -- it is a read, not an allocation -- so
        ``validate_unique_code`` must actually run.
        """
        with patch(
            "app.services.finance.ar.customer.validate_unique_code"
        ) as validate_code:
            inp = CustomerInput(
                customer_code="CUST-EXPLICIT",
                customer_type=MockCustomer().customer_type,
                customer_name="Explicit Co",
                default_receivable_account_id=uuid4(),
            )

            result = CustomerService.prepare_customer(mock_db, org_id, inp)

            validate_code.assert_called_once()
        assert result.customer_code == "CUST-EXPLICIT"

    def test_with_valid_parent_reads_but_does_not_mutate(self, mock_db, org_id):
        """A real parent-customer read still runs, and still touches nothing."""
        parent = MockCustomer(organization_id=org_id, customer_code="PARENT-010")
        parent_id = parent.customer_id

        with patch(
            "app.services.finance.ar.customer.get_org_scoped_entity",
            return_value=parent,
        ):
            inp = CustomerInput(
                customer_type=MockCustomer().customer_type,
                customer_name="Child Preview",
                default_receivable_account_id=uuid4(),
                parent_customer_id=parent_id,
            )
            result = CustomerService.prepare_customer(mock_db, org_id, inp)

        assert result.parent_customer_id == parent_id
        mock_db.add.assert_not_called()
        mock_db.flush.assert_not_called()

    def test_with_nonexistent_parent_raises(self, mock_db, org_id):
        """The same refusal ``create_customer`` gives, via the shared rule."""
        with patch(
            "app.services.finance.ar.customer.get_org_scoped_entity",
            return_value=None,
        ):
            inp = CustomerInput(
                customer_type=MockCustomer().customer_type,
                customer_name="Orphan Preview",
                default_receivable_account_id=uuid4(),
                parent_customer_id=uuid4(),
            )
            with pytest.raises(ValueError, match="Parent customer not found"):
                CustomerService.prepare_customer(mock_db, org_id, inp)


class TestCreateCustomerConsumesPreparation:
    """``create_customer`` consumes ``prepare_customer``'s result rather than
    deciding fields itself -- point 3 of the ruled shape: "the actual
    creation path consumes that same preparation result and remains the
    only writer."
    """

    def test_matches_the_preview_on_every_shared_field(self, mock_db, org_id):
        """One computation, two endings -- proven, not merely asserted.

        Same organization, same explicit code, same input: the persisted
        entity and the previewed one must decide every mapped business
        field identically, because ``create_customer`` now calls
        ``prepare_customer`` for that decision instead of re-deciding it.
        A future edit that forked the two would be caught here.
        """
        shared_input = CustomerInput(
            customer_code="CUST-SHARED-001",
            customer_type=MockCustomer().customer_type,
            customer_name="Shared Mapping Co",
            default_receivable_account_id=uuid4(),
            trading_name="Shared Co",
            credit_limit=Decimal("12345.00"),
            payment_terms_days=60,
        )

        with patch("app.services.finance.ar.customer.validate_unique_code"):
            persisted = CustomerService.create_customer(mock_db, org_id, shared_input)
            previewed = CustomerService.prepare_customer(mock_db, org_id, shared_input)

        mapped_fields = (
            "organization_id",
            "customer_code",
            "customer_type",
            "legal_name",
            "trading_name",
            "credit_limit",
            "credit_terms_days",
            "ar_control_account_id",
        )
        for field in mapped_fields:
            assert getattr(persisted, field) == getattr(previewed, field), field

    def test_only_create_customer_allocates_a_code(self, mock_db, org_id):
        """The allocator runs exactly once, and only on the writing path."""
        with (
            patch("app.services.finance.ar.customer.validate_unique_code"),
            patch.object(
                CustomerService, "_generate_customer_code", return_value="CUST-00042"
            ) as generate_code,
        ):
            inp = CustomerInput(
                customer_type=MockCustomer().customer_type,
                customer_name="Allocated Co",
                default_receivable_account_id=uuid4(),
            )

            result = CustomerService.create_customer(mock_db, org_id, inp)

            generate_code.assert_called_once()
        assert result.customer_code == "CUST-00042"

    def test_an_invalid_parent_is_refused_before_any_code_is_allocated(
        self, mock_db, org_id
    ):
        """The reordering this repair made: preparation (including parent
        validation) now runs BEFORE code allocation, so a bad parent no
        longer burns a real sequence number for a customer that is never
        created. An earlier version of ``create_customer`` allocated a code
        first and validated the parent second.
        """
        with (
            patch(
                "app.services.finance.ar.customer.get_org_scoped_entity",
                return_value=None,
            ),
            patch.object(CustomerService, "_generate_customer_code") as generate_code,
        ):
            inp = CustomerInput(
                customer_type=MockCustomer().customer_type,
                customer_name="Orphan Co",
                default_receivable_account_id=uuid4(),
                parent_customer_id=uuid4(),
            )

            with pytest.raises(ValueError, match="Parent customer not found"):
                CustomerService.create_customer(mock_db, org_id, inp)

            generate_code.assert_not_called()
