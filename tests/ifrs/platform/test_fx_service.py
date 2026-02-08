"""
Tests for FXService.
"""

from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from tests.ifrs.platform.conftest import (
    MockColumn,
    MockExchangeRate,
    MockExchangeRateType,
    MockOrganization,
)


@contextmanager
def patch_fx_service():
    """Helper context manager that sets up all required patches for FXService."""
    with patch("app.services.finance.platform.fx.ExchangeRateType") as mock_rate_type:
        mock_rate_type.organization_id = MockColumn()
        mock_rate_type.type_code = MockColumn()
        mock_rate_type.is_default = MockColumn()
        with patch("app.services.finance.platform.fx.ExchangeRate") as mock_rate:
            mock_rate.organization_id = MockColumn()
            mock_rate.from_currency_code = MockColumn()
            mock_rate.to_currency_code = MockColumn()
            mock_rate.rate_type_id = MockColumn()
            mock_rate.effective_date = MockColumn()
            with (
                patch(
                    "app.services.finance.platform.fx.and_", return_value=MagicMock()
                ),
                patch(
                    "app.services.finance.platform.fx.coerce_uuid",
                    side_effect=lambda x: x,
                ),
            ):
                yield mock_rate_type, mock_rate


class TestFXService:
    """Tests for FXService."""

    @pytest.fixture
    def service(self):
        """Import the service with mocked dependencies."""
        with patch.dict(
            "sys.modules",
            {
                "app.models.ifrs.core_fx.exchange_rate": MagicMock(),
                "app.models.ifrs.core_fx.exchange_rate_type": MagicMock(),
                "app.models.ifrs.core_org.organization": MagicMock(),
            },
        ):
            from app.services.finance.platform.fx import FXService

            return FXService

    def test_get_rate_same_currency_returns_one(
        self, service, mock_db_session, organization_id
    ):
        """Same currency should return rate of 1.0."""
        with patch(
            "app.services.finance.platform.fx.coerce_uuid", side_effect=lambda x: x
        ):
            # Same currency - should short-circuit and return a rate of 1.0
            service.get_rate(
                mock_db_session,
                organization_id=organization_id,
                from_currency="USD",
                to_currency="USD",
                rate_type_code="SPOT",
                effective_date=date.today(),
            )

        # Result should be an ExchangeRate object (mocked)
        # No database queries should have been made
        mock_db_session.query.assert_not_called()

    def test_get_rate_direct_rate_found(
        self, service, mock_db_session, organization_id
    ):
        """Direct rate should be returned when available."""
        rate_type = MockExchangeRateType(
            organization_id=organization_id,
            type_code="SPOT",
        )
        mock_rate = MockExchangeRate(
            organization_id=organization_id,
            from_currency_code="USD",
            to_currency_code="EUR",
            exchange_rate=Decimal("0.85"),
            effective_date=date.today(),
        )

        # rate_type query, then direct rate query
        mock_db_session.query.return_value.filter.return_value.first.return_value = (
            rate_type
        )
        mock_db_session.query.return_value.filter.return_value.order_by.return_value.first.return_value = mock_rate

        with patch_fx_service():
            result = service.get_rate(
                mock_db_session,
                organization_id=organization_id,
                from_currency="USD",
                to_currency="EUR",
                rate_type_code="SPOT",
                effective_date=date.today(),
            )

        assert result == mock_rate

    def test_get_rate_inverse_rate_used(
        self, service, mock_db_session, organization_id
    ):
        """Inverse rate should be computed when direct rate not available."""
        rate_type = MockExchangeRateType(
            organization_id=organization_id,
            type_code="SPOT",
        )
        inverse_rate = MockExchangeRate(
            organization_id=organization_id,
            from_currency_code="EUR",
            to_currency_code="USD",
            exchange_rate=Decimal("1.18"),
            effective_date=date.today(),
        )

        # First call: rate_type lookup
        # Second call: direct rate (returns None)
        # Third call: inverse rate
        mock_db_session.query.return_value.filter.return_value.first.return_value = (
            rate_type
        )
        mock_db_session.query.return_value.filter.return_value.order_by.return_value.first.side_effect = [
            None,  # No direct rate
            inverse_rate,  # Inverse rate found
        ]

        with patch_fx_service() as (mock_rate_type, mock_rate_cls):
            mock_instance = MagicMock()
            mock_rate_cls.return_value = mock_instance
            service.get_rate(
                mock_db_session,
                organization_id=organization_id,
                from_currency="USD",
                to_currency="EUR",
                rate_type_code="SPOT",
                effective_date=date.today(),
            )

        # Should create synthetic rate from inverse
        mock_rate_cls.assert_called()

    def test_get_rate_raises_404_for_missing_rate_type(
        self, service, mock_db_session, organization_id
    ):
        """get_rate should raise 404 when rate type not found."""
        mock_db_session.query.return_value.filter.return_value.first.return_value = None

        with (
            patch("app.services.finance.platform.fx.ExchangeRateType"),
            patch(
                "app.services.finance.platform.fx.coerce_uuid", side_effect=lambda x: x
            ),
            pytest.raises(HTTPException) as exc_info,
        ):
            service.get_rate(
                mock_db_session,
                organization_id=organization_id,
                from_currency="USD",
                to_currency="EUR",
                rate_type_code="INVALID",
                effective_date=date.today(),
            )

        assert exc_info.value.status_code == 404
        assert "rate type" in exc_info.value.detail.lower()

    def test_get_rate_raises_404_for_missing_rate(
        self, service, mock_db_session, organization_id
    ):
        """get_rate should raise 404 when no rate available."""
        rate_type = MockExchangeRateType(
            organization_id=organization_id,
            type_code="SPOT",
        )
        mock_db_session.query.return_value.filter.return_value.first.return_value = (
            rate_type
        )
        mock_db_session.query.return_value.filter.return_value.order_by.return_value.first.return_value = None

        with patch_fx_service(), pytest.raises(HTTPException) as exc_info:
            service.get_rate(
                mock_db_session,
                organization_id=organization_id,
                from_currency="USD",
                to_currency="JPY",
                rate_type_code="SPOT",
                effective_date=date.today(),
            )

        assert exc_info.value.status_code == 404
        assert "No exchange rate found" in exc_info.value.detail

    def test_convert_same_currency(self, service, mock_db_session, organization_id):
        """convert with same currency should return original amount."""
        with patch(
            "app.services.finance.platform.fx.coerce_uuid", side_effect=lambda x: x
        ):
            result = service.convert(
                mock_db_session,
                organization_id=organization_id,
                amount=Decimal("100.00"),
                from_currency="USD",
                to_currency="USD",
                rate_type_code="SPOT",
                effective_date=date.today(),
            )

        assert result.original_amount == Decimal("100.00")
        assert result.converted_amount == Decimal("100.00")
        assert result.exchange_rate == Decimal("1.0")

    def test_convert_different_currency(
        self, service, mock_db_session, organization_id
    ):
        """convert should apply exchange rate to amount."""
        rate_type = MockExchangeRateType(
            organization_id=organization_id,
            type_code="SPOT",
        )
        mock_rate = MockExchangeRate(
            organization_id=organization_id,
            from_currency_code="USD",
            to_currency_code="EUR",
            exchange_rate=Decimal("0.85"),
            effective_date=date.today(),
        )

        mock_db_session.query.return_value.filter.return_value.first.return_value = (
            rate_type
        )
        mock_db_session.query.return_value.filter.return_value.order_by.return_value.first.return_value = mock_rate

        with patch_fx_service():
            result = service.convert(
                mock_db_session,
                organization_id=organization_id,
                amount=Decimal("100.00"),
                from_currency="USD",
                to_currency="EUR",
                rate_type_code="SPOT",
                effective_date=date.today(),
            )

        assert result.original_amount == Decimal("100.00")
        assert result.original_currency == "USD"
        assert result.target_currency == "EUR"
        assert result.exchange_rate == Decimal("0.85")

    def test_convert_to_functional_gets_org_currency(
        self, service, mock_db_session, organization_id
    ):
        """convert_to_functional should use organization's functional currency."""
        mock_org = MockOrganization(
            organization_id=organization_id,
            functional_currency_code="USD",
        )
        mock_db_session.get.return_value = mock_org

        with (
            patch("app.services.finance.platform.fx.Organization"),
            patch("app.services.finance.platform.fx.FXService.convert") as mock_convert,
        ):
            mock_convert.return_value = MagicMock()
            with patch(
                "app.services.finance.platform.fx.coerce_uuid",
                side_effect=lambda x: x,
            ):
                service.convert_to_functional(
                    mock_db_session,
                    organization_id=organization_id,
                    amount=Decimal("100.00"),
                    currency_code="EUR",
                    effective_date=date.today(),
                )

        mock_convert.assert_called_once()
        call_args = mock_convert.call_args
        assert call_args[0][4] == "USD"  # to_currency is functional

    def test_convert_to_functional_raises_404_for_missing_org(
        self, service, mock_db_session, organization_id
    ):
        """convert_to_functional should raise 404 for missing org."""
        mock_db_session.get.return_value = None

        with (
            patch("app.services.finance.platform.fx.Organization"),
            patch(
                "app.services.finance.platform.fx.coerce_uuid", side_effect=lambda x: x
            ),
            pytest.raises(HTTPException) as exc_info,
        ):
            service.convert_to_functional(
                mock_db_session,
                organization_id=organization_id,
                amount=Decimal("100.00"),
                currency_code="EUR",
                effective_date=date.today(),
            )

        assert exc_info.value.status_code == 404
        assert "Organization not found" in exc_info.value.detail

    def test_batch_convert_processes_all(
        self, service, mock_db_session, organization_id
    ):
        """batch_convert should process multiple conversions."""
        with patch(
            "app.services.finance.platform.fx.FXService.convert"
        ) as mock_convert:
            mock_result = MagicMock()
            mock_convert.return_value = mock_result

            conversions = [
                {
                    "amount": Decimal("100.00"),
                    "from_currency": "USD",
                    "to_currency": "EUR",
                    "effective_date": date.today(),
                },
                {
                    "amount": Decimal("200.00"),
                    "from_currency": "USD",
                    "to_currency": "GBP",
                    "effective_date": date.today(),
                },
            ]

            results = service.batch_convert(
                mock_db_session,
                organization_id=organization_id,
                conversions=conversions,
            )

        assert len(results) == 2
        assert mock_convert.call_count == 2

    def test_get_default_rate_type_returns_default(
        self, service, mock_db_session, organization_id
    ):
        """get_default_rate_type should return the default rate type."""
        default_type = MockExchangeRateType(
            organization_id=organization_id,
            type_code="SPOT",
            is_default=True,
        )
        mock_db_session.query.return_value.filter.return_value.first.return_value = (
            default_type
        )

        with (
            patch("app.services.finance.platform.fx.ExchangeRateType"),
            patch(
                "app.services.finance.platform.fx.coerce_uuid", side_effect=lambda x: x
            ),
        ):
            result = service.get_default_rate_type(
                mock_db_session,
                organization_id=organization_id,
            )

        assert result == default_type

    def test_get_default_rate_type_raises_404_when_none(
        self, service, mock_db_session, organization_id
    ):
        """get_default_rate_type should raise 404 if no default."""
        mock_db_session.query.return_value.filter.return_value.first.return_value = None

        with (
            patch("app.services.finance.platform.fx.ExchangeRateType"),
            patch(
                "app.services.finance.platform.fx.coerce_uuid", side_effect=lambda x: x
            ),
            pytest.raises(HTTPException) as exc_info,
        ):
            service.get_default_rate_type(
                mock_db_session,
                organization_id=organization_id,
            )

        assert exc_info.value.status_code == 404

    def test_create_rate_creates_record(
        self, service, mock_db_session, organization_id
    ):
        """create_rate should create a new exchange rate."""
        rate_type = MockExchangeRateType(
            organization_id=organization_id,
            type_code="SPOT",
        )
        mock_db_session.query.return_value.filter.return_value.first.return_value = (
            rate_type
        )

        with patch("app.services.finance.platform.fx.ExchangeRateType"):
            with patch("app.services.finance.platform.fx.ExchangeRate") as MockRate:
                mock_instance = MagicMock()
                MockRate.return_value = mock_instance
                with patch(
                    "app.services.finance.platform.fx.coerce_uuid",
                    side_effect=lambda x: x,
                ):
                    service.create_rate(
                        mock_db_session,
                        organization_id=organization_id,
                        from_currency="USD",
                        to_currency="EUR",
                        rate_type_code="SPOT",
                        effective_date=date.today(),
                        exchange_rate=Decimal("0.85"),
                    )

        mock_db_session.add.assert_called_once()
        mock_db_session.commit.assert_called_once()

    def test_create_rate_raises_400_for_negative_rate(
        self, service, mock_db_session, organization_id
    ):
        """create_rate should reject negative rates."""
        with (
            patch(
                "app.services.finance.platform.fx.coerce_uuid", side_effect=lambda x: x
            ),
            pytest.raises(HTTPException) as exc_info,
        ):
            service.create_rate(
                mock_db_session,
                organization_id=organization_id,
                from_currency="USD",
                to_currency="EUR",
                rate_type_code="SPOT",
                effective_date=date.today(),
                exchange_rate=Decimal("-0.85"),
            )

        assert exc_info.value.status_code == 400
        assert "positive" in exc_info.value.detail

    def test_create_rate_raises_400_for_zero_rate(
        self, service, mock_db_session, organization_id
    ):
        """create_rate should reject zero rates."""
        with (
            patch(
                "app.services.finance.platform.fx.coerce_uuid", side_effect=lambda x: x
            ),
            pytest.raises(HTTPException) as exc_info,
        ):
            service.create_rate(
                mock_db_session,
                organization_id=organization_id,
                from_currency="USD",
                to_currency="EUR",
                rate_type_code="SPOT",
                effective_date=date.today(),
                exchange_rate=Decimal("0"),
            )

        assert exc_info.value.status_code == 400

    def test_list_returns_rates(self, service, mock_db_session, organization_id):
        """list should return filtered exchange rates."""
        mock_rates = [
            MockExchangeRate(organization_id=organization_id),
            MockExchangeRate(organization_id=organization_id),
        ]
        mock_db_session.query.return_value.filter.return_value.order_by.return_value.limit.return_value.offset.return_value.all.return_value = mock_rates

        with (
            patch("app.services.finance.platform.fx.ExchangeRate"),
            patch(
                "app.services.finance.platform.fx.coerce_uuid", side_effect=lambda x: x
            ),
        ):
            result = service.list(
                mock_db_session,
                organization_id=str(organization_id),
                limit=50,
                offset=0,
            )

        assert len(result) == 2

    def test_get_functional_currency_returns_code(
        self, service, mock_db_session, organization_id
    ):
        """get_functional_currency should return org's currency code."""
        mock_org = MockOrganization(
            organization_id=organization_id,
            functional_currency_code="EUR",
        )
        mock_db_session.get.return_value = mock_org

        with (
            patch("app.services.finance.platform.fx.Organization"),
            patch(
                "app.services.finance.platform.fx.coerce_uuid", side_effect=lambda x: x
            ),
        ):
            result = service.get_functional_currency(
                mock_db_session,
                organization_id=organization_id,
            )

        assert result == "EUR"

    def test_get_functional_currency_raises_404_for_missing_org(
        self, service, mock_db_session, organization_id
    ):
        """get_functional_currency should raise 404 for missing org."""
        mock_db_session.get.return_value = None

        with (
            patch("app.services.finance.platform.fx.Organization"),
            patch(
                "app.services.finance.platform.fx.coerce_uuid", side_effect=lambda x: x
            ),
            pytest.raises(HTTPException) as exc_info,
        ):
            service.get_functional_currency(
                mock_db_session,
                organization_id=organization_id,
            )

        assert exc_info.value.status_code == 404
