"""The legacy importer can construct an entity without persisting it.

Gate 4 of the ``dotmac-imports`` retirement needs row-for-row shadow
comparisons, but the retiring path has never produced anything to compare:
``BaseImporter._import_rows`` gates ``create_entity`` behind
``if not self.config.dry_run``, so a dry run decides customer type, the
legal/trading name split, field transforms and defaults, the payment-terms
fallback, the address and contact blocks and code allocation, and then throws
every one of those decisions away.

``ImportConfig.construct_only`` is a third mode: a dry run that additionally
builds the entity and keeps it on ``importer.constructed``.  These tests fix
its two halves — that it really constructs, and that it really does not
persist — and pin the existing dry run as unchanged.

Building the *capability* to compare field vectors is deliberately all this
does.  Which columns must match is a separate, unmade decision (``customer_code``
provably diverges between the two paths), so nothing here compares a legacy
entity with the durable path's ``apply`` input.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from sqlalchemy import inspect as sa_inspect

from app.models.finance.ar.customer import CustomerType
from app.services.finance.import_export.base import ImportConfig
from app.services.finance.import_export.contacts import CustomerImporter
from app.services.finance.import_export.invoices import InvoiceImporter

ORG = uuid.UUID("00000000-0000-0000-0000-000000000001")
USER = uuid.UUID("00000000-0000-0000-0000-000000000002")
AR = uuid.UUID("00000000-0000-0000-0000-000000000003")


class _ForbiddenSession:
    """A session that fails loudly on ANY use.

    "Constructs but does not persist" is asserted, not asserted-about: with
    ``skip_duplicates=False`` the retiring path has no legitimate reason to
    touch the session at all, so a session object that cannot be read from is
    the strongest available proof.  A stray ``db.add``, ``db.flush``,
    ``db.execute`` or ``db.commit`` raises here instead of passing quietly.
    """

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"a construct-only import touched the session: {name!r}")


class _CountingCustomerImporter(CustomerImporter):
    """The real importer, counting real ``create_entity`` calls.

    Nothing is stubbed: ``create_entity`` delegates to the real one.  The
    counter is how the "an ordinary dry run constructs nothing" claim is
    checked against behaviour rather than against an empty list that could be
    empty for some other reason.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.create_entity_calls = 0

    def create_entity(self, row: dict[str, Any]) -> Any:
        self.create_entity_calls += 1
        return super().create_entity(row)


def _rows() -> list[dict[str, str]]:
    """Two synthetic rows exercising both branches of the name split."""
    return [
        {
            "Display Name": "Northwind Trading",
            "Company Name": "Northwind Trading Limited",
            "Billing City": "Abuja",
            "Payment Terms": "45",
        },
        {
            "Display Name": "Ada Example",
            "First Name": "Ada",
            "Last Name": "Example",
        },
    ]


def _config(**overrides: Any) -> ImportConfig:
    base: dict[str, Any] = {
        "organization_id": ORG,
        "user_id": USER,
        "skip_duplicates": False,
        "dry_run": True,
    }
    base.update(overrides)
    return ImportConfig(**base)


def test_construct_only_builds_entities_that_never_reach_the_session() -> None:
    importer = CustomerImporter(_ForbiddenSession(), _config(construct_only=True), AR)

    result = importer.import_rows(_rows())

    assert result.error_count == 0
    assert len(importer.constructed) == 2
    for entity in importer.constructed:
        state = sa_inspect(entity)
        # Transient: never added to a session, never flushed, no identity key.
        assert state.session is None
        assert state.transient is True
        assert state.identity is None
    # And the batch path was never fed, so ``_commit_batch`` had nothing to do.
    assert result.imported_ids == []


def test_construct_only_is_refused_on_a_run_that_would_persist() -> None:
    # Without this, setting construct_only on a real import would silently
    # turn it into a no-op that still reports rows as imported.
    with pytest.raises(ValueError, match="dry-run refinement"):
        ImportConfig(
            organization_id=ORG,
            user_id=USER,
            dry_run=False,
            construct_only=True,
        )


def test_an_ordinary_dry_run_still_constructs_nothing() -> None:
    importer = _CountingCustomerImporter(_ForbiddenSession(), _config(), AR)

    result = importer.import_rows(_rows())

    assert importer.create_entity_calls == 0
    assert importer.constructed == []
    assert result.imported_count == 2


def test_construct_only_leaves_the_existing_dry_run_verdict_untouched() -> None:
    """The new mode must not move the numbers a dry run already reports.

    The parity guard reads exactly these counts to derive its RowStatus, so a
    shift here would change verdicts, not just add a field vector.
    """
    rows = _rows() + [{"Display Name": ""}]

    plain = _CountingCustomerImporter(_ForbiddenSession(), _config(), AR)
    plain_result = plain.import_rows(rows)
    built = _CountingCustomerImporter(
        _ForbiddenSession(), _config(construct_only=True), AR
    )
    built_result = built.import_rows(rows)

    verdict = ("imported_count", "skipped_count", "duplicate_count", "error_count")
    # Pinned absolutely, not just to each other: two runs that both collapsed
    # into the same swallowed exception would compare equal and prove nothing.
    assert [getattr(plain_result, name) for name in verdict] == [2, 1, 0, 1]
    assert [getattr(built_result, name) for name in verdict] == [2, 1, 0, 1]
    assert plain_result.status == built_result.status
    # The only difference is that one of them kept what it decided.
    assert plain.create_entity_calls == 0
    assert built.create_entity_calls == 2
    assert len(built.constructed) == 2


def test_construct_only_exposes_decisions_a_dry_run_could_not_show() -> None:
    """The six structurally uncomparable decisions become observable.

    This asserts on the retiring path alone.  It is the capability Gate 4
    needs, not a cross-path comparison.
    """
    importer = CustomerImporter(_ForbiddenSession(), _config(construct_only=True), AR)

    importer.import_rows(_rows())
    company, individual = importer.constructed

    # Customer type and the legal/trading name split.
    assert company.customer_type == CustomerType.COMPANY
    assert company.legal_name == "Northwind Trading Limited"
    assert company.trading_name == "Northwind Trading"
    assert individual.customer_type == CustomerType.INDIVIDUAL
    assert individual.legal_name == "Ada Example"
    assert individual.trading_name is None
    # Field transform, and the payment-terms fallback for an absent column.
    assert company.credit_terms_days == 45
    assert individual.credit_terms_days == 30
    # Address and contact blocks, present only when a field survived.
    assert company.billing_address == {"city": "Abuja"}
    assert individual.billing_address is None
    assert individual.primary_contact == {"name": "Ada Example"}


def test_the_legacy_customer_code_counter_is_per_importer_instance() -> None:
    """Allocation is instance state, which is why the parity loop never sees it.

    ``assert_legacy_customer_parity`` builds a FRESH ``CustomerImporter`` for
    every row, so ``_code_counter`` would restart at 1 on each one.  Recorded
    here as an observation only: whether ``customer_code`` must agree across
    the two paths is an open decision this test does not touch.
    """
    importer = CustomerImporter(_ForbiddenSession(), _config(construct_only=True), AR)
    importer.import_rows(_rows())
    assert [entity.customer_code for entity in importer.constructed] == [
        "CUST00001",
        "CUST00002",
    ]

    per_row = [
        CustomerImporter(_ForbiddenSession(), _config(construct_only=True), AR)
        for _ in _rows()
    ]
    for single, row in zip(per_row, _rows(), strict=True):
        single.import_rows([row])
    assert [one.constructed[0].customer_code for one in per_row] == [
        "CUST00001",
        "CUST00001",
    ]


def test_an_importer_that_owns_its_row_loop_refuses_the_mode_out_loud() -> None:
    """An unsupported construct-only run must fail, not build nothing quietly.

    ``InvoiceImporter`` overrides ``_import_rows`` to group rows into invoices,
    so it never reaches the base class's construct-only branch.  Left silent,
    it would report a clean dry run with an empty ``constructed`` list, which
    a shadow comparison would read as "no rows disagreed".
    """
    with pytest.raises(ValueError, match="construct_only"):
        InvoiceImporter(
            _ForbiddenSession(),
            _config(construct_only=True),
            AR,
            uuid.UUID("00000000-0000-0000-0000-000000000004"),
        )


def test_the_unsupported_mode_refusal_bites_only_on_the_mode() -> None:
    """Near-miss control for the refusal above.

    The same importer, the same session, the same everything except the flag,
    must construct normally — otherwise the guard is refusing the importer
    rather than the mode.
    """
    InvoiceImporter(
        _ForbiddenSession(),
        _config(),
        AR,
        uuid.UUID("00000000-0000-0000-0000-000000000004"),
    )
    # And the mode is accepted by an importer that does implement it.
    CustomerImporter(_ForbiddenSession(), _config(construct_only=True), AR)
