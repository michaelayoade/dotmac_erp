from __future__ import annotations

from pathlib import Path


def test_ap_payment_form_open_invoice_search_controls_present():
    template_path = (
        Path(__file__).resolve().parent.parent
        / "templates"
        / "finance"
        / "ap"
        / "payment_form.html"
    )
    template = template_path.read_text(encoding="utf-8")

    assert 'id="open_invoice_search"' in template
    assert 'x-model.trim="invoiceSearch"' in template
    assert "invoiceMatchesSearch" in template
    assert "Select invoice {{ inv.invoice_number }} for allocation" in template
