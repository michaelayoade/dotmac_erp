"""Tests for the FX revaluation service.

This file is grown task-by-task per the FX revaluation implementation plan
(``docs/superpowers/plans/2026-05-09-fx-revaluation.md``).

Task 1: Confirm the ``SettingDomain`` enum exposes a ``gl`` value so GL-level
configuration (FX revaluation, period-close prerequisites, etc.) can be stored
under the same domain-settings infrastructure used by other modules.

Task 2: Register settings specs ``fx_gain_account_id`` and ``fx_loss_account_id``
under the ``gl`` domain so the FX revaluation service can resolve them via the
canonical ``settings_spec.get_spec`` / ``resolve_value`` accessors.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.models.domain_settings import DomainSetting, SettingDomain, SettingValueType
from app.services.settings_spec import SettingSpec, get_spec


class TestSettingDomainGL:
    """SettingDomain must include a ``gl`` value for GL-scoped settings."""

    def test_gl_domain_exists(self) -> None:
        assert hasattr(SettingDomain, "gl"), (
            "SettingDomain.gl is required so GL-level settings (e.g. FX "
            "revaluation config) can be persisted via DomainSetting."
        )

    def test_gl_domain_value_is_lowercase_string(self) -> None:
        # The Postgres enum stores the lowercase string value, and other
        # domains in this enum follow the same convention (auth, banking, ...).
        assert SettingDomain.gl.value == "gl"

    def test_gl_domain_round_trips_from_value(self) -> None:
        # Confirm we can resolve the enum member from its string value, the
        # same way the settings spec / loaders do at runtime.
        assert SettingDomain("gl") is SettingDomain.gl


class TestFXAccountSettingSpecs:
    """Register two SettingSpec entries the FX revaluation service consumes.

    Note: The plan (lines 150-175) describes a ``SettingSpec`` shape with an
    explicit ``scope=SettingScope.ORG_SPECIFIC`` field, but the codebase's
    actual ``SettingSpec`` dataclass has no ``scope`` attribute — scope is a
    per-row concept on ``DomainSetting``. We therefore test the *real* fields
    the dataclass exposes (``value_type``, ``default``) while still capturing
    the plan's intent: string-typed (UUID-as-string), default ``""`` (so an
    unset value is distinguishable from a configured empty value at the
    service layer).
    """

    def test_fx_gain_account_spec_is_registered(self) -> None:
        spec = get_spec(SettingDomain.gl, "fx_gain_account_id")
        assert spec is not None, (
            "fx_gain_account_id must be registered under SettingDomain.gl so "
            "FXRevaluationService can resolve it via the canonical accessor."
        )
        assert isinstance(spec, SettingSpec)

    def test_fx_gain_account_spec_is_string_typed_with_empty_default(
        self,
    ) -> None:
        spec = get_spec(SettingDomain.gl, "fx_gain_account_id")
        assert spec is not None
        # UUID-as-string per plan.
        assert spec.value_type == SettingValueType.string
        # Empty string default — the service treats "" as "unconfigured".
        assert spec.default == ""

    def test_fx_loss_account_spec_is_registered(self) -> None:
        spec = get_spec(SettingDomain.gl, "fx_loss_account_id")
        assert spec is not None, (
            "fx_loss_account_id must be registered under SettingDomain.gl so "
            "FXRevaluationService can resolve it via the canonical accessor."
        )
        assert isinstance(spec, SettingSpec)

    def test_fx_loss_account_spec_is_string_typed_with_empty_default(
        self,
    ) -> None:
        spec = get_spec(SettingDomain.gl, "fx_loss_account_id")
        assert spec is not None
        assert spec.value_type == SettingValueType.string
        assert spec.default == ""


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


class TestReadFxAccountIds:
    """Hard-fail when fx_gain_account_id or fx_loss_account_id is unset.

    The service queries DomainSetting directly (filtered by
    organization_id) via ``db.scalar(select(DomainSetting)...)``. We stub
    ``db.scalar`` rather than the resolver so the multi-tenant filter is
    actually exercised — patching a global accessor would mask the bug
    these tests exist to prevent.
    """

    @staticmethod
    def _row(value_text: str) -> MagicMock:
        """Build a fake DomainSetting row with a populated value_text."""
        return MagicMock(spec=DomainSetting, value_text=value_text)

    def test_raises_400_when_gain_account_unset(self):
        from fastapi import HTTPException

        from app.services.finance.gl.fx_revaluation import FXRevaluationService

        db = MagicMock()
        # Order: gain query first, then loss query.
        db.scalar.side_effect = [None, self._row(str(uuid4()))]
        svc = FXRevaluationService(db)

        with pytest.raises(HTTPException) as exc:
            svc._read_fx_account_ids(uuid4())

        assert exc.value.status_code == 400
        assert "fx_gain_account_id" in exc.value.detail.lower()

    def test_raises_400_when_loss_account_unset(self):
        from fastapi import HTTPException

        from app.services.finance.gl.fx_revaluation import FXRevaluationService

        db = MagicMock()
        db.scalar.side_effect = [self._row(str(uuid4())), None]
        svc = FXRevaluationService(db)

        with pytest.raises(HTTPException) as exc:
            svc._read_fx_account_ids(uuid4())

        assert exc.value.status_code == 400
        assert "fx_loss_account_id" in exc.value.detail.lower()

    def test_returns_uuid_pair_when_both_set(self):
        from app.services.finance.gl.fx_revaluation import FXRevaluationService

        gain_id = uuid4()
        loss_id = uuid4()
        db = MagicMock()
        db.scalar.side_effect = [
            self._row(str(gain_id)),
            self._row(str(loss_id)),
        ]
        svc = FXRevaluationService(db)

        result_gain, result_loss = svc._read_fx_account_ids(uuid4())

        assert result_gain == gain_id
        assert result_loss == loss_id

    def test_different_orgs_get_different_account_ids(self):
        """Multi-tenant guard: org A and org B must resolve to *their own*
        DomainSetting rows, not a shared global row.

        We stub ``db.scalar`` to inspect each select()'s WHERE clause for
        the ``organization_id == <org>`` predicate and return that org's
        configured row. If the service ever drops the org_id filter, both
        orgs would receive whichever row the stub returned first — and
        this test would fail.
        """
        from app.services.finance.gl.fx_revaluation import FXRevaluationService

        org_a = uuid4()
        org_b = uuid4()

        org_a_gain = uuid4()
        org_a_loss = uuid4()
        org_b_gain = uuid4()
        org_b_loss = uuid4()

        rows_by_org_and_key: dict[tuple, MagicMock] = {
            (org_a, "fx_gain_account_id"): self._row(str(org_a_gain)),
            (org_a, "fx_loss_account_id"): self._row(str(org_a_loss)),
            (org_b, "fx_gain_account_id"): self._row(str(org_b_gain)),
            (org_b, "fx_loss_account_id"): self._row(str(org_b_loss)),
        }

        def fake_scalar(stmt):
            # Inspect the compiled SELECT's WHERE parameters to pick the
            # right row. We stringify with literal_binds so UUID + string
            # values appear in-line and we can match them.
            compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
            for (org_id, key), row in rows_by_org_and_key.items():
                if str(org_id) in compiled and key in compiled:
                    return row
            return None

        db = MagicMock()
        db.scalar.side_effect = fake_scalar

        svc = FXRevaluationService(db)

        gain_a, loss_a = svc._read_fx_account_ids(org_a)
        gain_b, loss_b = svc._read_fx_account_ids(org_b)

        assert gain_a == org_a_gain
        assert loss_a == org_a_loss
        assert gain_b == org_b_gain
        assert loss_b == org_b_loss
        # The crucial assertion: tenants do NOT share account IDs.
        assert gain_a != gain_b
        assert loss_a != loss_b


class TestDiscoverArOpenInvoices:
    """Returns AR invoices in non-functional currency with positive
    balance_due as-of period_end_date.

    Plan-vs-conftest reconciliation: the plan's test data assumes
    functional currency is NGN (so USD invoices are non-functional and
    in scope). The shared MockSettings in tests/conftest.py hardcodes
    ``default_functional_currency_code = "USD"`` for unrelated reasons.
    These tests therefore monkey-patch ``app_settings`` so the plan's
    NGN-functional assumption holds — without that patch, the service's
    "defense in depth" Python-side filter would (correctly) drop every
    USD invoice the stub returns.
    """

    @pytest.fixture(autouse=True)
    def _force_functional_ngn(self, monkeypatch):
        from app.services.finance.gl import fx_revaluation as mod

        monkeypatch.setattr(mod.app_settings, "default_functional_currency_code", "NGN")

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


class TestDiscoverApOpenInvoices:
    """Returns AP supplier invoices in non-functional currency with
    positive balance_due as-of period_end_date.

    Mirrors TestDiscoverArOpenInvoices: same NGN-functional fixture, same
    test shape, but reads SupplierInvoice + ap_control_account_id. Two
    near-identical methods is intentional — a generic helper would
    obscure the per-module model contract.
    """

    @pytest.fixture(autouse=True)
    def _force_functional_ngn(self, monkeypatch):
        from app.services.finance.gl import fx_revaluation as mod

        monkeypatch.setattr(mod.app_settings, "default_functional_currency_code", "NGN")

    def _make_invoice(self, **overrides):
        from types import SimpleNamespace

        defaults = {
            "invoice_id": uuid4(),
            "ap_control_account_id": uuid4(),
            "currency_code": "USD",
            "exchange_rate": Decimal("750.0"),
            "total_amount": Decimal("1000"),
            "amount_paid": Decimal("400"),
            "status": MagicMock(value="POSTED"),
        }
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

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

        result = svc._discover_ap_open_invoices(
            organization_id=uuid4(),
            period_end_date=date(2026, 1, 31),
        )

        assert len(result) == 1
        invoice_id, control_account_id, currency, posting_rate, balance_due = result[0]
        assert invoice_id == invoice.invoice_id
        assert control_account_id == invoice.ap_control_account_id
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

        result = svc._discover_ap_open_invoices(
            organization_id=uuid4(),
            period_end_date=date(2026, 1, 31),
        )

        assert result == []

    def test_excludes_approved_status_unbooked_invoices(self):
        """REGRESSION (P1-2): APPROVED supplier invoices are NOT
        gl_impacting — they have not yet been posted to the GL and so
        carry no booked exposure to revalue. Including them would
        produce FX journals against a phantom liability.

        The fix uses ``SupplierInvoiceStatus.gl_impacting()`` (POSTED,
        PARTIALLY_PAID, PAID) at the SQL filter; this test asserts the
        SQL ``status.in_(...)`` clause's set membership matches the
        gl-impacting set, not the outstanding set.
        """
        from app.models.finance.ap.supplier_invoice import SupplierInvoiceStatus
        from app.services.finance.gl.fx_revaluation import FXRevaluationService

        db = MagicMock()
        svc = FXRevaluationService(db)

        captured_statements: list = []

        def capture_scalars(stmt):
            captured_statements.append(stmt)
            mock = MagicMock()
            mock.all.return_value = []
            return mock

        db.scalars.side_effect = capture_scalars

        result = svc._discover_ap_open_invoices(
            organization_id=uuid4(),
            period_end_date=date(2026, 1, 31),
        )

        assert result == []
        assert len(captured_statements) == 1

        # Inspect the compiled SELECT to confirm the status filter uses
        # gl_impacting (POSTED, PARTIALLY_PAID, PAID), and explicitly NOT
        # the unbooked APPROVED status.
        compiled = str(
            captured_statements[0].compile(compile_kwargs={"literal_binds": True})
        )
        # APPROVED must not appear in the IN-clause (it would be a bug)
        assert "'APPROVED'" not in compiled, (
            f"APPROVED status leaked into FX revaluation discovery: {compiled}"
        )
        # The expected gl-impacting statuses must be present
        for status in SupplierInvoiceStatus.gl_impacting():
            assert f"'{status.value}'" in compiled, (
                f"Expected gl-impacting status {status.value} in: {compiled}"
            )


class TestDiscoverBankBalances:
    """Active bank accounts in non-functional currency with non-zero balance.

    Plan-vs-conftest reconciliation: same NGN-functional autouse fixture
    used by the AR/AP discovery test classes. Conftest defaults the
    functional currency to USD; production defaults to NGN; the plan's
    test data assumes NGN, so we patch ``app_settings`` here for parity.
    """

    @pytest.fixture(autouse=True)
    def _force_functional_ngn(self, monkeypatch):
        from app.services.finance.gl import fx_revaluation as mod

        monkeypatch.setattr(mod.app_settings, "default_functional_currency_code", "NGN")

    def _make_account(self, **overrides):
        from types import SimpleNamespace

        defaults = {
            "bank_account_id": uuid4(),
            "gl_account_id": uuid4(),
            # Required so _resolve_bank_balance can pass org_id through to
            # _compute_balance_from_journals — the production-code signature
            # adds organization_id (multi-tenant safety) beyond what the
            # plan's original signature called for.
            "organization_id": uuid4(),
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


class TestLookupClosingRates:
    """Resolve closing rates for a set of currencies; warn on misses.

    NOTE — adapted from plan Task 8: the plan assumed ``FXService.lookup_spot_rate``
    returns ``Decimal | None``, but verification of
    ``app/services/finance/platform/fx.py`` shows it is a ``@staticmethod``
    returning a ``dict`` with key ``"rate"`` holding either a stringified
    rate or ``None`` (when no rate is configured). The tests below patch
    that real shape; the production code unwraps ``result["rate"]``.
    """

    def test_returns_rates_for_known_currencies(self):
        from app.services.finance.gl.fx_revaluation import FXRevaluationService

        db = MagicMock()
        svc = FXRevaluationService(db)

        rate_map = {
            "USD": {"rate": "820", "effective_date": "2026-01-31", "source": "MANUAL"},
            "GBP": {"rate": "1050", "effective_date": "2026-01-31", "source": "MANUAL"},
        }

        with patch(
            "app.services.finance.gl.fx_revaluation.FXService.lookup_spot_rate",
            side_effect=lambda _db, _org, ccy, _date: rate_map[ccy],
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

        def _fake_lookup(_db, _org, ccy, _date):
            if ccy == "USD":
                return {
                    "rate": "820",
                    "effective_date": "2026-01-31",
                    "source": "MANUAL",
                }
            return {"rate": None, "message": f"No rate found for NGN/{ccy}"}

        with patch(
            "app.services.finance.gl.fx_revaluation.FXService.lookup_spot_rate",
            side_effect=_fake_lookup,
        ):
            rates, warnings = svc._lookup_closing_rates(
                organization_id=uuid4(),
                currencies={"USD", "EUR"},
                period_end_date=date(2026, 1, 31),
            )

        assert rates == {"USD": Decimal("820")}
        assert any("EUR" in w for w in warnings)


class TestComputeRevaluationLines:
    """Pure compute: items + rates → list[FXRevaluationLine].

    Plan-vs-implementation reconciliation: the plan's signature reads
    ``period_end_date`` from a magic instance attribute
    (``self._period_end_for_compute``) and calls ``_compute_balance_from_journals``
    with two args. Both are bugs:

    1. The instance-attribute hack is the "pass parameters via instance state"
       anti-pattern — it forces callers to mutate ``self`` before calling a
       compute method, which fights the type system and breaks under any
       parallel use.
    2. ``_compute_balance_from_journals`` was tightened in Task 7 to require
       ``organization_id`` as the third parameter (multi-tenant safety —
       ``JournalEntryLine`` has no org_id; we filter via the joined
       ``JournalEntry``).

    We therefore call ``_compute_revaluation_lines`` with explicit
    ``organization_id`` and ``period_end_date`` keyword arguments. The cash
    test's patch signature accepts the third positional ``organization_id``
    argument so the production code's call site is exercised correctly.
    """

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
            organization_id=uuid4(),
            period_end_date=date(2026, 1, 31),
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
        assert line.is_liability is False  # AR = asset

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
            organization_id=uuid4(),
            period_end_date=date(2026, 1, 31),
        )

        line = lines[0]
        assert line.delta_functional == Decimal("70000")  # 820000 - 750000
        assert line.is_gain is False  # liability up = loss
        assert line.is_liability is True  # AP = liability

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
            organization_id=uuid4(),
            period_end_date=date(2026, 1, 31),
        )

        assert lines == []

    def test_cash_revaluation_uses_current_book_from_journals(self):
        """Bank balance: $5,000 at posting rate captured per-receipt is
        irrelevant; we revalue the current book value in NGN against the
        closing-rate equivalent.

        The patch accepts the same positional args the production code
        passes: ``(gl_account_id, period_end_date, organization_id)``. If
        the production code drops org_id from this call, the patch's
        signature would not bind correctly — exposing the regression.
        """
        from app.services.finance.gl.fx_revaluation import FXRevaluationService

        db = MagicMock()
        svc = FXRevaluationService(db)
        gl_id = uuid4()
        org_id = uuid4()
        period_end = date(2026, 1, 31)

        captured_calls: list[tuple] = []

        def fake_balance(gl_account_id, as_of_date, organization_id):
            captured_calls.append((gl_account_id, as_of_date, organization_id))
            return Decimal("3700000")

        with patch.object(
            svc, "_compute_balance_from_journals", side_effect=fake_balance
        ):
            lines = svc._compute_revaluation_lines(
                ar_items=[],
                ap_items=[],
                cash_items=[(uuid4(), gl_id, "USD", None, Decimal("5000"))],
                rates={"USD": Decimal("820")},
                organization_id=org_id,
                period_end_date=period_end,
            )

        line = lines[0]
        assert line.revalued_value_functional == Decimal("4100000")  # 5000*820
        assert line.book_value_functional == Decimal("3700000")
        assert line.delta_functional == Decimal("400000")
        assert line.is_gain is True
        assert line.is_liability is False  # cash = asset
        # The compute helper must be called with org_id + period_end_date,
        # not with magic instance state or today's date.
        assert captured_calls == [(gl_id, period_end, org_id)]


class TestAggregatePerAccountCurrency:
    """Sum deltas per (account_id, currency_code)."""

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
            is_liability=False,
        )
        b = FXRevaluationLine(
            account_id=control,
            currency_code="USD",
            closing_rate=Decimal("820"),
            book_value_functional=Decimal("200"),
            revalued_value_functional=Decimal("215"),
            delta_functional=Decimal("15"),
            is_gain=True,
            is_liability=False,
        )

        result = svc._aggregate_per_account_currency([a, b])

        assert len(result) == 1
        agg = result[0]
        assert agg.account_id == control
        assert agg.currency_code == "USD"
        assert agg.delta_functional == Decimal("25")
        assert agg.book_value_functional == Decimal("300")
        assert agg.revalued_value_functional == Decimal("325")

    def test_re_derives_is_gain_after_sign_flip_on_aggregation(self):
        """REGRESSION (P0-1): Two AP lines on the same control account
        with opposite-sign deltas — net is a gain (liability went down)
        but the FIRST line was a loss. Without re-derivation, the bucket
        would keep ``is_gain=False`` from the first line and
        ``_build_journal_input`` would produce a backwards journal
        (debit FX Loss + credit AP, instead of debit AP + credit FX Gain).

        Scenario: same AP control account, two USD invoices revalued.
          - Invoice A: book=750k, revalued=760k, delta=+10k. AP delta>0
            means liability up → is_gain=False (loss).
          - Invoice B: book=800k, revalued=760k, delta=-40k. AP delta<0
            means liability down → is_gain=True (gain).
          Net: delta = -30k → liability net DOWN → net gain.
        """
        from app.services.finance.gl.fx_revaluation import (
            FXRevaluationLine,
            FXRevaluationService,
        )

        svc = FXRevaluationService(MagicMock())
        ap_control = uuid4()

        loss_first = FXRevaluationLine(
            account_id=ap_control,
            currency_code="USD",
            closing_rate=Decimal("820"),
            book_value_functional=Decimal("750000"),
            revalued_value_functional=Decimal("760000"),
            delta_functional=Decimal("10000"),
            is_gain=False,  # AP up = loss
            is_liability=True,
        )
        gain_second = FXRevaluationLine(
            account_id=ap_control,
            currency_code="USD",
            closing_rate=Decimal("820"),
            book_value_functional=Decimal("800000"),
            revalued_value_functional=Decimal("760000"),
            delta_functional=Decimal("-40000"),
            is_gain=True,  # AP down = gain
            is_liability=True,
        )

        result = svc._aggregate_per_account_currency([loss_first, gain_second])

        assert len(result) == 1
        agg = result[0]
        # Net delta is the loss (which reduces is_gain orientation): -30k
        assert agg.delta_functional == Decimal("-30000")
        # Critical re-derivation: after summation, the bucket must reflect
        # that liability went DOWN net → this is a GAIN. Without the fix
        # the bucket would carry ``is_gain=False`` from ``loss_first``.
        assert agg.is_gain is True
        # is_liability is preserved
        assert agg.is_liability is True

    def test_re_derives_is_gain_for_asset_after_sign_flip(self):
        """Symmetric coverage: AR (asset) — first line is a gain, second
        line wipes it out and pushes net delta negative. Aggregated
        ``is_gain`` must flip to False."""
        from app.services.finance.gl.fx_revaluation import (
            FXRevaluationLine,
            FXRevaluationService,
        )

        svc = FXRevaluationService(MagicMock())
        ar_control = uuid4()

        gain_first = FXRevaluationLine(
            account_id=ar_control,
            currency_code="USD",
            closing_rate=Decimal("820"),
            book_value_functional=Decimal("100000"),
            revalued_value_functional=Decimal("110000"),
            delta_functional=Decimal("10000"),
            is_gain=True,  # AR up = gain
            is_liability=False,
        )
        loss_second = FXRevaluationLine(
            account_id=ar_control,
            currency_code="USD",
            closing_rate=Decimal("820"),
            book_value_functional=Decimal("200000"),
            revalued_value_functional=Decimal("180000"),
            delta_functional=Decimal("-20000"),
            is_gain=False,  # AR down = loss
            is_liability=False,
        )

        result = svc._aggregate_per_account_currency([gain_first, loss_second])

        assert len(result) == 1
        agg = result[0]
        assert agg.delta_functional == Decimal("-10000")
        # AR delta < 0 → loss (asset down). Re-derivation must flip to False.
        assert agg.is_gain is False
        assert agg.is_liability is False


class TestDetectPriorRun:
    """Identify active FXR-source journals for a period."""

    def test_returns_empty_when_no_prior_run(self):
        from app.services.finance.gl.fx_revaluation import FXRevaluationService

        db = MagicMock()
        svc = FXRevaluationService(db)
        db.scalars.return_value.all.return_value = []

        ids = svc._detect_prior_run(organization_id=uuid4(), fiscal_period_id=uuid4())
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

        ids = svc._detect_prior_run(organization_id=uuid4(), fiscal_period_id=uuid4())
        assert set(ids) == {a, b}


class TestPreview:
    """End-to-end preview — composes discovery + compute, no DB writes."""

    def _setup_open_period(self, svc, period_id, end_date, organization_id):
        """Patch period lookup and status check for happy paths.

        Plan adaptation: the plan stubs ``period.organization_id = uuid4()``
        without threading the test's ``organization_id`` through to the
        period — but ``preview`` checks ``period.organization_id == organization_id``
        and raises 404 on mismatch. We thread the caller's org through so
        the happy-path test actually exercises the compute path rather
        than tripping the multi-tenant 404 guard.
        """
        from types import SimpleNamespace

        from app.models.finance.gl.fiscal_period import PeriodStatus

        period = SimpleNamespace(
            fiscal_period_id=period_id,
            organization_id=organization_id,
            start_date=date(2026, 1, 1),
            end_date=end_date,
            status=PeriodStatus.OPEN,
        )
        svc.db.get.return_value = period
        # _resolve_next_period_start uses db.scalar(); default to None so
        # the happy-path tests don't need to stub a "next period" row.
        svc.db.scalar.return_value = None
        return period

    def test_refuses_when_period_not_open(self):
        from types import SimpleNamespace

        from fastapi import HTTPException

        from app.models.finance.gl.fiscal_period import PeriodStatus
        from app.services.finance.gl.fx_revaluation import FXRevaluationService

        db = MagicMock()
        svc = FXRevaluationService(db)
        period_id = uuid4()
        org_id = uuid4()

        closed = SimpleNamespace(
            fiscal_period_id=period_id,
            organization_id=org_id,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
            status=PeriodStatus.HARD_CLOSED,
        )
        db.get.return_value = closed

        with pytest.raises(HTTPException) as exc:
            svc.preview(organization_id=org_id, fiscal_period_id=period_id)

        assert exc.value.status_code == 400
        assert "open" in exc.value.detail.lower()

    def test_returns_empty_preview_when_no_foreign_currency_items(self):
        from app.services.finance.gl.fx_revaluation import FXRevaluationService

        db = MagicMock()
        svc = FXRevaluationService(db)
        period_id = uuid4()
        org_id = uuid4()
        self._setup_open_period(svc, period_id, date(2026, 1, 31), org_id)

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
        org_id = uuid4()
        prior_a, prior_b = uuid4(), uuid4()
        self._setup_open_period(svc, period_id, date(2026, 1, 31), org_id)

        with (
            patch.object(svc, "_read_fx_account_ids", return_value=(uuid4(), uuid4())),
            patch.object(svc, "_discover_ar_open_invoices", return_value=[]),
            patch.object(svc, "_discover_ap_open_invoices", return_value=[]),
            patch.object(svc, "_discover_bank_balances", return_value=[]),
            patch.object(svc, "_detect_prior_run", return_value=[prior_a, prior_b]),
        ):
            preview = svc.preview(organization_id=org_id, fiscal_period_id=period_id)

        assert preview.prior_run_exists is True
        assert set(preview.prior_journal_ids) == {prior_a, prior_b}


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
        debits = [
            (l.account_id, l.debit_amount) for l in ji.lines if l.debit_amount > 0
        ]
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

        debits = [
            (l.account_id, l.debit_amount) for l in ji.lines if l.debit_amount > 0
        ]
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
        gain_credits = [l.credit_amount for l in ji.lines if l.account_id == gain_a]
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
                FXRevaluationLine(
                    ar,
                    "USD",
                    Decimal("820"),
                    Decimal("100"),
                    Decimal("110"),
                    Decimal("10"),
                    True,
                ),
                FXRevaluationLine(
                    ap,
                    "GBP",
                    Decimal("1050"),
                    Decimal("200"),
                    Decimal("220"),
                    Decimal("20"),
                    False,
                ),
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
        """True no-op: no prior run AND no new lines.

        ``prior_run_exists=False`` (default) AND ``lines=[]`` (default) →
        post() returns success without touching the journal subsystem and
        without referencing any reversed prior pair (there isn't one).
        See ``test_reverses_prior_pair_when_lines_empty_and_reason_given``
        for the re-run-with-cleared-balances variant.
        """
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
        assert result.reversed_prior_journal_ids == []
        assert "nothing to revalue" in result.message.lower()

    def test_reverses_prior_pair_when_lines_empty_and_reason_given(self):
        """REGRESSION (P0-2): Re-run with prior pair + reason, but the
        new revaluation has no lines (e.g., all FX invoices have since
        been settled or fully allocated). The prior pair MUST still be
        reversed — otherwise the user thinks they replaced the prior FXR
        run when in fact the prior journals remain active and posted.

        Order under test:
          1. reason guard (passes — reason supplied)
          2. prior-pair reversal loop (must run BEFORE the empty-lines
             short-circuit)
          3. empty-lines short-circuit (returns success)

        Without the fix, step 2 runs after step 3's early return, so the
        reversal never happens.
        """
        from app.services.finance.gl.fx_revaluation import (
            FXRevaluationPreview,
            FXRevaluationService,
        )

        db = MagicMock()
        svc = FXRevaluationService(db)
        period_id = uuid4()
        org_id = uuid4()
        user_id = uuid4()
        prior_a, prior_b = uuid4(), uuid4()

        preview = FXRevaluationPreview(
            fiscal_period_id=period_id,
            period_end_date=date(2026, 1, 31),
            next_period_start_date=date(2026, 2, 1),
            lines=[],  # cleared since prior run
            prior_run_exists=True,
            prior_journal_ids=[prior_a, prior_b],
        )

        with (
            patch.object(svc, "preview", return_value=preview),
            patch(
                "app.services.finance.gl.fx_revaluation.ReversalService.create_reversal"
            ) as rev_svc,
            patch(
                "app.services.finance.gl.fx_revaluation.JournalService.create_journal"
            ) as create_jrnl,
        ):
            result = svc.post(
                organization_id=org_id,
                fiscal_period_id=period_id,
                user_id=user_id,
                reason="Fixed an erroneous closing rate after re-allocation",
            )

        # Reversal called once per prior journal
        assert rev_svc.call_count == 2
        # No new journals created (no new lines to post)
        assert create_jrnl.call_count == 0
        assert result.success is True
        assert set(result.reversed_prior_journal_ids) == {prior_a, prior_b}
        assert "prior revaluation reversed" in result.message.lower()

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
            patch.object(
                svc, "_read_fx_account_ids", return_value=(gain_acct, loss_acct)
            ),
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

    def test_re_run_with_lines_but_missing_next_period_does_not_post_new_pair(self):
        """Torn-state guard: prior pair exists, reason given, lines non-empty,
        but next_period_start_date is None.

        Post() reverses the prior pair (flushed inside the same db tx) and
        THEN raises 400 on the missing-next-period check, before reaching
        JournalService.create_journal. The route's db.commit() is therefore
        never reached, and SQLAlchemy rolls back the entire transaction —
        leaving the prior pair intact. This test pins the contract that no
        partial new-pair is ever created in this scenario.
        """
        from fastapi import HTTPException

        from app.services.finance.gl.fx_revaluation import (
            FXRevaluationLine,
            FXRevaluationPreview,
            FXRevaluationService,
        )

        db = MagicMock()
        svc = FXRevaluationService(db)
        period_id = uuid4()
        prior_a, prior_b = uuid4(), uuid4()
        ar = uuid4()

        preview = FXRevaluationPreview(
            fiscal_period_id=period_id,
            period_end_date=date(2026, 1, 31),
            next_period_start_date=None,  # missing next period
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
            prior_run_exists=True,
            prior_journal_ids=[prior_a, prior_b],
        )

        with (
            patch.object(svc, "preview", return_value=preview),
            patch.object(svc, "_read_fx_account_ids", return_value=(uuid4(), uuid4())),
            patch(
                "app.services.finance.gl.fx_revaluation.ReversalService.create_reversal"
            ) as rev_svc,
            patch(
                "app.services.finance.gl.fx_revaluation.JournalService.create_journal"
            ) as create_jrnl,
            patch(
                "app.services.finance.gl.fx_revaluation.JournalService.post_journal"
            ) as post_jrnl,
        ):
            with pytest.raises(HTTPException) as exc:
                svc.post(
                    organization_id=uuid4(),
                    fiscal_period_id=period_id,
                    user_id=uuid4(),
                    reason="Re-run requested with no successor period",
                )

        # The reversal loop ran (flushes will be rolled back by the route on
        # the propagating exception, since SQLAlchemy rolls back the tx).
        assert rev_svc.call_count == 2
        # Critically: NO new journal was created or posted.
        assert create_jrnl.call_count == 0
        assert post_jrnl.call_count == 0
        # And the surfaced error must mention the missing next period.
        assert exc.value.status_code == 400
        assert "next" in exc.value.detail.lower()
