"""The one owner of bank-account provisioning and statement-line conversion.

`import_uba_statements.py` and `import_zenith_statements.py` each carried
their own `ensure_bank_account` — 90% identical, and both of them CREATE
`BankAccount` rows. That is two writers for the same table, living in
`scripts/`, where no guard was looking. `convert_to_statement_lines` was
duplicated the same way at 95%.

The only genuine difference between the two `ensure_bank_account` copies was
a hardcoded bank name and sort code. That is data about a bank, not
behaviour, so it moves into `BankProfile` and the code becomes shared.

## What this module does NOT do

It does not open workbooks, and it does not know what a UBA row looks like.
Format parsing stays with the format (see `statement_parsing` for the shared
value helpers); this owns the decisions — does the account exist, what GL
account backs it, what does a parsed transaction become. Keeping the two
apart is what lets the parsers be tested without a database and this be
tested without a spreadsheet.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance.banking.bank_account import (
    BankAccount,
    BankAccountStatus,
    BankAccountType,
)
from app.models.finance.banking.bank_statement import StatementLineType
from app.models.finance.gl.account import Account
from app.services.finance.banking.bank_statement import StatementLineInput

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BankProfile:
    """Which bank a batch of statements came from.

    Was a hardcoded string pair inside each importer's `ensure_bank_account`.
    """

    bank_name: str
    bank_code: str


@dataclass(frozen=True)
class AccountProfile:
    """One account at that bank, as the importer's configuration describes it."""

    account_number: str
    account_name: str
    currency_code: str
    gl_account_code: str


class GLAccountNotFound(LookupError):
    """The GL account backing a bank account is absent for this organization.

    Its own type rather than a bare `ValueError`: this is a provisioning gap
    in the chart of accounts, not a malformed statement, and a caller
    importing several accounts may reasonably skip one and continue.
    """


def find_bank_account(
    db: Session, *, organization_id: UUID, account_number: str
) -> BankAccount | None:
    """Look the account up without creating it.

    The read half of `ensure_bank_account`, separate because a dry run needs
    exactly this and must not take the create branch.
    """
    return db.execute(
        select(BankAccount).where(
            BankAccount.organization_id == organization_id,
            BankAccount.account_number == account_number,
        )
    ).scalar_one_or_none()


def ensure_bank_account(
    db: Session,
    *,
    organization_id: UUID,
    bank: BankProfile,
    account: AccountProfile,
) -> BankAccount:
    """Return the organization's bank account, creating it if absent.

    `organization_id` is required and explicit. Both importers previously
    inferred it with `select(BankAccount).limit(1)` — correct with one
    organization, and silently the wrong tenant with two.
    """
    existing = find_bank_account(
        db, organization_id=organization_id, account_number=account.account_number
    )

    if existing:
        logger.info("Found existing bank account: %s", account.account_number)
        return existing

    gl_account = db.execute(
        select(Account).where(
            Account.organization_id == organization_id,
            Account.account_code == account.gl_account_code,
        )
    ).scalar_one_or_none()

    if not gl_account:
        raise GLAccountNotFound(
            f"GL account {account.gl_account_code} not found for organization "
            f"{organization_id} — cannot create bank account "
            f"{account.account_number}"
        )

    created = BankAccount(
        organization_id=organization_id,
        bank_name=bank.bank_name,
        bank_code=bank.bank_code,
        account_name=account.account_name,
        account_number=account.account_number,
        account_type=BankAccountType.checking,
        currency_code=account.currency_code,
        gl_account_id=gl_account.account_id,
        status=BankAccountStatus.active,
    )
    db.add(created)
    db.flush()

    logger.info(
        "Created bank account: %s - %s", account.account_number, account.account_name
    )
    return created


def to_statement_lines(
    transactions: list[dict],
    start_line: int = 1,
) -> list[StatementLineInput]:
    """Parsed transaction dicts to statement-line inputs.

    `raw_data` keeps the original values in JSON-serialisable form so a line
    can be re-examined after import without the workbook — dates as ISO
    strings, amounts as strings rather than floats, since a float would lose
    the precision the Decimal was parsed to preserve.
    """
    lines: list[StatementLineInput] = []

    for offset, transaction in enumerate(transactions):
        debit = transaction.get("debit")
        credit = transaction.get("credit")
        amount = debit or credit or Decimal("0")
        line_type = StatementLineType.debit if debit else StatementLineType.credit

        # Indexed, not `.get()`: a statement line without a posting date
        # is not a line, and `StatementLineInput.transaction_date` is
        # non-optional. Failing loudly here beats inserting a null date.
        posted = transaction["date_posted"]
        valued = transaction.get("value_date")
        balance = transaction.get("balance")

        lines.append(
            StatementLineInput(
                line_number=start_line + offset,
                transaction_date=posted,
                value_date=valued,
                transaction_type=line_type,
                amount=amount,
                description=transaction.get("description"),
                reference=transaction.get("reference"),
                running_balance=balance,
                raw_data={
                    "date_posted": posted.isoformat() if posted else None,
                    "value_date": valued.isoformat() if valued else None,
                    "description": transaction.get("description"),
                    "reference": transaction.get("reference"),
                    "debit": str(debit) if debit else None,
                    "credit": str(credit) if credit else None,
                    "balance": str(balance) if balance else None,
                },
            )
        )

    return lines
