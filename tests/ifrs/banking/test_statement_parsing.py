"""The merged statement-value parsers still read BOTH banks' formats.

`parse_date`, `parse_period`, `parse_decimal` and `extract_account_number`
existed twice — once in the UBA importer, once in Zenith's — at 83–95%
identical. Merging them into `app/services/finance/banking/statement_parsing`
is only safe if the differences that were REAL survive, and one of them is:
UBA writes its period with a spaced dash, Zenith with a bare one.

These tests are written against the literal strings the two banks emit,
taken from the importers' own docstrings, so a future tidy-up of the
separator list fails here rather than in production against a workbook
nobody has locally.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.services.finance.banking.statement_parsing import (
    extract_account_number,
    parse_date,
    parse_decimal,
    parse_period,
)

# --------------------------------------------------------------------------
# parse_period — the load-bearing merge
# --------------------------------------------------------------------------


def test_uba_period_with_a_spaced_dash():
    assert parse_period("01-Jan-2022 - 31-Dec-2023") == (
        date(2022, 1, 1),
        date(2023, 12, 31),
    )


def test_zenith_period_with_a_bare_dash():
    assert parse_period("01/01/2022-30/09/2024") == (
        date(2022, 1, 1),
        date(2024, 9, 30),
    )


def test_zenith_period_with_the_word_to():
    assert parse_period("01/01/2022 TO 30/09/2024") == (
        date(2022, 1, 1),
        date(2024, 9, 30),
    )


def test_the_spaced_dash_is_tried_before_the_bare_one():
    """The whole reason the separator order is pinned.

    Splitting UBA's period on a bare '-' yields five fragments, not two. If
    the bare separator were tried first the `len(parts) == 2` guard is what
    saves it — this asserts the outcome either way, so a reordering that
    removes the guard is caught."""
    period = "01-Jan-2022 - 31-Dec-2023"
    assert len(period.split("-")) == 5, "premise: a bare split shatters this"
    assert parse_period(period) == (date(2022, 1, 1), date(2023, 12, 31))


def test_an_unreadable_period_is_not_an_error():
    """Callers treat a missing period as "derive it from the transactions"; a
    raise here would abandon an importable statement."""
    assert parse_period("") == (None, None)
    assert parse_period("sometime last year") == (None, None)


# --------------------------------------------------------------------------
# parse_date — four formats, previously listed in two different orders
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("15-Mar-2024", date(2024, 3, 15)),  # UBA
        ("15/03/2024", date(2024, 3, 15)),  # Zenith
        ("2024-03-15", date(2024, 3, 15)),  # ISO
        ("15-03-2024", date(2024, 3, 15)),  # numeric dashes
    ],
)
def test_every_format_both_importers_accepted(text, expected):
    assert parse_date(text) == expected


def test_the_formats_are_mutually_exclusive():
    """Justifies merging two differently-ordered lists into one.

    If any string parsed differently under the two orders, a single list
    could not serve both importers."""
    uba_order = ("%d-%b-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y")
    zenith_order = ("%d/%m/%Y", "%Y-%m-%d", "%d-%b-%Y", "%d-%m-%Y")

    from datetime import datetime

    def first_match(text, formats):
        for fmt in formats:
            try:
                return datetime.strptime(text, fmt).date()
            except ValueError:
                continue
        return None

    for text in ("15-Mar-2024", "15/03/2024", "2024-03-15", "15-03-2024"):
        assert first_match(text, uba_order) == first_match(text, zenith_order), text


def test_a_real_date_object_passes_through():
    assert parse_date(date(2024, 3, 15)) == date(2024, 3, 15)
    assert parse_date(None) is None


# --------------------------------------------------------------------------
# parse_decimal — money, and why not float
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1,234.56", Decimal("1234.56")),  # thousands separator
        ("1 234.56", Decimal("1234.56")),  # space separator
        ("-", None),  # an empty cell, drawn as a dash
        ("", None),
        ("not money", None),
        (Decimal("10.5"), Decimal("10.5")),
    ],
)
def test_amounts_from_a_spreadsheet_cell(value, expected):
    assert parse_decimal(value) == expected


def test_an_int_does_not_become_a_float():
    """`Decimal(str(value))` rather than `Decimal(value)` — the latter would
    inherit a float's representation error into a money column."""
    assert parse_decimal(1234) == Decimal("1234")
    assert parse_decimal(0.1) == Decimal("0.1")


# --------------------------------------------------------------------------
# extract_account_number — both banks bury it in prose
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("cell", "expected"),
    [
        ("1018904696 . ", "1018904696"),  # UBA
        ("CA         1011649523", "1011649523"),  # Zenith
        ("no number here", None),
        ("", None),
    ],
)
def test_the_account_number_is_found_inside_the_cell(cell, expected):
    assert extract_account_number(cell) == expected
