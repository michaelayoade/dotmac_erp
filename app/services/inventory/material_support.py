"""ERP owner for material support requested by Dotmac Sub service workflows.

Sub supplies an approved operational need.  ERP remains authoritative for
warehouse availability, serial validation, stock issue posting, and the
resulting backoffice status.  The existing CRM procurement implementation is
retained as a compatibility engine during migration; this service is the only
public owner used by the neutral ``/sync/sub/material-requests`` adapter.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.inventory.material_request import MaterialRequest
from app.schemas.sync.dotmac_crm import (
    CRMMaterialRequestPayload,
    CRMMaterialRequestResponse,
    CRMMaterialRequestStatusRead,
)
from app.services.sync.crm.procurement import _ProcurementMixin


@dataclass(frozen=True)
class MaterialSupportAcceptance:
    """Accepted ERP outcome plus whether the source request was a replay."""

    outcome: CRMMaterialRequestResponse
    replayed: bool


class MaterialSupportService(_ProcurementMixin):
    """Own the Sub-to-ERP material-support intake and outcome contract."""

    def __init__(self, db: Session):
        super().__init__(db)

    def accept_sub_request(
        self,
        *,
        organization_id: UUID,
        payload: CRMMaterialRequestPayload,
        actor_person_id: UUID | None,
    ) -> MaterialSupportAcceptance:
        """Accept one immutable Sub request and execute ERP inventory policy.

        ``omni_id`` is retained on the wire and in ``crm_id`` as a temporary
        compatibility field.  For this endpoint it always means the immutable
        Sub ``FieldMaterialRequest.id`` and never grants CRM authority.
        """
        replayed = bool(
            self.db.scalar(
                select(MaterialRequest.request_id).where(
                    MaterialRequest.organization_id == organization_id,
                    MaterialRequest.crm_id == payload.omni_id,
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
    ) -> CRMMaterialRequestStatusRead | None:
        """Return ERP's authoritative outcome for a Sub material request."""
        return self.get_material_request_by_crm_id(
            organization_id,
            source_request_id,
        )
