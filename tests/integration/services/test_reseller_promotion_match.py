"""Reseller merge-on-promotion — JSONB email match (PostgreSQL only).

Exercises ``_find_promotable_customer``'s real ``primary_contact ->> 'email'``
query, which the SQLite unit suite can't build (JSONB is patched to TEXT
there). Runs under the Integration Tests (PostgreSQL) job.
"""

import uuid

from app.models.finance.ar.customer import Customer, CustomerType
from app.services.dotmac_sub.client import ResellerRecord
from app.services.dotmac_sub.sync._resellers import ResellerSyncMixin


def _reseller(email):
    return ResellerRecord(
        id=str(uuid.uuid4()),
        name="CareNG",
        code="SPL-16",
        contact_email=email,
        contact_phone=None,
        is_active=True,
    )


def _customer(db, org_id, *, email, reseller_id=None, parent_id=None, sub_id="set"):
    cust = Customer(
        organization_id=org_id,
        customer_code=f"DSUB-{uuid.uuid4().hex[:12]}",
        customer_type=CustomerType.INDIVIDUAL,
        legal_name="Someone",
        ar_control_account_id=uuid.uuid4(),
        primary_contact={"email": email, "phone": None},
        dotmac_sub_id=str(uuid.uuid4()) if sub_id == "set" else None,
        dotmac_sub_reseller_id=reseller_id,
        parent_customer_id=parent_id,
        is_active=True,
    )
    db.add(cust)
    db.flush()
    return cust


def _mixin(db, org_id):
    m = ResellerSyncMixin.__new__(ResellerSyncMixin)
    m.db = db
    m.organization_id = org_id
    return m


def test_single_match_promotes(db, org_id):
    target = _customer(db, org_id, email="boss@careng.ng")
    m = _mixin(db, org_id)
    found = m._find_promotable_customer(_reseller("boss@careng.ng"))
    assert found is not None
    assert found.customer_id == target.customer_id


def test_email_match_is_case_insensitive(db, org_id):
    target = _customer(db, org_id, email="Boss@CareNG.NG")
    m = _mixin(db, org_id)
    found = m._find_promotable_customer(_reseller("boss@careng.ng"))
    assert found is not None and found.customer_id == target.customer_id


def test_ambiguous_match_does_not_promote(db, org_id):
    _customer(db, org_id, email="dup@x.ng")
    _customer(db, org_id, email="dup@x.ng")
    m = _mixin(db, org_id)
    assert m._find_promotable_customer(_reseller("dup@x.ng")) is None


def test_ineligible_rows_are_excluded(db, org_id):
    # Already a reseller, a child, or never sub-synced → not promotable.
    _customer(db, org_id, email="x@x.ng", reseller_id=str(uuid.uuid4()))
    parent = _customer(db, org_id, email="parent@x.ng")
    _customer(db, org_id, email="x@x.ng", parent_id=parent.customer_id)
    _customer(db, org_id, email="x@x.ng", sub_id=None)
    m = _mixin(db, org_id)
    assert m._find_promotable_customer(_reseller("x@x.ng")) is None
