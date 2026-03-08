from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.finance.banking.bank_account import BankAccount
from app.models.finance.banking.bank_statement import (
    BankStatement,
    BankStatementLine,
)

_TOKEN_RE = re.compile(r"[A-Z0-9][A-Z0-9/_-]{3,}", re.IGNORECASE)
_TRANSFER_HINT_RE = re.compile(
    r"transfer|inter.?bank|xfer|trx\s*to|trx\s*from|trf|settlement|nibss|nip",
    re.IGNORECASE,
)
_FEE_HINT_RE = re.compile(
    r"fee|charge|commission|levy",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class NormalizedStatementLine:
    line_id: UUID
    transaction_date: object
    amount: Decimal
    signed_amount: Decimal
    searchable_text: str
    references: tuple[str, ...]


@dataclass(frozen=True)
class LineSignals:
    references: frozenset[str]
    tokens: frozenset[str]
    has_transfer_hint: bool
    has_fee_hint: bool


@dataclass
class ReconciliationRunContext:
    db: Session
    organization_id: UUID
    statement: BankStatement
    bank_account: BankAccount
    unmatched_lines: list[BankStatementLine]
    matched_line_ids: set[UUID]
    extra_gl_account_ids: set[UUID] | None
    config: Any
    policy: Any
    result: Any
    normalized_lines: dict[UUID, NormalizedStatementLine] = field(default_factory=dict)
    line_signals: dict[UUID, LineSignals] = field(default_factory=dict)
    provider_cache: dict[str, list[Any]] = field(default_factory=dict)
    trackers: dict[str, set[UUID]] = field(default_factory=dict)

    def still_unmatched_lines(self) -> list[BankStatementLine]:
        return [
            line for line in self.unmatched_lines if line.line_id not in self.matched_line_ids
        ]

    def tracker(self, key: str) -> set[UUID]:
        if key not in self.trackers:
            self.trackers[key] = set()
        return self.trackers[key]


class CandidateProvider(Protocol):
    provider_key: str
    source_type: str

    def load(self, service: Any, ctx: ReconciliationRunContext) -> list[Any]:
        ...


class MatchStrategy(Protocol):
    strategy_id: str

    def run(self, service: Any, ctx: ReconciliationRunContext) -> None:
        ...


def normalize_statement_line(line: BankStatementLine) -> NormalizedStatementLine:
    references = tuple(
        value.strip()
        for value in (line.reference, line.bank_reference, line.transaction_id)
        if value and value.strip()
    )
    searchable_text = " ".join(
        value.strip()
        for value in (
            line.reference,
            line.bank_reference,
            line.description,
            line.payee_payer,
            line.transaction_id,
        )
        if value and value.strip()
    )
    return NormalizedStatementLine(
        line_id=line.line_id,
        transaction_date=line.transaction_date,
        amount=line.amount,
        signed_amount=line.signed_amount,
        searchable_text=searchable_text,
        references=references,
    )


def extract_line_signals(line: NormalizedStatementLine) -> LineSignals:
    tokens = frozenset(match.group(0).lower() for match in _TOKEN_RE.finditer(line.searchable_text))
    references = frozenset(ref.lower() for ref in line.references)
    text = line.searchable_text.lower()
    return LineSignals(
        references=references,
        tokens=tokens,
        has_transfer_hint=bool(_TRANSFER_HINT_RE.search(text)),
        has_fee_hint=bool(_FEE_HINT_RE.search(text)),
    )

