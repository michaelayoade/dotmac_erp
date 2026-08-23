#!/usr/bin/env python
"""Execute the Finance-approved bank-fee wrong-account correction atomically.

The default is a full database-state dry run.  ``--execute`` is accepted only
with the exact reviewed plan and aggregate confirmations.  The plan contains
Finance-private journal identities: create it outside Git, mode 0600, and
remove it after the execution evidence is captured.
"""

from __future__ import annotations

import argparse
import logging
import stat
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.session_context import session_for_org
from app.services.finance.gl.bank_fee_wrong_account_correction import (
    APPROVED_CORRECTION,
    BankFeeWrongAccountCorrectionService,
    CorrectionPlan,
    CorrectionRefused,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _decimal(value: str) -> Decimal:
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError("must be an exact decimal") from exc


def _private_plan_bytes(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise CorrectionRefused("plan must be a regular, non-symlink file")
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise CorrectionRefused("plan permissions must be owner-only (mode 0600)")
    return path.read_bytes()


def _require_confirmations(args: argparse.Namespace) -> None:
    approval = APPROVED_CORRECTION
    if args.plan_sha256 != approval.plan_sha256:
        raise CorrectionRefused("--plan-sha256 is not the Finance-approved digest")
    if args.schedule_digest != approval.schedule_digest:
        raise CorrectionRefused("--schedule-digest is not the Finance-approved digest")
    if args.confirm_target_count != approval.target_count:
        raise CorrectionRefused("--confirm-target-count does not match approval")
    if args.confirm_gross != approval.gross:
        raise CorrectionRefused("--confirm-gross does not match approval")
    if args.reversal_date != approval.reversal_date.isoformat():
        raise CorrectionRefused("--reversal-date does not match approval")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate or execute the digest-bound Gate D bank-fee linked reversals"
        )
    )
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--plan-sha256", required=True)
    parser.add_argument("--schedule-digest", required=True)
    parser.add_argument("--organization-id", type=UUID, required=True)
    parser.add_argument("--created-by-user-id", type=UUID, required=True)
    parser.add_argument("--expected-database", required=True)
    parser.add_argument("--expected-server-address", required=True)
    parser.add_argument("--reversal-date", required=True)
    parser.add_argument("--confirm-target-count", type=int, required=True)
    parser.add_argument("--confirm-gross", type=_decimal, required=True)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Write and post all linked reversals in one transaction",
    )
    return parser


def run(args: argparse.Namespace) -> int:
    _require_confirmations(args)
    plan = CorrectionPlan.from_bytes(
        _private_plan_bytes(args.plan), approval=APPROVED_CORRECTION
    )

    with session_for_org(args.organization_id) as db:
        # Bind validation and all 429 writes to one stable source snapshot.
        db.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        try:
            result = BankFeeWrongAccountCorrectionService(db).run(
                organization_id=args.organization_id,
                created_by_user_id=args.created_by_user_id,
                expected_database=args.expected_database,
                expected_server_address=args.expected_server_address,
                plan=plan,
                execute=args.execute,
            )
            if args.execute:
                db.commit()
            else:
                db.rollback()
        except Exception:
            db.rollback()
            raise

    mode = "EXECUTED" if result.executed else "DRY RUN PASSED"
    logger.info(
        "%s: targets=%d affected_statement_lines=%d gross=%s reversals=%d "
        "plan_sha256=%s schedule_digest=%s",
        mode,
        result.targets,
        result.affected_statement_lines,
        result.gross,
        result.reversals,
        plan.sha256,
        plan.schedule_digest,
    )
    return 0


def main() -> int:
    try:
        return run(_parser().parse_args())
    except CorrectionRefused as exc:
        logger.error("REFUSED: %s", exc)
        return 1
    except Exception:
        # Database-driver exceptions may include bound plan identities. Keep
        # this operator log aggregate-only; investigate interactively in the
        # same restricted Finance boundary if this generic failure fires.
        logger.error("correction failed; transaction rolled back")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
