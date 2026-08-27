"""ERP owner for material support requested by Dotmac Sub service workflows.

Sub supplies an approved operational need.  ERP remains authoritative for
warehouse availability, serial validation, stock issue posting, and the
resulting backoffice status. The source-qualified Sub projection service is an
implementation detail; this service remains the sole material-support owner.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.inventory.material_request import MaterialRequest
from app.schemas.sync.sub_operational import (
    SubMaterialRequestPayload,
    SubMaterialRequestResponse,
    SubMaterialRequestStatusRead,
)
from app.services.sync.sub.procurement import _ProcurementMixin


@dataclass(frozen=True)
class MaterialSupportAcceptance:
    """Accepted ERP outcome plus whether the source request was a replay."""

    outcome: SubMaterialRequestResponse
    replayed: bool


class MaterialSupportService(_ProcurementMixin):
    """Own the Sub-to-ERP material-support intake and outcome contract."""

    def __init__(self, db: Session):
        super().__init__(db)

    def accept_sub_request(
        self,
        *,
        organization_id: UUID,
        payload: SubMaterialRequestPayload,
        actor_person_id: UUID | None,
    ) -> MaterialSupportAcceptance:
        """Accept one immutable Sub request and execute ERP inventory policy.

        ``source_request_id`` is the immutable Sub ``FieldMaterialRequest.id`` and is
        stored with an explicit ``source_system='sub'`` discriminator.
        """
        replayed = bool(
            self.db.scalar(
                select(MaterialRequest.request_id).where(
                    MaterialRequest.organization_id == organization_id,
                    MaterialRequest.source_system == "sub",
                    MaterialRequest.source_reference == payload.source_request_id,
                )
            )
        )
        outcome = self.create_material_request(
            organization_id,
            payload,
            actor_person_id,
        )
        return MaterialSupportAcceptance(outcome=outcome, replayed=replayed)

    def get_sub_outcome(
        self,
        *,
        organization_id: UUID,
        source_request_id: str,
    ) -> SubMaterialRequestStatusRead | None:
        """Return ERP's authoritative outcome for a Sub material request."""
        return self.get_material_request_by_source_reference(
            organization_id,
            source_request_id,
        )
