from unittest.mock import MagicMock, patch

from fastapi.responses import RedirectResponse

from app.services.expense.web import ExpenseClaimsWebService


def _make_auth():
    auth = MagicMock()
    auth.organization_id = "00000000-0000-0000-0000-000000000001"
    return auth


def test_claim_receipt_response_redirects_external_url():
    db = MagicMock()
    auth = _make_auth()
    item = MagicMock()
    item.receipt_url = "https://cdn.example.com/receipts/r-001.pdf"
    db.scalar.return_value = item

    response = ExpenseClaimsWebService.claim_receipt_response(
        claim_id="11111111-1111-1111-1111-111111111111",
        item_id="22222222-2222-2222-2222-222222222222",
        auth=auth,
        db=db,
    )

    assert isinstance(response, RedirectResponse)
    assert response.status_code == 302
    assert response.headers["location"] == item.receipt_url


def test_claim_receipt_response_handles_unavailable_file():
    db = MagicMock()
    auth = _make_auth()
    item = MagicMock()
    item.receipt_url = "/app/uploads/expense_receipts/org/file.pdf"
    db.scalar.return_value = item

    with patch.object(
        ExpenseClaimsWebService,
        "_resolve_claim_receipt_path",
        side_effect=ValueError("bad path"),
    ):
        response = ExpenseClaimsWebService.claim_receipt_response(
            claim_id="11111111-1111-1111-1111-111111111111",
            item_id="22222222-2222-2222-2222-222222222222",
            auth=auth,
            db=db,
        )

    assert isinstance(response, RedirectResponse)
    assert response.status_code == 303
    assert "error=Receipt+file+is+unavailable" in response.headers["location"]


def test_claim_receipt_response_uses_index_for_multiple_receipts():
    db = MagicMock()
    auth = _make_auth()
    item = MagicMock()
    item.receipt_url = (
        '["https://cdn.example.com/receipts/r-001.pdf",'
        '"https://cdn.example.com/receipts/r-002.pdf"]'
    )
    db.scalar.return_value = item

    response = ExpenseClaimsWebService.claim_receipt_response(
        claim_id="11111111-1111-1111-1111-111111111111",
        item_id="22222222-2222-2222-2222-222222222222",
        auth=auth,
        db=db,
        index=1,
    )

    assert isinstance(response, RedirectResponse)
    assert response.status_code == 302
    assert response.headers["location"] == "https://cdn.example.com/receipts/r-002.pdf"
