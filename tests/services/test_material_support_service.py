from unittest.mock import MagicMock, patch
from uuid import uuid4

from app.schemas.sync.dotmac_crm import (
    CRMMaterialRequestPayload,
    CRMMaterialRequestResponse,
)
from app.services.inventory.material_support import MaterialSupportService


def _payload() -> CRMMaterialRequestPayload:
    return CRMMaterialRequestPayload(
        omni_id=str(uuid4()),
        request_type="ISSUE",
        status="submitted",
        requested_by_email="field@example.com",
        schedule_date="2026-07-19",
        items=[
            {
                "item_code": "DROP-CABLE",
                "quantity": "1",
                "uom": "PCS",
                "from_warehouse_code": "WH-ABUJA",
            }
        ],
    )


def test_accept_sub_request_reports_create_and_replay() -> None:
    db = MagicMock()
    org_id = uuid4()
    actor_id = uuid4()
    payload = _payload()
    response = CRMMaterialRequestResponse(
        request_id=uuid4(),
        request_number="MR-0001",
        status="submitted",
        omni_id=payload.omni_id,
    )
    service = MaterialSupportService(db)

    with patch.object(
        service, "create_material_request", return_value=response
    ) as create:
        db.scalar.return_value = None
        created = service.accept_sub_request(
            organization_id=org_id,
            payload=payload,
            actor_person_id=actor_id,
        )
        db.scalar.return_value = response.request_id
        replayed = service.accept_sub_request(
            organization_id=org_id,
            payload=payload,
            actor_person_id=actor_id,
        )

    assert created.outcome == response
    assert created.replayed is False
    assert replayed.replayed is True
    assert create.call_count == 2
    create.assert_called_with(org_id, payload, actor_id, source_system="sub")


def test_get_sub_outcome_uses_source_request_identity() -> None:
    service = MaterialSupportService(MagicMock())
    org_id = uuid4()
    source_request_id = str(uuid4())

    with patch.object(
        service,
        "get_material_request_by_crm_id",
        return_value=None,
    ) as get_status:
        assert (
            service.get_sub_outcome(
                organization_id=org_id,
                source_request_id=source_request_id,
            )
            is None
        )

    get_status.assert_called_once_with(org_id, source_request_id)
