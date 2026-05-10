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

from unittest.mock import MagicMock
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
            compiled = str(
                stmt.compile(compile_kwargs={"literal_binds": True})
            )
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
