"""
FX Revaluation Service.

Period-end revaluation of foreign-currency monetary items (AR open
invoices, AP open invoices, bank account balances) at the closing spot
rate, with auto-reversing journal posting on day 1 of the next period.

See docs/superpowers/specs/2026-05-09-fx-revaluation-design.md for the
contract and accounting rationale.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.domain_settings import DomainSetting, SettingDomain

logger = logging.getLogger(__name__)


@dataclass
class FXRevaluationLine:
    """One revaluation observation: a single (control_account, currency)
    pair's delta. The proposed journal is constructed from these — the
    asset/liability side becomes one journal line per FXRevaluationLine,
    while the gain/loss side aggregates across all observations into two
    summary lines."""

    account_id: UUID
    currency_code: str
    closing_rate: Decimal
    book_value_functional: Decimal       # current carrying amount in NGN
    revalued_value_functional: Decimal   # value at closing rate, in NGN
    delta_functional: Decimal            # revalued - book; signed
    is_gain: bool                        # True iff delta increases asset / decreases liability


@dataclass
class FXRevaluationPreview:
    """Output of FXRevaluationService.preview() — no DB writes."""

    fiscal_period_id: UUID
    period_end_date: date
    next_period_start_date: date | None
    lines: list[FXRevaluationLine] = field(default_factory=list)
    total_gain_functional: Decimal = Decimal("0")
    total_loss_functional: Decimal = Decimal("0")
    rates_used: dict[str, Decimal] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    prior_run_exists: bool = False
    prior_journal_ids: list[UUID] = field(default_factory=list)


@dataclass
class FXRevaluationResult:
    """Output of FXRevaluationService.post() — journals have been written."""

    success: bool
    period_end_journal_id: UUID | None = None
    reversal_journal_id: UUID | None = None
    reversed_prior_journal_ids: list[UUID] = field(default_factory=list)
    total_gain_functional: Decimal = Decimal("0")
    total_loss_functional: Decimal = Decimal("0")
    message: str = ""
    errors: list[str] = field(default_factory=list)


class FXRevaluationService:
    """Period-end FX revaluation for AR / AP / cash monetary items."""

    SOURCE_MODULE = "FXR"

    def __init__(self, db: Session) -> None:
        self.db = db

    def _read_fx_account_ids(self, organization_id: UUID) -> tuple[UUID, UUID]:
        """Read fx_gain_account_id and fx_loss_account_id from DomainSetting.

        Queries the org-specific DomainSetting row directly, filtered by
        organization_id. FX gain/loss accounts post real money to the GL,
        so this is security-critical: an unset org-specific row must mean
        "unconfigured" — we DO NOT fall back to a global row, otherwise
        every tenant would silently share the same accounts.

        Raises HTTPException(400) with admin-actionable detail when either
        is unset — refuse to post to a wrong account silently.
        """
        gain_setting = self.db.scalar(
            select(DomainSetting).where(
                DomainSetting.domain == SettingDomain.gl,
                DomainSetting.key == "fx_gain_account_id",
                DomainSetting.organization_id == organization_id,
                DomainSetting.is_active.is_(True),
            )
        )
        loss_setting = self.db.scalar(
            select(DomainSetting).where(
                DomainSetting.domain == SettingDomain.gl,
                DomainSetting.key == "fx_loss_account_id",
                DomainSetting.organization_id == organization_id,
                DomainSetting.is_active.is_(True),
            )
        )

        gain_raw = gain_setting.value_text if gain_setting is not None else None
        loss_raw = loss_setting.value_text if loss_setting is not None else None

        if not gain_raw:
            raise HTTPException(
                status_code=400,
                detail=(
                    "FX revaluation is not configured: fx_gain_account_id "
                    "is unset. Visit /admin/settings/gl/fx and set the "
                    "Foreign Exchange Gain account."
                ),
            )
        if not loss_raw:
            raise HTTPException(
                status_code=400,
                detail=(
                    "FX revaluation is not configured: fx_loss_account_id "
                    "is unset. Visit /admin/settings/gl/fx and set the "
                    "Foreign Exchange Loss account."
                ),
            )

        return UUID(gain_raw), UUID(loss_raw)
