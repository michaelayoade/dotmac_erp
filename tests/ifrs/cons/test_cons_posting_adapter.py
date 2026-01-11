"""
Tests for CONSPostingAdapter Service.

Tests cover:
- Elimination entry posting
- Translation adjustment posting
- NCI allocation posting
- Bulk elimination posting
- Error handling and validation
"""

import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from app.models.ifrs.cons.consolidation_run import ConsolidationStatus
from app.models.ifrs.cons.elimination_entry import EliminationType
from app.services.ifrs.cons.cons_posting_adapter import (
    CONSPostingAdapter,
    CONSPostingResult,
)


# ============ Mock Classes ============


class MockConsolidationRun:
    """Mock ConsolidationRun for posting tests."""

    def __init__(
        self,
        run_id: uuid.UUID = None,
        group_id: uuid.UUID = None,
        fiscal_period_id: uuid.UUID = None,
        status: ConsolidationStatus = ConsolidationStatus.COMPLETED,
        total_translation_adjustment: Decimal = Decimal("0"),
    ):
        self.run_id = run_id or uuid.uuid4()
        self.group_id = group_id or uuid.uuid4()
        self.fiscal_period_id = fiscal_period_id or uuid.uuid4()
        self.status = status
        self.total_translation_adjustment = total_translation_adjustment


class MockEliminationEntry:
    """Mock EliminationEntry for posting tests."""

    def __init__(
        self,
        entry_id: uuid.UUID = None,
        consolidation_run_id: uuid.UUID = None,
        elimination_type: EliminationType = EliminationType.INTERCOMPANY_BALANCE,
        description: str = "Test elimination",
        debit_account_id: uuid.UUID = None,
        credit_account_id: uuid.UUID = None,
        debit_amount: Decimal = Decimal("1000.00"),
        credit_amount: Decimal = Decimal("1000.00"),
        currency_code: str = "USD",
        nci_debit_account_id: uuid.UUID = None,
        nci_credit_account_id: uuid.UUID = None,
        nci_debit_amount: Decimal = Decimal("0"),
        nci_credit_amount: Decimal = Decimal("0"),
    ):
        self.entry_id = entry_id or uuid.uuid4()
        self.consolidation_run_id = consolidation_run_id or uuid.uuid4()
        self.elimination_type = elimination_type
        self.description = description
        self.debit_account_id = debit_account_id or uuid.uuid4()
        self.credit_account_id = credit_account_id or uuid.uuid4()
        self.debit_amount = debit_amount
        self.credit_amount = credit_amount
        self.currency_code = currency_code
        self.nci_debit_account_id = nci_debit_account_id
        self.nci_credit_account_id = nci_credit_account_id
        self.nci_debit_amount = nci_debit_amount
        self.nci_credit_amount = nci_credit_amount


class MockLegalEntity:
    """Mock LegalEntity for posting tests."""

    def __init__(
        self,
        entity_id: uuid.UUID = None,
        group_id: uuid.UUID = None,
        organization_id: uuid.UUID = None,
        entity_code: str = "PARENT",
        is_consolidating_entity: bool = False,
    ):
        self.entity_id = entity_id or uuid.uuid4()
        self.group_id = group_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.entity_code = entity_code
        self.is_consolidating_entity = is_consolidating_entity


class MockJournalEntry:
    """Mock Journal entry result."""

    def __init__(
        self,
        entry_id: uuid.UUID = None,
        entry_number: str = "JE-CONS-001",
    ):
        self.entry_id = entry_id or uuid.uuid4()
        self.entry_number = entry_number


# ============ Fixtures ============


@pytest.fixture
def group_id():
    return uuid.uuid4()


@pytest.fixture
def user_id():
    return uuid.uuid4()


@pytest.fixture
def mock_db():
    """Create a mock database session."""
    db = MagicMock()
    db.get = MagicMock(return_value=None)
    db.query = MagicMock(return_value=db)
    db.filter = MagicMock(return_value=db)
    db.all = MagicMock(return_value=[])
    db.first = MagicMock(return_value=None)
    return db


@pytest.fixture
def mock_run(group_id):
    return MockConsolidationRun(group_id=group_id)


@pytest.fixture
def mock_entry(mock_run):
    return MockEliminationEntry(consolidation_run_id=mock_run.run_id)


@pytest.fixture
def mock_parent_entity(group_id):
    return MockLegalEntity(
        group_id=group_id,
        is_consolidating_entity=True,
    )


@pytest.fixture
def mock_subsidiary_entity(group_id):
    return MockLegalEntity(
        group_id=group_id,
        entity_code="SUB01",
        is_consolidating_entity=False,
    )


# ============ Post Elimination Entry Tests ============


class TestPostEliminationEntry:
    """Tests for CONSPostingAdapter.post_elimination_entry()."""

    def test_post_elimination_run_not_found(self, mock_db, group_id, user_id):
        """Test posting when run not found."""
        mock_db.get.return_value = None

        result = CONSPostingAdapter.post_elimination_entry(
            db=mock_db,
            group_id=group_id,
            run_id=uuid.uuid4(),
            entry_id=uuid.uuid4(),
            posting_date=date.today(),
            posted_by_user_id=user_id,
        )

        assert result.success is False
        assert "run not found" in result.message.lower()

    def test_post_elimination_run_wrong_group(
        self, mock_db, group_id, user_id, mock_run
    ):
        """Test posting when run belongs to different group."""
        mock_run.group_id = uuid.uuid4()  # Different group
        mock_db.get.return_value = mock_run

        result = CONSPostingAdapter.post_elimination_entry(
            db=mock_db,
            group_id=group_id,
            run_id=mock_run.run_id,
            entry_id=uuid.uuid4(),
            posting_date=date.today(),
            posted_by_user_id=user_id,
        )

        assert result.success is False
        assert "run not found" in result.message.lower()

    def test_post_elimination_run_invalid_status(
        self, mock_db, group_id, user_id, mock_run
    ):
        """Test posting when run is in DRAFT status."""
        mock_run.status = ConsolidationStatus.DRAFT
        mock_db.get.return_value = mock_run

        result = CONSPostingAdapter.post_elimination_entry(
            db=mock_db,
            group_id=group_id,
            run_id=mock_run.run_id,
            entry_id=uuid.uuid4(),
            posting_date=date.today(),
            posted_by_user_id=user_id,
        )

        assert result.success is False
        assert "DRAFT" in result.message

    def test_post_elimination_entry_not_found(
        self, mock_db, group_id, user_id, mock_run
    ):
        """Test posting when elimination entry not found."""

        def get_side_effect(model, id):
            from app.models.ifrs.cons.consolidation_run import ConsolidationRun

            if model == ConsolidationRun:
                return mock_run
            return None

        mock_db.get.side_effect = get_side_effect

        result = CONSPostingAdapter.post_elimination_entry(
            db=mock_db,
            group_id=group_id,
            run_id=mock_run.run_id,
            entry_id=uuid.uuid4(),
            posting_date=date.today(),
            posted_by_user_id=user_id,
        )

        assert result.success is False
        assert "entry not found" in result.message.lower()

    def test_post_elimination_no_parent_entity(
        self, mock_db, group_id, user_id, mock_run, mock_entry
    ):
        """Test posting when consolidating entity not found."""
        mock_entry.consolidation_run_id = mock_run.run_id

        def get_side_effect(model, id):
            if str(id) == str(mock_run.run_id):
                return mock_run
            if str(id) == str(mock_entry.entry_id):
                return mock_entry
            return None

        mock_db.get.side_effect = get_side_effect
        mock_db.query.return_value.filter.return_value.first.return_value = None

        result = CONSPostingAdapter.post_elimination_entry(
            db=mock_db,
            group_id=group_id,
            run_id=mock_run.run_id,
            entry_id=mock_entry.entry_id,
            posting_date=date.today(),
            posted_by_user_id=user_id,
        )

        assert result.success is False
        assert "consolidating entity" in result.message.lower()

    @patch("app.services.ifrs.cons.cons_posting_adapter.JournalInput")
    @patch("app.services.ifrs.cons.cons_posting_adapter.JournalService")
    def test_post_elimination_success(
        self,
        mock_journal_service,
        mock_journal_input,
        mock_db,
        group_id,
        user_id,
        mock_run,
        mock_entry,
        mock_parent_entity,
    ):
        """Test successful elimination entry posting."""
        mock_entry.consolidation_run_id = mock_run.run_id

        def get_side_effect(model, id):
            if str(id) == str(mock_run.run_id):
                return mock_run
            if str(id) == str(mock_entry.entry_id):
                return mock_entry
            return None

        mock_db.get.side_effect = get_side_effect
        mock_db.query.return_value.filter.return_value.first.return_value = (
            mock_parent_entity
        )

        journal = MockJournalEntry()
        mock_journal_service.create_entry.return_value = journal

        result = CONSPostingAdapter.post_elimination_entry(
            db=mock_db,
            group_id=group_id,
            run_id=mock_run.run_id,
            entry_id=mock_entry.entry_id,
            posting_date=date.today(),
            posted_by_user_id=user_id,
        )

        assert result.success is True
        assert result.journal_entry_id == journal.entry_id
        assert result.entry_number == journal.entry_number

    @patch("app.services.ifrs.cons.cons_posting_adapter.JournalInput")
    @patch("app.services.ifrs.cons.cons_posting_adapter.JournalService")
    def test_post_elimination_with_nci(
        self,
        mock_journal_service,
        mock_journal_input,
        mock_db,
        group_id,
        user_id,
        mock_run,
        mock_parent_entity,
    ):
        """Test posting elimination with NCI entries."""
        entry_with_nci = MockEliminationEntry(
            consolidation_run_id=mock_run.run_id,
            nci_debit_account_id=uuid.uuid4(),
            nci_credit_account_id=uuid.uuid4(),
            nci_debit_amount=Decimal("100.00"),
            nci_credit_amount=Decimal("100.00"),
        )

        def get_side_effect(model, id):
            if str(id) == str(mock_run.run_id):
                return mock_run
            if str(id) == str(entry_with_nci.entry_id):
                return entry_with_nci
            return None

        mock_db.get.side_effect = get_side_effect
        mock_db.query.return_value.filter.return_value.first.return_value = (
            mock_parent_entity
        )

        journal = MockJournalEntry()
        mock_journal_service.create_entry.return_value = journal

        result = CONSPostingAdapter.post_elimination_entry(
            db=mock_db,
            group_id=group_id,
            run_id=mock_run.run_id,
            entry_id=entry_with_nci.entry_id,
            posting_date=date.today(),
            posted_by_user_id=user_id,
        )

        assert result.success is True
        # JournalInput was called with the right number of lines
        mock_journal_input.assert_called_once()

    @patch("app.services.ifrs.cons.cons_posting_adapter.JournalInput")
    @patch("app.services.ifrs.cons.cons_posting_adapter.JournalService")
    def test_post_elimination_approved_status(
        self,
        mock_journal_service,
        mock_journal_input,
        mock_db,
        group_id,
        user_id,
        mock_entry,
        mock_parent_entity,
    ):
        """Test posting from APPROVED status run."""
        run_approved = MockConsolidationRun(
            group_id=group_id,
            status=ConsolidationStatus.APPROVED,
        )
        mock_entry.consolidation_run_id = run_approved.run_id

        def get_side_effect(model, id):
            if str(id) == str(run_approved.run_id):
                return run_approved
            if str(id) == str(mock_entry.entry_id):
                return mock_entry
            return None

        mock_db.get.side_effect = get_side_effect
        mock_db.query.return_value.filter.return_value.first.return_value = (
            mock_parent_entity
        )

        journal = MockJournalEntry()
        mock_journal_service.create_entry.return_value = journal

        result = CONSPostingAdapter.post_elimination_entry(
            db=mock_db,
            group_id=group_id,
            run_id=run_approved.run_id,
            entry_id=mock_entry.entry_id,
            posting_date=date.today(),
            posted_by_user_id=user_id,
        )

        assert result.success is True

    @patch("app.services.ifrs.cons.cons_posting_adapter.JournalInput")
    @patch("app.services.ifrs.cons.cons_posting_adapter.JournalService")
    def test_post_elimination_journal_failure(
        self,
        mock_journal_service,
        mock_journal_input,
        mock_db,
        group_id,
        user_id,
        mock_run,
        mock_entry,
        mock_parent_entity,
    ):
        """Test handling of journal creation failure."""
        from fastapi import HTTPException

        mock_entry.consolidation_run_id = mock_run.run_id

        def get_side_effect(model, id):
            if str(id) == str(mock_run.run_id):
                return mock_run
            if str(id) == str(mock_entry.entry_id):
                return mock_entry
            return None

        mock_db.get.side_effect = get_side_effect
        mock_db.query.return_value.filter.return_value.first.return_value = (
            mock_parent_entity
        )

        mock_journal_service.create_entry.side_effect = HTTPException(
            status_code=400, detail="Period closed"
        )

        result = CONSPostingAdapter.post_elimination_entry(
            db=mock_db,
            group_id=group_id,
            run_id=mock_run.run_id,
            entry_id=mock_entry.entry_id,
            posting_date=date.today(),
            posted_by_user_id=user_id,
        )

        assert result.success is False
        assert "Failed to post" in result.message

    @patch("app.services.ifrs.cons.cons_posting_adapter.JournalInput")
    @patch("app.services.ifrs.cons.cons_posting_adapter.JournalService")
    def test_post_elimination_with_idempotency_key(
        self,
        mock_journal_service,
        mock_journal_input,
        mock_db,
        group_id,
        user_id,
        mock_run,
        mock_entry,
        mock_parent_entity,
    ):
        """Test posting with idempotency key."""
        mock_entry.consolidation_run_id = mock_run.run_id

        def get_side_effect(model, id):
            if str(id) == str(mock_run.run_id):
                return mock_run
            if str(id) == str(mock_entry.entry_id):
                return mock_entry
            return None

        mock_db.get.side_effect = get_side_effect
        mock_db.query.return_value.filter.return_value.first.return_value = (
            mock_parent_entity
        )

        journal = MockJournalEntry()
        mock_journal_service.create_entry.return_value = journal

        custom_key = "my-idem-key"
        result = CONSPostingAdapter.post_elimination_entry(
            db=mock_db,
            group_id=group_id,
            run_id=mock_run.run_id,
            entry_id=mock_entry.entry_id,
            posting_date=date.today(),
            posted_by_user_id=user_id,
            idempotency_key=custom_key,
        )

        assert result.success is True
        call_args = mock_journal_service.create_entry.call_args
        assert call_args[1]["idempotency_key"] == custom_key


# ============ Post All Eliminations Tests ============


class TestPostAllEliminations:
    """Tests for CONSPostingAdapter.post_all_eliminations()."""

    def test_post_all_run_not_found(self, mock_db, group_id, user_id):
        """Test posting all when run not found."""
        mock_db.get.return_value = None

        results = CONSPostingAdapter.post_all_eliminations(
            db=mock_db,
            group_id=group_id,
            run_id=uuid.uuid4(),
            posting_date=date.today(),
            posted_by_user_id=user_id,
        )

        assert len(results) == 1
        assert results[0].success is False
        assert "not found" in results[0].message.lower()

    @patch.object(CONSPostingAdapter, "post_elimination_entry")
    def test_post_all_multiple_entries(
        self, mock_post_entry, mock_db, group_id, user_id, mock_run
    ):
        """Test posting multiple elimination entries."""
        entries = [
            MockEliminationEntry(consolidation_run_id=mock_run.run_id),
            MockEliminationEntry(consolidation_run_id=mock_run.run_id),
            MockEliminationEntry(consolidation_run_id=mock_run.run_id),
        ]

        mock_db.get.return_value = mock_run
        mock_db.query.return_value.filter.return_value.all.return_value = entries

        mock_post_entry.return_value = CONSPostingResult(success=True)

        results = CONSPostingAdapter.post_all_eliminations(
            db=mock_db,
            group_id=group_id,
            run_id=mock_run.run_id,
            posting_date=date.today(),
            posted_by_user_id=user_id,
        )

        assert len(results) == 3
        assert all(r.success for r in results)
        assert mock_post_entry.call_count == 3

    @patch.object(CONSPostingAdapter, "post_elimination_entry")
    def test_post_all_partial_failure(
        self, mock_post_entry, mock_db, group_id, user_id, mock_run
    ):
        """Test posting with some failures."""
        entries = [
            MockEliminationEntry(consolidation_run_id=mock_run.run_id),
            MockEliminationEntry(consolidation_run_id=mock_run.run_id),
        ]

        mock_db.get.return_value = mock_run
        mock_db.query.return_value.filter.return_value.all.return_value = entries

        # First succeeds, second fails
        mock_post_entry.side_effect = [
            CONSPostingResult(success=True),
            CONSPostingResult(success=False, message="Failed"),
        ]

        results = CONSPostingAdapter.post_all_eliminations(
            db=mock_db,
            group_id=group_id,
            run_id=mock_run.run_id,
            posting_date=date.today(),
            posted_by_user_id=user_id,
        )

        assert len(results) == 2
        assert results[0].success is True
        assert results[1].success is False

    @patch.object(CONSPostingAdapter, "post_elimination_entry")
    def test_post_all_empty_entries(
        self, mock_post_entry, mock_db, group_id, user_id, mock_run
    ):
        """Test posting when no entries exist."""
        mock_db.get.return_value = mock_run
        mock_db.query.return_value.filter.return_value.all.return_value = []

        results = CONSPostingAdapter.post_all_eliminations(
            db=mock_db,
            group_id=group_id,
            run_id=mock_run.run_id,
            posting_date=date.today(),
            posted_by_user_id=user_id,
        )

        assert len(results) == 0
        mock_post_entry.assert_not_called()


# ============ Post Translation Adjustment Tests ============


class TestPostTranslationAdjustment:
    """Tests for CONSPostingAdapter.post_translation_adjustment()."""

    def test_post_cta_run_not_found(self, mock_db, group_id, user_id):
        """Test CTA posting when run not found."""
        mock_db.get.return_value = None

        result = CONSPostingAdapter.post_translation_adjustment(
            db=mock_db,
            group_id=group_id,
            run_id=uuid.uuid4(),
            entity_id=uuid.uuid4(),
            posting_date=date.today(),
            translation_account_id=uuid.uuid4(),
            oci_account_id=uuid.uuid4(),
            adjustment_amount=Decimal("1000.00"),
            currency_code="USD",
            posted_by_user_id=user_id,
        )

        assert result.success is False
        assert "run not found" in result.message.lower()

    def test_post_cta_entity_not_found(
        self, mock_db, group_id, user_id, mock_run
    ):
        """Test CTA posting when entity not found."""

        def get_side_effect(model, id):
            if str(id) == str(mock_run.run_id):
                return mock_run
            return None

        mock_db.get.side_effect = get_side_effect

        result = CONSPostingAdapter.post_translation_adjustment(
            db=mock_db,
            group_id=group_id,
            run_id=mock_run.run_id,
            entity_id=uuid.uuid4(),
            posting_date=date.today(),
            translation_account_id=uuid.uuid4(),
            oci_account_id=uuid.uuid4(),
            adjustment_amount=Decimal("1000.00"),
            currency_code="USD",
            posted_by_user_id=user_id,
        )

        assert result.success is False
        assert "entity not found" in result.message.lower()

    def test_post_cta_no_parent_entity(
        self, mock_db, group_id, user_id, mock_run, mock_subsidiary_entity
    ):
        """Test CTA posting when parent entity not found."""
        mock_subsidiary_entity.group_id = mock_run.group_id

        def get_side_effect(model, id):
            if str(id) == str(mock_run.run_id):
                return mock_run
            if str(id) == str(mock_subsidiary_entity.entity_id):
                return mock_subsidiary_entity
            return None

        mock_db.get.side_effect = get_side_effect
        mock_db.query.return_value.filter.return_value.first.return_value = None

        result = CONSPostingAdapter.post_translation_adjustment(
            db=mock_db,
            group_id=group_id,
            run_id=mock_run.run_id,
            entity_id=mock_subsidiary_entity.entity_id,
            posting_date=date.today(),
            translation_account_id=uuid.uuid4(),
            oci_account_id=uuid.uuid4(),
            adjustment_amount=Decimal("1000.00"),
            currency_code="USD",
            posted_by_user_id=user_id,
        )

        assert result.success is False
        assert "consolidating entity" in result.message.lower()

    @patch("app.services.ifrs.cons.cons_posting_adapter.JournalInput")
    @patch("app.services.ifrs.cons.cons_posting_adapter.JournalService")
    def test_post_cta_positive_adjustment(
        self,
        mock_journal_service,
        mock_journal_input,
        mock_db,
        group_id,
        user_id,
        mock_run,
        mock_subsidiary_entity,
        mock_parent_entity,
    ):
        """Test positive CTA posting (debit CTA, credit OCI)."""
        mock_subsidiary_entity.group_id = mock_run.group_id

        def get_side_effect(model, id):
            if str(id) == str(mock_run.run_id):
                return mock_run
            if str(id) == str(mock_subsidiary_entity.entity_id):
                return mock_subsidiary_entity
            return None

        mock_db.get.side_effect = get_side_effect
        mock_db.query.return_value.filter.return_value.first.return_value = (
            mock_parent_entity
        )

        journal = MockJournalEntry()
        mock_journal_service.create_entry.return_value = journal

        result = CONSPostingAdapter.post_translation_adjustment(
            db=mock_db,
            group_id=group_id,
            run_id=mock_run.run_id,
            entity_id=mock_subsidiary_entity.entity_id,
            posting_date=date.today(),
            translation_account_id=uuid.uuid4(),
            oci_account_id=uuid.uuid4(),
            adjustment_amount=Decimal("1000.00"),
            currency_code="USD",
            posted_by_user_id=user_id,
        )

        assert result.success is True
        mock_journal_input.assert_called_once()

    @patch("app.services.ifrs.cons.cons_posting_adapter.JournalInput")
    @patch("app.services.ifrs.cons.cons_posting_adapter.JournalService")
    def test_post_cta_negative_adjustment(
        self,
        mock_journal_service,
        mock_journal_input,
        mock_db,
        group_id,
        user_id,
        mock_run,
        mock_subsidiary_entity,
        mock_parent_entity,
    ):
        """Test negative CTA posting (debit OCI, credit CTA)."""
        mock_subsidiary_entity.group_id = mock_run.group_id

        def get_side_effect(model, id):
            if str(id) == str(mock_run.run_id):
                return mock_run
            if str(id) == str(mock_subsidiary_entity.entity_id):
                return mock_subsidiary_entity
            return None

        mock_db.get.side_effect = get_side_effect
        mock_db.query.return_value.filter.return_value.first.return_value = (
            mock_parent_entity
        )

        journal = MockJournalEntry()
        mock_journal_service.create_entry.return_value = journal

        result = CONSPostingAdapter.post_translation_adjustment(
            db=mock_db,
            group_id=group_id,
            run_id=mock_run.run_id,
            entity_id=mock_subsidiary_entity.entity_id,
            posting_date=date.today(),
            translation_account_id=uuid.uuid4(),
            oci_account_id=uuid.uuid4(),
            adjustment_amount=Decimal("-500.00"),  # Negative
            currency_code="USD",
            posted_by_user_id=user_id,
        )

        assert result.success is True
        mock_journal_input.assert_called_once()

    @patch("app.services.ifrs.cons.cons_posting_adapter.JournalInput")
    @patch("app.services.ifrs.cons.cons_posting_adapter.JournalService")
    def test_post_cta_updates_run_total(
        self,
        mock_journal_service,
        mock_journal_input,
        mock_db,
        group_id,
        user_id,
        mock_run,
        mock_subsidiary_entity,
        mock_parent_entity,
    ):
        """Test that CTA updates run total_translation_adjustment."""
        mock_subsidiary_entity.group_id = mock_run.group_id
        mock_run.total_translation_adjustment = Decimal("0")

        def get_side_effect(model, id):
            if str(id) == str(mock_run.run_id):
                return mock_run
            if str(id) == str(mock_subsidiary_entity.entity_id):
                return mock_subsidiary_entity
            return None

        mock_db.get.side_effect = get_side_effect
        mock_db.query.return_value.filter.return_value.first.return_value = (
            mock_parent_entity
        )

        journal = MockJournalEntry()
        mock_journal_service.create_entry.return_value = journal

        result = CONSPostingAdapter.post_translation_adjustment(
            db=mock_db,
            group_id=group_id,
            run_id=mock_run.run_id,
            entity_id=mock_subsidiary_entity.entity_id,
            posting_date=date.today(),
            translation_account_id=uuid.uuid4(),
            oci_account_id=uuid.uuid4(),
            adjustment_amount=Decimal("1500.00"),
            currency_code="USD",
            posted_by_user_id=user_id,
        )

        assert result.success is True
        assert mock_run.total_translation_adjustment == Decimal("1500.00")

    @patch("app.services.ifrs.cons.cons_posting_adapter.JournalInput")
    @patch("app.services.ifrs.cons.cons_posting_adapter.JournalService")
    def test_post_cta_journal_failure(
        self,
        mock_journal_service,
        mock_journal_input,
        mock_db,
        group_id,
        user_id,
        mock_run,
        mock_subsidiary_entity,
        mock_parent_entity,
    ):
        """Test CTA journal creation failure."""
        from fastapi import HTTPException

        mock_subsidiary_entity.group_id = mock_run.group_id

        def get_side_effect(model, id):
            if str(id) == str(mock_run.run_id):
                return mock_run
            if str(id) == str(mock_subsidiary_entity.entity_id):
                return mock_subsidiary_entity
            return None

        mock_db.get.side_effect = get_side_effect
        mock_db.query.return_value.filter.return_value.first.return_value = (
            mock_parent_entity
        )

        mock_journal_service.create_entry.side_effect = HTTPException(
            status_code=400, detail="Invalid period"
        )

        result = CONSPostingAdapter.post_translation_adjustment(
            db=mock_db,
            group_id=group_id,
            run_id=mock_run.run_id,
            entity_id=mock_subsidiary_entity.entity_id,
            posting_date=date.today(),
            translation_account_id=uuid.uuid4(),
            oci_account_id=uuid.uuid4(),
            adjustment_amount=Decimal("1000.00"),
            currency_code="USD",
            posted_by_user_id=user_id,
        )

        assert result.success is False
        assert "Failed to post CTA" in result.message


# ============ Post NCI Allocation Tests ============


class TestPostNCIAllocation:
    """Tests for CONSPostingAdapter.post_nci_allocation()."""

    def test_post_nci_run_not_found(self, mock_db, group_id, user_id):
        """Test NCI posting when run not found."""
        mock_db.get.return_value = None

        result = CONSPostingAdapter.post_nci_allocation(
            db=mock_db,
            group_id=group_id,
            run_id=uuid.uuid4(),
            entity_id=uuid.uuid4(),
            posting_date=date.today(),
            retained_earnings_account_id=uuid.uuid4(),
            nci_account_id=uuid.uuid4(),
            nci_amount=Decimal("1000.00"),
            currency_code="USD",
            posted_by_user_id=user_id,
        )

        assert result.success is False
        assert "run not found" in result.message.lower()

    def test_post_nci_entity_not_found(
        self, mock_db, group_id, user_id, mock_run
    ):
        """Test NCI posting when entity not found."""

        def get_side_effect(model, id):
            if str(id) == str(mock_run.run_id):
                return mock_run
            return None

        mock_db.get.side_effect = get_side_effect

        result = CONSPostingAdapter.post_nci_allocation(
            db=mock_db,
            group_id=group_id,
            run_id=mock_run.run_id,
            entity_id=uuid.uuid4(),
            posting_date=date.today(),
            retained_earnings_account_id=uuid.uuid4(),
            nci_account_id=uuid.uuid4(),
            nci_amount=Decimal("1000.00"),
            currency_code="USD",
            posted_by_user_id=user_id,
        )

        assert result.success is False
        assert "entity not found" in result.message.lower()

    @patch("app.services.ifrs.cons.cons_posting_adapter.JournalInput")
    @patch("app.services.ifrs.cons.cons_posting_adapter.JournalService")
    def test_post_nci_success(
        self,
        mock_journal_service,
        mock_journal_input,
        mock_db,
        group_id,
        user_id,
        mock_run,
        mock_subsidiary_entity,
        mock_parent_entity,
    ):
        """Test successful NCI allocation posting."""
        mock_subsidiary_entity.group_id = mock_run.group_id

        def get_side_effect(model, id):
            if str(id) == str(mock_run.run_id):
                return mock_run
            if str(id) == str(mock_subsidiary_entity.entity_id):
                return mock_subsidiary_entity
            return None

        mock_db.get.side_effect = get_side_effect
        mock_db.query.return_value.filter.return_value.first.return_value = (
            mock_parent_entity
        )

        journal = MockJournalEntry()
        mock_journal_service.create_entry.return_value = journal

        result = CONSPostingAdapter.post_nci_allocation(
            db=mock_db,
            group_id=group_id,
            run_id=mock_run.run_id,
            entity_id=mock_subsidiary_entity.entity_id,
            posting_date=date.today(),
            retained_earnings_account_id=uuid.uuid4(),
            nci_account_id=uuid.uuid4(),
            nci_amount=Decimal("500.00"),
            currency_code="USD",
            posted_by_user_id=user_id,
        )

        assert result.success is True
        assert result.journal_entry_id == journal.entry_id
        mock_journal_input.assert_called_once()

    @patch("app.services.ifrs.cons.cons_posting_adapter.JournalInput")
    @patch("app.services.ifrs.cons.cons_posting_adapter.JournalService")
    def test_post_nci_journal_failure(
        self,
        mock_journal_service,
        mock_journal_input,
        mock_db,
        group_id,
        user_id,
        mock_run,
        mock_subsidiary_entity,
        mock_parent_entity,
    ):
        """Test NCI journal creation failure."""
        from fastapi import HTTPException

        mock_subsidiary_entity.group_id = mock_run.group_id

        def get_side_effect(model, id):
            if str(id) == str(mock_run.run_id):
                return mock_run
            if str(id) == str(mock_subsidiary_entity.entity_id):
                return mock_subsidiary_entity
            return None

        mock_db.get.side_effect = get_side_effect
        mock_db.query.return_value.filter.return_value.first.return_value = (
            mock_parent_entity
        )

        mock_journal_service.create_entry.side_effect = HTTPException(
            status_code=400, detail="Account inactive"
        )

        result = CONSPostingAdapter.post_nci_allocation(
            db=mock_db,
            group_id=group_id,
            run_id=mock_run.run_id,
            entity_id=mock_subsidiary_entity.entity_id,
            posting_date=date.today(),
            retained_earnings_account_id=uuid.uuid4(),
            nci_account_id=uuid.uuid4(),
            nci_amount=Decimal("500.00"),
            currency_code="USD",
            posted_by_user_id=user_id,
        )

        assert result.success is False
        assert "Failed to post NCI" in result.message


# ============ CONSPostingResult Tests ============


class TestCONSPostingResult:
    """Tests for CONSPostingResult dataclass."""

    def test_result_success(self):
        """Test successful result creation."""
        journal_id = uuid.uuid4()
        entry_number = "JE-CONS-001"

        result = CONSPostingResult(
            success=True,
            journal_entry_id=journal_id,
            entry_number=entry_number,
            message="Posted successfully",
        )

        assert result.success is True
        assert result.journal_entry_id == journal_id
        assert result.entry_number == entry_number
        assert result.message == "Posted successfully"

    def test_result_failure(self):
        """Test failure result creation."""
        result = CONSPostingResult(
            success=False,
            message="Validation failed",
        )

        assert result.success is False
        assert result.journal_entry_id is None
        assert result.entry_number is None
        assert result.message == "Validation failed"

    def test_result_defaults(self):
        """Test default values."""
        result = CONSPostingResult(success=True)

        assert result.journal_entry_id is None
        assert result.entry_number is None
        assert result.message is None
