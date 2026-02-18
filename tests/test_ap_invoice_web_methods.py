import inspect


def test_ap_invoice_web_service_has_edit_and_update_methods():
    """
    Regression test: AP invoice web routes call these methods.
    Missing them results in runtime 500s on /finance/ap/invoices/{id}/edit and actions.
    """
    from app.services.finance.ap.web import ap_web_service

    for name in [
        "invoice_edit_form_response",
        "update_invoice_response",
        "submit_invoice_response",
        "approve_invoice_response",
        "post_invoice_response",
        "void_invoice_response",
    ]:
        assert hasattr(ap_web_service, name), name

    sig = inspect.signature(ap_web_service.invoice_edit_form_response)
    assert "invoice_id" in sig.parameters
