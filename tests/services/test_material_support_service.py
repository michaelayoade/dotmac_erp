from unittest.mock import MagicMock, patch
from uuid import uuid4

from app.schemas.sync.sub_operational import (
    SubMaterialRequestPayload,
    SubMaterialRequestResponse,
)
from app.services.inventory.material_support import MaterialSupportService


def _payload() -> SubMaterialRequestPayload:
    return SubMaterialRequestPayload(
        source_request_id=str(uuid4()),
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
    response = SubMaterialRequestResponse(
        request_id=uuid4(),
        request_number="MR-0001",
        status="submitted",
        source_request_id=payload.source_request_id,
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
    create.assert_called_with(org_id, payload, actor_id)


def test_get_sub_outcome_uses_source_request_identity() -> None:
    service = MaterialSupportService(MagicMock())
    org_id = uuid4()
    source_request_id = str(uuid4())

    with patch.object(
        service,
        "get_material_request_by_source_reference",
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


def test_sub_status_lookup_is_qualified_against_legacy_collision() -> None:
    db = MagicMock()
    service = MaterialSupportService(db)
    db.scalar.return_value = None

    assert (
        service.get_sub_outcome(
            organization_id=uuid4(),
            source_request_id="same-reference",
        )
        is None
    )

    sql = str(db.scalar.call_args.args[0])
    assert "material_request.source_system" in sql
    assert "material_request.source_reference" in sql


def test_pending_worker_leaves_legacy_unknown_request_unchecked() -> None:
    db = MagicMock()
    service = MaterialSupportService(db)
    legacy_unknown = MagicMock(source_system="legacy_unknown", status="PENDING_STOCK")

    def rows_for(statement):
        sql = str(statement.compile(compile_kwargs={"literal_binds": True}))
        assert "source_system = 'sub'" in sql
        result = MagicMock()
        result.unique.return_value.all.return_value = []
        return result

    db.scalars.side_effect = rows_for
    result = service.process_pending_stock_material_requests(uuid4())

    assert result["checked"] == 0
    assert result["issued"] == 0
    assert legacy_unknown.status == "PENDING_STOCK"
