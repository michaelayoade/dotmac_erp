"""C1 backstop: the dotmac_sub payment sync can't double-post cash.

Verifies the partial-unique index ``uq_customer_payment_dotmac_sub_id`` — a given
upstream payment (``dotmac_sub_id``) maps to at most one ``CustomerPayment`` per
org, so a concurrent poll/webhook race can never insert two. Manually-entered
payments (NULL ``dotmac_sub_id``) stay unconstrained.

Hermetic: builds a throwaway in-memory SQLite engine translating the ``ar``
schema to the default one (the shared harness omits ``ar``) and creates only the
``customer_payment`` table. ``postgresql_where`` is ignored by SQLite, so the
index becomes a plain unique on ``(organization_id, dotmac_sub_id)`` — which is
exactly the behaviour under test (Postgres treats NULLs as distinct too).
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.finance.ar.customer_payment import (
    CustomerPayment,
    PaymentMethod,
    PaymentStatus,
)

_ORG = uuid.UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture()
def session():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        execution_options={"schema_translate_map": {"ar": None}},
    )
    # SQLite can't parse Postgres server-defaults like gen_random_uuid(); drop
    # them (Python-side defaults still supply the PK), mirroring the shared harness.
    for col in CustomerPayment.__table__.columns:
        default = col.server_default
        if default is not None and "gen_random_uuid" in str(
            getattr(default, "arg", default)
        ):
            col.server_default = None
    CustomerPayment.__table__.create(engine)
    maker = sessionmaker(bind=engine)
    db = maker()
    try:
        yield db
    finally:
        db.close()


def _payment(*, dotmac_sub_id: str | None, number: str) -> CustomerPayment:
    return CustomerPayment(
        organization_id=_ORG,
        customer_id=uuid.uuid4(),
        payment_number=number,
        payment_date=date(2026, 7, 1),
        payment_method=PaymentMethod.BANK_TRANSFER,
        currency_code="NGN",
        gross_amount=Decimal("100.00"),
        amount=Decimal("100.00"),
        functional_currency_amount=Decimal("100.00"),
        status=PaymentStatus.CLEARED,
        created_by_user_id=uuid.uuid4(),
        dotmac_sub_id=dotmac_sub_id,
    )


def test_duplicate_dotmac_sub_id_is_rejected(session):
    """A second payment with the same upstream id in the same org is refused."""
    session.add(_payment(dotmac_sub_id="pay-777", number="PMT-0001"))
    session.commit()

    session.add(_payment(dotmac_sub_id="pay-777", number="PMT-0002"))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    # The first (winning) row survives; no duplicate cash.
    rows = session.query(CustomerPayment).filter_by(dotmac_sub_id="pay-777").all()
    assert len(rows) == 1
    assert rows[0].payment_number == "PMT-0001"


def test_manual_payments_with_null_sub_id_are_unconstrained(session):
    """Manually-entered payments (no upstream id) are never blocked."""
    session.add(_payment(dotmac_sub_id=None, number="PMT-0003"))
    session.add(_payment(dotmac_sub_id=None, number="PMT-0004"))
    session.commit()

    assert session.query(CustomerPayment).filter_by(dotmac_sub_id=None).count() == 2


def test_same_sub_id_different_number_still_one_payment(session):
    """The dedup key is (org, dotmac_sub_id) — a different payment_number on the
    same upstream id does not create a second row."""
    session.add(_payment(dotmac_sub_id="pay-888", number="PMT-0005"))
    session.commit()
    session.add(_payment(dotmac_sub_id="pay-888", number="PMT-9999"))
    with pytest.raises(IntegrityError):
        session.commit()
