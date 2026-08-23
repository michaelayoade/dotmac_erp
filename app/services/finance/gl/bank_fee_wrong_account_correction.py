"""Digest-bound execution of the approved bank-fee wrong-account correction.

The checked-in SQL schedule owns admission.  This service consumes its private
W3a output, binds it to the reviewed digests, revalidates the named database
state under row locks, and delegates every write to :class:`ReversalService`.
It deliberately does not commit; the operator entry point owns the one atomic
transaction.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from ipaddress import ip_address
from typing import Any
from uuid import UUID

from sqlalchemy import bindparam, func, select, text
from sqlalchemy.orm import Session

from app.models.finance.gl.fiscal_period import FiscalPeriod, PeriodStatus
from app.models.finance.gl.journal_entry import JournalEntry, JournalStatus
from app.models.finance.gl.journal_entry_line import JournalEntryLine
from app.models.finance.gl.posted_ledger_line import PostedLedgerLine
from app.models.finance.gl.posting_batch import BatchStatus, PostingBatch
from app.services.finance.gl.reversal import ReversalService

_PLAN_FIELD_COUNT = 22
_HEX_MD5 = re.compile(r"^[0-9a-f]{32}$")
_BANK_FEE_CORRELATION = re.compile(
    r"^bank-fee-(?P<line_id>[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$"
)
_CANONICAL_LEDGER_DATE = date(2026, 3, 13)
_CANONICAL_LEDGER_PERIOD_ID = UUID("7bc1edbb-270c-4096-b9e4-67cc72dd44a4")


class CorrectionRefused(RuntimeError):
    """The approved correction no longer matches its evidence or target."""


@dataclass(frozen=True)
class MappingApproval:
    canonical_bank_code: str
    legacy_bank_code: str
    expected_statement_line_resolves: bool


APPROVED_MAPPINGS: dict[str, MappingApproval] = {
    "PAYSTACK_OPEX_LEGACY_TO_1211": MappingApproval(
        canonical_bank_code="1211",
        legacy_bank_code="Paystack OPEX - DT",
        expected_statement_line_resolves=True,
    ),
    "ZENITH_USD_LEGACY_TO_1207": MappingApproval(
        canonical_bank_code="1207",
        legacy_bank_code="Zenith USD - DT",
        expected_statement_line_resolves=False,
    ),
}


@dataclass(frozen=True)
class CorrectionApproval:
    """Finance-approved aggregate and digest binding for one private plan."""

    plan_sha256: str
    schedule_digest: str
    target_count: int
    affected_statement_lines: int
    gross: Decimal
    reversal_date: date
    reversal_fiscal_period_id: UUID
    mapping_counts: dict[str, int]


APPROVED_CORRECTION = CorrectionApproval(
    plan_sha256="dbeab5dafe0d27bafa834fde43c35ae9f36996ba5a332623784619cddcbd9148",
    schedule_digest="a5e1776785856c579bd3ed6bc0d68308",
    target_count=429,
    affected_statement_lines=39,
    gross=Decimal("7764.680000"),
    reversal_date=date(2026, 8, 23),
    reversal_fiscal_period_id=UUID("13659716-9fe2-42f8-8aca-32cd67da22b6"),
    mapping_counts={
        "PAYSTACK_OPEX_LEGACY_TO_1211": 352,
        "ZENITH_USD_LEGACY_TO_1207": 77,
    },
)


def _parse_bool(value: str, *, field: str) -> bool:
    if value == "t":
        return True
    if value == "f":
        return False
    raise CorrectionRefused(f"plan {field} must use PostgreSQL t/f text")


def _parse_uuid(value: str, *, field: str) -> UUID:
    try:
        parsed = UUID(value)
    except ValueError as exc:
        raise CorrectionRefused(f"plan {field} is not a UUID") from exc
    if str(parsed) != value:
        raise CorrectionRefused(f"plan {field} is not a canonical UUID")
    return parsed


def _parse_date(value: str, *, field: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise CorrectionRefused(f"plan {field} is not an ISO date") from exc


def _md5(value: str) -> str:
    # This is an evidence-compatibility digest, not a password or signature.
    return hashlib.md5(value.encode(), usedforsecurity=False).hexdigest()  # noqa: S324


@dataclass(frozen=True)
class CorrectionPlanRow:
    target_journal_entry_id: UUID
    target_journal_number: str
    target_posting_batch_id: UUID
    target_idempotency_key: str
    statement_line_id: UUID
    canonical_journal_entry_id: UUID
    canonical_journal_number: str
    mapping_name: str
    legacy_bank_code: str
    canonical_bank_code: str
    statement_line_resolves: bool
    expected_statement_line_resolves: bool
    total_debit_functional: Decimal
    target_effect_hash: str
    canonical_effect_hash: str
    target_ledger_posting_date: date
    target_ledger_period_id: UUID
    canonical_ledger_posting_date: date
    canonical_ledger_period_id: UUID
    reversal_date: date
    reversal_fiscal_period_id: UUID
    schedule_row_hash: str

    @classmethod
    def from_fields(cls, fields: list[str]) -> CorrectionPlanRow:
        if len(fields) != _PLAN_FIELD_COUNT:
            raise CorrectionRefused(
                f"plan row has {len(fields)} fields; expected {_PLAN_FIELD_COUNT}"
            )
        try:
            amount = Decimal(fields[12])
        except InvalidOperation as exc:
            raise CorrectionRefused("plan gross amount is not decimal") from exc
        if amount.as_tuple().exponent != -6 or amount <= 0:
            raise CorrectionRefused("plan gross amount must be positive scale-6")

        hashes = (fields[13], fields[14], fields[21])
        if any(_HEX_MD5.fullmatch(value) is None for value in hashes):
            raise CorrectionRefused("plan contains a non-canonical MD5 digest")

        row = cls(
            target_journal_entry_id=_parse_uuid(fields[0], field="target id"),
            target_journal_number=fields[1],
            target_posting_batch_id=_parse_uuid(fields[2], field="target batch id"),
            target_idempotency_key=fields[3],
            statement_line_id=_parse_uuid(fields[4], field="statement line id"),
            canonical_journal_entry_id=_parse_uuid(
                fields[5], field="canonical journal id"
            ),
            canonical_journal_number=fields[6],
            mapping_name=fields[7],
            legacy_bank_code=fields[8],
            canonical_bank_code=fields[9],
            statement_line_resolves=_parse_bool(
                fields[10], field="statement resolution"
            ),
            expected_statement_line_resolves=_parse_bool(
                fields[11], field="expected statement resolution"
            ),
            total_debit_functional=amount,
            target_effect_hash=fields[13],
            canonical_effect_hash=fields[14],
            target_ledger_posting_date=_parse_date(
                fields[15], field="target ledger date"
            ),
            target_ledger_period_id=_parse_uuid(
                fields[16], field="target ledger period"
            ),
            canonical_ledger_posting_date=_parse_date(
                fields[17], field="canonical ledger date"
            ),
            canonical_ledger_period_id=_parse_uuid(
                fields[18], field="canonical ledger period"
            ),
            reversal_date=_parse_date(fields[19], field="reversal date"),
            reversal_fiscal_period_id=_parse_uuid(
                fields[20], field="reversal period"
            ),
            schedule_row_hash=fields[21],
        )
        row._validate_closed_vocabulary()
        return row

    def _validate_closed_vocabulary(self) -> None:
        mapping = APPROVED_MAPPINGS.get(self.mapping_name)
        if mapping is None:
            raise CorrectionRefused("plan contains an unapproved account mapping")
        if (
            self.canonical_bank_code != mapping.canonical_bank_code
            or self.legacy_bank_code != mapping.legacy_bank_code
            or self.expected_statement_line_resolves
            is not mapping.expected_statement_line_resolves
            or self.statement_line_resolves
            is not self.expected_statement_line_resolves
        ):
            raise CorrectionRefused("plan account mapping or source state drifted")
        if not self.target_journal_number or not self.canonical_journal_number:
            raise CorrectionRefused("plan journal number is empty")
        expected_key = f"backfill-stranded-bank-fees-{self.target_journal_number}"
        if self.target_idempotency_key != expected_key:
            raise CorrectionRefused("plan target idempotency namespace drifted")
        if (
            self.canonical_ledger_posting_date != _CANONICAL_LEDGER_DATE
            or self.canonical_ledger_period_id != _CANONICAL_LEDGER_PERIOD_ID
        ):
            raise CorrectionRefused("plan canonical timing drifted")
        if self.schedule_row_hash != self.recomputed_schedule_row_hash():
            raise CorrectionRefused("plan schedule row hash does not reproduce")

    def recomputed_schedule_row_hash(self) -> str:
        material = "|".join(
            (
                str(self.target_journal_entry_id),
                str(self.target_posting_batch_id),
                self.target_idempotency_key,
                str(self.canonical_journal_entry_id),
                self.mapping_name,
                self.canonical_bank_code,
                self.legacy_bank_code,
                str(self.expected_statement_line_resolves).lower(),
                self.target_ledger_posting_date.isoformat(),
                str(self.target_ledger_period_id),
                self.canonical_ledger_posting_date.isoformat(),
                str(self.canonical_ledger_period_id),
                self.reversal_date.isoformat(),
                str(self.reversal_fiscal_period_id),
                self.target_effect_hash,
                self.canonical_effect_hash,
            )
        )
        return _md5(material)


@dataclass(frozen=True)
class CorrectionPlan:
    raw_bytes: bytes
    rows: tuple[CorrectionPlanRow, ...]
    sha256: str
    schedule_digest: str

    @classmethod
    def from_bytes(
        cls,
        payload: bytes,
        *,
        approval: CorrectionApproval = APPROVED_CORRECTION,
    ) -> CorrectionPlan:
        digest = hashlib.sha256(payload).hexdigest()
        if digest != approval.plan_sha256:
            raise CorrectionRefused("plan SHA-256 does not match Finance approval")
        if not payload or not payload.endswith(b"\n") or b"\r" in payload:
            raise CorrectionRefused("plan must be canonical LF-terminated output")
        try:
            source = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise CorrectionRefused("plan is not UTF-8") from exc

        lines = source.splitlines()
        if any(not line for line in lines):
            raise CorrectionRefused("plan contains a blank row")
        rows = tuple(CorrectionPlanRow.from_fields(line.split("|")) for line in lines)
        cls._validate_rows(rows, approval=approval)
        schedule_digest = _md5(
            ",".join(row.schedule_row_hash for row in rows)
        )
        if schedule_digest != approval.schedule_digest:
            raise CorrectionRefused("plan schedule digest does not match approval")
        return cls(
            raw_bytes=payload,
            rows=rows,
            sha256=digest,
            schedule_digest=schedule_digest,
        )

    @staticmethod
    def _validate_rows(
        rows: tuple[CorrectionPlanRow, ...], *, approval: CorrectionApproval
    ) -> None:
        if len(rows) != approval.target_count:
            raise CorrectionRefused("plan target count does not match approval")
        target_ids = [row.target_journal_entry_id for row in rows]
        if len(set(target_ids)) != len(target_ids):
            raise CorrectionRefused("plan contains a duplicate target")
        if target_ids != sorted(target_ids):
            raise CorrectionRefused("plan targets are not in canonical order")
        batch_ids = [row.target_posting_batch_id for row in rows]
        if len(set(batch_ids)) != len(batch_ids):
            raise CorrectionRefused("plan contains a duplicate target batch")
        keys = [row.target_idempotency_key for row in rows]
        if len(set(keys)) != len(keys):
            raise CorrectionRefused("plan contains a duplicate historical key")
        if len({row.statement_line_id for row in rows}) != (
            approval.affected_statement_lines
        ):
            raise CorrectionRefused("plan affected-line count does not match approval")
        if sum((row.total_debit_functional for row in rows), Decimal()) != (
            approval.gross
        ):
            raise CorrectionRefused("plan gross does not match approval")
        if Counter(row.mapping_name for row in rows) != Counter(
            approval.mapping_counts
        ):
            raise CorrectionRefused("plan mapping cardinality does not match approval")
        if any(
            row.reversal_date != approval.reversal_date
            or row.reversal_fiscal_period_id
            != approval.reversal_fiscal_period_id
            for row in rows
        ):
            raise CorrectionRefused("plan reversal timing does not match approval")

    @property
    def target_count(self) -> int:
        return len(self.rows)

    @property
    def affected_statement_lines(self) -> int:
        return len({row.statement_line_id for row in self.rows})

    @property
    def gross(self) -> Decimal:
        return sum((row.total_debit_functional for row in self.rows), Decimal())


@dataclass(frozen=True)
class CorrectionResult:
    executed: bool
    targets: int
    affected_statement_lines: int
    gross: Decimal
    reversals: int


_EFFECT_HASH_SQL = text(
    """
    SELECT p.journal_entry_id,
           md5(jsonb_agg(
             jsonb_build_array(
               p.journal_line_id, p.account_id, p.account_code,
               p.debit_amount, p.credit_amount,
               p.original_currency_code,
               p.original_debit_amount, p.original_credit_amount,
               p.exchange_rate, p.business_unit_id, p.cost_center_id,
               p.project_id, p.segment_id, p.entry_date, p.posting_date,
               p.fiscal_period_id, p.source_module, p.source_document_type,
               p.source_document_id
             ) ORDER BY p.journal_line_id
           )::text) AS effect_hash
      FROM gl.posted_ledger_line p
     WHERE p.organization_id = :organization_id
       AND p.journal_entry_id IN :journal_ids
     GROUP BY p.journal_entry_id
    """
).bindparams(bindparam("journal_ids", expanding=True))


class BankFeeWrongAccountCorrectionService:
    """Validate and atomically prepare the approved linked reversals."""

    def __init__(self, db: Session):
        self.db = db

    def run(
        self,
        *,
        organization_id: UUID,
        created_by_user_id: UUID,
        expected_database: str,
        expected_server_address: str,
        plan: CorrectionPlan,
        execute: bool = False,
    ) -> CorrectionResult:
        self._require_approved_plan(plan)
        self._validate_current_state(
            organization_id=organization_id,
            expected_database=expected_database,
            expected_server_address=expected_server_address,
            plan=plan,
            lock_targets=execute,
        )
        if not execute:
            return CorrectionResult(
                executed=False,
                targets=plan.target_count,
                affected_statement_lines=plan.affected_statement_lines,
                gross=plan.gross,
                reversals=0,
            )

        for row in plan.rows:
            result = ReversalService.create_reversal(
                db=self.db,
                organization_id=organization_id,
                original_journal_id=row.target_journal_entry_id,
                reversal_date=row.reversal_date,
                created_by_user_id=created_by_user_id,
                reason=(
                    "Finance-approved Gate D bank-fee wrong-account correction; "
                    f"plan {plan.sha256}"
                ),
                auto_post=True,
                idempotency_key=(
                    f"{organization_id}:GL:{row.target_journal_entry_id}:"
                    "bank-fee-wrong-account:v1"
                ),
            )
            if not result.success:
                raise CorrectionRefused(
                    "linked reversal failed; the complete transaction is refused"
                )

        self._validate_postconditions(organization_id=organization_id, plan=plan)
        return CorrectionResult(
            executed=True,
            targets=plan.target_count,
            affected_statement_lines=plan.affected_statement_lines,
            gross=plan.gross,
            reversals=plan.target_count,
        )

    @staticmethod
    def _require_approved_plan(plan: CorrectionPlan) -> None:
        approval = APPROVED_CORRECTION
        if (
            plan.sha256 != approval.plan_sha256
            or plan.schedule_digest != approval.schedule_digest
            or plan.target_count != approval.target_count
            or plan.affected_statement_lines != approval.affected_statement_lines
            or plan.gross != approval.gross
            or Counter(row.mapping_name for row in plan.rows)
            != Counter(approval.mapping_counts)
            or any(
                row.reversal_date != approval.reversal_date
                or row.reversal_fiscal_period_id
                != approval.reversal_fiscal_period_id
                for row in plan.rows
            )
        ):
            raise CorrectionRefused("service received a non-approved correction plan")

    def _validate_current_state(
        self,
        *,
        organization_id: UUID,
        expected_database: str,
        expected_server_address: str,
        plan: CorrectionPlan,
        lock_targets: bool,
    ) -> None:
        self._validate_database_identity(
            expected_database=expected_database,
            expected_server_address=expected_server_address,
        )
        self._validate_reversal_period(organization_id=organization_id, plan=plan)
        journals = self._load_approved_journals(
            organization_id=organization_id,
            plan=plan,
            lock_targets=lock_targets,
        )
        self._validate_journal_headers_and_batches(
            organization_id=organization_id,
            plan=plan,
            journals=journals,
        )
        self._validate_complete_live_population(
            organization_id=organization_id,
            plan=plan,
        )
        self._validate_effect_hashes_and_line_parity(
            organization_id=organization_id,
            plan=plan,
        )
        self._validate_statement_resolution(
            organization_id=organization_id,
            plan=plan,
        )

    def _validate_database_identity(
        self, *, expected_database: str, expected_server_address: str
    ) -> None:
        if not expected_database or any(ch.isspace() for ch in expected_database):
            raise CorrectionRefused("expected database identity is invalid")
        try:
            expected_address = str(ip_address(expected_server_address))
        except ValueError as exc:
            raise CorrectionRefused("expected server address is invalid") from exc
        actual_database, actual_address = self.db.execute(
            text("SELECT current_database(), inet_server_addr()::text")
        ).one()
        if actual_database != expected_database or actual_address != expected_address:
            raise CorrectionRefused("database name or server address does not match")

    def _validate_reversal_period(
        self, *, organization_id: UUID, plan: CorrectionPlan
    ) -> None:
        period = self.db.scalar(
            select(FiscalPeriod).where(
                FiscalPeriod.organization_id == organization_id,
                FiscalPeriod.fiscal_period_id
                == APPROVED_CORRECTION.reversal_fiscal_period_id,
            )
        )
        if (
            period is None
            or period.status != PeriodStatus.OPEN
            or period.is_adjustment_period
            or period.is_closing_period
            or not (
                period.start_date
                <= APPROVED_CORRECTION.reversal_date
                <= period.end_date
            )
            or any(
                row.reversal_fiscal_period_id != period.fiscal_period_id
                for row in plan.rows
            )
        ):
            raise CorrectionRefused("approved reversal period is not ordinary and OPEN")

    def _load_approved_journals(
        self,
        *,
        organization_id: UUID,
        plan: CorrectionPlan,
        lock_targets: bool,
    ) -> dict[UUID, JournalEntry]:
        journal_ids = {
            journal_id
            for row in plan.rows
            for journal_id in (
                row.target_journal_entry_id,
                row.canonical_journal_entry_id,
            )
        }
        statement = (
            select(JournalEntry)
            .where(
                JournalEntry.organization_id == organization_id,
                JournalEntry.journal_entry_id.in_(journal_ids),
            )
            .order_by(JournalEntry.journal_entry_id)
        )
        if lock_targets:
            statement = statement.with_for_update()
        journals = list(self.db.scalars(statement).all())
        if len(journals) != len(journal_ids):
            raise CorrectionRefused("an approved target or canonical journal is missing")
        return {journal.journal_entry_id: journal for journal in journals}

    @staticmethod
    def _header_signature(journal: JournalEntry) -> tuple[Any, ...]:
        return (
            journal.entry_date,
            journal.posting_date,
            journal.fiscal_period_id,
            journal.currency_code,
            journal.exchange_rate,
            journal.total_debit,
            journal.total_credit,
            journal.total_debit_functional,
            journal.total_credit_functional,
            journal.source_module,
            journal.source_document_type,
            journal.source_document_id,
            journal.correlation_id,
        )

    def _validate_journal_headers_and_batches(
        self,
        *,
        organization_id: UUID,
        plan: CorrectionPlan,
        journals: dict[UUID, JournalEntry],
    ) -> None:
        raw_batch_ids = [journal.posting_batch_id for journal in journals.values()]
        if any(batch_id is None for batch_id in raw_batch_ids):
            raise CorrectionRefused("an approved journal lost its posting batch")
        batch_ids = {batch_id for batch_id in raw_batch_ids if batch_id is not None}
        batches = list(
            self.db.scalars(
                select(PostingBatch).where(
                    PostingBatch.organization_id == organization_id,
                    PostingBatch.batch_id.in_(batch_ids),
                )
            ).all()
        )
        batch_by_id = {batch.batch_id: batch for batch in batches}
        if len(batch_by_id) != len(batch_ids):
            raise CorrectionRefused("an approved posting batch is missing")

        for row in plan.rows:
            target = journals[row.target_journal_entry_id]
            canonical = journals[row.canonical_journal_entry_id]
            if (
                target.journal_number != row.target_journal_number
                or target.posting_batch_id != row.target_posting_batch_id
                or target.status != JournalStatus.POSTED
                or target.is_reversal
                or target.reversal_journal_id is not None
                or target.posting_date != row.target_ledger_posting_date
                or target.fiscal_period_id != row.target_ledger_period_id
                or target.total_debit_functional != row.total_debit_functional
                or target.total_credit_functional != row.total_debit_functional
            ):
                raise CorrectionRefused("an approved target journal header drifted")
            if (
                canonical.journal_number != row.canonical_journal_number
                or canonical.status != JournalStatus.POSTED
                or canonical.is_reversal
                or canonical.reversal_journal_id is not None
                or self._header_signature(target) != self._header_signature(canonical)
            ):
                raise CorrectionRefused("an approved canonical journal header drifted")
            expected_correlation = f"bank-fee-{row.statement_line_id}"
            if (
                target.source_document_type != "BANK_FEE"
                or target.correlation_id != expected_correlation
                or canonical.correlation_id != expected_correlation
            ):
                raise CorrectionRefused("an approved bank-fee source identity drifted")

            target_batch_id = target.posting_batch_id
            canonical_batch_id = canonical.posting_batch_id
            if target_batch_id is None or canonical_batch_id is None:
                raise CorrectionRefused("an approved journal lost its posting batch")
            target_batch = batch_by_id[target_batch_id]
            canonical_batch = batch_by_id[canonical_batch_id]
            expected_canonical_key = (
                f"{organization_id}:BANKING:{row.statement_line_id}:bank-fee:v1"
            )
            if (
                target_batch.status != BatchStatus.POSTED
                or target_batch.idempotency_key != row.target_idempotency_key
                or canonical_batch.status != BatchStatus.POSTED
                or canonical_batch.idempotency_key != expected_canonical_key
            ):
                raise CorrectionRefused("an approved posting idempotency key drifted")

    def _validate_complete_live_population(
        self, *, organization_id: UUID, plan: CorrectionPlan
    ) -> None:
        rows = self.db.execute(
            text(
                """
                SELECT je.journal_entry_id, je.journal_number, je.correlation_id,
                       je.is_reversal, je.reversal_journal_id,
                       pb.idempotency_key,
                       EXISTS (
                         SELECT 1 FROM gl.posted_ledger_line pll
                          WHERE pll.organization_id = je.organization_id
                            AND pll.journal_entry_id = je.journal_entry_id
                       ) AS has_ledger
                  FROM gl.journal_entry je
                  LEFT JOIN gl.posting_batch pb
                    ON pb.organization_id = je.organization_id
                   AND pb.batch_id = je.posting_batch_id
                 WHERE je.organization_id = :organization_id
                   AND je.source_document_type = 'BANK_FEE'
                   AND je.status = 'POSTED'
                """
            ),
            {"organization_id": organization_id},
        ).all()
        plan_targets = {row.target_journal_entry_id for row in plan.rows}
        canonical_by_line = {
            row.statement_line_id: row.canonical_journal_entry_id for row in plan.rows
        }
        live_targets: set[UUID] = set()
        live_canonicals: defaultdict[UUID, list[UUID]] = defaultdict(list)
        unknown = 0
        for (
            journal_id,
            journal_number,
            correlation_id,
            is_reversal,
            reversal_journal_id,
            idempotency_key,
            has_ledger,
        ) in rows:
            if is_reversal:
                continue
            match = _BANK_FEE_CORRELATION.fullmatch(correlation_id or "")
            statement_line_id = UUID(match.group("line_id")) if match else None
            line_key = (
                f"{organization_id}:BANKING:{statement_line_id}:bank-fee:v1"
                if statement_line_id is not None
                else None
            )
            journal_key = f"backfill-stranded-bank-fees-{journal_number}"
            if idempotency_key == line_key and statement_line_id is not None:
                if reversal_journal_id is None and has_ledger:
                    live_canonicals[statement_line_id].append(journal_id)
            elif idempotency_key == journal_key:
                if reversal_journal_id is None and has_ledger:
                    live_targets.add(journal_id)
            else:
                unknown += 1
        if unknown:
            raise CorrectionRefused("the live bank-fee namespace contains unknown keys")
        if live_targets != plan_targets:
            raise CorrectionRefused("the complete live target population changed")
        if any(
            live_canonicals.get(line_id) != [canonical_id]
            for line_id, canonical_id in canonical_by_line.items()
        ):
            raise CorrectionRefused("the one-canonical-per-line population changed")

    def _validate_effect_hashes_and_line_parity(
        self, *, organization_id: UUID, plan: CorrectionPlan
    ) -> None:
        expected_hashes: dict[UUID, str] = {}
        for row in plan.rows:
            expected_hashes[row.target_journal_entry_id] = row.target_effect_hash
            existing = expected_hashes.setdefault(
                row.canonical_journal_entry_id, row.canonical_effect_hash
            )
            if existing != row.canonical_effect_hash:
                raise CorrectionRefused("canonical effect hashes disagree in the plan")
        effect_hashes = dict(
            self.db.execute(
                _EFFECT_HASH_SQL,
                {
                    "organization_id": organization_id,
                    "journal_ids": sorted(expected_hashes),
                },
            ).all()
        )
        if effect_hashes != expected_hashes:
            raise CorrectionRefused("an immutable target or canonical effect drifted")

        target_ids = [row.target_journal_entry_id for row in plan.rows]
        journal_lines = list(
            self.db.scalars(
                select(JournalEntryLine)
                .join(
                    JournalEntry,
                    JournalEntry.journal_entry_id
                    == JournalEntryLine.journal_entry_id,
                )
                .where(
                    JournalEntry.organization_id == organization_id,
                    JournalEntryLine.journal_entry_id.in_(target_ids),
                )
            ).all()
        )
        ledger_lines = list(
            self.db.scalars(
                select(PostedLedgerLine).where(
                    PostedLedgerLine.organization_id == organization_id,
                    PostedLedgerLine.journal_entry_id.in_(target_ids),
                )
            ).all()
        )
        source_by_id = {line.line_id: line for line in journal_lines}
        if len(source_by_id) != len(journal_lines) or len(journal_lines) != len(
            ledger_lines
        ):
            raise CorrectionRefused("journal-line to immutable-ledger cardinality drifted")
        account_codes: defaultdict[UUID, list[str]] = defaultdict(list)
        for ledger in ledger_lines:
            source = source_by_id.get(ledger.journal_line_id)
            if source is None or not self._journal_line_matches_ledger(source, ledger):
                raise CorrectionRefused("journal lines no longer reproduce target effects")
            account_codes[ledger.journal_entry_id].append(ledger.account_code)
        expected_legacy = {
            row.target_journal_entry_id: row.legacy_bank_code for row in plan.rows
        }
        if any(
            sorted(account_codes[target_id]) != sorted(("6080", legacy_code))
            for target_id, legacy_code in expected_legacy.items()
        ):
            raise CorrectionRefused("a target account substitution drifted")

    @staticmethod
    def _journal_line_matches_ledger(
        source: JournalEntryLine, ledger: PostedLedgerLine
    ) -> bool:
        return (
            source.journal_entry_id == ledger.journal_entry_id
            and source.account_id == ledger.account_id
            and source.debit_amount_functional == ledger.debit_amount
            and source.credit_amount_functional == ledger.credit_amount
            and source.debit_amount == ledger.original_debit_amount
            and source.credit_amount == ledger.original_credit_amount
            and source.currency_code == ledger.original_currency_code
            and source.exchange_rate == ledger.exchange_rate
            and source.business_unit_id == ledger.business_unit_id
            and source.cost_center_id == ledger.cost_center_id
            and source.project_id == ledger.project_id
            and source.segment_id == ledger.segment_id
        )

    def _validate_statement_resolution(
        self, *, organization_id: UUID, plan: CorrectionPlan
    ) -> None:
        line_ids = sorted({row.statement_line_id for row in plan.rows})
        statement = text(
            """
            SELECT sl.line_id
              FROM banking.bank_statement_lines sl
              JOIN banking.bank_statements bs
                ON bs.statement_id = sl.statement_id
               AND bs.organization_id = :organization_id
             WHERE sl.line_id IN :line_ids
            """
        ).bindparams(bindparam("line_ids", expanding=True))
        resolved = set(
            self.db.scalars(
                statement,
                {"organization_id": organization_id, "line_ids": line_ids},
            ).all()
        )
        if any(
            (row.statement_line_id in resolved) is not row.statement_line_resolves
            for row in plan.rows
        ):
            raise CorrectionRefused("bank-statement source resolution state drifted")

    def _validate_postconditions(
        self, *, organization_id: UUID, plan: CorrectionPlan
    ) -> None:
        self.db.flush()
        target_ids = [row.target_journal_entry_id for row in plan.rows]
        targets = list(
            self.db.scalars(
                select(JournalEntry).where(
                    JournalEntry.organization_id == organization_id,
                    JournalEntry.journal_entry_id.in_(target_ids),
                )
            ).all()
        )
        reversals = list(
            self.db.scalars(
                select(JournalEntry).where(
                    JournalEntry.organization_id == organization_id,
                    JournalEntry.reversed_journal_id.in_(target_ids),
                )
            ).all()
        )
        target_by_id = {journal.journal_entry_id: journal for journal in targets}
        reversal_by_target: dict[UUID, JournalEntry] = {}
        for journal in reversals:
            if journal.reversed_journal_id is None:
                raise CorrectionRefused("a reversal lost its target link")
            reversal_by_target[journal.reversed_journal_id] = journal
        if (
            len(target_by_id) != plan.target_count
            or len(reversal_by_target) != plan.target_count
            or len(reversals) != plan.target_count
        ):
            raise CorrectionRefused("linked reversal postcondition cardinality failed")
        for row in plan.rows:
            target = target_by_id[row.target_journal_entry_id]
            reversal = reversal_by_target.get(row.target_journal_entry_id)
            if (
                reversal is None
                or target.status != JournalStatus.REVERSED
                or target.reversal_journal_id != reversal.journal_entry_id
                or reversal.status != JournalStatus.POSTED
                or not reversal.is_reversal
                or reversal.posting_date != row.reversal_date
                or reversal.fiscal_period_id != row.reversal_fiscal_period_id
            ):
                raise CorrectionRefused("a linked reversal postcondition failed")

        reversal_ids = [journal.journal_entry_id for journal in reversals]
        ledger_lines = list(
            self.db.scalars(
                select(PostedLedgerLine).where(
                    PostedLedgerLine.organization_id == organization_id,
                    PostedLedgerLine.journal_entry_id.in_(target_ids + reversal_ids),
                )
            ).all()
        )
        reversal_target = {
            reversal.journal_entry_id: target_id
            for target_id, reversal in reversal_by_target.items()
        }
        net: defaultdict[tuple[UUID, UUID], Decimal] = defaultdict(Decimal)
        row_counts: Counter[UUID] = Counter()
        for line in ledger_lines:
            target_id = reversal_target.get(line.journal_entry_id, line.journal_entry_id)
            net[(target_id, line.account_id)] += line.debit_amount - line.credit_amount
            row_counts[line.journal_entry_id] += 1
        if any(value != 0 for value in net.values()):
            raise CorrectionRefused("a target/account net is nonzero after reversal")
        if any(
            row_counts[target_id]
            != row_counts[reversal_by_target[target_id].journal_entry_id]
            for target_id in target_ids
        ):
            raise CorrectionRefused("a reversal ledger row count differs from its target")

        canonical_ids = {row.canonical_journal_entry_id for row in plan.rows}
        retained = self.db.scalar(
            select(func.count())
            .select_from(JournalEntry)
            .where(
                JournalEntry.organization_id == organization_id,
                JournalEntry.journal_entry_id.in_(canonical_ids),
                JournalEntry.status == JournalStatus.POSTED,
                JournalEntry.reversal_journal_id.is_(None),
            )
            .count()
        )
        if retained != len(canonical_ids):
            raise CorrectionRefused("a retained canonical journal changed during correction")
