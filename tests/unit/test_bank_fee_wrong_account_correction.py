"""Canaries for the digest-bound bank-fee wrong-account correction operator."""

from __future__ import annotations

import ast
import hashlib
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import UUID

import pytest

from app.services.finance.gl.bank_fee_wrong_account_correction import (
    BankFeeWrongAccountCorrectionService,
    CorrectionApproval,
    CorrectionPlan,
    CorrectionRefused,
)
from app.services.finance.gl.reversal import ReversalResult

SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "execute_bank_fee_wrong_account_correction.py"
)

ORG_ID = UUID("00000000-0000-0000-0000-000000000001")
ACTOR_ID = UUID("00000000-0000-0000-0000-000000000099")
PERIOD_ID = UUID("00000000-0000-0000-0000-000000000077")
CANONICAL_PERIOD_ID = UUID("7bc1edbb-270c-4096-b9e4-67cc72dd44a4")
REVERSAL_DATE = date(2026, 8, 23)


def _row(
    *,
    target_number: int,
    statement_number: int,
    canonical_number: int,
    amount: str,
    mapping_name: str = "PAYSTACK_OPEX_LEGACY_TO_1211",
    legacy_code: str = "Paystack OPEX - DT",
    canonical_code: str = "1211",
    resolves: bool = True,
) -> list[str]:
    target_id = UUID(int=target_number)
    target_batch_id = UUID(int=10_000 + target_number)
    statement_id = UUID(int=20_000 + statement_number)
    canonical_id = UUID(int=30_000 + canonical_number)
    target_effect_hash = f"{target_number:032x}"
    canonical_effect_hash = f"{canonical_number:032x}"
    expected_text = "true" if resolves else "false"
    schedule_material = "|".join(
        (
            str(target_id),
            str(target_batch_id),
            f"backfill-stranded-bank-fees-JE-{target_number}",
            str(canonical_id),
            mapping_name,
            canonical_code,
            legacy_code,
            expected_text,
            "2025-01-20",
            "00000000-0000-0000-0000-000000000055",
            "2026-03-13",
            str(CANONICAL_PERIOD_ID),
            REVERSAL_DATE.isoformat(),
            str(PERIOD_ID),
            target_effect_hash,
            canonical_effect_hash,
        )
    )
    return [
        str(target_id),
        f"JE-{target_number}",
        str(target_batch_id),
        f"backfill-stranded-bank-fees-JE-{target_number}",
        str(statement_id),
        str(canonical_id),
        f"JE-C-{canonical_number}",
        mapping_name,
        legacy_code,
        canonical_code,
        "t" if resolves else "f",
        "t" if resolves else "f",
        amount,
        target_effect_hash,
        canonical_effect_hash,
        "2025-01-20",
        "00000000-0000-0000-0000-000000000055",
        "2026-03-13",
        str(CANONICAL_PERIOD_ID),
        REVERSAL_DATE.isoformat(),
        str(PERIOD_ID),
        hashlib.md5(schedule_material.encode(), usedforsecurity=False).hexdigest(),
    ]


def _approved_plan() -> tuple[CorrectionPlan, CorrectionApproval]:
    rows = [
        _row(
            target_number=1, statement_number=1, canonical_number=1, amount="17.500000"
        ),
        _row(
            target_number=2, statement_number=1, canonical_number=1, amount="17.500000"
        ),
    ]
    payload = ("\n".join("|".join(row) for row in rows) + "\n").encode()
    schedule_digest = hashlib.md5(  # noqa: S324 - matches PostgreSQL evidence
        ",".join(row[-1] for row in rows).encode(), usedforsecurity=False
    ).hexdigest()
    approval = CorrectionApproval(
        plan_sha256=hashlib.sha256(payload).hexdigest(),
        schedule_digest=schedule_digest,
        target_count=2,
        affected_statement_lines=1,
        gross=Decimal("35.000000"),
        reversal_date=REVERSAL_DATE,
        reversal_fiscal_period_id=PERIOD_ID,
        mapping_counts={"PAYSTACK_OPEX_LEGACY_TO_1211": 2},
    )
    return CorrectionPlan.from_bytes(payload, approval=approval), approval


def test_exact_approved_plan_is_accepted() -> None:
    plan, _approval = _approved_plan()

    assert plan.target_count == 2
    assert plan.gross == Decimal("35.000000")
    assert plan.affected_statement_lines == 1


def test_one_byte_of_plan_drift_is_refused() -> None:
    plan, approval = _approved_plan()
    changed = plan.raw_bytes.replace(b"17.500000", b"17.500001", 1)

    with pytest.raises(CorrectionRefused, match="SHA-256"):
        CorrectionPlan.from_bytes(changed, approval=approval)


def test_duplicate_target_is_refused_even_with_a_reapproved_file_digest() -> None:
    row = _row(
        target_number=1, statement_number=1, canonical_number=1, amount="17.500000"
    )
    payload = (("|".join(row) + "\n") * 2).encode()
    approval = CorrectionApproval(
        plan_sha256=hashlib.sha256(payload).hexdigest(),
        schedule_digest=hashlib.md5(  # noqa: S324 - matches PostgreSQL evidence
            f"{row[-1]},{row[-1]}".encode(), usedforsecurity=False
        ).hexdigest(),
        target_count=2,
        affected_statement_lines=1,
        gross=Decimal("35.000000"),
        reversal_date=REVERSAL_DATE,
        reversal_fiscal_period_id=PERIOD_ID,
        mapping_counts={"PAYSTACK_OPEX_LEGACY_TO_1211": 2},
    )

    with pytest.raises(CorrectionRefused, match="duplicate target"):
        CorrectionPlan.from_bytes(payload, approval=approval)


def test_dry_run_validates_but_never_reaches_the_reversal_writer() -> None:
    plan, _approval = _approved_plan()
    service = BankFeeWrongAccountCorrectionService(MagicMock())

    with (
        patch.object(service, "_require_approved_plan") as approved,
        patch.object(service, "_validate_current_state") as validate,
        patch(
            "app.services.finance.gl.bank_fee_wrong_account_correction."
            "ReversalService.create_reversal"
        ) as reverse,
    ):
        result = service.run(
            organization_id=ORG_ID,
            created_by_user_id=ACTOR_ID,
            expected_database="dotmac_erp",
            expected_server_address="75.119.157.91",
            plan=plan,
            execute=False,
        )

    approved.assert_called_once_with(plan)
    validate.assert_called_once_with(
        organization_id=ORG_ID,
        expected_database="dotmac_erp",
        expected_server_address="75.119.157.91",
        plan=plan,
        lock_targets=False,
    )
    reverse.assert_not_called()
    assert result.executed is False
    assert result.reversals == 0


def test_execute_uses_linked_reversal_service_for_every_target() -> None:
    plan, _approval = _approved_plan()
    service = BankFeeWrongAccountCorrectionService(MagicMock())
    successful = ReversalResult(success=True, reversal_journal_id=UUID(int=90))

    with (
        patch.object(service, "_require_approved_plan"),
        patch.object(service, "_validate_current_state") as validate,
        patch.object(service, "_validate_postconditions") as postconditions,
        patch(
            "app.services.finance.gl.bank_fee_wrong_account_correction."
            "ReversalService.create_reversal",
            return_value=successful,
        ) as reverse,
    ):
        result = service.run(
            organization_id=ORG_ID,
            created_by_user_id=ACTOR_ID,
            expected_database="dotmac_erp",
            expected_server_address="75.119.157.91",
            plan=plan,
            execute=True,
        )

    validate.assert_called_once_with(
        organization_id=ORG_ID,
        expected_database="dotmac_erp",
        expected_server_address="75.119.157.91",
        plan=plan,
        lock_targets=True,
    )
    assert reverse.call_count == 2
    assert all(call.kwargs["auto_post"] is True for call in reverse.call_args_list)
    assert all(
        call.kwargs["reversal_date"] == REVERSAL_DATE for call in reverse.call_args_list
    )
    postconditions.assert_called_once_with(organization_id=ORG_ID, plan=plan)
    assert result.executed is True
    assert result.reversals == 2


def test_a_failed_reversal_aborts_before_postconditions() -> None:
    plan, _approval = _approved_plan()
    service = BankFeeWrongAccountCorrectionService(MagicMock())

    with (
        patch.object(service, "_require_approved_plan"),
        patch.object(service, "_validate_current_state"),
        patch.object(service, "_validate_postconditions") as postconditions,
        patch(
            "app.services.finance.gl.bank_fee_wrong_account_correction."
            "ReversalService.create_reversal",
            return_value=ReversalResult(success=False, message="posting refused"),
        ),
        pytest.raises(CorrectionRefused, match="linked reversal failed"),
    ):
        service.run(
            organization_id=ORG_ID,
            created_by_user_id=ACTOR_ID,
            expected_database="dotmac_erp",
            expected_server_address="75.119.157.91",
            plan=plan,
            execute=True,
        )

    postconditions.assert_not_called()


def test_service_refuses_a_plan_parsed_under_any_other_approval() -> None:
    plan, _approval = _approved_plan()

    with pytest.raises(CorrectionRefused, match="non-approved correction plan"):
        BankFeeWrongAccountCorrectionService(MagicMock()).run(
            organization_id=ORG_ID,
            created_by_user_id=ACTOR_ID,
            expected_database="dotmac_erp",
            expected_server_address="75.119.157.91",
            plan=plan,
        )


def test_script_requires_explicit_execution_and_target_identity() -> None:
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    flags = {
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "attr", "") == "add_argument"
        and node.args
        and isinstance(node.args[0], ast.Constant)
    }

    assert {
        "--execute",
        "--plan",
        "--plan-sha256",
        "--schedule-digest",
        "--organization-id",
        "--created-by-user-id",
        "--expected-database",
        "--expected-server-address",
        "--confirm-target-count",
        "--confirm-gross",
    } <= flags


def test_script_commits_only_the_successful_execute_path() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert source.count("db.commit()") == 1
    assert "if args.execute:" in source
    assert "db.rollback()" in source
    assert "session_for_org" in source
    assert '"isolation_level": "REPEATABLE READ"' in source
