from pathlib import Path


def _read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def _assert_header_order(path: str, first_header: str, next_header: str) -> None:
    html = _read(path)
    first_pos = html.find(first_header)
    next_pos = html.find(next_header)
    assert first_pos != -1, f"Missing header '{first_header}' in {path}"
    assert next_pos != -1, f"Missing header '{next_header}' in {path}"
    assert first_pos < next_pos, (
        f"Expected '{first_header}' before '{next_header}' in {path}"
    )


def test_ar_ap_transaction_tables_put_date_first():
    ordered_headers = {
        "templates/finance/ar/invoices.html": ("Invoice Date", "Invoice #"),
        "templates/finance/ar/receipts.html": ("Receipt Date", "Receipt #"),
        "templates/finance/ar/credit_notes.html": (
            '<th scope="col">Date</th>',
            "Credit Note #",
        ),
        "templates/finance/ar/quotes.html": (
            '<th scope="col">Date</th>',
            '<th scope="col">Number</th>',
        ),
        "templates/finance/ar/sales_orders.html": (
            '<th scope="col">Date</th>',
            "SO Number",
        ),
        "templates/finance/ap/invoices.html": ("Invoice Date", "Invoice #"),
        "templates/finance/ap/payments.html": ("Payment Date", "Payment #"),
        "templates/finance/ap/purchase_orders.html": ("PO Date", "PO Number"),
        "templates/finance/ap/goods_receipts.html": ("Receipt Date", "Receipt #"),
        "templates/finance/ap/payment_batches.html": (
            '<th scope="col">Date</th>',
            "Batch #",
        ),
    }

    for path, (date_header, next_header) in ordered_headers.items():
        _assert_header_order(path, date_header, next_header)


def test_other_finance_transaction_tables_put_date_first():
    ordered_headers = {
        "templates/finance/banking/statements.html": ("Statement Date", "Statement #"),
        "templates/finance/banking/reconciliations.html": (
            '<th scope="col">Date</th>',
            "Bank Account",
        ),
        "templates/finance/gl/journals.html": ("Date</th>", "Entry #"),
        "templates/expense/list.html": (
            '<th scope="col">Date</th>',
            '<th scope="col">Number</th>',
        ),
    }

    for path, (date_header, next_header) in ordered_headers.items():
        _assert_header_order(path, date_header, next_header)


def test_key_transaction_date_columns_are_visible_on_small_screens():
    for path, date_header in [
        ("templates/finance/ar/invoices.html", "Invoice Date"),
        ("templates/finance/ar/receipts.html", "Receipt Date"),
        ("templates/finance/ap/invoices.html", "Invoice Date"),
        ("templates/finance/ap/payments.html", "Payment Date"),
    ]:
        html = _read(path)
        assert date_header in html, f"Missing header '{date_header}' in {path}"
        assert f'hidden sm:table-cell" scope="col">{date_header}' not in html
