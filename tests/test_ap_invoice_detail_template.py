from __future__ import annotations

from pathlib import Path


def test_ap_invoice_detail_shows_payment_history_before_comments():
    template_path = (
        Path(__file__).resolve().parent.parent
        / "templates"
        / "finance"
        / "ap"
        / "invoice_detail.html"
    )
    template = template_path.read_text(encoding="utf-8")

    assert template.index("Payment History") < template.index('id="comments"')
