"""
Tests for the ExchangeRateFetcher service.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from app.models.finance.core_fx.exchange_rate import ExchangeRate, ExchangeRateSource
from app.models.finance.core_fx.exchange_rate_type import ExchangeRateType
from app.models.finance.core_org.organization import Organization
from app.services.finance.platform.ecb_rate_fetcher import (
    ExchangeRateFetcher,
    FetchResult,
)


@pytest.fixture()
def org_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture()
def user_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture()
def spot_type(org_id: uuid.UUID) -> MagicMock:
    st = MagicMock(spec=ExchangeRateType)
    st.rate_type_id = uuid.uuid4()
    st.organization_id = org_id
    st.type_code = "SPOT"
    st.type_name = "Spot Rate"
    st.is_default = True
    return st


@pytest.fixture()
def org(org_id: uuid.UUID) -> MagicMock:
    o = MagicMock(spec=Organization)
    o.organization_id = org_id
    o.functional_currency_code = "NGN"
    o.presentation_currency_code = "NGN"
    return o


@pytest.fixture()
def sample_api_response() -> dict:
    return {
        "date": "2026-02-10",
        "ngn": {
            "usd": 0.000625,
            "eur": 0.000525,
            "gbp": 0.000475,
            "ngn": 1.0,
            "btc": 0.0000000001,
        },
    }


class TestFetchResult:
    def test_defaults(self) -> None:
        result = FetchResult()
        assert result.rates_created == 0
        assert result.rates_updated == 0
        assert result.rates_skipped == 0
        assert result.errors == []

    def test_frozen(self) -> None:
        result = FetchResult(rates_created=5)
        with pytest.raises(AttributeError):
            result.rates_created = 10  # type: ignore[misc]


class TestExchangeRateFetcher:
    def test_org_not_found(self, org_id: uuid.UUID) -> None:
        db = MagicMock()
        db.get.return_value = None

        fetcher = ExchangeRateFetcher()
        result = fetcher.fetch_latest_rates(db, org_id)

        assert result.errors == ["Organization not found"]
        assert result.rates_created == 0

    @patch("app.services.finance.platform.ecb_rate_fetcher.httpx.get")
    def test_api_failure_both_urls(
        self, mock_get: MagicMock, org: Organization
    ) -> None:
        import httpx

        mock_get.side_effect = httpx.ConnectError("Connection refused")

        db = MagicMock()
        db.get.return_value = org

        fetcher = ExchangeRateFetcher()
        result = fetcher.fetch_latest_rates(db, org.organization_id)

        assert "Failed to fetch rates from API" in result.errors[0]
        assert mock_get.call_count == 2  # primary + fallback

    @patch("app.services.finance.platform.ecb_rate_fetcher.httpx.get")
    def test_successful_fetch_creates_rates(
        self,
        mock_get: MagicMock,
        org: Organization,
        spot_type: ExchangeRateType,
        sample_api_response: dict,
        user_id: uuid.UUID,
    ) -> None:
        # Mock HTTP response
        mock_resp = MagicMock()
        mock_resp.json.return_value = sample_api_response
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        db = MagicMock()
        db.get.return_value = org
        # Return USD, EUR, GBP as active currencies (not BTC)
        db.scalars.return_value.all.return_value = ["USD", "EUR", "GBP", "NGN"]
        db.scalar.side_effect = [
            spot_type,  # _get_or_create_spot_type
            None,  # USD rate doesn't exist
            None,  # EUR rate doesn't exist
            None,  # GBP rate doesn't exist
        ]

        fetcher = ExchangeRateFetcher()
        result = fetcher.fetch_latest_rates(db, org.organization_id, user_id=user_id)

        assert result.rates_created == 3
        assert result.rates_updated == 0
        assert result.errors == []
        assert db.add.call_count == 3
        db.flush.assert_called_once()

    @patch("app.services.finance.platform.ecb_rate_fetcher.httpx.get")
    def test_existing_rate_updated_when_different(
        self,
        mock_get: MagicMock,
        org: Organization,
        spot_type: ExchangeRateType,
    ) -> None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "date": "2026-02-10",
            "ngn": {"usd": 0.000700},
        }
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        existing_rate = MagicMock(spec=ExchangeRate)
        existing_rate.exchange_rate = Decimal("0.000625")
        existing_rate.source = ExchangeRateSource.API

        db = MagicMock()
        db.get.return_value = org
        db.scalars.return_value.all.return_value = ["USD", "NGN"]
        db.scalar.side_effect = [
            spot_type,  # _get_or_create_spot_type
            existing_rate,  # existing USD rate found
        ]

        fetcher = ExchangeRateFetcher()
        result = fetcher.fetch_latest_rates(db, org.organization_id)

        assert result.rates_updated == 1
        assert result.rates_created == 0
        assert existing_rate.exchange_rate == Decimal("0.0007")

    @patch("app.services.finance.platform.ecb_rate_fetcher.httpx.get")
    def test_existing_rate_skipped_when_same(
        self,
        mock_get: MagicMock,
        org: Organization,
        spot_type: ExchangeRateType,
    ) -> None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "date": "2026-02-10",
            "ngn": {"usd": 0.000625},
        }
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        existing_rate = MagicMock(spec=ExchangeRate)
        existing_rate.exchange_rate = Decimal("0.000625")
        existing_rate.source = ExchangeRateSource.API

        db = MagicMock()
        db.get.return_value = org
        db.scalars.return_value.all.return_value = ["USD", "NGN"]
        db.scalar.side_effect = [
            spot_type,
            existing_rate,
        ]

        fetcher = ExchangeRateFetcher()
        result = fetcher.fetch_latest_rates(db, org.organization_id)

        assert result.rates_skipped == 1
        assert result.rates_created == 0
        assert result.rates_updated == 0

    @patch("app.services.finance.platform.ecb_rate_fetcher.httpx.get")
    def test_invalid_rate_value_recorded_as_error(
        self,
        mock_get: MagicMock,
        org: Organization,
        spot_type: ExchangeRateType,
    ) -> None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "date": "2026-02-10",
            "ngn": {"usd": "not-a-number"},
        }
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        db = MagicMock()
        db.get.return_value = org
        db.scalars.return_value.all.return_value = ["USD", "NGN"]
        db.scalar.side_effect = [spot_type]

        fetcher = ExchangeRateFetcher()
        result = fetcher.fetch_latest_rates(db, org.organization_id)

        assert len(result.errors) == 1
        assert "Invalid rate value for USD" in result.errors[0]

    @patch("app.services.finance.platform.ecb_rate_fetcher.httpx.get")
    def test_empty_rates_map(
        self,
        mock_get: MagicMock,
        org: Organization,
    ) -> None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"date": "2026-02-10", "ngn": {}}
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        db = MagicMock()
        db.get.return_value = org

        fetcher = ExchangeRateFetcher()
        result = fetcher.fetch_latest_rates(db, org.organization_id)

        assert "No rates returned" in result.errors[0]

    @patch("app.services.finance.platform.ecb_rate_fetcher.httpx.get")
    def test_fallback_url_used_on_primary_failure(
        self,
        mock_get: MagicMock,
        org: Organization,
    ) -> None:
        import httpx as httpx_mod

        call_count = 0

        def side_effect(url: str, **kwargs: object) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise httpx_mod.ConnectError("primary down")
            mock_resp = MagicMock()
            mock_resp.json.return_value = {
                "date": "2026-02-10",
                "ngn": {"usd": 0.0006},
            }
            mock_resp.raise_for_status.return_value = None
            return mock_resp

        mock_get.side_effect = side_effect

        db = MagicMock()
        db.get.return_value = org
        db.scalars.return_value.all.return_value = ["USD", "NGN"]
        spot = MagicMock(spec=ExchangeRateType)
        spot.rate_type_id = uuid.uuid4()
        db.scalar.side_effect = [spot, None]

        fetcher = ExchangeRateFetcher()
        result = fetcher.fetch_latest_rates(db, org.organization_id)

        assert result.rates_created == 1
        assert call_count == 2

    def test_get_or_create_spot_type_creates_when_missing(
        self, org_id: uuid.UUID
    ) -> None:
        db = MagicMock()
        db.scalar.return_value = None

        fetcher = ExchangeRateFetcher()
        spot = fetcher._get_or_create_spot_type(db, org_id)

        db.add.assert_called_once()
        db.flush.assert_called_once()
        assert spot.type_code == "SPOT"
        assert spot.organization_id == org_id

    def test_get_or_create_spot_type_returns_existing(
        self, org_id: uuid.UUID, spot_type: ExchangeRateType
    ) -> None:
        db = MagicMock()
        db.scalar.return_value = spot_type

        fetcher = ExchangeRateFetcher()
        result = fetcher._get_or_create_spot_type(db, org_id)

        assert result is spot_type
        db.add.assert_not_called()

    @patch("app.services.finance.platform.ecb_rate_fetcher.httpx.get")
    def test_self_currency_pair_skipped(
        self,
        mock_get: MagicMock,
        org: Organization,
        spot_type: ExchangeRateType,
    ) -> None:
        """NGN→NGN pair should be skipped even though NGN is in active currencies."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "date": "2026-02-10",
            "ngn": {"ngn": 1.0, "usd": 0.0006},
        }
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        db = MagicMock()
        db.get.return_value = org
        db.scalars.return_value.all.return_value = ["NGN", "USD"]
        db.scalar.side_effect = [
            spot_type,
            None,
        ]  # spot type, then no existing rate for USD

        fetcher = ExchangeRateFetcher()
        result = fetcher.fetch_latest_rates(db, org.organization_id)

        # Only USD created, NGN→NGN skipped
        assert result.rates_created == 1

    @patch("app.services.finance.platform.ecb_rate_fetcher.httpx.get")
    def test_negative_rate_skipped(
        self,
        mock_get: MagicMock,
        org: Organization,
        spot_type: ExchangeRateType,
    ) -> None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "date": "2026-02-10",
            "ngn": {"usd": -0.5},
        }
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        db = MagicMock()
        db.get.return_value = org
        db.scalars.return_value.all.return_value = ["USD", "NGN"]
        db.scalar.side_effect = [spot_type]

        fetcher = ExchangeRateFetcher()
        result = fetcher.fetch_latest_rates(db, org.organization_id)

        assert result.rates_created == 0
        db.add.assert_not_called()
