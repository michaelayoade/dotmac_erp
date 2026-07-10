"""Reseller merge-on-promotion (hermetic).

A reseller created by promoting an existing subscriber is the same
real-world entity as that subscriber's customer row. The sync must promote
that row into the reseller parent (history stays on the entity) instead of
creating an empty duplicate parent.
"""

from unittest.mock import MagicMock
from uuid import uuid4

from app.models.finance.ar.customer import CustomerType
from app.services.dotmac_sub.client import ResellerRecord
from app.services.dotmac_sub.sync._resellers import ResellerSyncMixin
from app.services.dotmac_sub.sync._types import SyncResult


def _reseller(**overrides):
    base = dict(
        id=str(uuid4()),
        name="CareNG",
        code="SPL-16",
        contact_email="boss@careng.ng",
        contact_phone="+2348030000000",
        is_active=True,
    )
    base.update(overrides)
    return ResellerRecord(**base)


def _mixin(*, reseller_lookup=None, promotable=None):
    m = ResellerSyncMixin.__new__(ResellerSyncMixin)
    m.db = MagicMock()
    m.organization_id = uuid4()
    m.ar_control_account_id = uuid4()
    m.default_revenue_account_id = None
    m._reseller_cache = {}
    m.SOURCE_PREFIX = "DSUB"
    m.CUSTOMER_CODE_MAX = 30
    m._customer_code = lambda marker, ref: f"DSUB-{marker}-{ref.replace('-', '')}"[:30]
    m._compute_hash = MagicMock(return_value="hash")
    m._has_changed = MagicMock(return_value=True)
    m._record_sync = MagicMock()
    m._get_synced_entity = MagicMock(return_value=None)
    m._get_reseller_customer = MagicMock(return_value=reseller_lookup)
    m._find_promotable_customer = MagicMock(return_value=promotable)
    return m


def _existing_subscriber_customer():
    cust = MagicMock()
    cust.customer_id = uuid4()
    cust.dotmac_sub_id = str(uuid4())  # subscriber id — must be preserved
    cust.dotmac_sub_reseller_id = None
    cust.parent_customer_id = None
    cust.customer_type = CustomerType.INDIVIDUAL
    return cust


def test_promotion_reuses_existing_customer_row():
    reseller = _reseller()
    cust = _existing_subscriber_customer()
    original_sub_id = cust.dotmac_sub_id
    m = _mixin(reseller_lookup=None, promotable=cust)
    result = SyncResult(success=True, entity_type="resellers")

    m._sync_single_reseller(reseller, None, result, skip_unchanged=False)

    # Promoted in place: became the reseller parent, kept its subscriber id
    # (and its AR history with it), no new customer row created.
    assert cust.customer_type == CustomerType.COMPANY
    assert cust.dotmac_sub_reseller_id == reseller.id
    assert cust.dotmac_sub_id == original_sub_id
    assert cust.legal_name == "CareNG"
    m.db.add.assert_not_called()
    assert result.updated == 1
    assert result.created == 0
    m._record_sync.assert_called_once()


def test_no_match_creates_fresh_parent():
    reseller = _reseller()
    m = _mixin(reseller_lookup=None, promotable=None)
    result = SyncResult(success=True, entity_type="resellers")

    m._sync_single_reseller(reseller, None, result, skip_unchanged=False)

    m.db.add.assert_called_once()
    assert result.created == 1


def test_already_mapped_reseller_skips_promotion_lookup():
    reseller = _reseller()
    parent = _existing_subscriber_customer()
    parent.dotmac_sub_reseller_id = reseller.id
    m = _mixin(reseller_lookup=parent, promotable=None)
    result = SyncResult(success=True, entity_type="resellers")

    m._sync_single_reseller(reseller, None, result, skip_unchanged=False)

    m._find_promotable_customer.assert_not_called()
    # Regular update path must NOT rewrite customer_type.
    assert parent.customer_type == CustomerType.INDIVIDUAL
    assert result.updated == 1


def test_find_promotable_requires_email_and_unambiguous_match():
    m = ResellerSyncMixin.__new__(ResellerSyncMixin)
    m.organization_id = uuid4()
    m.db = MagicMock()

    # No email → no lookup at all.
    assert m._find_promotable_customer(_reseller(contact_email=None)) is None
    m.db.scalars.assert_not_called()

    # Two matches → ambiguous → None.
    m.db.scalars.return_value = [MagicMock(), MagicMock()]
    assert m._find_promotable_customer(_reseller()) is None

    # Exactly one match → promote it.
    only = MagicMock()
    m.db.scalars.return_value = [only]
    assert m._find_promotable_customer(_reseller()) is only
