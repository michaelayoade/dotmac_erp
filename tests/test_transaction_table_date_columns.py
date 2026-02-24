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


def _find_header(html: str, *patterns: str) -> int:
    """Find the first occurrence of any of the given patterns.

    This supports both literal <th> headers and sortable_th macro calls.
    Returns the position of the first match, or -1 if none found.
    """
    for pattern in patterns:
        pos = html.find(pattern)
        if pos != -1:
            return pos
    return -1


def _assert_header_order_multi(
    path: str,
    first_patterns: tuple[str, ...],
    next_patterns: tuple[str, ...],
) -> None:
    """Assert header ordering, accepting multiple patterns per header.

    Useful when headers may be literal <th> or sortable_th() macro calls.
    """
    html = _read(path)
    first_pos = _find_header(html, *first_patterns)
    next_pos = _find_header(html, *next_patterns)
    assert first_pos != -1, f"Missing header {first_patterns} in {path}"
    assert next_pos != -1, f"Missing header {next_patterns} in {path}"
    assert first_pos < next_pos, (
        f"Expected {first_patterns} before {next_patterns} in {path}"
    )


def test_ar_ap_transaction_tables_put_date_first():
    # Templates may use literal <th> or sortable_th() macro.
    # Each entry: (path, first_header_patterns, next_header_patterns)
    ordered_headers = [
        (
            "templates/finance/ar/invoices.html",
            ("Invoice Date", 'sortable_th("Invoice Date"'),
            ("Invoice #", 'sortable_th("Invoice #"'),
        ),
        (
            "templates/finance/ar/receipts.html",
            ("Receipt Date", 'sortable_th("Receipt Date"'),
            ("Receipt #", 'sortable_th("Receipt #"'),
        ),
        (
            "templates/finance/ar/credit_notes.html",
            ('<th scope="col">Date</th>', 'sortable_th("Date"'),
            ("Credit Note #", 'sortable_th("Credit Note #"'),
        ),
        (
            "templates/finance/ar/quotes.html",
            ('<th scope="col">Date</th>', 'sortable_th("Date"'),
            (
                '<th scope="col">Number</th>',
                'sortable_th("Number"',
                'sortable_th("Quote #"',
            ),
        ),
        (
            "templates/finance/ar/sales_orders.html",
            ('<th scope="col">Date</th>', 'sortable_th("Date"'),
            ("SO Number", 'sortable_th("SO Number"', 'sortable_th("SO #"'),
        ),
        (
            "templates/finance/ap/invoices.html",
            ("Invoice Date", 'sortable_th("Invoice Date"'),
            ("Invoice #", 'sortable_th("Invoice #"'),
        ),
        (
            "templates/finance/ap/payments.html",
            ("Payment Date", 'sortable_th("Payment Date"'),
            ("Payment #", 'sortable_th("Payment #"'),
        ),
        (
            "templates/finance/ap/purchase_orders.html",
            ("PO Date", 'sortable_th("PO Date"'),
            ("PO Number", 'sortable_th("PO Number"'),
        ),
        (
            "templates/finance/ap/goods_receipts.html",
            ("Receipt Date", 'sortable_th("Receipt Date"'),
            ("Receipt #", 'sortable_th("Receipt #"'),
        ),
        (
            "templates/finance/ap/payment_batches.html",
            ('<th scope="col">Date</th>', 'sortable_th("Date"'),
            ("Batch #", 'sortable_th("Batch #"'),
        ),
    ]

    for path, first_patterns, next_patterns in ordered_headers:
        _assert_header_order_multi(path, first_patterns, next_patterns)


def test_other_finance_transaction_tables_put_date_first():
    ordered_headers = [
        (
            "templates/finance/banking/statements.html",
            ("Statement Date", 'sortable_th("Statement Date"', 'sortable_th("Date"'),
            ("Statement #", 'sortable_th("Statement #"', "STMT"),
        ),
        (
            "templates/finance/banking/reconciliations.html",
            ('<th scope="col">Date</th>', 'sortable_th("Date"'),
            ("Bank Account", 'sortable_th("Bank Account"'),
        ),
        (
            "templates/finance/gl/journals.html",
            ("Date</th>", 'sortable_th("Date"'),
            ("Entry #", 'sortable_th("Entry #"'),
        ),
        (
            "templates/expense/list.html",
            ('<th scope="col">Date</th>', 'sortable_th("Date"'),
            ('<th scope="col">Number</th>', 'sortable_th("Number"'),
        ),
    ]

    for path, first_patterns, next_patterns in ordered_headers:
        _assert_header_order_multi(path, first_patterns, next_patterns)


def test_key_transaction_date_columns_are_visible_on_small_screens():
    for path, date_header in [
        ("templates/finance/ar/invoices.html", "Invoice Date"),
        ("templates/finance/ar/receipts.html", "Receipt Date"),
        ("templates/finance/ap/invoices.html", "Invoice Date"),
        ("templates/finance/ap/payments.html", "Payment Date"),
    ]:
        html = _read(path)
        # Header may be literal or in a sortable_th macro call
        has_literal = date_header in html
        has_macro = f'sortable_th("{date_header}"' in html
        assert has_literal or has_macro, f"Missing header '{date_header}' in {path}"
        # If literal, ensure it's not hidden on small screens
        if has_literal:
            assert f'hidden sm:table-cell" scope="col">{date_header}' not in html
