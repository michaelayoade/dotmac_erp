from decimal import Decimal
from unittest.mock import MagicMock
from uuid import uuid4

from app.services.expense.approval_service import ExpenseApprovalService


def _make_category(*, category_name: str, category_code: str, requires_receipt: bool):
    category = MagicMock()
    category.category_name = category_name
    category.category_code = category_code
    category.requires_receipt = requires_receipt
    category.max_amount_per_claim = None
    return category


def _make_item(*, category_id, description: str, amount: str = "1000.00"):
    item = MagicMock()
    item.category_id = category_id
    item.description = description
    item.claimed_amount = Decimal(amount)
    item.receipt_url = None
    item.receipt_number = None
    return item


def test_validate_receipt_requirements_allows_fuel_mileage_without_receipt():
    db = MagicMock()
    svc = ExpenseApprovalService(db)

    category_id = uuid4()
    category = _make_category(
        category_name="Fuel/Mileage Expenses",
        category_code="FUEL_MILEAGE_EXPENSES",
        requires_receipt=True,
    )
    db.get.return_value = category

    claim = MagicMock()
    claim.items = [
        _make_item(category_id=category_id, description="Fuel top-up for sales trip")
    ]

    result = svc.validate_receipt_requirements(claim)

    assert result.is_valid is True
    assert result.missing_receipts == []


def test_validate_receipt_requirements_still_blocks_other_required_categories():
    db = MagicMock()
    svc = ExpenseApprovalService(db)

    category_id = uuid4()
    category = _make_category(
        category_name="Travel Expenses",
        category_code="TRAVEL",
        requires_receipt=True,
    )
    db.get.return_value = category

    claim = MagicMock()
    claim.items = [
        _make_item(category_id=category_id, description="Intercity bus fare")
    ]

    result = svc.validate_receipt_requirements(claim)

    assert result.is_valid is False
    assert len(result.missing_receipts) == 1
    assert "Receipt required" in result.missing_receipts[0]
