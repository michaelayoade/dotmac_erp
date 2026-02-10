"""
Tests for FX Rate Lookup API endpoint.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from app.api.finance.fx import lookup_rate


@pytest.fixture
def org_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def auth(org_id: uuid.UUID) -> dict:
    return {"organization_id": str(org_id), "person_id": str(uuid.uuid4())}


@pytest.fixture
def mock_db() -> MagicMock:
    return MagicMock()


@pytest.fixture
def mock_org(org_id: uuid.UUID) -> MagicMock:
    org = MagicMock()
    org.functional_currency_code = "NGN"
    org.organization_id = org_id
    return org


class TestFXRateLookup:
    """Tests for the /fx/rate lookup endpoint."""

    def test_same_currency_returns_one(
        self, mock_db: MagicMock, auth: dict, mock_org: MagicMock
    ) -> None:
        """When to-currency matches functional currency, rate is 1."""
        mock_db.get.return_value = mock_org

        result = lookup_rate(
            to="NGN", rate_date=date(2026, 2, 10), auth=auth, db=mock_db
        )

        assert result["rate"] == "1"
        assert result["inverse_rate"] == "1"
        assert result["source"] == "identity"

    def test_no_org_returns_null(self, mock_db: MagicMock, auth: dict) -> None:
        """When org not found, returns null rate."""
        mock_db.get.return_value = None

        result = lookup_rate(
            to="USD", rate_date=date(2026, 2, 10), auth=auth, db=mock_db
        )

        assert result["rate"] is None

    def test_no_functional_currency_returns_null(
        self, mock_db: MagicMock, auth: dict
    ) -> None:
        """When org has no functional currency configured, returns null."""
        org = MagicMock()
        org.functional_currency_code = None
        mock_db.get.return_value = org

        result = lookup_rate(
            to="USD", rate_date=date(2026, 2, 10), auth=auth, db=mock_db
        )

        assert result["rate"] is None

    @patch("app.api.finance.fx.select")
    def test_no_spot_rate_type_returns_null(
        self,
        mock_select: MagicMock,
        mock_db: MagicMock,
        auth: dict,
        mock_org: MagicMock,
    ) -> None:
        """When no SPOT rate type is configured, returns null."""
        mock_db.get.return_value = mock_org
        mock_db.scalar.return_value = None  # No rate type found

        result = lookup_rate(
            to="USD", rate_date=date(2026, 2, 10), auth=auth, db=mock_db
        )

        assert result["rate"] is None
        assert "SPOT" in result.get("message", "")

    @patch("app.api.finance.fx.select")
    def test_direct_rate_found(
        self,
        mock_select: MagicMock,
        mock_db: MagicMock,
        auth: dict,
        mock_org: MagicMock,
    ) -> None:
        """When a direct rate exists, returns it."""
        mock_db.get.return_value = mock_org

        rate_type = MagicMock()
        rate_type.rate_type_id = uuid.uuid4()

        rate = MagicMock()
        rate.exchange_rate = Decimal("0.000625")
        rate.inverse_rate = Decimal("1600")
        rate.effective_date = date(2026, 2, 10)
        rate.source = MagicMock()
        rate.source.value = "ECB"

        # First scalar call = rate_type, second = direct rate
        mock_db.scalar.side_effect = [rate_type, rate]

        result = lookup_rate(
            to="USD", rate_date=date(2026, 2, 10), auth=auth, db=mock_db
        )

        assert result["rate"] == "0.000625"
        assert result["inverse_rate"] == "1600"
        assert result["source"] == "ECB"

    @patch("app.api.finance.fx.select")
    def test_inverse_rate_found(
        self,
        mock_select: MagicMock,
        mock_db: MagicMock,
        auth: dict,
        mock_org: MagicMock,
    ) -> None:
        """When only an inverse rate exists, returns the inverted rate."""
        mock_db.get.return_value = mock_org

        rate_type = MagicMock()
        rate_type.rate_type_id = uuid.uuid4()

        inverse_record = MagicMock()
        inverse_record.exchange_rate = Decimal("1600")
        inverse_record.inverse_rate = Decimal("0.000625")
        inverse_record.effective_date = date(2026, 2, 9)
        inverse_record.source = MagicMock()
        inverse_record.source.value = "API"

        # First = rate_type, second = None (no direct), third = inverse
        mock_db.scalar.side_effect = [rate_type, None, inverse_record]

        result = lookup_rate(
            to="USD", rate_date=date(2026, 2, 10), auth=auth, db=mock_db
        )

        assert result["rate"] == "0.000625"
        assert result["inverse_rate"] == "1600"
        assert result["source"] == "API"

    @patch("app.api.finance.fx.select")
    def test_no_rate_found_returns_null(
        self,
        mock_select: MagicMock,
        mock_db: MagicMock,
        auth: dict,
        mock_org: MagicMock,
    ) -> None:
        """When no rate exists (direct or inverse), returns null."""
        mock_db.get.return_value = mock_org

        rate_type = MagicMock()
        rate_type.rate_type_id = uuid.uuid4()

        # First = rate_type, second = None (no direct), third = None (no inverse)
        mock_db.scalar.side_effect = [rate_type, None, None]

        result = lookup_rate(
            to="USD", rate_date=date(2026, 2, 10), auth=auth, db=mock_db
        )

        assert result["rate"] is None
        assert "NGN/USD" in result.get("message", "")

    def test_defaults_to_today_when_no_date(
        self, mock_db: MagicMock, auth: dict, mock_org: MagicMock
    ) -> None:
        """When no date param, defaults to today."""
        mock_db.get.return_value = mock_org

        result = lookup_rate(to="NGN", rate_date=None, auth=auth, db=mock_db)

        assert result["rate"] == "1"
        assert result["effective_date"] == str(date.today())

    def test_case_insensitive_same_currency(
        self, mock_db: MagicMock, auth: dict, mock_org: MagicMock
    ) -> None:
        """Currency comparison is case-insensitive."""
        mock_db.get.return_value = mock_org

        result = lookup_rate(
            to="ngn", rate_date=date(2026, 2, 10), auth=auth, db=mock_db
        )

        assert result["rate"] == "1"

    @patch("app.api.finance.fx.select")
    def test_rate_with_null_source(
        self,
        mock_select: MagicMock,
        mock_db: MagicMock,
        auth: dict,
        mock_org: MagicMock,
    ) -> None:
        """When rate has null source, defaults to MANUAL."""
        mock_db.get.return_value = mock_org

        rate_type = MagicMock()
        rate_type.rate_type_id = uuid.uuid4()

        rate = MagicMock()
        rate.exchange_rate = Decimal("0.5")
        rate.inverse_rate = Decimal("2.0")
        rate.effective_date = date(2026, 2, 10)
        rate.source = None

        mock_db.scalar.side_effect = [rate_type, rate]

        result = lookup_rate(
            to="USD", rate_date=date(2026, 2, 10), auth=auth, db=mock_db
        )

        assert result["source"] == "MANUAL"
