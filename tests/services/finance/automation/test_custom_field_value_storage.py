"""Custom field value normalization and destructive-change guards."""

from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.models.finance.automation import CustomFieldType
from app.services.finance.automation.custom_fields import CustomFieldsService


def test_value_normalization_preserves_typed_json_contract() -> None:
    service = CustomFieldsService()

    assert service._json_value(CustomFieldType.NUMBER, "7") == 7
    assert service._json_value(CustomFieldType.DECIMAL, Decimal("7.20")) == "7.20"
    assert service._json_value(CustomFieldType.BOOLEAN, True) is True
    assert service._json_value(CustomFieldType.MULTISELECT, ("a", "b")) == ["a", "b"]


def test_hard_delete_refuses_definition_with_values() -> None:
    db = MagicMock()
    db.get.return_value = MagicMock()
    db.scalar.return_value = 2

    with pytest.raises(HTTPException) as exc:
        CustomFieldsService().hard_delete(db, MagicMock())

    assert exc.value.status_code == 409
    db.delete.assert_not_called()
