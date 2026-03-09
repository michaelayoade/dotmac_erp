"""
Tests for ConsolidationService (IFRS 10 Consolidation).

Tests consolidation run lifecycle, elimination entries, and consolidated balances.
"""

import uuid
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.models.finance.cons.consolidated_balance import ConsolidatedBalance
from app.models.finance.cons.consolidation_run import (
    ConsolidationRun,
    ConsolidationStatus,
)
from app.models.finance.cons.elimination_entry import (
    EliminationEntry,
    EliminationType,
)
from app.models.finance.cons.intercompany_balance import IntercompanyBalance
from app.models.finance.cons.legal_entity import (
    ConsolidationMethod,
    EntityType,
    LegalEntity,
)
from app.models.finance.cons.ownership_interest import OwnershipInterest
from app.services.finance.cons.consolidation import (
    ConsolidationRunInput,
    ConsolidationService,
    ConsolidationSummary,
    EliminationInput,
)

# -----------------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------------


@pytest.fixture
def mock_db():
    """Create a mock database session."""
    return MagicMock()


@pytest.fixture
def group_id():
    """Standard group ID for tests."""
    return uuid.uuid4()


@pytest.fixture
def fiscal_period_id():
    """Standard fiscal period ID for tests."""
    return uuid.uuid4()


@pytest.fixture
def user_id():
    """Standard user ID for tests."""
    return uuid.uuid4()


@pytest.fixture
def other_user_id():
    """Another user ID for SoD tests."""
    return uuid.uuid4()


@pytest.fixture
def run_input(fiscal_period_id):
    """Standard consolidation run input."""
    return ConsolidationRunInput(
        fiscal_period_id=fiscal_period_id,
        reporting_currency_code="USD",
        run_description="Q4 2024 Consolidation",
    )


@pytest.fixture
def mock_consolidation_run(group_id, fiscal_period_id, user_id):
    """Create a mock consolidation run."""
    run = MagicMock(spec=ConsolidationRun)
    run.run_id = uuid.uuid4()
    run.group_id = group_id
    run.fiscal_period_id = fiscal_period_id
    run.run_number = 1
    run.run_description = "Q4 2024 Consolidation"
    run.reporting_currency_code = "USD"
    run.status = ConsolidationStatus.DRAFT
    run.entities_count = 3
    run.subsidiaries_count = 2
    run.associates_count = 1
    run.elimination_entries_count = 0
    run.total_eliminations_amount = Decimal("0")
    run.total_translation_adjustment = Decimal("0")
    run.total_nci = Decimal("0")
    run.intercompany_differences = Decimal("0")
    run.created_by_user_id = user_id
    run.approved_by_user_id = None
    run.started_at = None
    run.completed_at = None
    run.approved_at = None
    return run


@pytest.fixture
def mock_legal_entity(group_id):
    """Create a mock legal entity."""
    entity = MagicMock(spec=LegalEntity)
    entity.entity_id = uuid.uuid4()
    entity.group_id = group_id
    entity.entity_code = "SUB001"
    entity.entity_name = "Subsidiary One"
    entity.legal_name = "Subsidiary One Ltd"
    entity.entity_type = EntityType.SUBSIDIARY
    entity.consolidation_method = ConsolidationMethod.FULL
    entity.is_active = True
    entity.functional_currency_code = "USD"
    entity.reporting_currency_code = "USD"
    entity.goodwill_at_acquisition = Decimal("50000")
    return entity


@pytest.fixture
def mock_intercompany_balance(fiscal_period_id):
    """Create a mock intercompany balance."""
    balance = MagicMock(spec=IntercompanyBalance)
    balance.balance_id = uuid.uuid4()
    balance.fiscal_period_id = fiscal_period_id
    balance.from_entity_id = uuid.uuid4()
    balance.to_entity_id = uuid.uuid4()
    balance.from_entity_gl_account_id = uuid.uuid4()
    balance.to_entity_gl_account_id = uuid.uuid4()
    balance.reporting_currency_code = "USD"
    balance.reporting_currency_amount = Decimal("100000")
    balance.is_matched = True
    balance.is_eliminated = False
    balance.difference_amount = Decimal("0")
    return balance


@pytest.fixture
def mock_ownership_interest():
    """Create a mock ownership interest."""
    ownership = MagicMock(spec=OwnershipInterest)
    ownership.interest_id = uuid.uuid4()
    ownership.investor_entity_id = uuid.uuid4()
    ownership.investee_entity_id = uuid.uuid4()
    ownership.ownership_percentage = Decimal("80")
    ownership.voting_rights_percentage = Decimal("80")
    ownership.effective_ownership_percentage = Decimal("80")
    ownership.investment_cost = Decimal("800000")
    ownership.nci_at_acquisition = Decimal("200000")
    ownership.is_current = True
    return ownership


# -----------------------------------------------------------------------------
# Test: create_run
# -----------------------------------------------------------------------------


class TestCreateRun:
    """Tests for ConsolidationService.create_run."""

    def test_create_run_success(self, mock_db, group_id, run_input, user_id):
        """Test creating a consolidation run successfully."""
        # Mock no existing runs
        mock_db.scalar.return_value = None

        # Mock entities query
        mock_entities = [
            MagicMock(consolidation_method=ConsolidationMethod.FULL),
            MagicMock(consolidation_method=ConsolidationMethod.FULL),
            MagicMock(consolidation_method=ConsolidationMethod.EQUITY),
        ]
        mock_db.scalars.return_value.all.return_value = mock_entities

        ConsolidationService.create_run(
            db=mock_db,
            group_id=group_id,
            input=run_input,
            created_by_user_id=user_id,
        )

        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()
        mock_db.refresh.assert_called_once()

    def test_create_run_increments_run_number(
        self, mock_db, group_id, run_input, user_id
    ):
        """Test that run number increments for same period."""
        # Mock existing run with number 3
        mock_db.scalar.return_value = 3
        mock_db.scalars.return_value.all.return_value = []

        ConsolidationService.create_run(
            db=mock_db,
            group_id=group_id,
            input=run_input,
            created_by_user_id=user_id,
        )

        # Verify the run was added
        added_run = mock_db.add.call_args[0][0]
        assert added_run.run_number == 4

    def test_create_run_counts_entities_correctly(
        self, mock_db, group_id, run_input, user_id
    ):
        """Test that entity counts are calculated correctly."""
        mock_db.scalar.return_value = None

        # 2 FULL, 1 EQUITY, 1 NOT_CONSOLIDATED (should be excluded)
        mock_entities = [
            MagicMock(consolidation_method=ConsolidationMethod.FULL),
            MagicMock(consolidation_method=ConsolidationMethod.FULL),
            MagicMock(consolidation_method=ConsolidationMethod.EQUITY),
        ]
        mock_db.scalars.return_value.all.return_value = mock_entities

        ConsolidationService.create_run(
            db=mock_db,
            group_id=group_id,
            input=run_input,
            created_by_user_id=user_id,
        )

        added_run = mock_db.add.call_args[0][0]
        assert added_run.entities_count == 3
        assert added_run.subsidiaries_count == 2
        assert added_run.associates_count == 1


# -----------------------------------------------------------------------------
# Test: start_run
# -----------------------------------------------------------------------------


class TestStartRun:
    """Tests for ConsolidationService.start_run."""

    def test_start_run_success(self, mock_db, group_id, mock_consolidation_run):
        """Test starting a consolidation run successfully."""
        mock_consolidation_run.status = ConsolidationStatus.DRAFT
        mock_db.get.return_value = mock_consolidation_run

        ConsolidationService.start_run(
            db=mock_db,
            group_id=group_id,
            run_id=mock_consolidation_run.run_id,
        )

        assert mock_consolidation_run.status == ConsolidationStatus.IN_PROGRESS
        assert mock_consolidation_run.started_at is not None
        mock_db.commit.assert_called_once()

    def test_start_run_not_found(self, mock_db, group_id):
        """Test starting a non-existent run."""
        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            ConsolidationService.start_run(
                db=mock_db,
                group_id=group_id,
                run_id=uuid.uuid4(),
            )

        assert exc_info.value.status_code == 404
        assert "not found" in exc_info.value.detail.lower()

    def test_start_run_wrong_group(self, mock_db, mock_consolidation_run):
        """Test starting a run from different group."""
        mock_db.get.return_value = mock_consolidation_run
        different_group_id = uuid.uuid4()

        with pytest.raises(HTTPException) as exc_info:
            ConsolidationService.start_run(
                db=mock_db,
                group_id=different_group_id,
                run_id=mock_consolidation_run.run_id,
            )

        assert exc_info.value.status_code == 404

    def test_start_run_wrong_status(self, mock_db, group_id, mock_consolidation_run):
        """Test starting a run that's not in DRAFT status."""
        mock_consolidation_run.status = ConsolidationStatus.IN_PROGRESS
        mock_db.get.return_value = mock_consolidation_run

        with pytest.raises(HTTPException) as exc_info:
            ConsolidationService.start_run(
                db=mock_db,
                group_id=group_id,
                run_id=mock_consolidation_run.run_id,
            )

        assert exc_info.value.status_code == 400
        assert "cannot start" in exc_info.value.detail.lower()


# -----------------------------------------------------------------------------
# Test: create_elimination_entry
# -----------------------------------------------------------------------------


class TestCreateEliminationEntry:
    """Tests for ConsolidationService.create_elimination_entry."""

    def test_create_elimination_entry_success(
        self, mock_db, group_id, mock_consolidation_run
    ):
        """Test creating an elimination entry successfully."""
        mock_consolidation_run.status = ConsolidationStatus.IN_PROGRESS
        mock_db.get.return_value = mock_consolidation_run

        elimination_input = EliminationInput(
            elimination_type=EliminationType.INTERCOMPANY_BALANCE,
            description="Eliminate IC balance",
            currency_code="USD",
            debit_account_id=uuid.uuid4(),
            debit_amount=Decimal("100000"),
            credit_account_id=uuid.uuid4(),
            credit_amount=Decimal("100000"),
        )

        ConsolidationService.create_elimination_entry(
            db=mock_db,
            group_id=group_id,
            run_id=mock_consolidation_run.run_id,
            input=elimination_input,
        )

        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()

        # Verify run statistics updated
        assert mock_consolidation_run.elimination_entries_count == 1
        assert mock_consolidation_run.total_eliminations_amount == Decimal("100000")

    def test_create_elimination_entry_with_nci(
        self, mock_db, group_id, mock_consolidation_run
    ):
        """Test creating elimination entry with NCI amounts."""
        mock_consolidation_run.status = ConsolidationStatus.IN_PROGRESS
        mock_consolidation_run.elimination_entries_count = 0
        mock_consolidation_run.total_eliminations_amount = Decimal("0")
        mock_consolidation_run.total_nci = Decimal("0")
        mock_db.get.return_value = mock_consolidation_run

        elimination_input = EliminationInput(
            elimination_type=EliminationType.INVESTMENT_IN_SUBSIDIARY,
            description="Eliminate investment",
            currency_code="USD",
            debit_account_id=uuid.uuid4(),
            debit_amount=Decimal("800000"),
            credit_account_id=uuid.uuid4(),
            credit_amount=Decimal("800000"),
            nci_credit_account_id=uuid.uuid4(),
            nci_credit_amount=Decimal("200000"),
            nci_debit_account_id=uuid.uuid4(),
            nci_debit_amount=Decimal("200000"),
        )

        ConsolidationService.create_elimination_entry(
            db=mock_db,
            group_id=group_id,
            run_id=mock_consolidation_run.run_id,
            input=elimination_input,
        )

        # Verify NCI was tracked
        assert mock_consolidation_run.total_nci == Decimal("200000")

    def test_create_elimination_entry_unbalanced(
        self, mock_db, group_id, mock_consolidation_run
    ):
        """Test that unbalanced eliminations are rejected."""
        mock_consolidation_run.status = ConsolidationStatus.IN_PROGRESS
        mock_db.get.return_value = mock_consolidation_run

        elimination_input = EliminationInput(
            elimination_type=EliminationType.INTERCOMPANY_BALANCE,
            description="Unbalanced entry",
            currency_code="USD",
            debit_account_id=uuid.uuid4(),
            debit_amount=Decimal("100000"),
            credit_account_id=uuid.uuid4(),
            credit_amount=Decimal("90000"),  # Mismatched!
        )

        with pytest.raises(HTTPException) as exc_info:
            ConsolidationService.create_elimination_entry(
                db=mock_db,
                group_id=group_id,
                run_id=mock_consolidation_run.run_id,
                input=elimination_input,
            )

        assert exc_info.value.status_code == 400
        assert "balance" in exc_info.value.detail.lower()

    def test_create_elimination_entry_wrong_status(
        self, mock_db, group_id, mock_consolidation_run
    ):
        """Test creating elimination when run is not in progress."""
        mock_consolidation_run.status = ConsolidationStatus.DRAFT
        mock_db.get.return_value = mock_consolidation_run

        elimination_input = EliminationInput(
            elimination_type=EliminationType.INTERCOMPANY_BALANCE,
            description="Test entry",
            currency_code="USD",
            debit_account_id=uuid.uuid4(),
            debit_amount=Decimal("100000"),
            credit_account_id=uuid.uuid4(),
            credit_amount=Decimal("100000"),
        )

        with pytest.raises(HTTPException) as exc_info:
            ConsolidationService.create_elimination_entry(
                db=mock_db,
                group_id=group_id,
                run_id=mock_consolidation_run.run_id,
                input=elimination_input,
            )

        assert exc_info.value.status_code == 400
        assert "in-progress" in exc_info.value.detail.lower()


# -----------------------------------------------------------------------------
# Test: generate_intercompany_eliminations
# -----------------------------------------------------------------------------


class TestGenerateIntercompanyEliminations:
    """Tests for ConsolidationService.generate_intercompany_eliminations."""

    def test_generate_ic_eliminations_success(
        self,
        mock_db,
        group_id,
        mock_consolidation_run,
        mock_legal_entity,
        mock_intercompany_balance,
    ):
        """Test generating intercompany eliminations."""
        mock_consolidation_run.status = ConsolidationStatus.IN_PROGRESS
        mock_consolidation_run.elimination_entries_count = 0
        mock_consolidation_run.total_eliminations_amount = Decimal("0")
        mock_consolidation_run.total_nci = Decimal("0")

        # Create two entities
        entity1 = MagicMock(spec=LegalEntity)
        entity1.entity_id = mock_intercompany_balance.from_entity_id
        entity1.entity_code = "ENT001"
        entity1.is_active = True

        entity2 = MagicMock(spec=LegalEntity)
        entity2.entity_id = mock_intercompany_balance.to_entity_id
        entity2.entity_code = "ENT002"
        entity2.is_active = True

        # Setup mock db queries
        mock_db.get.return_value = mock_consolidation_run

        # First scalars().all() returns entities, second returns balances
        mock_db.scalars.return_value.all.side_effect = [
            [entity1, entity2],  # entities
            [mock_intercompany_balance],  # balances
        ]
        # scalar() returns unmatched sum
        mock_db.scalar.return_value = Decimal("0")

        ConsolidationService.generate_intercompany_eliminations(
            db=mock_db,
            group_id=group_id,
            run_id=mock_consolidation_run.run_id,
            intercompany_elimination_account_id=uuid.uuid4(),
        )

        mock_db.commit.assert_called()

    def test_generate_ic_eliminations_no_matched_balances(
        self, mock_db, group_id, mock_consolidation_run, mock_legal_entity
    ):
        """Test when there are no matched intercompany balances."""
        mock_consolidation_run.status = ConsolidationStatus.IN_PROGRESS
        mock_db.get.return_value = mock_consolidation_run

        entity1 = MagicMock(spec=LegalEntity)
        entity1.entity_id = uuid.uuid4()
        entity1.is_active = True

        mock_db.scalars.return_value.all.side_effect = [
            [entity1],  # entities
            [],  # no matched balances
        ]
        mock_db.scalar.return_value = Decimal("0")

        result = ConsolidationService.generate_intercompany_eliminations(
            db=mock_db,
            group_id=group_id,
            run_id=mock_consolidation_run.run_id,
            intercompany_elimination_account_id=uuid.uuid4(),
        )

        assert result == []


# -----------------------------------------------------------------------------
# Test: generate_investment_eliminations
# -----------------------------------------------------------------------------


class TestGenerateInvestmentEliminations:
    """Tests for ConsolidationService.generate_investment_eliminations."""

    def test_generate_investment_eliminations_success(
        self,
        mock_db,
        group_id,
        mock_consolidation_run,
        mock_legal_entity,
        mock_ownership_interest,
    ):
        """Test generating investment eliminations."""
        mock_consolidation_run.status = ConsolidationStatus.IN_PROGRESS
        mock_consolidation_run.elimination_entries_count = 0
        mock_consolidation_run.total_eliminations_amount = Decimal("0")
        mock_consolidation_run.total_nci = Decimal("0")
        mock_consolidation_run.reporting_currency_code = "USD"

        mock_legal_entity.consolidation_method = ConsolidationMethod.FULL
        mock_legal_entity.goodwill_at_acquisition = Decimal("50000")

        mock_ownership_interest.investee_entity_id = mock_legal_entity.entity_id
        mock_ownership_interest.investment_cost = Decimal("800000")
        mock_ownership_interest.nci_at_acquisition = Decimal("200000")

        # Setup queries
        mock_db.get.return_value = mock_consolidation_run
        mock_db.scalars.return_value.all.return_value = [
            mock_legal_entity
        ]
        mock_db.scalars.return_value.first.return_value = (
            mock_ownership_interest
        )

        ConsolidationService.generate_investment_eliminations(
            db=mock_db,
            group_id=group_id,
            run_id=mock_consolidation_run.run_id,
            investment_account_id=uuid.uuid4(),
            equity_account_id=uuid.uuid4(),
            goodwill_account_id=uuid.uuid4(),
            nci_account_id=uuid.uuid4(),
        )

        mock_db.commit.assert_called()

    def test_generate_investment_eliminations_no_ownership(
        self, mock_db, group_id, mock_consolidation_run, mock_legal_entity
    ):
        """Test when subsidiary has no ownership record."""
        mock_consolidation_run.status = ConsolidationStatus.IN_PROGRESS
        mock_legal_entity.consolidation_method = ConsolidationMethod.FULL

        mock_db.get.return_value = mock_consolidation_run
        mock_db.scalars.return_value.all.return_value = [
            mock_legal_entity
        ]
        mock_db.scalars.return_value.first.return_value = (
            None  # No ownership
        )

        result = ConsolidationService.generate_investment_eliminations(
            db=mock_db,
            group_id=group_id,
            run_id=mock_consolidation_run.run_id,
            investment_account_id=uuid.uuid4(),
            equity_account_id=uuid.uuid4(),
            goodwill_account_id=uuid.uuid4(),
            nci_account_id=uuid.uuid4(),
        )

        assert result == []


# -----------------------------------------------------------------------------
# Test: complete_run
# -----------------------------------------------------------------------------


class TestCompleteRun:
    """Tests for ConsolidationService.complete_run."""

    def test_complete_run_success(self, mock_db, group_id, mock_consolidation_run):
        """Test completing a consolidation run successfully."""
        mock_consolidation_run.status = ConsolidationStatus.IN_PROGRESS
        mock_db.get.return_value = mock_consolidation_run

        ConsolidationService.complete_run(
            db=mock_db,
            group_id=group_id,
            run_id=mock_consolidation_run.run_id,
        )

        assert mock_consolidation_run.status == ConsolidationStatus.COMPLETED
        assert mock_consolidation_run.completed_at is not None
        mock_db.commit.assert_called_once()

    def test_complete_run_not_found(self, mock_db, group_id):
        """Test completing a non-existent run."""
        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            ConsolidationService.complete_run(
                db=mock_db,
                group_id=group_id,
                run_id=uuid.uuid4(),
            )

        assert exc_info.value.status_code == 404

    def test_complete_run_wrong_status(self, mock_db, group_id, mock_consolidation_run):
        """Test completing a run that's not in progress."""
        mock_consolidation_run.status = ConsolidationStatus.DRAFT
        mock_db.get.return_value = mock_consolidation_run

        with pytest.raises(HTTPException) as exc_info:
            ConsolidationService.complete_run(
                db=mock_db,
                group_id=group_id,
                run_id=mock_consolidation_run.run_id,
            )

        assert exc_info.value.status_code == 400
        assert "cannot complete" in exc_info.value.detail.lower()


# -----------------------------------------------------------------------------
# Test: approve_run
# -----------------------------------------------------------------------------


class TestApproveRun:
    """Tests for ConsolidationService.approve_run."""

    def test_approve_run_success(
        self, mock_db, group_id, mock_consolidation_run, other_user_id
    ):
        """Test approving a consolidation run successfully."""
        mock_consolidation_run.status = ConsolidationStatus.COMPLETED
        mock_db.get.return_value = mock_consolidation_run

        ConsolidationService.approve_run(
            db=mock_db,
            group_id=group_id,
            run_id=mock_consolidation_run.run_id,
            approved_by_user_id=other_user_id,
        )

        assert mock_consolidation_run.status == ConsolidationStatus.APPROVED
        assert mock_consolidation_run.approved_by_user_id == other_user_id
        assert mock_consolidation_run.approved_at is not None
        mock_db.commit.assert_called_once()

    def test_approve_run_not_found(self, mock_db, group_id, other_user_id):
        """Test approving a non-existent run."""
        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            ConsolidationService.approve_run(
                db=mock_db,
                group_id=group_id,
                run_id=uuid.uuid4(),
                approved_by_user_id=other_user_id,
            )

        assert exc_info.value.status_code == 404

    def test_approve_run_wrong_status(
        self, mock_db, group_id, mock_consolidation_run, other_user_id
    ):
        """Test approving a run that's not completed."""
        mock_consolidation_run.status = ConsolidationStatus.IN_PROGRESS
        mock_db.get.return_value = mock_consolidation_run

        with pytest.raises(HTTPException) as exc_info:
            ConsolidationService.approve_run(
                db=mock_db,
                group_id=group_id,
                run_id=mock_consolidation_run.run_id,
                approved_by_user_id=other_user_id,
            )

        assert exc_info.value.status_code == 400
        assert "completed" in exc_info.value.detail.lower()

    def test_approve_run_sod_violation(
        self, mock_db, group_id, mock_consolidation_run, user_id
    ):
        """Test SoD: creator cannot approve."""
        mock_consolidation_run.status = ConsolidationStatus.COMPLETED
        mock_consolidation_run.created_by_user_id = user_id
        mock_db.get.return_value = mock_consolidation_run

        with pytest.raises(HTTPException) as exc_info:
            ConsolidationService.approve_run(
                db=mock_db,
                group_id=group_id,
                run_id=mock_consolidation_run.run_id,
                approved_by_user_id=user_id,  # Same as creator!
            )

        assert exc_info.value.status_code == 400
        assert "segregation" in exc_info.value.detail.lower()


# -----------------------------------------------------------------------------
# Test: create_consolidated_balance
# -----------------------------------------------------------------------------


class TestCreateConsolidatedBalance:
    """Tests for ConsolidationService.create_consolidated_balance."""

    def test_create_consolidated_balance_success(self, mock_db):
        """Test creating a consolidated balance record."""
        run_id = uuid.uuid4()
        account_id = uuid.uuid4()

        ConsolidationService.create_consolidated_balance(
            db=mock_db,
            run_id=run_id,
            account_id=account_id,
            currency_code="USD",
            subsidiary_balance_sum=Decimal("1000000"),
            equity_method_balance=Decimal("50000"),
            intercompany_eliminations=Decimal("100000"),
            investment_eliminations=Decimal("200000"),
            translation_adjustment=Decimal("25000"),
            nci_share=Decimal("150000"),
        )

        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()

        # Verify calculations
        added_balance = mock_db.add.call_args[0][0]

        # Total eliminations = IC + Investment + Unrealized + Other
        expected_total_elim = Decimal("300000")
        assert added_balance.total_eliminations == expected_total_elim

        # Consolidated balance = subsidiary + equity method - eliminations + translation
        # = 1,000,000 + 50,000 - 300,000 + 25,000 = 775,000
        expected_consolidated = Decimal("775000")
        assert added_balance.consolidated_balance == expected_consolidated

        # Parent share = consolidated - NCI
        expected_parent = Decimal("625000")
        assert added_balance.parent_share == expected_parent

    def test_create_consolidated_balance_with_segment(self, mock_db):
        """Test creating a consolidated balance with segment."""
        run_id = uuid.uuid4()
        account_id = uuid.uuid4()
        segment_id = uuid.uuid4()

        ConsolidationService.create_consolidated_balance(
            db=mock_db,
            run_id=run_id,
            account_id=account_id,
            currency_code="USD",
            subsidiary_balance_sum=Decimal("500000"),
            segment_id=segment_id,
        )

        added_balance = mock_db.add.call_args[0][0]
        assert added_balance.segment_id == segment_id

    def test_create_consolidated_balance_no_eliminations(self, mock_db):
        """Test creating a consolidated balance with no eliminations."""
        run_id = uuid.uuid4()
        account_id = uuid.uuid4()

        ConsolidationService.create_consolidated_balance(
            db=mock_db,
            run_id=run_id,
            account_id=account_id,
            currency_code="USD",
            subsidiary_balance_sum=Decimal("100000"),
        )

        added_balance = mock_db.add.call_args[0][0]
        assert added_balance.total_eliminations == Decimal("0")
        assert added_balance.consolidated_balance == Decimal("100000")
        assert added_balance.parent_share == Decimal("100000")


# -----------------------------------------------------------------------------
# Test: get_summary
# -----------------------------------------------------------------------------


class TestGetSummary:
    """Tests for ConsolidationService.get_summary."""

    def test_get_summary_success(self, mock_db, group_id, mock_consolidation_run):
        """Test getting consolidation run summary."""
        mock_db.get.return_value = mock_consolidation_run

        result = ConsolidationService.get_summary(
            db=mock_db,
            group_id=group_id,
            run_id=mock_consolidation_run.run_id,
        )

        assert isinstance(result, ConsolidationSummary)
        assert result.run_id == mock_consolidation_run.run_id
        assert result.status == mock_consolidation_run.status
        assert result.entities_count == mock_consolidation_run.entities_count

    def test_get_summary_not_found(self, mock_db, group_id):
        """Test getting summary for non-existent run."""
        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            ConsolidationService.get_summary(
                db=mock_db,
                group_id=group_id,
                run_id=uuid.uuid4(),
            )

        assert exc_info.value.status_code == 404


# -----------------------------------------------------------------------------
# Test: get_elimination_entries
# -----------------------------------------------------------------------------


class TestGetEliminationEntries:
    """Tests for ConsolidationService.get_elimination_entries."""

    def test_get_elimination_entries_all(self, mock_db):
        """Test getting all elimination entries for a run."""
        run_id = uuid.uuid4()
        mock_entries = [MagicMock(spec=EliminationEntry) for _ in range(3)]
        mock_db.scalars.return_value.all.return_value = mock_entries

        result = ConsolidationService.get_elimination_entries(
            db=mock_db,
            run_id=run_id,
        )

        assert len(result) == 3

    def test_get_elimination_entries_by_type(self, mock_db):
        """Test getting elimination entries filtered by type."""
        run_id = uuid.uuid4()
        mock_entries = [MagicMock(spec=EliminationEntry)]
        mock_db.scalars.return_value.all.return_value = mock_entries

        result = ConsolidationService.get_elimination_entries(
            db=mock_db,
            run_id=run_id,
            elimination_type=EliminationType.INTERCOMPANY_BALANCE,
        )

        assert len(result) == 1


# -----------------------------------------------------------------------------
# Test: get_consolidated_balances
# -----------------------------------------------------------------------------


class TestGetConsolidatedBalances:
    """Tests for ConsolidationService.get_consolidated_balances."""

    def test_get_consolidated_balances_all(self, mock_db):
        """Test getting all consolidated balances for a run."""
        run_id = uuid.uuid4()
        mock_balances = [MagicMock(spec=ConsolidatedBalance) for _ in range(5)]
        mock_db.scalars.return_value.all.return_value = mock_balances

        result = ConsolidationService.get_consolidated_balances(
            db=mock_db,
            run_id=run_id,
        )

        assert len(result) == 5

    def test_get_consolidated_balances_by_segment(self, mock_db):
        """Test getting consolidated balances filtered by segment."""
        run_id = uuid.uuid4()
        segment_id = uuid.uuid4()
        mock_balances = [MagicMock(spec=ConsolidatedBalance)]
        mock_db.scalars.return_value.all.return_value = mock_balances

        result = ConsolidationService.get_consolidated_balances(
            db=mock_db,
            run_id=run_id,
            segment_id=segment_id,
        )

        assert len(result) == 1


# -----------------------------------------------------------------------------
# Test: get
# -----------------------------------------------------------------------------


class TestGet:
    """Tests for ConsolidationService.get."""

    def test_get_success(self, mock_db, mock_consolidation_run):
        """Test getting a consolidation run by ID."""
        mock_db.get.return_value = mock_consolidation_run

        result = ConsolidationService.get(
            db=mock_db,
            run_id=str(mock_consolidation_run.run_id),
        )

        assert result == mock_consolidation_run

    def test_get_not_found(self, mock_db):
        """Test getting a non-existent run."""
        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            ConsolidationService.get(
                db=mock_db,
                run_id=str(uuid.uuid4()),
            )

        assert exc_info.value.status_code == 404


# -----------------------------------------------------------------------------
# Test: list
# -----------------------------------------------------------------------------


class TestList:
    """Tests for ConsolidationService.list."""

    def test_list_all(self, mock_db):
        """Test listing all consolidation runs."""
        mock_runs = [MagicMock(spec=ConsolidationRun) for _ in range(3)]
        mock_db.scalars.return_value.all.return_value = mock_runs

        result = ConsolidationService.list(
            db=mock_db,
        )

        assert len(result) == 3

    def test_list_by_group(self, mock_db, group_id):
        """Test listing runs filtered by group."""
        mock_runs = [MagicMock(spec=ConsolidationRun)]
        mock_db.scalars.return_value.all.return_value = mock_runs

        result = ConsolidationService.list(
            db=mock_db,
            group_id=str(group_id),
        )

        assert len(result) == 1

    def test_list_by_status(self, mock_db):
        """Test listing runs filtered by status."""
        mock_runs = [MagicMock(spec=ConsolidationRun)]
        mock_db.scalars.return_value.all.return_value = mock_runs

        result = ConsolidationService.list(
            db=mock_db,
            status=ConsolidationStatus.APPROVED,
        )

        assert len(result) == 1

    def test_list_by_fiscal_period(self, mock_db, fiscal_period_id):
        """Test listing runs filtered by fiscal period."""
        mock_runs = [MagicMock(spec=ConsolidationRun)]
        mock_db.scalars.return_value.all.return_value = mock_runs

        result = ConsolidationService.list(
            db=mock_db,
            fiscal_period_id=str(fiscal_period_id),
        )

        assert len(result) == 1

    def test_list_with_pagination(self, mock_db):
        """Test listing with pagination."""
        mock_runs = [MagicMock(spec=ConsolidationRun)]
        mock_db.scalars.return_value.all.return_value = mock_runs

        ConsolidationService.list(
            db=mock_db,
            limit=10,
            offset=20,
        )

        # Verify scalars was called (SA2 pattern)
        mock_db.scalars.assert_called()


# -----------------------------------------------------------------------------
# Test: Run Lifecycle Integration
# -----------------------------------------------------------------------------


class TestRunLifecycle:
    """Integration tests for the consolidation run lifecycle."""

    def test_full_lifecycle(self, mock_db, group_id, run_input, user_id, other_user_id):
        """Test complete run lifecycle: create -> start -> complete -> approve."""
        # Setup - mock will return same run object at different statuses
        mock_run = MagicMock(spec=ConsolidationRun)
        mock_run.run_id = uuid.uuid4()
        mock_run.group_id = group_id
        mock_run.status = ConsolidationStatus.DRAFT
        mock_run.created_by_user_id = user_id
        mock_run.elimination_entries_count = 0
        mock_run.total_eliminations_amount = Decimal("0")
        mock_run.total_nci = Decimal("0")

        # Mock queries for create_run
        mock_db.scalar.return_value = None
        mock_db.scalars.return_value.all.return_value = []

        # Step 1: Create
        ConsolidationService.create_run(
            db=mock_db,
            group_id=group_id,
            input=run_input,
            created_by_user_id=user_id,
        )
        mock_db.add.assert_called()
        mock_db.commit.assert_called()

        # Step 2: Start
        mock_db.get.return_value = mock_run
        ConsolidationService.start_run(
            db=mock_db,
            group_id=group_id,
            run_id=mock_run.run_id,
        )
        assert mock_run.status == ConsolidationStatus.IN_PROGRESS

        # Step 3: Complete
        ConsolidationService.complete_run(
            db=mock_db,
            group_id=group_id,
            run_id=mock_run.run_id,
        )
        assert mock_run.status == ConsolidationStatus.COMPLETED

        # Step 4: Approve
        ConsolidationService.approve_run(
            db=mock_db,
            group_id=group_id,
            run_id=mock_run.run_id,
            approved_by_user_id=other_user_id,
        )
        assert mock_run.status == ConsolidationStatus.APPROVED
        assert mock_run.approved_by_user_id == other_user_id
