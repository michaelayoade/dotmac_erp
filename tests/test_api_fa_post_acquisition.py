from datetime import date
from decimal import Decimal
import uuid
from unittest.mock import MagicMock

from app.models.fixed_assets.asset import Asset
from app.models.fixed_assets.asset_category import AssetCategory
from app.services.fixed_assets.fa_posting_adapter import FAPostingAdapter
from app.services.finance.gl.journal import JournalService
from app.services.finance.gl.ledger_posting import LedgerPostingService, PostingResult
from app.services.finance.platform import org_context_service


def test_post_asset_acquisition_creates_journal_lines(monkeypatch):
    org_id = uuid.uuid4()
    asset_id = uuid.uuid4()
    category_id = uuid.uuid4()
    asset_account_id = uuid.uuid4()
    credit_account_id = uuid.uuid4()

    asset = Asset(
        asset_id=asset_id,
        organization_id=org_id,
        asset_number="AST-001",
        asset_name="Test Asset",
        category_id=category_id,
        acquisition_date=date(2025, 1, 15),
        acquisition_cost=Decimal("1200.00"),
        currency_code="USD",
        functional_currency_cost=Decimal("1200.00"),
        depreciation_method="STRAIGHT_LINE",
        useful_life_months=60,
        remaining_life_months=60,
        residual_value=Decimal("0"),
        net_book_value=Decimal("1200.00"),
        status="DRAFT",
    )

    category = AssetCategory(
        category_id=category_id,
        organization_id=org_id,
        category_code="CAT-001",
        category_name="Equipment",
        asset_account_id=asset_account_id,
        accumulated_depreciation_account_id=uuid.uuid4(),
        depreciation_expense_account_id=uuid.uuid4(),
        gain_loss_disposal_account_id=uuid.uuid4(),
        useful_life_months=60,
        depreciation_method="STRAIGHT_LINE",
        residual_value_percent=Decimal("0"),
        capitalization_threshold=Decimal("0"),
        revaluation_model_allowed=False,
        is_active=True,
    )

    db = MagicMock()
    db.get.side_effect = lambda model, _id: asset if model is Asset else category

    monkeypatch.setattr(
        org_context_service, "get_functional_currency", lambda *_: "USD"
    )

    captured = {}

    class _DummyJournal:
        def __init__(self, journal_entry_id):
            self.journal_entry_id = journal_entry_id

    def _create_journal(db, organization_id, input, created_by_user_id):
        captured["input"] = input
        return _DummyJournal(uuid.uuid4())

    monkeypatch.setattr(JournalService, "create_journal", staticmethod(_create_journal))
    monkeypatch.setattr(
        JournalService, "submit_journal", staticmethod(lambda *args, **kwargs: None)
    )
    monkeypatch.setattr(
        JournalService, "approve_journal", staticmethod(lambda *args, **kwargs: None)
    )

    def _post_journal_entry_success(db, request):
        return PostingResult(success=True, batch_id=uuid.uuid4(), posted_lines=2)

    monkeypatch.setattr(
        LedgerPostingService,
        "post_journal_entry",
        staticmethod(_post_journal_entry_success),
    )

    result = FAPostingAdapter.post_asset_acquisition(
        db=db,
        organization_id=org_id,
        asset_id=asset_id,
        posting_date=date(2025, 2, 1),
        posted_by_user_id=uuid.uuid4(),
        credit_account_id=credit_account_id,
        description="Asset acquisition",
    )

    assert result.success is True
    journal_input = captured["input"]
    assert len(journal_input.lines) == 2
    assert journal_input.lines[0].account_id == asset_account_id
    assert journal_input.lines[0].debit_amount == Decimal("1200.00")
    assert journal_input.lines[1].account_id == credit_account_id
    assert journal_input.lines[1].credit_amount == Decimal("1200.00")
