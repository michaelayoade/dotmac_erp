from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.finance.payments import paystack_webhook


@pytest.mark.asyncio
async def test_unknown_verified_charge_is_forwarded_to_selfcare():
    request = MagicMock()
    request.body = AsyncMock(return_value=b'{"event":"charge.success"}')
    request.json = AsyncMock(
        return_value={
            "event": "charge.success",
            "data": {"reference": "DMAC-SELFCARE-1"},
        }
    )
    db = MagicMock()
    sub_config = MagicMock()
    sub_config.is_configured.return_value = True
    sub_client = MagicMock()

    with (
        patch(
            "app.api.finance.payments.PaymentService.get_intent_by_reference",
            return_value=None,
        ),
        patch("app.api.finance.payments.settings") as settings,
        patch("app.api.finance.payments.get_paystack_config", return_value=MagicMock()),
        patch("app.api.finance.payments.prime_session"),
        patch("app.api.finance.payments.set_payment_tenant_context"),
        patch(
            "app.services.finance.payments.paystack_client.PaystackClient.verify_webhook_signature",
            return_value=True,
        ),
        patch(
            "app.services.dotmac_sub.DotmacSubConfig.for_org",
            return_value=sub_config,
        ),
        patch("app.services.dotmac_sub.DotmacSubClient", return_value=sub_client),
    ):
        settings.default_organization_id = "89ed80a7-7f7e-4f27-8794-cf260d985542"
        response = await paystack_webhook(
            request=request,
            x_paystack_signature="verified-signature",
            db=db,
        )

    assert response.status == "forwarded"
    sub_client.reconcile_paystack_reference.assert_called_once_with("DMAC-SELFCARE-1")


@pytest.mark.asyncio
async def test_unknown_charge_with_invalid_signature_is_not_forwarded():
    request = MagicMock()
    request.body = AsyncMock(return_value=b"{}")
    request.json = AsyncMock(
        return_value={
            "event": "charge.success",
            "data": {"reference": "DMAC-SELFCARE-2"},
        }
    )

    with (
        patch(
            "app.api.finance.payments.PaymentService.get_intent_by_reference",
            return_value=None,
        ),
        patch("app.api.finance.payments.settings") as settings,
        patch("app.api.finance.payments.get_paystack_config", return_value=MagicMock()),
        patch("app.api.finance.payments.prime_session"),
        patch("app.api.finance.payments.set_payment_tenant_context"),
        patch(
            "app.services.finance.payments.paystack_client.PaystackClient.verify_webhook_signature",
            return_value=False,
        ),
        pytest.raises(Exception) as exc_info,
    ):
        settings.default_organization_id = "89ed80a7-7f7e-4f27-8794-cf260d985542"
        await paystack_webhook(
            request=request,
            x_paystack_signature="invalid-signature",
            db=MagicMock(),
        )

    assert getattr(exc_info.value, "status_code", None) == 401
