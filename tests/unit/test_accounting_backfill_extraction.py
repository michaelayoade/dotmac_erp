"""The ERP-side backfill extraction, and the decisions it must not guess.

Two things are worth proving before a single row moves:

1. **Every ERP value has a mapped module value.** ERP's `IFRSCategory` and
   `AccountType` are closed enums.  If a member is missing from a mapping table,
   the extraction fails on the first account that uses it — in production, mid
   run.  Checking the tables against the enums makes that a build failure
   instead, which is the only time it is cheap.

2. **An unmapped value fails loudly rather than defaulting.** A default here
   does not produce an error; it produces a chart of accounts where something is
   filed under the wrong class, and a trial balance that still adds up.  The
   tests below plant exactly that and require a refusal.

The extraction's SQL is exercised against a real database by the integration
suite; these are the decisions, which need no database and must never depend on
one.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.models.finance.gl.account import AccountType, NormalBalance
from app.models.finance.gl.account_category import IFRSCategory
from app.services.finance.gl.accounting_backfill import (
    ACCOUNT_TYPE_TO_KIND,
    DIMENSION_BINDINGS,
    IFRS_CATEGORY_TO_ACCOUNT_CLASS,
    AccountingBackfillExtractor,
    BackfillNotPossible,
)

ORG = uuid4()


class _StubResult:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def all(self) -> list[object]:
        return self._rows

    def __iter__(self):
        return iter(self._rows)


class _StubSession:
    """Just enough Session to drive the extraction's read paths."""

    def __init__(
        self, scalars: list[object] | None = None, execute: list[object] | None = None
    ):
        self._scalars = scalars or []
        self._execute = execute or []

    def scalars(self, _stmt: object) -> _StubResult:
        return _StubResult(self._scalars)

    def execute(self, _stmt: object) -> _StubResult:
        return _StubResult(self._execute)


def _category(code: str, ifrs: IFRSCategory, *, parent_id=None, category_id=None):
    return SimpleNamespace(
        category_id=category_id or uuid4(),
        category_code=code,
        category_name=f"{code} name",
        ifrs_category=ifrs,
        parent_category_id=parent_id,
        hierarchy_level=1,
        display_order=0,
        is_active=True,
    )


def _account(code: str, account_type: AccountType):
    return SimpleNamespace(
        account_code=code,
        account_name=f"{code} name",
        account_type=account_type,
        normal_balance=NormalBalance.DEBIT,
        default_currency_code="NGN",
        is_active=True,
        is_posting_allowed=True,
    )


def test_every_erp_ifrs_category_has_a_module_account_class() -> None:
    """Exhaustive over ERP's enum: a member added without a mapping fails here,
    not mid-run against production data."""
    assert set(IFRS_CATEGORY_TO_ACCOUNT_CLASS) == set(IFRSCategory)


def test_every_erp_account_type_has_a_module_kind() -> None:
    assert set(ACCOUNT_TYPE_TO_KIND) == set(AccountType)


def test_both_mappings_are_injective() -> None:
    """Exhaustive is not sufficient — a TOTAL mapping can still be lossy.

    If two ERP classifications collapsed onto one module value, every account
    would import and the trial balance would still add up, while two distinct
    populations became indistinguishable and could never be separated again.
    A classification migration must be reversible in principle; injectivity is
    what makes that true.
    """
    assert len(set(IFRS_CATEGORY_TO_ACCOUNT_CLASS.values())) == len(
        IFRS_CATEGORY_TO_ACCOUNT_CLASS
    )
    assert len(set(ACCOUNT_TYPE_TO_KIND.values())) == len(ACCOUNT_TYPE_TO_KIND)


def test_neither_mapping_can_emit_an_empty_or_lowercase_value() -> None:
    """The module stores these as string enums; a blank or wrongly-cased value
    fails at insert, one row at a time, deep inside a backfill run."""
    for mapping in (IFRS_CATEGORY_TO_ACCOUNT_CLASS, ACCOUNT_TYPE_TO_KIND):
        for value in mapping.values():
            assert value and value == value.upper(), value


def test_account_type_maps_to_kind_not_to_class() -> None:
    """The confusion this extraction exists to avoid: ERP's `account_type` is
    control/posting/statistical — the module's KIND.  The module's CLASS
    (asset/liability/...) comes from the category's IFRS classification.  Reading
    one as the other misclassifies the whole trial balance while every total
    still agrees."""
    kinds = set(ACCOUNT_TYPE_TO_KIND.values())
    classes = set(IFRS_CATEGORY_TO_ACCOUNT_CLASS.values())
    assert kinds == {"CONTROL", "POSTING", "STATISTICAL"}
    assert not kinds & classes, (
        "the two vocabularies must not overlap; an overlapping value is a value "
        "that could be written into either column without failing"
    )


def test_the_exhaustiveness_guard_is_sensitive() -> None:
    """Sensitivity proof (ADR-0018): the checks above pass over mappings that
    happen to be complete.  Prove they would fail on an incomplete one, so a
    future edit cannot weaken them into a shape check."""
    stripped = dict(IFRS_CATEGORY_TO_ACCOUNT_CLASS)
    stripped.pop(IFRSCategory.OTHER_COMPREHENSIVE_INCOME)
    assert set(stripped) != set(IFRSCategory)

    collapsed = dict(IFRS_CATEGORY_TO_ACCOUNT_CLASS)
    collapsed[IFRSCategory.REVENUE] = collapsed[IFRSCategory.EXPENSES]
    assert len(set(collapsed.values())) != len(collapsed)


def test_categories_carry_the_mapped_class_and_a_resolved_parent_code() -> None:
    """Parents cross by CODE, not by ERP's surrogate id — the module mints its
    own ids and cannot be handed ERP's."""
    parent = _category("A", IFRSCategory.ASSETS)
    child = _category("A10", IFRSCategory.ASSETS, parent_id=parent.category_id)
    extractor = AccountingBackfillExtractor(_StubSession(scalars=[parent, child]))

    rows = extractor._categories(ORG)

    assert [row.code for row in rows] == ["A", "A10"]
    assert [row.account_class for row in rows] == ["ASSET", "ASSET"]
    assert rows[0].parent_code is None
    assert rows[1].parent_code == "A"


def test_a_parent_outside_the_organization_is_refused() -> None:
    """Silently dropping the parent would flatten the hierarchy and produce a
    chart of accounts that imports cleanly and reports differently."""
    orphan = _category("A10", IFRSCategory.ASSETS, parent_id=uuid4())
    extractor = AccountingBackfillExtractor(_StubSession(scalars=[orphan]))

    with pytest.raises(BackfillNotPossible, match="outside this organization"):
        extractor._categories(ORG)


def test_an_unmapped_ifrs_category_is_refused_rather_than_defaulted() -> None:
    unmapped = _category("Z", "SOMETHING_NEW")  # type: ignore[arg-type]
    extractor = AccountingBackfillExtractor(_StubSession(scalars=[unmapped]))

    with pytest.raises(BackfillNotPossible, match="no mapped module account class"):
        extractor._categories(ORG)


def test_accounts_carry_the_mapped_kind_and_their_category_code() -> None:
    extractor = AccountingBackfillExtractor(
        _StubSession(execute=[(_account("1200", AccountType.CONTROL), "A10")])
    )

    (row,) = extractor._accounts(ORG)

    assert row.code == "1200"
    assert row.category_code == "A10"
    assert row.kind == "CONTROL"
    assert row.normal_balance == "DEBIT"
    assert row.currency_code == "NGN"


def test_an_unmapped_account_type_is_refused_rather_than_defaulted() -> None:
    extractor = AccountingBackfillExtractor(
        _StubSession(execute=[(_account("9999", "LEGACY"), "A10")])  # type: ignore[arg-type]
    )

    with pytest.raises(BackfillNotPossible, match="no mapped module kind"):
        extractor._accounts(ORG)


def test_the_four_fixed_dimensions_are_declared_with_their_erp_columns() -> None:
    """ERP's four dimension columns become four module dimensions.  The binding
    names the ERP column so the line-level backfill has one place to read it
    from rather than four hard-coded strings."""
    assert [binding.code for binding in DIMENSION_BINDINGS] == [
        "BUSINESS_UNIT",
        "COST_CENTER",
        "PROJECT",
        "SEGMENT",
    ]
    assert [binding.line_column for binding in DIMENSION_BINDINGS] == [
        "business_unit_id",
        "cost_center_id",
        "project_id",
        "segment_id",
    ]


def test_every_declared_dimension_column_exists_on_both_erp_line_tables() -> None:
    """The binding is a claim about ERP's schema; check it against the schema.

    A renamed column would otherwise make the line-level backfill read `None`
    for every dimension and produce an unposted-dimension ledger that looks
    complete.
    """
    from app.models.finance.gl.journal_entry_line import JournalEntryLine
    from app.models.finance.gl.posted_ledger_line import PostedLedgerLine

    for binding in DIMENSION_BINDINGS:
        assert hasattr(JournalEntryLine, binding.line_column), binding.line_column
        assert hasattr(PostedLedgerLine, binding.line_column), binding.line_column


def test_the_legacy_extractor_exports_no_module_loader() -> None:
    """ADR-0003 makes this evidence path permanently read-only.

    A loader appearing here would route around the reviewed clean-bootstrap
    manifest and make legacy rows importable again.
    """
    from app.services.finance.gl import accounting_backfill

    assert "load_masters" not in accounting_backfill.__all__
    assert not hasattr(accounting_backfill, "load_masters")


def test_every_mapped_class_is_a_real_module_account_class() -> None:
    """The module is installed at gate C, so these are checked, not skipped."""
    from dotmac_accounting.contracts import AccountClass

    assert set(IFRS_CATEGORY_TO_ACCOUNT_CLASS.values()) <= {
        member.value for member in AccountClass
    }


def test_every_mapped_kind_is_a_real_module_account_kind() -> None:
    from dotmac_accounting.contracts import AccountKind

    assert set(ACCOUNT_TYPE_TO_KIND.values()) == {
        member.value for member in AccountKind
    }


def test_erps_normal_balance_vocabulary_matches_the_modules() -> None:
    """`normal_balance` is passed through unmapped, which is only safe while the
    two enums agree exactly.  If either side gains a member, the pass-through
    needs a mapping table like the other two."""
    from dotmac_accounting.contracts import NormalBalance as ModuleNormalBalance

    assert {member.value for member in NormalBalance} == {
        member.value for member in ModuleNormalBalance
    }
