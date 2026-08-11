"""Value parsing shared by every bank-statement importer.

`scripts/import_uba_statements.py` and `scripts/import_zenith_statements.py`
each carried their own `parse_date`, `parse_period`, `parse_decimal` and
`extract_account_number`. They were 83–95% identical, which is the worst
ratio to have: close enough that nobody noticed two copies, different enough
that a fix to one never reached the other.

The differences that were real are preserved here as a UNION, not by picking
a winner — see `parse_period`, where the merge order is load-bearing.

Pure functions: no database, no ORM, no I/O. That is what makes the format
parsers testable without a bank, and it is why this module sits apart from
`statement_import`, which owns the writes.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

# Both banks' formats. These are mutually exclusive by separator and field
# order — `%d-%b-%Y` needs a month NAME, `%d/%m/%Y` needs slashes, and
# `%Y-%m-%d` leads with a four-digit year — so the two importers listing them
# in different orders was harmless, and one order serves both.
_DATE_FORMATS = ("%d-%b-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y")

# ORDER IS LOAD-BEARING. UBA writes '01-Jan-2022 - 31-Dec-2023' (spaced
# dash); Zenith writes '01/01/2022-30/09/2024' (bare dash). The spaced form
# must be tried FIRST: splitting a UBA period on a bare '-' shatters it into
# six pieces rather than two. The `len(parts) == 2` check below is the
# backstop that makes a wrong split fail closed instead of parsing garbage.
_PERIOD_SEPARATORS = (" - ", " TO ", " to ", "-")

_ACCOUNT_NUMBER = re.compile(r"(\d{10})")


def parse_date(value: object) -> date | None:
    """A statement date, from whatever the workbook cell happened to hold."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        for fmt in _DATE_FORMATS:
            try:
                return datetime.strptime(text, fmt).date()
            except ValueError:
                continue
    return None


def parse_period(period: str) -> tuple[date | None, date | None]:
    """The (start, end) of a statement period string.

    Returns `(None, None)` rather than raising: a statement whose period
    header cannot be read is still importable line by line, and the callers
    treat a missing period as "derive it from the transactions".
    """
    if not period:
        return None, None

    text = str(period).strip()

    for separator in _PERIOD_SEPARATORS:
        if separator not in text:
            continue
        parts = text.split(separator)
        if len(parts) != 2:
            # Wrong separator for this format — e.g. a bare '-' against a
            # '01-Jan-2022 - 31-Dec-2023' period. Keep looking.
            continue
        start = parse_date(parts[0].strip())
        end = parse_date(parts[1].strip())
        if start and end:
            return start, end

    return None, None


def parse_decimal(value: object) -> Decimal | None:
    """A money amount, from a cell that may be numeric, text, or a dash."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    if isinstance(value, str):
        text = value.strip().replace(",", "").replace(" ", "")
        if not text or text == "-":
            return None
        try:
            return Decimal(text)
        except InvalidOperation:
            return None
    return None


def extract_account_number(value: str) -> str | None:
    """The ten-digit account number embedded in a header cell.

    Both banks bury it in prose — UBA writes `'1018904696 . '`, Zenith writes
    `'CA         1011649523'` — so this looks for the number rather than
    trusting the cell to contain only that.
    """
    if not value:
        return None
    match = _ACCOUNT_NUMBER.search(str(value))
    return match.group(1) if match else None
