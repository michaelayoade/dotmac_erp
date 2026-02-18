from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

from app.api.finance.tax import TaxTransactionRead
from app.models.finance.tax.tax_transaction import TaxTransactionType


def test_tax_transaction_read_matches_tax_transaction_model_fields():
    obj = SimpleNamespace(
        transaction_id=uuid4(),
        organization_id=uuid4(),
        tax_code_id=uuid4(),
        transaction_date=date.today(),
        base_amount=Decimal("100.00"),
        tax_amount=Decimal("15.00"),
        transaction_type=TaxTransactionType.OUTPUT,
        source_document_type="AR_INVOICE",
        is_included_in_return=False,
        journal_entry_id=None,
    )

    result = TaxTransactionRead.model_validate(obj)

    assert result.transaction_type == TaxTransactionType.OUTPUT
    assert result.is_included_in_return is False
