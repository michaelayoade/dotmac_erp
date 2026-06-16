"""Tests for fixed asset GL reconciliation approval packages."""

import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


def _totals(**overrides):
    totals = {
        "category_count": 1,
        "asset_count": 2,
        "register_cost": Decimal("1000.00"),
        "gl_cost": Decimal("1000.00"),
        "cost_variance": Decimal("0.00"),
        "register_accumulated_depreciation": Decimal("100.00"),
        "gl_accumulated_depreciation": Decimal("100.00"),
        "accumulated_depreciation_variance": Decimal("0.00"),
        "register_nbv": Decimal("900.00"),
        "gl_nbv": Decimal("900.00"),
        "nbv_variance": Decimal("0.00"),
    }
    totals.update(overrides)
    return totals


def test_create_package_balanced_does_not_submit_for_approval():
    from app.services.fixed_assets.reconciliation import (
        FixedAssetGLReconciliationPackageService,
    )

    db = MagicMock()
    org_id = uuid.uuid4()
    context = {
        "as_of": "2026-04-30",
        "rows": [],
        "totals": _totals(),
        "out_of_balance_count": 0,
        "is_balanced": True,
    }

    with (
        patch(
            "app.services.fixed_assets.reconciliation.FixedAssetWebService.gl_reconciliation_context",
            return_value=context,
        ),
        patch(
            "app.services.fixed_assets.reconciliation.ApprovalWorkflowService.check_workflow_required"
        ) as check_workflow,
    ):
        run = FixedAssetGLReconciliationPackageService.create_package(db, org_id)

    assert run.status == FixedAssetGLReconciliationPackageService.STATUS_BALANCED
    assert run.total_variance_abs == Decimal("0.00")
    check_workflow.assert_not_called()
    db.commit.assert_called_once()


def test_create_package_with_variance_submits_approval_request():
    from app.services.fixed_assets.reconciliation import (
        FA_GL_RECONCILIATION_DOCUMENT_TYPE,
        FixedAssetGLReconciliationPackageService,
    )

    db = MagicMock()
    org_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    approval_request_id = uuid.uuid4()
    asset_account_id = uuid.uuid4()
    accum_account_id = uuid.uuid4()
    context = {
        "as_of": "2026-04-30",
        "rows": [
            {
                "is_balanced": False,
                "asset_account": {"account_id": str(asset_account_id)},
                "accumulated_depreciation_account": {
                    "account_id": str(accum_account_id)
                },
                "category_codes": "EQUIP",
                "category_code": "EQUIP",
                "category_name": "Equipment",
                "asset_count": 2,
                "register_cost": Decimal("1000.00"),
                "gl_cost": Decimal("1000.00"),
                "cost_variance": Decimal("0.00"),
                "register_accumulated_depreciation": Decimal("100.00"),
                "gl_accumulated_depreciation": Decimal("150.00"),
                "accumulated_depreciation_variance": Decimal("-50.00"),
                "register_nbv": Decimal("900.00"),
                "gl_nbv": Decimal("850.00"),
                "nbv_variance": Decimal("50.00"),
            }
        ],
        "totals": _totals(
            gl_accumulated_depreciation=Decimal("150.00"),
            accumulated_depreciation_variance=Decimal("-50.00"),
            gl_nbv=Decimal("850.00"),
            nbv_variance=Decimal("50.00"),
        ),
        "out_of_balance_count": 1,
        "is_balanced": False,
    }

    with (
        patch(
            "app.services.fixed_assets.reconciliation.FixedAssetWebService.gl_reconciliation_context",
            return_value=context,
        ),
        patch(
            "app.services.fixed_assets.reconciliation.ApprovalWorkflowService.check_workflow_required",
            return_value=workflow_id,
        ) as check_workflow,
        patch(
            "app.services.fixed_assets.reconciliation.ApprovalWorkflowService.submit_for_approval",
            return_value=approval_request_id,
        ) as submit_for_approval,
    ):
        run = FixedAssetGLReconciliationPackageService.create_package(db, org_id)

    assert (
        run.status == FixedAssetGLReconciliationPackageService.STATUS_PENDING_APPROVAL
    )
    assert run.approval_request_id == approval_request_id
    assert run.total_variance_abs == Decimal("50.00")
    assert db.add.call_count == 2
    exception = db.add.call_args_list[1].args[0]
    assert exception.variance_amount == Decimal("50.00")
    check_workflow.assert_called_once_with(
        db,
        org_id,
        FA_GL_RECONCILIATION_DOCUMENT_TYPE,
        document_amount=Decimal("50.00"),
        currency_code=None,
    )
    submit_for_approval.assert_called_once()
    db.commit.assert_called_once()


def test_create_draft_correction_journal_requires_approved_package():
    from app.models.finance.audit.approval_request import (
        ApprovalRequest,
        ApprovalRequestStatus,
    )
    from app.models.fixed_assets.gl_reconciliation import (
        FixedAssetGLReconciliationRun,
    )
    from app.services.fixed_assets.reconciliation import (
        FixedAssetGLReconciliationPackageService,
    )

    db = MagicMock()
    org_id = uuid.uuid4()
    run_id = uuid.uuid4()
    approval_request_id = uuid.uuid4()
    run = FixedAssetGLReconciliationRun(
        run_id=run_id,
        organization_id=org_id,
        as_of_date=date(2026, 4, 30),
        status=FixedAssetGLReconciliationPackageService.STATUS_PENDING_APPROVAL,
        total_variance_abs=Decimal("100.00"),
        nbv_variance=Decimal("50.00"),
        cost_variance=Decimal("0.00"),
        accumulated_depreciation_variance=Decimal("-50.00"),
        approval_request_id=approval_request_id,
        created_by_user_id=uuid.uuid4(),
    )
    approval = ApprovalRequest(
        request_id=approval_request_id,
        organization_id=org_id,
        workflow_id=uuid.uuid4(),
        document_type="FA_GL_RECONCILIATION",
        document_id=run_id,
        requested_by_user_id=uuid.uuid4(),
        current_level=1,
        status=ApprovalRequestStatus.PENDING,
    )
    db.get.side_effect = [run, approval]

    try:
        FixedAssetGLReconciliationPackageService.create_draft_correction_journal(
            db,
            org_id,
            run_id,
        )
    except Exception as exc:
        assert "must be approved" in str(exc)
    else:
        raise AssertionError(
            "Expected pending approval to block draft journal creation"
        )

    db.add.assert_not_called()
    db.commit.assert_not_called()


def test_create_draft_correction_journal_is_idempotent():
    from app.models.fixed_assets.gl_reconciliation import (
        FixedAssetGLReconciliationRun,
    )
    from app.models.finance.gl.journal_entry import JournalEntry, JournalStatus
    from app.services.fixed_assets.reconciliation import (
        FixedAssetGLReconciliationPackageService,
    )

    db = MagicMock()
    org_id = uuid.uuid4()
    run_id = uuid.uuid4()
    journal_id = uuid.uuid4()
    run = FixedAssetGLReconciliationRun(
        run_id=run_id,
        organization_id=org_id,
        as_of_date=date(2026, 4, 30),
        status=FixedAssetGLReconciliationPackageService.STATUS_DRAFT_CREATED,
        total_variance_abs=Decimal("100.00"),
        nbv_variance=Decimal("50.00"),
        cost_variance=Decimal("0.00"),
        accumulated_depreciation_variance=Decimal("-50.00"),
        approval_request_id=uuid.uuid4(),
        proposed_journal_entry_id=journal_id,
        created_by_user_id=uuid.uuid4(),
    )
    journal = JournalEntry(
        journal_entry_id=journal_id,
        organization_id=org_id,
        journal_number="JE-0001",
        journal_type="ADJUSTMENT",
        entry_date=date(2026, 4, 30),
        posting_date=date(2026, 4, 30),
        fiscal_period_id=uuid.uuid4(),
        description="Draft FA GL reconciliation correction",
        currency_code="NGN",
        exchange_rate=Decimal("1.0"),
        total_debit=Decimal("50.00"),
        total_credit=Decimal("50.00"),
        total_debit_functional=Decimal("50.00"),
        total_credit_functional=Decimal("50.00"),
        status=JournalStatus.DRAFT,
        created_by_user_id=uuid.uuid4(),
    )
    db.get.side_effect = [run, journal]

    result = FixedAssetGLReconciliationPackageService.create_draft_correction_journal(
        db,
        org_id,
        run_id,
    )

    assert result is journal
    db.add.assert_not_called()
    db.commit.assert_not_called()


def test_create_draft_correction_journal_after_approval_creates_draft():
    from app.models.finance.audit.approval_request import (
        ApprovalRequest,
        ApprovalRequestStatus,
    )
    from app.models.fixed_assets.gl_reconciliation import (
        FixedAssetGLReconciliationRun,
    )
    from app.models.finance.gl.journal_entry import JournalEntry, JournalStatus
    from app.models.finance.gl.journal_entry_line import JournalEntryLine
    from app.services.fixed_assets.reconciliation import (
        FA_GL_RECONCILIATION_DOCUMENT_TYPE,
        FixedAssetGLReconciliationPackageService,
    )

    db = MagicMock()
    org_id = uuid.uuid4()
    run_id = uuid.uuid4()
    approval_request_id = uuid.uuid4()
    accum_account_id = uuid.uuid4()
    expense_account_id = uuid.uuid4()
    run = FixedAssetGLReconciliationRun(
        run_id=run_id,
        organization_id=org_id,
        as_of_date=date(2026, 4, 30),
        status=FixedAssetGLReconciliationPackageService.STATUS_PENDING_APPROVAL,
        total_variance_abs=Decimal("100.00"),
        nbv_variance=Decimal("50.00"),
        cost_variance=Decimal("0.00"),
        accumulated_depreciation_variance=Decimal("-50.00"),
        approval_request_id=approval_request_id,
        created_by_user_id=uuid.uuid4(),
    )
    approval = ApprovalRequest(
        request_id=approval_request_id,
        organization_id=org_id,
        workflow_id=uuid.uuid4(),
        document_type=FA_GL_RECONCILIATION_DOCUMENT_TYPE,
        document_id=run_id,
        requested_by_user_id=uuid.uuid4(),
        current_level=1,
        status=ApprovalRequestStatus.APPROVED,
    )
    db.get.side_effect = [run, approval]
    db.scalars.return_value.all.return_value = [MagicMock()]
    period = SimpleNamespace(fiscal_period_id=uuid.uuid4(), fiscal_year_id=uuid.uuid4())
    lines = [
        {
            "account_id": accum_account_id,
            "description": "Reduce over-posted accumulated depreciation",
            "debit": Decimal("50.00"),
            "credit": Decimal("0.00"),
        },
        {
            "account_id": expense_account_id,
            "description": "Reverse over-posted depreciation expense",
            "debit": Decimal("0.00"),
            "credit": Decimal("50.00"),
        },
    ]

    with (
        patch(
            "app.services.fixed_assets.reconciliation.PeriodGuardService.get_period_for_date",
            return_value=period,
        ),
        patch(
            "app.services.fixed_assets.reconciliation.SequenceService.get_next_number",
            return_value="JE-202604-0001",
        ),
        patch(
            "app.services.fixed_assets.reconciliation.org_context_service.get_functional_currency",
            return_value="NGN",
        ),
        patch(
            "app.services.fixed_assets.reconciliation.FixedAssetGLReconciliationPackageService._draft_correction_lines",
            return_value=lines,
        ),
    ):
        journal = (
            FixedAssetGLReconciliationPackageService.create_draft_correction_journal(
                db,
                org_id,
                run_id,
            )
        )

    assert journal.status == JournalStatus.DRAFT
    assert journal.journal_number == "JE-202604-0001"
    assert journal.source_document_type == FA_GL_RECONCILIATION_DOCUMENT_TYPE
    assert journal.total_debit == Decimal("50.00")
    assert journal.total_credit == Decimal("50.00")
    assert run.status == FixedAssetGLReconciliationPackageService.STATUS_DRAFT_CREATED
    added_objects = [call.args[0] for call in db.add.call_args_list]
    assert sum(isinstance(obj, JournalEntry) for obj in added_objects) == 1
    assert sum(isinstance(obj, JournalEntryLine) for obj in added_objects) == 2
    db.commit.assert_called_once()
