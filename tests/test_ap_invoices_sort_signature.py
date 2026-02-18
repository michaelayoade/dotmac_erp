import inspect


def test_ap_invoices_list_includes_sort_params():
    """
    Regression test: /finance/ap/invoices web route passes sort + sort_dir
    to ap_web_service.list_invoices_response().

    If the service signature drops these parameters, the route will raise
    a TypeError at runtime and the page will 500.
    """
    from app.services.finance.ap.web import ap_web_service

    sig = inspect.signature(ap_web_service.list_invoices_response)
    params = list(sig.parameters.keys())

    assert "sort" in params
    assert "sort_dir" in params
