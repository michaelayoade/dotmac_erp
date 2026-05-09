# FX Revaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement period-end FX revaluation for AR invoices, AP invoices, and bank-account balances in non-functional currencies, with admin-triggered preview-then-confirm UX and atomic auto-reversing journal posting.

**Architecture:** A new `FXRevaluationService` (single class with private helpers) reads per-org `DomainSetting` for the FX gain/loss accounts, discovers foreign-currency monetary items, computes deltas at the closing spot rate via existing `FXService.lookup_spot_rate`, and posts a pair of revaluation journals (period-end + day-1-of-next-period reversal) atomically through the existing `JournalService.create_journal` / `post_journal`. Re-runs use `ReversalService.create_reversal` to undo the prior pair before posting fresh. A thin `FXRevaluationWebService` and two routes on the period detail page expose preview-then-confirm UX.

**Tech Stack:** Python 3.12 / FastAPI / SQLAlchemy 2.0 / Pydantic dataclasses / Jinja2 templates / Alembic migrations / pytest with `unittest.mock`. All money math in `Decimal`. Tests use `MagicMock()` for db session and patch `FXService` / `JournalService` / `ReversalService` at the canonical class location.

**Spec reference:** `docs/superpowers/specs/2026-05-09-fx-revaluation-design.md`

---

## File Structure

```
app/services/finance/gl/
  fx_revaluation.py                              ← FXRevaluationService (new)

app/services/finance/gl/web/
  fx_revaluation_web.py                          ← FXRevaluationWebService (new)

app/web/finance/gl.py                            ← + 2 routes on existing router
templates/finance/gl/
  period_fx_revaluation.html                     ← preview + confirm form (new)
  period_detail.html                             ← + "Run FX Revaluation" button

app/models/domain_settings.py                    ← + SettingDomain.gl
app/services/settings_spec.py                    ← + 2 SettingSpec entries

alembic/versions/
  YYYYMMDD_add_gl_setting_domain.py              ← enum extension migration

tests/ifrs/gl/
  test_fx_revaluation_service.py                 ← service-level TDD
  test_fx_revaluation_web_service.py             ← web-service-level
```

---

## Task 1: Extend `SettingDomain` enum with `gl` value

**Files:**
- Modify: `app/models/domain_settings.py:35-58`
- Create: `alembic/versions/YYYYMMDD_HHMM_add_gl_setting_domain.py`
- Test: `tests/ifrs/gl/test_fx_revaluation_service.py` (new file, first test)

- [ ] **Step 1: Add `gl` value to `SettingDomain` enum**

In `app/models/domain_settings.py`, locate `class SettingDomain(enum.Enum):` (around line 35) and add `gl = "gl"` to the enum members. Order doesn't matter; place it near the other finance-adjacent values:

```python
class SettingDomain(enum.Enum):
    auth = "auth"
    audit = "audit"
    scheduler = "scheduler"
    automation = "automation"
    email = "email"
    features = "features"
    reporting = "reporting"
    payments = "payments"
    operations = "operations"
    support = "support"
    inventory = "inventory"
    projects = "projects"
    fleet = "fleet"
    procurement = "procurement"
    settings = "settings"
    payroll = "payroll"
    banking = "banking"
    coach = "coach"
    notifications = "notifications"
    expense = "expense"
    gl = "gl"   # ← new: FX gain/loss accounts and other GL-level config
```

- [ ] **Step 2: Generate the Alembic migration to add the enum value to the DB**

The codebase uses Alembic. Run:

```bash
poetry run alembic revision -m "add_gl_setting_domain"
```

This creates a stub at `alembic/versions/<id>_add_gl_setting_domain.py`. Edit the `upgrade()` and `downgrade()` functions:

```python
def upgrade() -> None:
    # PostgreSQL enum: must use ALTER TYPE ... ADD VALUE
    op.execute("ALTER TYPE settingdomain ADD VALUE IF NOT EXISTS 'gl'")


def downgrade() -> None:
    # Postgres does not support removing enum values cleanly.
    # Document the no-op rather than attempting a fragile rewrite.
    pass
```

Verify the existing migration history exposes the type name `settingdomain` (lowercase). If your installation uses a quoted/cased name, adjust accordingly — `\dT+` in psql confirms.

- [ ] **Step 3: Apply the migration locally**

```bash
poetry run alembic upgrade head
```

Expected: clean apply, no errors. The enum now accepts the `gl` value.

- [ ] **Step 4: Commit**

```bash
git add app/models/domain_settings.py alembic/versions/<filename>
git commit -m "Add gl SettingDomain for GL-level config"
```

---

## Task 2: Register settings specs for `fx_gain_account_id` and `fx_loss_account_id`

**Files:**
- Modify: `app/services/settings_spec.py` (add two `SettingSpec` entries near other domain specs)

The existing `settings_spec.py` defines `SettingSpec` rows that the `resolve_value(...)` accessor uses to look up DomainSetting values. We register two specs under the new `gl` domain so the FX revaluation service can read them via the canonical accessor.

- [ ] **Step 1: Find the registration point**

Search for an existing spec registration to see the pattern:

```bash
grep -n "SettingSpec(\|SettingDomain\." app/services/settings_spec.py | head -20
```

You'll see entries shaped like:

```python
SettingSpec(
    domain=SettingDomain.banking,
    key="mono_secret_key",
    setting_type=SettingType.string,
    default_value="",
    description="Mono Connect secret key",
)
```

- [ ] **Step 2: Add the two FX-account specs**

In `app/services/settings_spec.py`, alongside the other domain registrations (typically at module load level — find the existing list and append, or whatever existing pattern the file uses):

```python
SettingSpec(
    domain=SettingDomain.gl,
    key="fx_gain_account_id",
    setting_type=SettingType.string,   # UUID stored as string
    default_value="",
    description=(
        "GL account that receives credit-side FX gains during period-end "
        "revaluation. Must be set per organization before FX revaluation "
        "can run."
    ),
    scope=SettingScope.ORG_SPECIFIC,
),
SettingSpec(
    domain=SettingDomain.gl,
    key="fx_loss_account_id",
    setting_type=SettingType.string,
    default_value="",
    description=(
        "GL account that receives debit-side FX losses during period-end "
        "revaluation. Must be set per organization before FX revaluation "
        "can run."
    ),
    scope=SettingScope.ORG_SPECIFIC,
),
```

If `SettingScope` isn't imported in this file, add it to the existing imports from `app.models.domain_settings`.

- [ ] **Step 3: Verify the specs load without error**

```bash
poetry run python -c "from app.services.settings_spec import get_spec; from app.models.domain_settings import SettingDomain; print(get_spec(SettingDomain.gl, 'fx_gain_account_id')); print(get_spec(SettingDomain.gl, 'fx_loss_account_id'))"
```

Expected: prints two `SettingSpec` objects, no `None`.

- [ ] **Step 4: Commit**

```bash
git add app/services/settings_spec.py
git commit -m "Register fx_gain_account_id and fx_loss_account_id specs"
```

---

## Task 3: Service skeleton + dataclasses

**Files:**
- Create: `app/services/finance/gl/fx_revaluation.py`
- Test: `tests/ifrs/gl/test_fx_revaluation_service.py`

- [ ] **Step 1: Write the failing import test**

Create `tests/ifrs/gl/test_fx_revaluation_service.py`:

```python
"""Tests for FXRevaluationService (period-end FX revaluation)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest


def test_module_exposes_service_and_dataclasses():
    """The new module must export FXRevaluationService plus the three
    dataclasses the web service depends on."""
    from app.services.finance.gl.fx_revaluation import (
        FXRevaluationLine,
        FXRevaluationPreview,
        FXRevaluationResult,
        FXRevaluationService,
    )

    assert FXRevaluationLine is not None
    assert FXRevaluationPreview is not None
    assert FXRevaluationResult is not None
    assert FXRevaluationService is not None
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
poetry run pytest tests/ifrs/gl/test_fx_revaluation_service.py::test_module_exposes_service_and_dataclasses -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.finance.gl.fx_revaluation'`.

- [ ] **Step 3: Create the module skeleton**

Create `app/services/finance/gl/fx_revaluation.py`:

```python
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

from sqlalchemy.orm import Session

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
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
poetry run pytest tests/ifrs/gl/test_fx_revaluation_service.py::test_module_exposes_service_and_dataclasses -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/finance/gl/fx_revaluation.py tests/ifrs/gl/test_fx_revaluation_service.py
git commit -m "Scaffold FXRevaluationService and dataclasses"
```

---

## Task 4: `_read_fx_account_ids()` — refuse when unconfigured

**Files:**
- Modify: `app/services/finance/gl/fx_revaluation.py`
- Modify: `tests/ifrs/gl/test_fx_revaluation_service.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/ifrs/gl/test_fx_revaluation_service.py`:

```python
class TestReadFxAccountIds:
    """Hard-fail when fx_gain_account_id or fx_loss_account_id is unset."""

    def test_raises_400_when_gain_account_unset(self):
        from fastapi import HTTPException

        from app.services.finance.gl.fx_revaluation import FXRevaluationService

        db = MagicMock()
        svc = FXRevaluationService(db)

        with patch(
            "app.services.finance.gl.fx_revaluation.resolve_value",
            side_effect=lambda _db, _domain, key, **kw: (
                "" if key == "fx_gain_account_id" else str(uuid4())
            ),
        ):
            with pytest.raises(HTTPException) as exc:
                svc._read_fx_account_ids(uuid4())

        assert exc.value.status_code == 400
        assert "fx_gain_account_id" in exc.value.detail.lower()

    def test_raises_400_when_loss_account_unset(self):
        from fastapi import HTTPException

        from app.services.finance.gl.fx_revaluation import FXRevaluationService

        db = MagicMock()
        svc = FXRevaluationService(db)

        with patch(
            "app.services.finance.gl.fx_revaluation.resolve_value",
            side_effect=lambda _db, _domain, key, **kw: (
                str(uuid4()) if key == "fx_gain_account_id" else ""
            ),
        ):
            with pytest.raises(HTTPException) as exc:
                svc._read_fx_account_ids(uuid4())

        assert exc.value.status_code == 400
        assert "fx_loss_account_id" in exc.value.detail.lower()

    def test_returns_uuid_pair_when_both_set(self):
        from app.services.finance.gl.fx_revaluation import FXRevaluationService

        gain_id = uuid4()
        loss_id = uuid4()
        db = MagicMock()
        svc = FXRevaluationService(db)

        with patch(
            "app.services.finance.gl.fx_revaluation.resolve_value",
            side_effect=lambda _db, _domain, key, **kw: (
                str(gain_id) if key == "fx_gain_account_id" else str(loss_id)
            ),
        ):
            result_gain, result_loss = svc._read_fx_account_ids(uuid4())

        assert result_gain == gain_id
        assert result_loss == loss_id
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
poetry run pytest tests/ifrs/gl/test_fx_revaluation_service.py::TestReadFxAccountIds -v
```

Expected: 3 failures with `AttributeError: 'FXRevaluationService' object has no attribute '_read_fx_account_ids'`.

- [ ] **Step 3: Implement `_read_fx_account_ids`**

In `app/services/finance/gl/fx_revaluation.py`, add the import and method:

```python
# Add to top-of-file imports:
from fastapi import HTTPException

from app.models.domain_settings import SettingDomain
from app.services.settings_spec import resolve_value


# Add as a method on FXRevaluationService:
    def _read_fx_account_ids(self, organization_id: UUID) -> tuple[UUID, UUID]:
        """Read fx_gain_account_id and fx_loss_account_id from DomainSetting.

        Raises HTTPException(400) with admin-actionable detail when either
        is unset — refuse to post to a wrong account silently.
        """
        gain_raw = resolve_value(
            self.db, SettingDomain.gl, "fx_gain_account_id"
        )
        loss_raw = resolve_value(
            self.db, SettingDomain.gl, "fx_loss_account_id"
        )

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

        return UUID(str(gain_raw)), UUID(str(loss_raw))
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
poetry run pytest tests/ifrs/gl/test_fx_revaluation_service.py::TestReadFxAccountIds -v
```

Expected: 3 passes.

- [ ] **Step 5: Commit**

```bash
git add app/services/finance/gl/fx_revaluation.py tests/ifrs/gl/test_fx_revaluation_service.py
git commit -m "FXRevaluationService._read_fx_account_ids with hard-fail on unset"
```

---

## Task 5: `_discover_ar_open_invoices(period_end_date, organization_id)`

**Files:**
- Modify: `app/services/finance/gl/fx_revaluation.py`
- Modify: `tests/ifrs/gl/test_fx_revaluation_service.py`

Discovers AR invoices in non-functional currency with `balance_due > 0` as-of `period_end_date`. Returns a list of tuples: `(invoice_id, control_account_id, currency_code, exchange_rate_at_posting, balance_due_currency)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/ifrs/gl/test_fx_revaluation_service.py`:

```python
class TestDiscoverArOpenInvoices:
    """Returns AR invoices in non-functional currency with positive
    balance_due as-of period_end_date."""

    def _make_invoice(self, **overrides):
        from types import SimpleNamespace

        defaults = {
            "invoice_id": uuid4(),
            "ar_control_account_id": uuid4(),
            "currency_code": "USD",
            "exchange_rate": Decimal("750.0"),
            "total_amount": Decimal("1000"),
            "amount_paid": Decimal("400"),
            "status": MagicMock(value="OPEN"),
        }
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    def test_excludes_functional_currency_invoices(self):
        from app.services.finance.gl.fx_revaluation import FXRevaluationService

        db = MagicMock()
        svc = FXRevaluationService(db)

        ngn_invoice = self._make_invoice(currency_code="NGN")
        db.scalars.return_value.all.return_value = [ngn_invoice]

        result = svc._discover_ar_open_invoices(
            organization_id=uuid4(),
            period_end_date=date(2026, 1, 31),
        )

        # The query itself filters out NGN, but our test confirms the
        # service does not expose functional-currency invoices in its
        # return shape even if the query mock yields them.
        assert all(item[2] != "NGN" for item in result)

    def test_returns_balance_due_in_invoice_currency(self):
        """balance_due is total_amount - amount_paid (the unpaid USD)."""
        from app.services.finance.gl.fx_revaluation import FXRevaluationService

        db = MagicMock()
        svc = FXRevaluationService(db)

        invoice = self._make_invoice(
            total_amount=Decimal("1000"),
            amount_paid=Decimal("400"),
        )
        db.scalars.return_value.all.return_value = [invoice]

        result = svc._discover_ar_open_invoices(
            organization_id=uuid4(),
            period_end_date=date(2026, 1, 31),
        )

        assert len(result) == 1
        invoice_id, control_account_id, currency, posting_rate, balance_due = result[0]
        assert invoice_id == invoice.invoice_id
        assert control_account_id == invoice.ar_control_account_id
        assert currency == "USD"
        assert posting_rate == Decimal("750.0")
        assert balance_due == Decimal("600")  # 1000 - 400

    def test_skips_fully_paid_invoices(self):
        """balance_due == 0 → not in scope for revaluation."""
        from app.services.finance.gl.fx_revaluation import FXRevaluationService

        db = MagicMock()
        svc = FXRevaluationService(db)

        paid = self._make_invoice(
            total_amount=Decimal("1000"),
            amount_paid=Decimal("1000"),
        )
        db.scalars.return_value.all.return_value = [paid]

        result = svc._discover_ar_open_invoices(
            organization_id=uuid4(),
            period_end_date=date(2026, 1, 31),
        )

        assert result == []
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
poetry run pytest tests/ifrs/gl/test_fx_revaluation_service.py::TestDiscoverArOpenInvoices -v
```

Expected: 3 failures (`AttributeError: ... no attribute '_discover_ar_open_invoices'`).

- [ ] **Step 3: Implement `_discover_ar_open_invoices`**

Add to `app/services/finance/gl/fx_revaluation.py`:

```python
# Add to top-of-file imports:
from sqlalchemy import select
from app.config import settings as app_settings
from app.models.finance.ar.invoice import Invoice, InvoiceStatus


# Add as a method on FXRevaluationService:
    def _discover_ar_open_invoices(
        self,
        organization_id: UUID,
        period_end_date: date,
    ) -> list[tuple[UUID, UUID, str, Decimal, Decimal]]:
        """List AR invoices in non-functional currency with balance_due > 0.

        Returns tuples of:
          (invoice_id, ar_control_account_id, currency_code,
           posting_exchange_rate, balance_due_in_invoice_currency)

        Note on the "as-of period_end_date" semantics: the invoice's
        amount_paid is read at the current point in time, which is
        accurate for typical close workflows where revaluation runs
        within the period. Strict as-of-period-end accuracy for
        invoices that received late allocations would require walking
        the AR/AP allocation table for postings dated <= period_end_date;
        that's a known limitation deferred to a follow-up if real-world
        usage shows tenants running revaluation well after period close.
        """
        functional = app_settings.default_functional_currency_code

        stmt = (
            select(Invoice)
            .where(
                Invoice.organization_id == organization_id,
                Invoice.currency_code != functional,
                Invoice.status.in_(InvoiceStatus.outstanding()),
            )
        )
        invoices = self.db.scalars(stmt).all()

        result: list[tuple[UUID, UUID, str, Decimal, Decimal]] = []
        for inv in invoices:
            balance_due = (inv.total_amount or Decimal("0")) - (
                inv.amount_paid or Decimal("0")
            )
            if balance_due <= 0:
                continue
            if inv.currency_code == functional:
                # Defense in depth — query already filters this
                continue
            result.append(
                (
                    inv.invoice_id,
                    inv.ar_control_account_id,
                    inv.currency_code,
                    inv.exchange_rate or Decimal("1"),
                    balance_due,
                )
            )
        return result
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
poetry run pytest tests/ifrs/gl/test_fx_revaluation_service.py::TestDiscoverArOpenInvoices -v
```

Expected: 3 passes.

- [ ] **Step 5: Commit**

```bash
git add app/services/finance/gl/fx_revaluation.py tests/ifrs/gl/test_fx_revaluation_service.py
git commit -m "FXRevaluationService._discover_ar_open_invoices"
```

---

## Task 6: `_discover_ap_open_invoices(period_end_date, organization_id)`

**Files:**
- Modify: `app/services/finance/gl/fx_revaluation.py`
- Modify: `tests/ifrs/gl/test_fx_revaluation_service.py`

Mirrors Task 5 against `SupplierInvoice` / `ap_control_account_id`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/ifrs/gl/test_fx_revaluation_service.py`:

```python
class TestDiscoverApOpenInvoices:
    """Same shape as AR but reads SupplierInvoice + ap_control_account_id."""

    def _make_supplier_invoice(self, **overrides):
        from types import SimpleNamespace

        defaults = {
            "invoice_id": uuid4(),
            "ap_control_account_id": uuid4(),
            "currency_code": "USD",
            "exchange_rate": Decimal("750.0"),
            "total_amount": Decimal("2000"),
            "amount_paid": Decimal("500"),
            "status": MagicMock(value="OPEN"),
        }
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    def test_returns_balance_due_for_open_supplier_invoice(self):
        from app.services.finance.gl.fx_revaluation import FXRevaluationService

        db = MagicMock()
        svc = FXRevaluationService(db)

        bill = self._make_supplier_invoice(
            total_amount=Decimal("2000"),
            amount_paid=Decimal("500"),
        )
        db.scalars.return_value.all.return_value = [bill]

        result = svc._discover_ap_open_invoices(
            organization_id=uuid4(),
            period_end_date=date(2026, 1, 31),
        )

        assert len(result) == 1
        _, control_account_id, currency, posting_rate, balance_due = result[0]
        assert control_account_id == bill.ap_control_account_id
        assert currency == "USD"
        assert posting_rate == Decimal("750.0")
        assert balance_due == Decimal("1500")

    def test_skips_fully_paid_supplier_invoices(self):
        from app.services.finance.gl.fx_revaluation import FXRevaluationService

        db = MagicMock()
        svc = FXRevaluationService(db)

        paid = self._make_supplier_invoice(
            total_amount=Decimal("2000"),
            amount_paid=Decimal("2000"),
        )
        db.scalars.return_value.all.return_value = [paid]

        result = svc._discover_ap_open_invoices(
            organization_id=uuid4(),
            period_end_date=date(2026, 1, 31),
        )

        assert result == []
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
poetry run pytest tests/ifrs/gl/test_fx_revaluation_service.py::TestDiscoverApOpenInvoices -v
```

Expected: 2 failures (`AttributeError: ... no attribute '_discover_ap_open_invoices'`).

- [ ] **Step 3: Implement `_discover_ap_open_invoices`**

Add to `app/services/finance/gl/fx_revaluation.py`:

```python
# Add to top-of-file imports:
from app.models.finance.ap.supplier_invoice import (
    SupplierInvoice,
    SupplierInvoiceStatus,
)


# Add as a method on FXRevaluationService:
    def _discover_ap_open_invoices(
        self,
        organization_id: UUID,
        period_end_date: date,
    ) -> list[tuple[UUID, UUID, str, Decimal, Decimal]]:
        """List AP supplier invoices in non-functional currency with
        balance_due > 0. Mirrors _discover_ar_open_invoices but reads
        SupplierInvoice + ap_control_account_id.
        """
        functional = app_settings.default_functional_currency_code

        stmt = (
            select(SupplierInvoice)
            .where(
                SupplierInvoice.organization_id == organization_id,
                SupplierInvoice.currency_code != functional,
                SupplierInvoice.status.in_(SupplierInvoiceStatus.outstanding()),
            )
        )
        invoices = self.db.scalars(stmt).all()

        result: list[tuple[UUID, UUID, str, Decimal, Decimal]] = []
        for inv in invoices:
            balance_due = (inv.total_amount or Decimal("0")) - (
                inv.amount_paid or Decimal("0")
            )
            if balance_due <= 0:
                continue
            if inv.currency_code == functional:
                continue
            result.append(
                (
                    inv.invoice_id,
                    inv.ap_control_account_id,
                    inv.currency_code,
                    inv.exchange_rate or Decimal("1"),
                    balance_due,
                )
            )
        return result
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
poetry run pytest tests/ifrs/gl/test_fx_revaluation_service.py::TestDiscoverApOpenInvoices -v
```

Expected: 2 passes.

- [ ] **Step 5: Commit**

```bash
git add app/services/finance/gl/fx_revaluation.py tests/ifrs/gl/test_fx_revaluation_service.py
git commit -m "FXRevaluationService._discover_ap_open_invoices"
```

---

## Task 7: `_discover_bank_balances(period_end_date, organization_id)`

**Files:**
- Modify: `app/services/finance/gl/fx_revaluation.py`
- Modify: `tests/ifrs/gl/test_fx_revaluation_service.py`

Each active foreign-currency `BankAccount` contributes a `(account_id, gl_account_id, currency_code, posting_rate=None, balance_in_currency)` tuple. Posting rate is `None` because there's no per-account posting rate — bank balances are translated at the closing rate against their currency, no comparison to a prior posting rate. Balance source: prefer `last_statement_balance` if `last_statement_date >= period_end_date`; otherwise compute from journal postings.

- [ ] **Step 1: Write the failing tests**

Append to `tests/ifrs/gl/test_fx_revaluation_service.py`:

```python
class TestDiscoverBankBalances:
    """Active bank accounts in non-functional currency with non-zero balance."""

    def _make_account(self, **overrides):
        from types import SimpleNamespace

        defaults = {
            "bank_account_id": uuid4(),
            "gl_account_id": uuid4(),
            "currency_code": "USD",
            "status": MagicMock(value="active"),
            "last_statement_balance": Decimal("12345.67"),
            "last_statement_date": date(2026, 1, 31),
        }
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    def test_uses_last_statement_balance_when_recent(self):
        from app.services.finance.gl.fx_revaluation import FXRevaluationService

        db = MagicMock()
        svc = FXRevaluationService(db)

        acct = self._make_account(
            last_statement_balance=Decimal("12345.67"),
            last_statement_date=date(2026, 1, 31),
        )
        db.scalars.return_value.all.return_value = [acct]

        result = svc._discover_bank_balances(
            organization_id=uuid4(),
            period_end_date=date(2026, 1, 31),
        )

        assert len(result) == 1
        _, gl_acct_id, currency, posting_rate, balance = result[0]
        assert gl_acct_id == acct.gl_account_id
        assert currency == "USD"
        assert posting_rate is None  # bank balances have no single posting rate
        assert balance == Decimal("12345.67")

    def test_falls_back_to_journal_sum_when_statement_stale(self):
        """If last_statement_date < period_end_date, compute from GL postings."""
        from app.services.finance.gl.fx_revaluation import FXRevaluationService

        db = MagicMock()
        svc = FXRevaluationService(db)

        stale = self._make_account(
            last_statement_balance=Decimal("100.00"),
            last_statement_date=date(2026, 1, 1),  # stale relative to 1/31
        )
        db.scalars.return_value.all.return_value = [stale]

        # Patch the journal-sum helper to return a known value
        with patch.object(
            svc, "_compute_balance_from_journals", return_value=Decimal("9999.99")
        ):
            result = svc._discover_bank_balances(
                organization_id=uuid4(),
                period_end_date=date(2026, 1, 31),
            )

        assert result[0][4] == Decimal("9999.99")

    def test_skips_zero_balance_accounts(self):
        from app.services.finance.gl.fx_revaluation import FXRevaluationService

        db = MagicMock()
        svc = FXRevaluationService(db)

        empty = self._make_account(last_statement_balance=Decimal("0"))
        db.scalars.return_value.all.return_value = [empty]

        result = svc._discover_bank_balances(
            organization_id=uuid4(),
            period_end_date=date(2026, 1, 31),
        )
        assert result == []

    def test_skips_functional_currency_accounts(self):
        from app.services.finance.gl.fx_revaluation import FXRevaluationService

        db = MagicMock()
        svc = FXRevaluationService(db)

        ngn = self._make_account(currency_code="NGN")
        db.scalars.return_value.all.return_value = [ngn]

        result = svc._discover_bank_balances(
            organization_id=uuid4(),
            period_end_date=date(2026, 1, 31),
        )
        assert result == []
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
poetry run pytest tests/ifrs/gl/test_fx_revaluation_service.py::TestDiscoverBankBalances -v
```

Expected: 4 failures.

- [ ] **Step 3: Implement `_discover_bank_balances` and `_compute_balance_from_journals`**

Add to `app/services/finance/gl/fx_revaluation.py`:

```python
# Add to top-of-file imports:
from sqlalchemy import func
from app.models.finance.banking import BankAccount, BankAccountStatus
from app.models.finance.gl.journal_entry import JournalEntry, JournalStatus
from app.models.finance.gl.journal_entry_line import JournalEntryLine


# Add as methods on FXRevaluationService:
    def _discover_bank_balances(
        self,
        organization_id: UUID,
        period_end_date: date,
    ) -> list[tuple[UUID, UUID, str, Decimal | None, Decimal]]:
        """List active foreign-currency bank accounts with non-zero balance.

        Returns tuples of:
          (bank_account_id, gl_account_id, currency_code,
           None,                  # bank balances have no single posting rate
           balance_in_account_currency)

        Balance source: prefer ``last_statement_balance`` if the linked
        statement date covers period_end_date; otherwise compute from
        posted journal lines on ``gl_account_id``.
        """
        functional = app_settings.default_functional_currency_code

        stmt = (
            select(BankAccount)
            .where(
                BankAccount.organization_id == organization_id,
                BankAccount.currency_code != functional,
                BankAccount.status == BankAccountStatus.active,
            )
        )
        accounts = self.db.scalars(stmt).all()

        result: list[tuple[UUID, UUID, str, Decimal | None, Decimal]] = []
        for acct in accounts:
            if acct.currency_code == functional:
                continue
            balance = self._resolve_bank_balance(acct, period_end_date)
            if balance == 0:
                continue
            result.append(
                (
                    acct.bank_account_id,
                    acct.gl_account_id,
                    acct.currency_code,
                    None,
                    balance,
                )
            )
        return result

    def _resolve_bank_balance(
        self, account: BankAccount, period_end_date: date
    ) -> Decimal:
        """Resolve account balance as-of period_end_date.

        Prefer last_statement_balance when its date is >= period_end_date;
        otherwise compute from GL journals.
        """
        stmt_date = getattr(account, "last_statement_date", None)
        stmt_balance = getattr(account, "last_statement_balance", None)
        if (
            stmt_balance is not None
            and stmt_date is not None
            and stmt_date >= period_end_date
        ):
            return Decimal(str(stmt_balance))
        return self._compute_balance_from_journals(
            account.gl_account_id, period_end_date
        )

    def _compute_balance_from_journals(
        self, gl_account_id: UUID, as_of_date: date
    ) -> Decimal:
        """Sum (debits - credits) on gl_account_id through as_of_date,
        across POSTED journals only. Returns 0 if no postings."""
        stmt = (
            select(
                func.coalesce(func.sum(JournalEntryLine.debit_amount), 0)
                - func.coalesce(func.sum(JournalEntryLine.credit_amount), 0)
            )
            .select_from(JournalEntryLine)
            .join(
                JournalEntry,
                JournalEntry.journal_entry_id == JournalEntryLine.journal_entry_id,
            )
            .where(
                JournalEntryLine.account_id == gl_account_id,
                JournalEntry.status == JournalStatus.POSTED,
                JournalEntry.posting_date <= as_of_date,
            )
        )
        result = self.db.scalar(stmt)
        return Decimal(str(result)) if result is not None else Decimal("0")
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
poetry run pytest tests/ifrs/gl/test_fx_revaluation_service.py::TestDiscoverBankBalances -v
```

Expected: 4 passes.

- [ ] **Step 5: Commit**

```bash
git add app/services/finance/gl/fx_revaluation.py tests/ifrs/gl/test_fx_revaluation_service.py
git commit -m "FXRevaluationService._discover_bank_balances"
```

---

## Task 8: `_lookup_closing_rates(currencies, period_end_date)` with warning path

**Files:**
- Modify: `app/services/finance/gl/fx_revaluation.py`
- Modify: `tests/ifrs/gl/test_fx_revaluation_service.py`

Returns `(rates: dict[str, Decimal], warnings: list[str])`. A currency without a rate is omitted from `rates` and produces a warning string.

- [ ] **Step 1: Write the failing tests**

Append:

```python
class TestLookupClosingRates:
    """Resolve closing rates for a set of currencies; warn on misses."""

    def test_returns_rates_for_known_currencies(self):
        from app.services.finance.gl.fx_revaluation import FXRevaluationService

        db = MagicMock()
        svc = FXRevaluationService(db)

        with patch(
            "app.services.finance.gl.fx_revaluation.FXService.lookup_spot_rate",
            side_effect=lambda _db, _org, ccy, _date: {
                "USD": Decimal("820"),
                "GBP": Decimal("1050"),
            }.get(ccy),
        ):
            rates, warnings = svc._lookup_closing_rates(
                organization_id=uuid4(),
                currencies={"USD", "GBP"},
                period_end_date=date(2026, 1, 31),
            )

        assert rates == {"USD": Decimal("820"), "GBP": Decimal("1050")}
        assert warnings == []

    def test_warns_and_omits_currencies_without_rate(self):
        from app.services.finance.gl.fx_revaluation import FXRevaluationService

        db = MagicMock()
        svc = FXRevaluationService(db)

        with patch(
            "app.services.finance.gl.fx_revaluation.FXService.lookup_spot_rate",
            side_effect=lambda _db, _org, ccy, _date: (
                Decimal("820") if ccy == "USD" else None
            ),
        ):
            rates, warnings = svc._lookup_closing_rates(
                organization_id=uuid4(),
                currencies={"USD", "EUR"},
                period_end_date=date(2026, 1, 31),
            )

        assert rates == {"USD": Decimal("820")}
        assert any("EUR" in w for w in warnings)
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
poetry run pytest tests/ifrs/gl/test_fx_revaluation_service.py::TestLookupClosingRates -v
```

Expected: 2 failures.

- [ ] **Step 3: Implement `_lookup_closing_rates`**

Add to `app/services/finance/gl/fx_revaluation.py`:

```python
# Add to top-of-file imports:
from app.services.finance.platform.fx import FXService


# Add as a method on FXRevaluationService:
    def _lookup_closing_rates(
        self,
        organization_id: UUID,
        currencies: set[str],
        period_end_date: date,
    ) -> tuple[dict[str, Decimal], list[str]]:
        """Look up the closing spot rate for each currency at period_end_date.

        Currencies without a recorded rate are omitted from the result
        and produce a warning. Items in those currencies are skipped at
        the compute step.
        """
        rates: dict[str, Decimal] = {}
        warnings: list[str] = []

        for currency in sorted(currencies):
            rate = FXService.lookup_spot_rate(
                self.db, organization_id, currency, period_end_date
            )
            if rate is None:
                warnings.append(
                    f"No closing rate available for {currency} on "
                    f"{period_end_date}; items in {currency} will be skipped."
                )
                continue
            rates[currency] = Decimal(str(rate))

        return rates, warnings
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
poetry run pytest tests/ifrs/gl/test_fx_revaluation_service.py::TestLookupClosingRates -v
```

Expected: 2 passes.

- [ ] **Step 5: Commit**

```bash
git add app/services/finance/gl/fx_revaluation.py tests/ifrs/gl/test_fx_revaluation_service.py
git commit -m "FXRevaluationService._lookup_closing_rates with warning path"
```

---

## Task 9: `_compute_revaluation_lines(items, rates, control_account_normal_balance)` and `_aggregate_per_account_currency`

**Files:**
- Modify: `app/services/finance/gl/fx_revaluation.py`
- Modify: `tests/ifrs/gl/test_fx_revaluation_service.py`

For each item, compute `delta_functional = (balance × closing_rate) − (balance × posting_rate)` for AR/AP, or `delta_functional = (balance × closing_rate) − current_book_value_in_NGN` for cash. The "is_gain" flag depends on whether the account is asset (delta positive = gain) or liability (delta positive = loss). For v1 we determine this from the source: AR control + bank GL = asset (positive delta = gain); AP control = liability (positive delta = loss).

After computing per-item, aggregate by `(control_account_id, currency_code)` summing the deltas, producing one `FXRevaluationLine` per pair.

- [ ] **Step 1: Write the failing tests**

Append:

```python
class TestComputeRevaluationLines:
    """Pure compute: items + rates → list[FXRevaluationLine]."""

    def test_ar_gain_when_foreign_currency_strengthens(self):
        """USD invoice $600 outstanding, posted at 750, closing 820.
        Functional book value: 600*750=450,000.
        Functional revalued:   600*820=492,000.
        Delta: +42,000 → gain (asset increased)."""
        from app.services.finance.gl.fx_revaluation import (
            FXRevaluationLine,
            FXRevaluationService,
        )

        db = MagicMock()
        svc = FXRevaluationService(db)
        control_account_id = uuid4()

        lines = svc._compute_revaluation_lines(
            ar_items=[
                (uuid4(), control_account_id, "USD", Decimal("750"), Decimal("600")),
            ],
            ap_items=[],
            cash_items=[],
            rates={"USD": Decimal("820")},
        )

        assert len(lines) == 1
        line = lines[0]
        assert isinstance(line, FXRevaluationLine)
        assert line.account_id == control_account_id
        assert line.currency_code == "USD"
        assert line.closing_rate == Decimal("820")
        assert line.book_value_functional == Decimal("450000")
        assert line.revalued_value_functional == Decimal("492000")
        assert line.delta_functional == Decimal("42000")
        assert line.is_gain is True

    def test_ap_loss_when_foreign_currency_strengthens(self):
        """USD bill $1000 outstanding, posted at 750, closing 820.
        Liability went up in functional terms → loss."""
        from app.services.finance.gl.fx_revaluation import FXRevaluationService

        db = MagicMock()
        svc = FXRevaluationService(db)
        control_account_id = uuid4()

        lines = svc._compute_revaluation_lines(
            ar_items=[],
            ap_items=[
                (uuid4(), control_account_id, "USD", Decimal("750"), Decimal("1000")),
            ],
            cash_items=[],
            rates={"USD": Decimal("820")},
        )

        line = lines[0]
        assert line.delta_functional == Decimal("70000")  # 820000 - 750000
        assert line.is_gain is False  # liability up = loss

    def test_skips_items_in_currencies_without_rate(self):
        from app.services.finance.gl.fx_revaluation import FXRevaluationService

        db = MagicMock()
        svc = FXRevaluationService(db)
        control_id = uuid4()

        lines = svc._compute_revaluation_lines(
            ar_items=[
                (uuid4(), control_id, "EUR", Decimal("800"), Decimal("100")),
            ],
            ap_items=[],
            cash_items=[],
            rates={},  # no rate for EUR
        )

        assert lines == []

    def test_cash_revaluation_uses_current_book_from_journals(self):
        """Bank balance: $5,000 at posting rate captured per-receipt is
        irrelevant; we revalue the current book value in NGN against the
        closing-rate equivalent."""
        from app.services.finance.gl.fx_revaluation import FXRevaluationService

        db = MagicMock()
        svc = FXRevaluationService(db)
        gl_id = uuid4()

        # Patch the GL-balance helper used for cash items
        with patch.object(
            svc, "_compute_balance_from_journals", return_value=Decimal("3700000")
        ):
            lines = svc._compute_revaluation_lines(
                ar_items=[],
                ap_items=[],
                cash_items=[(uuid4(), gl_id, "USD", None, Decimal("5000"))],
                rates={"USD": Decimal("820")},
            )

        line = lines[0]
        assert line.revalued_value_functional == Decimal("4100000")  # 5000*820
        assert line.book_value_functional == Decimal("3700000")
        assert line.delta_functional == Decimal("400000")
        assert line.is_gain is True


class TestAggregatePerAccountCurrency:
    """Sum deltas per (control_account_id, currency_code)."""

    def test_aggregates_two_invoices_against_same_control_account(self):
        from app.services.finance.gl.fx_revaluation import (
            FXRevaluationLine,
            FXRevaluationService,
        )

        svc = FXRevaluationService(MagicMock())
        control = uuid4()
        a = FXRevaluationLine(
            account_id=control,
            currency_code="USD",
            closing_rate=Decimal("820"),
            book_value_functional=Decimal("100"),
            revalued_value_functional=Decimal("110"),
            delta_functional=Decimal("10"),
            is_gain=True,
        )
        b = FXRevaluationLine(
            account_id=control,
            currency_code="USD",
            closing_rate=Decimal("820"),
            book_value_functional=Decimal("200"),
            revalued_value_functional=Decimal("215"),
            delta_functional=Decimal("15"),
            is_gain=True,
        )

        result = svc._aggregate_per_account_currency([a, b])

        assert len(result) == 1
        agg = result[0]
        assert agg.account_id == control
        assert agg.currency_code == "USD"
        assert agg.delta_functional == Decimal("25")
        assert agg.book_value_functional == Decimal("300")
        assert agg.revalued_value_functional == Decimal("325")
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
poetry run pytest tests/ifrs/gl/test_fx_revaluation_service.py::TestComputeRevaluationLines tests/ifrs/gl/test_fx_revaluation_service.py::TestAggregatePerAccountCurrency -v
```

Expected: 5 failures.

- [ ] **Step 3: Implement `_compute_revaluation_lines` and `_aggregate_per_account_currency`**

Add to `app/services/finance/gl/fx_revaluation.py`:

```python
    def _compute_revaluation_lines(
        self,
        ar_items: list[tuple[UUID, UUID, str, Decimal, Decimal]],
        ap_items: list[tuple[UUID, UUID, str, Decimal, Decimal]],
        cash_items: list[tuple[UUID, UUID, str, Decimal | None, Decimal]],
        rates: dict[str, Decimal],
    ) -> list[FXRevaluationLine]:
        """Compute one FXRevaluationLine per item (pre-aggregation).

        Items in currencies without a closing rate are skipped silently
        — the caller has already produced a warning for them.
        """
        out: list[FXRevaluationLine] = []

        # AR: asset side. Delta positive = gain (asset went up).
        for _id, control, currency, posting_rate, balance in ar_items:
            rate = rates.get(currency)
            if rate is None:
                continue
            book = balance * posting_rate
            revalued = balance * rate
            delta = revalued - book
            out.append(
                FXRevaluationLine(
                    account_id=control,
                    currency_code=currency,
                    closing_rate=rate,
                    book_value_functional=book,
                    revalued_value_functional=revalued,
                    delta_functional=delta,
                    is_gain=delta > 0,
                )
            )

        # AP: liability side. Delta positive (liability up) = loss.
        for _id, control, currency, posting_rate, balance in ap_items:
            rate = rates.get(currency)
            if rate is None:
                continue
            book = balance * posting_rate
            revalued = balance * rate
            delta = revalued - book
            out.append(
                FXRevaluationLine(
                    account_id=control,
                    currency_code=currency,
                    closing_rate=rate,
                    book_value_functional=book,
                    revalued_value_functional=revalued,
                    delta_functional=delta,
                    is_gain=delta < 0,
                    # asymmetry: liability up = loss
                )
            )

        # Cash: asset side. Book value is the current ledger balance in NGN
        # (computed independently of any single posting rate). Revalued
        # value is balance_in_currency × closing_rate.
        for _id, gl_account_id, currency, _no_rate, balance_in_ccy in cash_items:
            rate = rates.get(currency)
            if rate is None:
                continue
            # period_end_date isn't stored on the item tuple; compute
            # against today is fine for cash since the journal-sum
            # path was already evaluated as-of period_end_date in
            # _resolve_bank_balance. However, to keep this method pure,
            # we re-derive the book from journals here using a sentinel
            # past date — the caller passes period_end_date through a
            # closure. For the dataflow used by preview() we pass
            # period_end_date through self._period_end_for_compute set
            # before invocation.
            book = self._compute_balance_from_journals(
                gl_account_id,
                getattr(self, "_period_end_for_compute", date.today()),
            )
            revalued = balance_in_ccy * rate
            delta = revalued - book
            out.append(
                FXRevaluationLine(
                    account_id=gl_account_id,
                    currency_code=currency,
                    closing_rate=rate,
                    book_value_functional=book,
                    revalued_value_functional=revalued,
                    delta_functional=delta,
                    is_gain=delta > 0,
                )
            )

        return out

    def _aggregate_per_account_currency(
        self, lines: list[FXRevaluationLine]
    ) -> list[FXRevaluationLine]:
        """Sum deltas per (account_id, currency_code) pair. Drops zero-net
        aggregations."""
        buckets: dict[tuple[UUID, str], FXRevaluationLine] = {}

        for line in lines:
            key = (line.account_id, line.currency_code)
            existing = buckets.get(key)
            if existing is None:
                buckets[key] = FXRevaluationLine(
                    account_id=line.account_id,
                    currency_code=line.currency_code,
                    closing_rate=line.closing_rate,
                    book_value_functional=line.book_value_functional,
                    revalued_value_functional=line.revalued_value_functional,
                    delta_functional=line.delta_functional,
                    is_gain=line.is_gain,
                )
            else:
                existing.book_value_functional += line.book_value_functional
                existing.revalued_value_functional += line.revalued_value_functional
                existing.delta_functional += line.delta_functional
                # is_gain re-derived from aggregated delta + side. Use the
                # incoming line's side (AR/AP/cash) — both lines for the
                # same control_account come from the same source, so
                # is_gain is consistent already.

        # Drop zero-net aggregations
        return [line for line in buckets.values() if line.delta_functional != 0]
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
poetry run pytest tests/ifrs/gl/test_fx_revaluation_service.py::TestComputeRevaluationLines tests/ifrs/gl/test_fx_revaluation_service.py::TestAggregatePerAccountCurrency -v
```

Expected: 5 passes.

- [ ] **Step 5: Commit**

```bash
git add app/services/finance/gl/fx_revaluation.py tests/ifrs/gl/test_fx_revaluation_service.py
git commit -m "FXRevaluationService compute and aggregate steps"
```

---

## Task 10: `_detect_prior_run(organization_id, fiscal_period_id)`

**Files:**
- Modify: `app/services/finance/gl/fx_revaluation.py`
- Modify: `tests/ifrs/gl/test_fx_revaluation_service.py`

Searches for previously-posted FX revaluation journals for the period (via `source_module = "FXR"` and `fiscal_period_id` match), excluding `REVERSED`/`VOID`. Returns the journal IDs of any active prior pair. Used to populate `prior_run_exists` on the preview and to identify which journals to reverse on re-run.

- [ ] **Step 1: Write the failing tests**

Append:

```python
class TestDetectPriorRun:
    """Identify active FXR-source journals for a period."""

    def test_returns_empty_when_no_prior_run(self):
        from app.services.finance.gl.fx_revaluation import FXRevaluationService

        db = MagicMock()
        svc = FXRevaluationService(db)
        db.scalars.return_value.all.return_value = []

        ids = svc._detect_prior_run(
            organization_id=uuid4(), fiscal_period_id=uuid4()
        )
        assert ids == []

    def test_returns_journal_ids_when_prior_run_exists(self):
        from types import SimpleNamespace

        from app.services.finance.gl.fx_revaluation import FXRevaluationService

        db = MagicMock()
        svc = FXRevaluationService(db)

        a, b = uuid4(), uuid4()
        je_a = SimpleNamespace(journal_entry_id=a)
        je_b = SimpleNamespace(journal_entry_id=b)
        db.scalars.return_value.all.return_value = [je_a, je_b]

        ids = svc._detect_prior_run(
            organization_id=uuid4(), fiscal_period_id=uuid4()
        )
        assert set(ids) == {a, b}
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
poetry run pytest tests/ifrs/gl/test_fx_revaluation_service.py::TestDetectPriorRun -v
```

Expected: 2 failures.

- [ ] **Step 3: Implement `_detect_prior_run`**

Add to `app/services/finance/gl/fx_revaluation.py`:

```python
    def _detect_prior_run(
        self,
        organization_id: UUID,
        fiscal_period_id: UUID,
    ) -> list[UUID]:
        """Return journal_entry_ids of active prior FXR journals for this
        period — excludes REVERSED and VOID statuses (those are settled).
        """
        stmt = (
            select(JournalEntry)
            .where(
                JournalEntry.organization_id == organization_id,
                JournalEntry.source_module == self.SOURCE_MODULE,
                JournalEntry.fiscal_period_id == fiscal_period_id,
                JournalEntry.status.in_(
                    {JournalStatus.POSTED, JournalStatus.DRAFT, JournalStatus.SUBMITTED}
                ),
            )
        )
        rows = self.db.scalars(stmt).all()
        return [row.journal_entry_id for row in rows]
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
poetry run pytest tests/ifrs/gl/test_fx_revaluation_service.py::TestDetectPriorRun -v
```

Expected: 2 passes.

- [ ] **Step 5: Commit**

```bash
git add app/services/finance/gl/fx_revaluation.py tests/ifrs/gl/test_fx_revaluation_service.py
git commit -m "FXRevaluationService._detect_prior_run"
```

---

## Task 11: `preview()` — assemble discovery + compute, no DB writes

**Files:**
- Modify: `app/services/finance/gl/fx_revaluation.py`
- Modify: `tests/ifrs/gl/test_fx_revaluation_service.py`

Public method. Verifies period is OPEN/REOPENED, reads FX accounts (raises 400 if unset), discovers items, looks up rates, computes lines, aggregates, detects prior run, packages everything into `FXRevaluationPreview`.

- [ ] **Step 1: Write the failing tests**

Append:

```python
class TestPreview:
    """End-to-end preview — composes discovery + compute, no DB writes."""

    def _setup_open_period(self, svc, period_id, end_date):
        """Patch period lookup and status check for happy paths."""
        from types import SimpleNamespace

        from app.models.finance.gl.fiscal_period import PeriodStatus

        period = SimpleNamespace(
            fiscal_period_id=period_id,
            organization_id=uuid4(),
            start_date=date(2026, 1, 1),
            end_date=end_date,
            status=PeriodStatus.OPEN,
        )
        svc.db.get.return_value = period
        return period

    def test_refuses_when_period_not_open(self):
        from fastapi import HTTPException
        from types import SimpleNamespace

        from app.models.finance.gl.fiscal_period import PeriodStatus
        from app.services.finance.gl.fx_revaluation import FXRevaluationService

        db = MagicMock()
        svc = FXRevaluationService(db)
        period_id = uuid4()

        closed = SimpleNamespace(
            fiscal_period_id=period_id,
            organization_id=uuid4(),
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
            status=PeriodStatus.HARD_CLOSED,
        )
        db.get.return_value = closed

        with pytest.raises(HTTPException) as exc:
            svc.preview(organization_id=uuid4(), fiscal_period_id=period_id)

        assert exc.value.status_code == 400
        assert "open" in exc.value.detail.lower()

    def test_returns_empty_preview_when_no_foreign_currency_items(self):
        from app.services.finance.gl.fx_revaluation import FXRevaluationService

        db = MagicMock()
        svc = FXRevaluationService(db)
        period_id = uuid4()
        org_id = uuid4()
        self._setup_open_period(svc, period_id, date(2026, 1, 31))

        with (
            patch.object(svc, "_read_fx_account_ids", return_value=(uuid4(), uuid4())),
            patch.object(svc, "_discover_ar_open_invoices", return_value=[]),
            patch.object(svc, "_discover_ap_open_invoices", return_value=[]),
            patch.object(svc, "_discover_bank_balances", return_value=[]),
            patch.object(svc, "_detect_prior_run", return_value=[]),
        ):
            preview = svc.preview(organization_id=org_id, fiscal_period_id=period_id)

        assert preview.lines == []
        assert preview.total_gain_functional == Decimal("0")
        assert preview.total_loss_functional == Decimal("0")
        assert preview.warnings == []
        assert preview.prior_run_exists is False

    def test_detects_prior_run_and_populates_journal_ids(self):
        from app.services.finance.gl.fx_revaluation import FXRevaluationService

        db = MagicMock()
        svc = FXRevaluationService(db)
        period_id = uuid4()
        prior_a, prior_b = uuid4(), uuid4()
        self._setup_open_period(svc, period_id, date(2026, 1, 31))

        with (
            patch.object(svc, "_read_fx_account_ids", return_value=(uuid4(), uuid4())),
            patch.object(svc, "_discover_ar_open_invoices", return_value=[]),
            patch.object(svc, "_discover_ap_open_invoices", return_value=[]),
            patch.object(svc, "_discover_bank_balances", return_value=[]),
            patch.object(svc, "_detect_prior_run", return_value=[prior_a, prior_b]),
        ):
            preview = svc.preview(organization_id=uuid4(), fiscal_period_id=period_id)

        assert preview.prior_run_exists is True
        assert set(preview.prior_journal_ids) == {prior_a, prior_b}
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
poetry run pytest tests/ifrs/gl/test_fx_revaluation_service.py::TestPreview -v
```

Expected: 3 failures.

- [ ] **Step 3: Implement `preview`**

Add to `app/services/finance/gl/fx_revaluation.py`:

```python
# Add to top-of-file imports:
from app.models.finance.gl.fiscal_period import FiscalPeriod, PeriodStatus


# Add as a method on FXRevaluationService:
    POSTABLE_PERIOD_STATUSES = {PeriodStatus.OPEN, PeriodStatus.REOPENED}

    def preview(
        self,
        organization_id: UUID,
        fiscal_period_id: UUID,
    ) -> FXRevaluationPreview:
        """Read-only preview of period-end revaluation. Raises HTTPException(400)
        on configuration or period-status problems."""
        period = self.db.get(FiscalPeriod, fiscal_period_id)
        if not period or period.organization_id != organization_id:
            raise HTTPException(
                status_code=404, detail="Fiscal period not found"
            )
        if period.status not in self.POSTABLE_PERIOD_STATUSES:
            raise HTTPException(
                status_code=400,
                detail=f"Period must be open to run FX revaluation; "
                f"current status is {period.status.value}",
            )

        # Refuse early if FX accounts unconfigured (raises 400)
        self._read_fx_account_ids(organization_id)

        # Discover monetary items in non-functional currency
        ar_items = self._discover_ar_open_invoices(
            organization_id, period.end_date
        )
        ap_items = self._discover_ap_open_invoices(
            organization_id, period.end_date
        )
        cash_items = self._discover_bank_balances(
            organization_id, period.end_date
        )

        # Collect currencies in scope
        currencies: set[str] = set()
        for src in (ar_items, ap_items, cash_items):
            for item in src:
                currencies.add(item[2])

        # Look up closing rates; warn on misses
        rates, warnings = self._lookup_closing_rates(
            organization_id, currencies, period.end_date
        )

        # Compute & aggregate
        self._period_end_for_compute = period.end_date  # used by cash branch
        try:
            raw_lines = self._compute_revaluation_lines(
                ar_items, ap_items, cash_items, rates
            )
        finally:
            del self._period_end_for_compute

        aggregated = self._aggregate_per_account_currency(raw_lines)

        # Totals
        total_gain = sum(
            (line.delta_functional for line in aggregated if line.is_gain),
            Decimal("0"),
        )
        total_loss = sum(
            (-line.delta_functional for line in aggregated if not line.is_gain),
            Decimal("0"),
        )

        # Detect prior run
        prior_journal_ids = self._detect_prior_run(
            organization_id, fiscal_period_id
        )

        # Resolve next-period start date for the reversal
        next_period_start = self._resolve_next_period_start(
            organization_id, period.end_date
        )

        return FXRevaluationPreview(
            fiscal_period_id=fiscal_period_id,
            period_end_date=period.end_date,
            next_period_start_date=next_period_start,
            lines=aggregated,
            total_gain_functional=total_gain,
            total_loss_functional=total_loss,
            rates_used=rates,
            warnings=warnings,
            prior_run_exists=bool(prior_journal_ids),
            prior_journal_ids=prior_journal_ids,
        )

    def _resolve_next_period_start(
        self, organization_id: UUID, period_end_date: date
    ) -> date | None:
        """Find the period whose start_date == period_end_date + 1 day.
        Returns None if no such period exists."""
        from datetime import timedelta

        target = period_end_date + timedelta(days=1)
        stmt = select(FiscalPeriod).where(
            FiscalPeriod.organization_id == organization_id,
            FiscalPeriod.start_date == target,
        )
        period = self.db.scalar(stmt)
        return period.start_date if period else None
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
poetry run pytest tests/ifrs/gl/test_fx_revaluation_service.py::TestPreview -v
```

Expected: 3 passes.

- [ ] **Step 5: Commit**

```bash
git add app/services/finance/gl/fx_revaluation.py tests/ifrs/gl/test_fx_revaluation_service.py
git commit -m "FXRevaluationService.preview composing discovery + compute"
```

---

## Task 12: `_build_journal_input(...)` — convert lines into `JournalInput`

**Files:**
- Modify: `app/services/finance/gl/fx_revaluation.py`
- Modify: `tests/ifrs/gl/test_fx_revaluation_service.py`

Builds a `JournalInput` from aggregated `FXRevaluationLine`s plus the `fx_gain_account_id` and `fx_loss_account_id`. The asset/liability side gets one line per FXRevaluationLine; the gain/loss side gets two summary lines (one debit to FX Loss for total losses, one credit to FX Gain for total gains). The shape of debit-vs-credit on the asset/liability side depends on (`is_gain` × asset/liability orientation) — but since `is_gain` is already computed correctly per line, we apply: AR/cash gain ⇒ debit control account, credit FX Gain. AP gain ⇒ debit AP, credit FX Gain. Etc.

For v1 simplicity: track each side as `(debit_total, credit_total)` then emit. The sign rule below is in the implementation.

- [ ] **Step 1: Write the failing tests**

Append:

```python
class TestBuildJournalInput:
    """Convert aggregated lines into JournalInput for posting."""

    def test_gain_line_credits_fx_gain_account(self):
        from app.services.finance.gl.fx_revaluation import (
            FXRevaluationLine,
            FXRevaluationService,
        )

        svc = FXRevaluationService(MagicMock())
        ar_account = uuid4()
        gain_account = uuid4()
        loss_account = uuid4()

        line = FXRevaluationLine(
            account_id=ar_account,
            currency_code="USD",
            closing_rate=Decimal("820"),
            book_value_functional=Decimal("450000"),
            revalued_value_functional=Decimal("492000"),
            delta_functional=Decimal("42000"),
            is_gain=True,
        )

        ji = svc._build_journal_input(
            lines=[line],
            posting_date=date(2026, 1, 31),
            description="FX revaluation as at 2026-01-31",
            fx_gain_account_id=gain_account,
            fx_loss_account_id=loss_account,
            correlation_id="abc",
        )

        # Debit AR control 42000, credit FX Gain 42000
        debits = [(l.account_id, l.debit_amount) for l in ji.lines if l.debit_amount > 0]
        credits = [
            (l.account_id, l.credit_amount) for l in ji.lines if l.credit_amount > 0
        ]
        assert (ar_account, Decimal("42000")) in debits
        assert (gain_account, Decimal("42000")) in credits

    def test_loss_line_debits_fx_loss_account(self):
        from app.services.finance.gl.fx_revaluation import (
            FXRevaluationLine,
            FXRevaluationService,
        )

        svc = FXRevaluationService(MagicMock())
        ap_account = uuid4()
        gain_account = uuid4()
        loss_account = uuid4()

        # AP loss: liability up, delta positive in liability terms
        line = FXRevaluationLine(
            account_id=ap_account,
            currency_code="USD",
            closing_rate=Decimal("820"),
            book_value_functional=Decimal("750000"),
            revalued_value_functional=Decimal("820000"),
            delta_functional=Decimal("70000"),
            is_gain=False,  # liability up = loss
        )

        ji = svc._build_journal_input(
            lines=[line],
            posting_date=date(2026, 1, 31),
            description="FX revaluation as at 2026-01-31",
            fx_gain_account_id=gain_account,
            fx_loss_account_id=loss_account,
            correlation_id="abc",
        )

        debits = [(l.account_id, l.debit_amount) for l in ji.lines if l.debit_amount > 0]
        credits = [
            (l.account_id, l.credit_amount) for l in ji.lines if l.credit_amount > 0
        ]
        # Loss → debit FX Loss, credit AP control (liability up = credit AP)
        assert (loss_account, Decimal("70000")) in debits
        assert (ap_account, Decimal("70000")) in credits

    def test_aggregates_gains_and_losses_into_single_summary_lines(self):
        """Two gain lines → one combined credit to FX Gain.
        Two loss lines → one combined debit to FX Loss."""
        from app.services.finance.gl.fx_revaluation import (
            FXRevaluationLine,
            FXRevaluationService,
        )

        svc = FXRevaluationService(MagicMock())
        ar_a, ar_b, gain_a, loss_a = uuid4(), uuid4(), uuid4(), uuid4()

        gain1 = FXRevaluationLine(
            account_id=ar_a,
            currency_code="USD",
            closing_rate=Decimal("820"),
            book_value_functional=Decimal("100"),
            revalued_value_functional=Decimal("110"),
            delta_functional=Decimal("10"),
            is_gain=True,
        )
        gain2 = FXRevaluationLine(
            account_id=ar_b,
            currency_code="GBP",
            closing_rate=Decimal("1050"),
            book_value_functional=Decimal("200"),
            revalued_value_functional=Decimal("215"),
            delta_functional=Decimal("15"),
            is_gain=True,
        )

        ji = svc._build_journal_input(
            lines=[gain1, gain2],
            posting_date=date(2026, 1, 31),
            description="FX revaluation",
            fx_gain_account_id=gain_a,
            fx_loss_account_id=loss_a,
            correlation_id="abc",
        )

        # Two debits to AR controls (10 + 15) + one credit to FX Gain (25)
        gain_credits = [
            l.credit_amount for l in ji.lines if l.account_id == gain_a
        ]
        assert sum(gain_credits) == Decimal("25")
        # No FX Loss line should exist (no losses)
        assert all(l.account_id != loss_a for l in ji.lines)

    def test_journal_balances(self):
        """Total debits == total credits in any built journal."""
        from app.services.finance.gl.fx_revaluation import (
            FXRevaluationLine,
            FXRevaluationService,
        )

        svc = FXRevaluationService(MagicMock())
        ar = uuid4()
        ap = uuid4()
        gain = uuid4()
        loss = uuid4()

        ji = svc._build_journal_input(
            lines=[
                FXRevaluationLine(ar, "USD", Decimal("820"), Decimal("100"),
                                  Decimal("110"), Decimal("10"), True),
                FXRevaluationLine(ap, "GBP", Decimal("1050"), Decimal("200"),
                                  Decimal("220"), Decimal("20"), False),
            ],
            posting_date=date(2026, 1, 31),
            description="FX revaluation",
            fx_gain_account_id=gain,
            fx_loss_account_id=loss,
            correlation_id="abc",
        )

        total_dr = sum(l.debit_amount or Decimal("0") for l in ji.lines)
        total_cr = sum(l.credit_amount or Decimal("0") for l in ji.lines)
        assert total_dr == total_cr
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
poetry run pytest tests/ifrs/gl/test_fx_revaluation_service.py::TestBuildJournalInput -v
```

Expected: 4 failures.

- [ ] **Step 3: Implement `_build_journal_input` and `_build_reversal_input`**

Add to `app/services/finance/gl/fx_revaluation.py`:

```python
# Add to top-of-file imports:
from app.models.finance.gl.journal_entry import JournalType
from app.services.finance.gl.journal import JournalInput, JournalLineInput


    def _build_journal_input(
        self,
        lines: list[FXRevaluationLine],
        posting_date: date,
        description: str,
        fx_gain_account_id: UUID,
        fx_loss_account_id: UUID,
        correlation_id: str,
    ) -> JournalInput:
        """Build a balanced JournalInput from aggregated FXRevaluationLines.

        - Asset/liability side: one line per FXRevaluationLine (control
          account, debit/credit per gain/loss orientation).
        - Gain/loss side: aggregate summary lines (single debit to
          fx_loss_account_id for total losses, single credit to
          fx_gain_account_id for total gains).
        """
        journal_lines: list[JournalLineInput] = []

        total_gain = Decimal("0")
        total_loss = Decimal("0")

        for line in lines:
            amount = abs(line.delta_functional)
            if amount == 0:
                continue

            # Asset/liability side: dr if gain on asset OR loss on liability;
            # cr if loss on asset OR gain on liability. The is_gain flag was
            # set with the asset/liability orientation already taken into
            # account in _compute_revaluation_lines, so:
            #   is_gain → debit the control account, credit FX Gain
            #   not is_gain → credit the control account, debit FX Loss
            #
            # For AP specifically: is_gain=False means liability went up,
            # and "credit the AP control" matches accounting convention
            # (liabilities increase on credit side).
            if line.is_gain:
                journal_lines.append(
                    JournalLineInput(
                        account_id=line.account_id,
                        debit_amount=amount,
                        credit_amount=Decimal("0"),
                        description=f"FX revaluation: {line.currency_code} "
                        f"@ {line.closing_rate}",
                        currency_code=line.currency_code,
                        exchange_rate=line.closing_rate,
                    )
                )
                total_gain += amount
            else:
                journal_lines.append(
                    JournalLineInput(
                        account_id=line.account_id,
                        debit_amount=Decimal("0"),
                        credit_amount=amount,
                        description=f"FX revaluation: {line.currency_code} "
                        f"@ {line.closing_rate}",
                        currency_code=line.currency_code,
                        exchange_rate=line.closing_rate,
                    )
                )
                total_loss += amount

        # Gain/loss summary lines (functional currency, no per-currency)
        if total_gain > 0:
            journal_lines.append(
                JournalLineInput(
                    account_id=fx_gain_account_id,
                    debit_amount=Decimal("0"),
                    credit_amount=total_gain,
                    description="Total FX revaluation gains for the period",
                )
            )
        if total_loss > 0:
            journal_lines.append(
                JournalLineInput(
                    account_id=fx_loss_account_id,
                    debit_amount=total_loss,
                    credit_amount=Decimal("0"),
                    description="Total FX revaluation losses for the period",
                )
            )

        return JournalInput(
            journal_type=JournalType.REVALUATION,
            entry_date=posting_date,
            posting_date=posting_date,
            description=description,
            source_module=self.SOURCE_MODULE,
            correlation_id=correlation_id,
            lines=journal_lines,
        )

    def _build_reversal_input(
        self,
        period_end_input: JournalInput,
        reversal_date: date,
        original_journal_number: str,
    ) -> JournalInput:
        """Mirror the period-end journal — debits become credits and vice
        versa — with reversal_date as posting_date."""
        reversed_lines: list[JournalLineInput] = []
        for ln in period_end_input.lines:
            reversed_lines.append(
                JournalLineInput(
                    account_id=ln.account_id,
                    debit_amount=ln.credit_amount,
                    credit_amount=ln.debit_amount,
                    description=ln.description,
                    currency_code=ln.currency_code,
                    exchange_rate=ln.exchange_rate,
                )
            )
        return JournalInput(
            journal_type=JournalType.REVALUATION,
            entry_date=reversal_date,
            posting_date=reversal_date,
            description=f"Reversal of {original_journal_number}: "
            f"period-end FX revaluation",
            source_module=self.SOURCE_MODULE,
            correlation_id=period_end_input.correlation_id,
            lines=reversed_lines,
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
poetry run pytest tests/ifrs/gl/test_fx_revaluation_service.py::TestBuildJournalInput -v
```

Expected: 4 passes.

- [ ] **Step 5: Commit**

```bash
git add app/services/finance/gl/fx_revaluation.py tests/ifrs/gl/test_fx_revaluation_service.py
git commit -m "FXRevaluationService journal-input builders for period-end and reversal"
```

---

## Task 13: `post()` — atomic post with reverse-prior-pair on re-run

**Files:**
- Modify: `app/services/finance/gl/fx_revaluation.py`
- Modify: `tests/ifrs/gl/test_fx_revaluation_service.py`

Public method. Re-runs preview internally (state may have changed since GET). Locks the period row. If prior pair exists, validates reason and reverses both. Then posts new period-end and day-1 reversal pair. All within a single SAVEPOINT.

- [ ] **Step 1: Write the failing tests**

Append:

```python
class TestPost:
    """Atomic post with optional re-run reversal."""

    def _setup_open_period(self, db, period_id, end_date):
        from types import SimpleNamespace

        from app.models.finance.gl.fiscal_period import PeriodStatus

        period = SimpleNamespace(
            fiscal_period_id=period_id,
            organization_id=uuid4(),
            start_date=date(2026, 1, 1),
            end_date=end_date,
            status=PeriodStatus.OPEN,
        )
        db.get.return_value = period
        return period

    def test_no_journals_posted_when_nothing_to_revalue(self):
        from app.services.finance.gl.fx_revaluation import (
            FXRevaluationPreview,
            FXRevaluationResult,
            FXRevaluationService,
        )

        db = MagicMock()
        svc = FXRevaluationService(db)
        period_id = uuid4()
        empty_preview = FXRevaluationPreview(
            fiscal_period_id=period_id,
            period_end_date=date(2026, 1, 31),
            next_period_start_date=date(2026, 2, 1),
        )

        with patch.object(svc, "preview", return_value=empty_preview):
            result = svc.post(
                organization_id=uuid4(),
                fiscal_period_id=period_id,
                user_id=uuid4(),
            )

        assert isinstance(result, FXRevaluationResult)
        assert result.success is True
        assert result.period_end_journal_id is None
        assert result.reversal_journal_id is None
        assert "nothing to revalue" in result.message.lower()

    def test_re_run_requires_reason(self):
        from fastapi import HTTPException

        from app.services.finance.gl.fx_revaluation import (
            FXRevaluationPreview,
            FXRevaluationService,
        )

        db = MagicMock()
        svc = FXRevaluationService(db)
        period_id = uuid4()
        prior_pair = [uuid4(), uuid4()]
        preview = FXRevaluationPreview(
            fiscal_period_id=period_id,
            period_end_date=date(2026, 1, 31),
            next_period_start_date=date(2026, 2, 1),
            lines=[],  # doesn't matter; reason check fires before line check
            prior_run_exists=True,
            prior_journal_ids=prior_pair,
        )

        with patch.object(svc, "preview", return_value=preview):
            with pytest.raises(HTTPException) as exc:
                svc.post(
                    organization_id=uuid4(),
                    fiscal_period_id=period_id,
                    user_id=uuid4(),
                    reason=None,
                )

        assert exc.value.status_code == 400
        assert "reason" in exc.value.detail.lower()

    def test_reverses_prior_pair_then_posts_new_pair(self):
        """Re-run with reason: ReversalService.create_reversal called for
        each prior journal, then JournalService.create_journal called twice
        for the new pair."""
        from app.services.finance.gl.fx_revaluation import (
            FXRevaluationLine,
            FXRevaluationPreview,
            FXRevaluationService,
        )

        db = MagicMock()
        svc = FXRevaluationService(db)
        period_id = uuid4()
        org_id = uuid4()
        user_id = uuid4()
        prior_a, prior_b = uuid4(), uuid4()
        ar = uuid4()
        gain_acct = uuid4()
        loss_acct = uuid4()

        preview = FXRevaluationPreview(
            fiscal_period_id=period_id,
            period_end_date=date(2026, 1, 31),
            next_period_start_date=date(2026, 2, 1),
            lines=[
                FXRevaluationLine(
                    account_id=ar,
                    currency_code="USD",
                    closing_rate=Decimal("820"),
                    book_value_functional=Decimal("100"),
                    revalued_value_functional=Decimal("110"),
                    delta_functional=Decimal("10"),
                    is_gain=True,
                ),
            ],
            total_gain_functional=Decimal("10"),
            prior_run_exists=True,
            prior_journal_ids=[prior_a, prior_b],
        )

        # Mock created journals (returned by JournalService.create_journal)
        from types import SimpleNamespace
        created_pe = SimpleNamespace(
            journal_entry_id=uuid4(),
            journal_number="JE-FX-1",
        )
        created_rev = SimpleNamespace(
            journal_entry_id=uuid4(),
            journal_number="JE-FX-2",
        )

        with (
            patch.object(svc, "preview", return_value=preview),
            patch.object(svc, "_read_fx_account_ids", return_value=(gain_acct, loss_acct)),
            patch(
                "app.services.finance.gl.fx_revaluation.ReversalService.create_reversal"
            ) as rev_svc,
            patch(
                "app.services.finance.gl.fx_revaluation.JournalService.create_journal",
                side_effect=[created_pe, created_rev],
            ) as jrnl_svc,
            patch(
                "app.services.finance.gl.fx_revaluation.JournalService.post_journal"
            ) as post_jrnl,
        ):
            result = svc.post(
                organization_id=org_id,
                fiscal_period_id=period_id,
                user_id=user_id,
                reason="Closing rate was wrong",
            )

        # ReversalService called twice, once per prior journal
        assert rev_svc.call_count == 2
        # JournalService.create_journal called twice (period-end + reversal)
        assert jrnl_svc.call_count == 2
        # post_journal called twice
        assert post_jrnl.call_count == 2

        assert result.success is True
        assert result.period_end_journal_id == created_pe.journal_entry_id
        assert result.reversal_journal_id == created_rev.journal_entry_id
        assert set(result.reversed_prior_journal_ids) == {prior_a, prior_b}

    def test_rolls_back_when_next_period_missing(self):
        """If next_period_start_date is None on the preview, post must
        refuse before any journal is created."""
        from fastapi import HTTPException

        from app.services.finance.gl.fx_revaluation import (
            FXRevaluationLine,
            FXRevaluationPreview,
            FXRevaluationService,
        )

        db = MagicMock()
        svc = FXRevaluationService(db)
        period_id = uuid4()
        ar = uuid4()

        preview = FXRevaluationPreview(
            fiscal_period_id=period_id,
            period_end_date=date(2026, 1, 31),
            next_period_start_date=None,  # the trigger
            lines=[
                FXRevaluationLine(
                    account_id=ar,
                    currency_code="USD",
                    closing_rate=Decimal("820"),
                    book_value_functional=Decimal("100"),
                    revalued_value_functional=Decimal("110"),
                    delta_functional=Decimal("10"),
                    is_gain=True,
                )
            ],
            total_gain_functional=Decimal("10"),
        )

        with (
            patch.object(svc, "preview", return_value=preview),
            patch.object(svc, "_read_fx_account_ids", return_value=(uuid4(), uuid4())),
        ):
            with pytest.raises(HTTPException) as exc:
                svc.post(
                    organization_id=uuid4(),
                    fiscal_period_id=period_id,
                    user_id=uuid4(),
                )

        assert exc.value.status_code == 400
        assert "next" in exc.value.detail.lower()
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
poetry run pytest tests/ifrs/gl/test_fx_revaluation_service.py::TestPost -v
```

Expected: 4 failures.

- [ ] **Step 3: Implement `post`**

Add to `app/services/finance/gl/fx_revaluation.py`:

```python
# Add to top-of-file imports:
from uuid import uuid4 as _new_uuid

from app.services.finance.gl.journal import JournalService
from app.services.finance.gl.reversal import ReversalService


    def post(
        self,
        organization_id: UUID,
        fiscal_period_id: UUID,
        user_id: UUID,
        reason: str | None = None,
    ) -> FXRevaluationResult:
        """Atomic post of period-end FX revaluation pair.

        On re-run (prior pair exists), reverses both prior journals first
        — reason is required.

        Raises HTTPException(400) on:
          - period not OPEN/REOPENED
          - FX accounts unconfigured
          - prior pair exists but reason missing
          - next period is missing or closed
        """
        # Re-run preview inside the transaction so state is current
        preview = self.preview(organization_id, fiscal_period_id)

        if not preview.lines:
            return FXRevaluationResult(
                success=True,
                message="Nothing to revalue: no foreign-currency monetary "
                "items found in scope.",
            )

        if preview.prior_run_exists and not (reason and reason.strip()):
            raise HTTPException(
                status_code=400,
                detail="Re-running FX revaluation requires a reason for "
                "replacing the prior revaluation.",
            )

        if preview.next_period_start_date is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"FX revaluation cannot post: no fiscal period covers "
                    f"the day after {preview.period_end_date}. The day-1 "
                    f"reversal needs a target period."
                ),
            )

        gain_account_id, loss_account_id = self._read_fx_account_ids(
            organization_id
        )

        # Reverse prior pair if re-run
        reversed_ids: list[UUID] = []
        if preview.prior_run_exists:
            for prior_id in preview.prior_journal_ids:
                ReversalService.create_reversal(
                    db=self.db,
                    organization_id=organization_id,
                    original_journal_id=prior_id,
                    # Reverse on the period_end_date so the reversal
                    # lands within an open period.
                    reversal_date=preview.period_end_date,
                    created_by_user_id=user_id,
                    reason=reason,
                    auto_post=True,
                )
                reversed_ids.append(prior_id)

        # Build and post the new period-end journal
        correlation_id = str(_new_uuid())
        period_end_input = self._build_journal_input(
            lines=preview.lines,
            posting_date=preview.period_end_date,
            description=(
                f"FX revaluation as at {preview.period_end_date}. "
                f"Rates used: "
                + ", ".join(
                    f"{ccy}={rate}" for ccy, rate in sorted(preview.rates_used.items())
                )
            ),
            fx_gain_account_id=gain_account_id,
            fx_loss_account_id=loss_account_id,
            correlation_id=correlation_id,
        )
        period_end_journal = JournalService.create_journal(
            db=self.db,
            organization_id=organization_id,
            input=period_end_input,
            created_by_user_id=user_id,
        )
        JournalService.post_journal(
            db=self.db,
            organization_id=organization_id,
            journal_entry_id=period_end_journal.journal_entry_id,
            posted_by_user_id=user_id,
        )

        # Build and post the day-1 reversal
        reversal_input = self._build_reversal_input(
            period_end_input=period_end_input,
            reversal_date=preview.next_period_start_date,
            original_journal_number=period_end_journal.journal_number,
        )
        reversal_journal = JournalService.create_journal(
            db=self.db,
            organization_id=organization_id,
            input=reversal_input,
            created_by_user_id=user_id,
        )
        JournalService.post_journal(
            db=self.db,
            organization_id=organization_id,
            journal_entry_id=reversal_journal.journal_entry_id,
            posted_by_user_id=user_id,
        )

        return FXRevaluationResult(
            success=True,
            period_end_journal_id=period_end_journal.journal_entry_id,
            reversal_journal_id=reversal_journal.journal_entry_id,
            reversed_prior_journal_ids=reversed_ids,
            total_gain_functional=preview.total_gain_functional,
            total_loss_functional=preview.total_loss_functional,
            message=(
                f"FX revaluation posted: gain ₦{preview.total_gain_functional}, "
                f"loss ₦{preview.total_loss_functional} across "
                f"{len(preview.rates_used)} currencies."
            ),
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
poetry run pytest tests/ifrs/gl/test_fx_revaluation_service.py::TestPost -v
```

Expected: 4 passes.

- [ ] **Step 5: Run the full service test suite to confirm no regressions**

```bash
poetry run pytest tests/ifrs/gl/test_fx_revaluation_service.py -v
```

Expected: ~25 passes.

- [ ] **Step 6: Commit**

```bash
git add app/services/finance/gl/fx_revaluation.py tests/ifrs/gl/test_fx_revaluation_service.py
git commit -m "FXRevaluationService.post — atomic with re-run reversal"
```

---

## Task 14: `FXRevaluationWebService` — preview_response and post_response

**Files:**
- Create: `app/services/finance/gl/web/fx_revaluation_web.py`
- Create: `tests/ifrs/gl/test_fx_revaluation_web_service.py`

Web-service wrapper that builds template context for the preview page and handles the form submission for the post route.

- [ ] **Step 1: Write the failing tests**

Create `tests/ifrs/gl/test_fx_revaluation_web_service.py`:

```python
"""Tests for FXRevaluationWebService."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4


class TestPreviewResponse:
    def test_preview_response_returns_template_context(self):
        from app.services.finance.gl.fx_revaluation import (
            FXRevaluationLine,
            FXRevaluationPreview,
        )
        from app.services.finance.gl.web.fx_revaluation_web import (
            FXRevaluationWebService,
        )

        db = MagicMock()
        svc = FXRevaluationWebService(db)
        period_id = uuid4()
        org_id = uuid4()

        ar = uuid4()
        preview = FXRevaluationPreview(
            fiscal_period_id=period_id,
            period_end_date=date(2026, 1, 31),
            next_period_start_date=date(2026, 2, 1),
            lines=[
                FXRevaluationLine(
                    account_id=ar,
                    currency_code="USD",
                    closing_rate=Decimal("820"),
                    book_value_functional=Decimal("100"),
                    revalued_value_functional=Decimal("110"),
                    delta_functional=Decimal("10"),
                    is_gain=True,
                )
            ],
            total_gain_functional=Decimal("10"),
            rates_used={"USD": Decimal("820")},
            warnings=[],
            prior_run_exists=False,
        )

        with patch(
            "app.services.finance.gl.web.fx_revaluation_web.FXRevaluationService"
        ) as MockSvc:
            instance = MockSvc.return_value
            instance.preview.return_value = preview

            ctx = svc.preview_response(
                organization_id=org_id, fiscal_period_id=period_id
            )

        assert ctx["preview"] is preview
        assert ctx["period_id"] == period_id
        assert ctx["prior_run_exists"] is False
        assert ctx["lines"][0].account_id == ar


class TestPostResponse:
    def test_post_response_returns_success_and_result(self):
        from app.services.finance.gl.fx_revaluation import FXRevaluationResult
        from app.services.finance.gl.web.fx_revaluation_web import (
            FXRevaluationWebService,
        )

        db = MagicMock()
        svc = FXRevaluationWebService(db)
        period_id = uuid4()
        org_id = uuid4()
        user_id = uuid4()

        result = FXRevaluationResult(
            success=True,
            period_end_journal_id=uuid4(),
            reversal_journal_id=uuid4(),
            total_gain_functional=Decimal("100"),
            message="FX revaluation posted",
        )

        with patch(
            "app.services.finance.gl.web.fx_revaluation_web.FXRevaluationService"
        ) as MockSvc:
            instance = MockSvc.return_value
            instance.post.return_value = result

            res = svc.post_response(
                organization_id=org_id,
                fiscal_period_id=period_id,
                user_id=user_id,
                reason=None,
            )

        assert res.success is True
        assert res.period_end_journal_id is not None
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
poetry run pytest tests/ifrs/gl/test_fx_revaluation_web_service.py -v
```

Expected: 2 failures (`ModuleNotFoundError`).

- [ ] **Step 3: Implement `FXRevaluationWebService`**

Create `app/services/finance/gl/web/__init__.py` if it doesn't already exist (empty file). Create `app/services/finance/gl/web/fx_revaluation_web.py`:

```python
"""Web service wrapper for FXRevaluationService."""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy.orm import Session

from app.services.finance.gl.fx_revaluation import (
    FXRevaluationResult,
    FXRevaluationService,
)

logger = logging.getLogger(__name__)


class FXRevaluationWebService:
    """Builds template context and handles form submission for FX
    revaluation pages."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def preview_response(
        self,
        organization_id: UUID,
        fiscal_period_id: UUID,
    ) -> dict:
        """Build template context for the preview page."""
        svc = FXRevaluationService(self.db)
        preview = svc.preview(organization_id, fiscal_period_id)
        return {
            "preview": preview,
            "period_id": fiscal_period_id,
            "prior_run_exists": preview.prior_run_exists,
            "lines": preview.lines,
            "warnings": preview.warnings,
            "rates_used": preview.rates_used,
            "total_gain": preview.total_gain_functional,
            "total_loss": preview.total_loss_functional,
            "next_period_start_date": preview.next_period_start_date,
        }

    def post_response(
        self,
        organization_id: UUID,
        fiscal_period_id: UUID,
        user_id: UUID,
        reason: str | None,
    ) -> FXRevaluationResult:
        """Invoke service.post and return the result for the route to
        translate to flash + redirect."""
        svc = FXRevaluationService(self.db)
        return svc.post(
            organization_id=organization_id,
            fiscal_period_id=fiscal_period_id,
            user_id=user_id,
            reason=reason,
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
poetry run pytest tests/ifrs/gl/test_fx_revaluation_web_service.py -v
```

Expected: 2 passes.

- [ ] **Step 5: Commit**

```bash
git add app/services/finance/gl/web/__init__.py app/services/finance/gl/web/fx_revaluation_web.py tests/ifrs/gl/test_fx_revaluation_web_service.py
git commit -m "FXRevaluationWebService for period-detail UX"
```

---

## Task 15: Routes — `GET` and `POST` `/finance/gl/periods/{period_id}/fx-revaluation`

**Files:**
- Modify: `app/web/finance/gl.py` (add routes to existing router)

- [ ] **Step 1: Locate the existing GL router**

```bash
grep -n "router\s*=\s*APIRouter\|@router\." app/web/finance/gl.py | head -10
```

You'll see the existing router definition and route patterns. Match the existing style (auth dependency, base_context helper, etc.).

- [ ] **Step 2: Add the GET (preview) route**

Append to `app/web/finance/gl.py`:

```python
# Imports to add at the top of the file (if not already there):
from uuid import UUID
from app.services.finance.gl.web.fx_revaluation_web import FXRevaluationWebService


@router.get(
    "/periods/{period_id}/fx-revaluation",
    response_class=HTMLResponse,
)
def fx_revaluation_preview(
    period_id: UUID,
    request: Request,
    auth: WebAuthContext = Depends(require_finance_admin),
    db: Session = Depends(get_db),
):
    """Preview FX revaluation for a fiscal period — read-only."""
    context = base_context(request, auth, "FX Revaluation Preview", "finance")
    ws = FXRevaluationWebService(db)
    context.update(ws.preview_response(auth.organization_id, period_id))
    return templates.TemplateResponse(
        request,
        "finance/gl/period_fx_revaluation.html",
        context,
    )


@router.post("/periods/{period_id}/fx-revaluation")
async def fx_revaluation_post(
    period_id: UUID,
    request: Request,
    auth: WebAuthContext = Depends(require_finance_admin),
    db: Session = Depends(get_db),
):
    """Post FX revaluation pair atomically. Re-runs require a reason."""
    form = await request.form()
    reason = (form.get("reason") or "").strip() or None

    ws = FXRevaluationWebService(db)
    result = ws.post_response(
        organization_id=auth.organization_id,
        fiscal_period_id=period_id,
        user_id=auth.user_id,
        reason=reason,
    )
    db.commit()

    # Redirect back to period detail with a flash message
    return RedirectResponse(
        url=f"/finance/gl/periods/{period_id}?fx_msg={result.message}",
        status_code=303,
    )
```

If `RedirectResponse` is not already imported, add `from fastapi.responses import RedirectResponse, HTMLResponse` to the imports.

- [ ] **Step 3: Verify routes register without import errors**

```bash
poetry run python -c "from app.main import app; routes = [r.path for r in app.routes]; print([r for r in routes if 'fx-revaluation' in r])"
```

Expected: prints both routes.

- [ ] **Step 4: Commit**

```bash
git add app/web/finance/gl.py
git commit -m "Routes for FX revaluation preview and post"
```

---

## Task 16: Templates — `period_fx_revaluation.html` + button on `period_detail.html`

**Files:**
- Create: `templates/finance/gl/period_fx_revaluation.html`
- Modify: `templates/finance/gl/period_detail.html` (add button)

- [ ] **Step 1: Create the preview template**

Create `templates/finance/gl/period_fx_revaluation.html`:

```html
{% extends "finance/base_finance.html" %}
{% from "components/_topbar.html" import topbar %}
{% from "components/_status_badge.html" import status_badge %}

{% block content %}
{% call(breadcrumbs, actions) topbar("FX Revaluation Preview", accent="teal") %}
  {% call(bc) breadcrumbs %}
    {{ bc("Finance", "/finance") }}
    {{ bc("General Ledger", "/finance/gl") }}
    {{ bc("Periods", "/finance/gl/periods") }}
    {{ bc("FX Revaluation") }}
  {% endcall %}
{% endcall %}

<div class="space-y-6">

  {% if warnings %}
  <div class="bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 rounded-xl p-4">
    <strong class="text-amber-700 dark:text-amber-400">Warnings</strong>
    <ul class="text-amber-600 dark:text-amber-300 text-sm mt-2 list-disc pl-5">
      {% for w in warnings %}<li>{{ w }}</li>{% endfor %}
    </ul>
  </div>
  {% endif %}

  {% if prior_run_exists %}
  <div class="bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded-xl p-4">
    <strong class="text-blue-700 dark:text-blue-400">Prior revaluation exists</strong>
    <p class="text-blue-600 dark:text-blue-300 text-sm mt-1">
      Posting this revaluation will reverse the prior pair before posting the new pair.
      A reason is required.
    </p>
  </div>
  {% endif %}

  <div class="card">
    <div class="card-header">
      <h2 class="text-lg font-semibold">Proposed revaluation lines</h2>
      <p class="text-sm text-slate-500">
        Period end: {{ preview.period_end_date.strftime('%d %b %Y') }} ·
        Reversal: {{ preview.next_period_start_date.strftime('%d %b %Y') if preview.next_period_start_date else '—' }}
      </p>
    </div>

    <div class="table-container">
      <table class="table">
        <thead>
          <tr>
            <th scope="col">Account</th>
            <th scope="col">Currency</th>
            <th scope="col" class="text-right">Closing rate</th>
            <th scope="col" class="text-right">Book value (₦)</th>
            <th scope="col" class="text-right">Revalued (₦)</th>
            <th scope="col" class="text-right">Delta (₦)</th>
            <th scope="col" class="text-center">Result</th>
          </tr>
        </thead>
        <tbody>
        {% for line in lines %}
          <tr>
            <td class="font-mono">{{ line.account_id }}</td>
            <td>{{ line.currency_code }}</td>
            <td class="text-right font-mono tabular-nums">{{ line.closing_rate }}</td>
            <td class="text-right font-mono tabular-nums">{{ "{:,.2f}".format(line.book_value_functional) }}</td>
            <td class="text-right font-mono tabular-nums">{{ "{:,.2f}".format(line.revalued_value_functional) }}</td>
            <td class="text-right font-mono tabular-nums {{ 'text-emerald-700' if line.is_gain else 'text-rose-700' }}">
              {{ "{:,.2f}".format(line.delta_functional) }}
            </td>
            <td class="text-center">{{ status_badge('GAIN' if line.is_gain else 'LOSS', 'sm') }}</td>
          </tr>
        {% else %}
          <tr><td colspan="7" class="text-center text-slate-500 p-6">No foreign-currency monetary items in scope.</td></tr>
        {% endfor %}
        </tbody>
        <tfoot>
          <tr>
            <td colspan="5" class="text-right font-semibold">Total gain</td>
            <td class="text-right font-mono tabular-nums text-emerald-700">{{ "{:,.2f}".format(total_gain) }}</td>
            <td></td>
          </tr>
          <tr>
            <td colspan="5" class="text-right font-semibold">Total loss</td>
            <td class="text-right font-mono tabular-nums text-rose-700">{{ "{:,.2f}".format(total_loss) }}</td>
            <td></td>
          </tr>
        </tfoot>
      </table>
    </div>
  </div>

  {% if lines %}
  <form method="POST" action="/finance/gl/periods/{{ period_id }}/fx-revaluation" class="card space-y-4">
    {{ request.state.csrf_form | safe }}

    {% if prior_run_exists %}
    <div>
      <label class="form-label">Reason for re-running <span class="text-rose-500">*</span></label>
      <textarea name="reason" class="form-input" rows="3" required
                placeholder="e.g., Closing rate was wrong"></textarea>
    </div>
    {% endif %}

    <div class="flex gap-3 justify-end">
      <a href="/finance/gl/periods/{{ period_id }}" class="btn btn-secondary">Cancel</a>
      <button type="submit" class="btn btn-primary">
        {% if prior_run_exists %}Replace prior revaluation{% else %}Post FX revaluation{% endif %}
      </button>
    </div>
  </form>
  {% endif %}

</div>
{% endblock %}
```

- [ ] **Step 2: Add the "Run FX Revaluation" button to `period_detail.html`**

Find the topbar's `actions` block in `templates/finance/gl/period_detail.html`. Add a link styled as a secondary button:

```html
<a href="/finance/gl/periods/{{ period.fiscal_period_id }}/fx-revaluation"
   class="btn btn-secondary">
  Run FX Revaluation
</a>
```

Place it in the existing actions caller block alongside "Close period" or similar buttons. If the period_detail.html structure doesn't yet have an actions block, model the addition on a sibling page (e.g., `journals.html`) that does.

- [ ] **Step 3: Smoke-test the preview page renders**

Start the local server (or rely on the test harness) and visit `/finance/gl/periods/<some-period-id>/fx-revaluation`. Expected: page renders without 500. (If you don't have a local data set with foreign-currency items, the page will show the empty-state row — that's correct.)

- [ ] **Step 4: Commit**

```bash
git add templates/finance/gl/period_fx_revaluation.html templates/finance/gl/period_detail.html
git commit -m "FX revaluation preview template + period-detail button"
```

---

## Task 17: Final test sweep across affected areas

- [ ] **Step 1: Run the full GL test suite**

```bash
poetry run pytest tests/ifrs/gl/ -q
```

Expected: all green. Includes the new `test_fx_revaluation_service.py` (~25 tests) and `test_fx_revaluation_web_service.py` (~2 tests), plus pre-existing GL coverage.

- [ ] **Step 2: Run the full sync + import_export sweep**

```bash
poetry run pytest tests/sync/ tests/ifrs/sync/ tests/ifrs/import_export/ tests/services/test_mono_sync.py -q
```

Expected: all green. Confirms the prior P0 bundle still passes.

- [ ] **Step 3: Run a broader sanity sweep on services + web**

```bash
poetry run pytest tests/services/ tests/ifrs/ -q
```

Expected: all green.

- [ ] **Step 4: Lint and type-check the new files**

```bash
poetry run ruff check app/services/finance/gl/fx_revaluation.py app/services/finance/gl/web/fx_revaluation_web.py app/web/finance/gl.py
poetry run mypy app/services/finance/gl/fx_revaluation.py app/services/finance/gl/web/fx_revaluation_web.py
```

Expected: clean. Fix any issues that surface; the `enforce-quality.sh` Stop hook will rerun and is non-blocking but informative.

- [ ] **Step 5: Final summary commit (if there's anything dangling)**

If lint/format auto-fixed anything during the sweep:

```bash
git add -p   # review and stage cleanups
git commit -m "Lint/format pass on FX revaluation"
```

---

## Plan summary

| | |
|---|---|
| Tasks | 17 |
| Estimated total commits | 16-17 (one per task, plus an optional final lint commit) |
| New files | 6 (service, web service, template, migration, two test files) |
| Modified files | 4 (`SettingDomain`, settings_spec, GL routes, period detail template) |
| Estimated total LOC added | ~1,400 (service ~450, web service ~80, templates ~150, tests ~700, migration ~15) |
| Estimated effort | 1-2 weeks of focused engineering |

The plan terminates with a working manual UX path: admin sets the two FX accounts via existing `/admin/settings`, navigates to a fiscal period, clicks "Run FX Revaluation", reviews the per-currency preview, and confirms. Re-runs require a reason. All journal posting is atomic across the period-end + day-1 reversal pair, with prior pairs reversed (not voided) on re-run for full audit trail.
