"""Integrator ProductPort wire contract for Sub's invoice-accounting-sync feed.

ERP is an ingress adapter, not a party to the generic v2 envelope's design:
the outer ``IntegratorInvoiceSyncEnvelope`` is the provider-neutral
``dotmac.io/product-observation/v1`` shape ``dotmac_integration`` and
``dotmac_integrator`` already define and transport unchanged (Sub's own
settlement/messaging receivers publish the identical outer shape — see
``dotmac_sub/app/schemas/integrator_settlement_observation.py``), while
``InvoiceAccountingSyncObservation`` is ERP's own explicitly published inner
contract, matching field-for-field what
``dotmac_connector_sub_accounting.mapping.map_item`` actually produces
(verified directly against that module's source, not re-derived).

The idempotency key travels as the ``Idempotency-Key`` HTTP header, never a
body field — ``organization_id`` is likewise never a body field; both come
exclusively from the authenticated service principal / request headers at
the route layer. There is no field here for either to disagree with.

## The ``contract_version`` naming collision (read this before touching either field)

Two DIFFERENT fields share the name ``contract_version`` at two different
nesting levels, and they must never be conflated:

- ``IntegratorInvoiceSyncEnvelope.contract_version`` is an **int** (currently
  always ``1``) — the TRANSPORT contract version this destination's
  descriptor declares for the generic ProductObservation wire itself.
- ``InvoiceAccountingSyncObservation.contract_version`` is a **string**
  (``"invoice-accounting-sync.v2"``) — Sub's own FEED contract version for
  the invoice-accounting-sync capability, completely independent of the
  transport wire version above.

## Five fields with no ERP counterpart — declared, and explicitly discarded

``source_account_id``, ``source_total_amount``, ``source_currency``,
``source_issued_at`` and ``source_due_at`` are part of the connector's real
wire output (so ``extra="forbid"`` would reject every real delivery without
them) but have NO counterpart anywhere in ERP's internal
``RecordInvoiceSyncOutcome`` / ``DotmacSubInvoiceSyncOutcome`` persistence
model. They are validated here and then thrown away — a future reviewer
should not assume they are persisted anywhere. ERP's invoice-accounting-sync
outcome table records only invoice identity, revision, disposition, kind,
digest/fingerprint and issue evidence; it is not a ledger of Self-Care's
account, money or date fields.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Final, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.services.dotmac_sub.invoice_sync_outcomes import (
    InvoiceSyncDisposition,
    InvoiceSyncIssueCode,
    InvoiceSyncSourceKind,
)

PRODUCT_OBSERVATION_SCHEMA_VERSION: Final[
    Literal["dotmac.io/product-observation/v1"]
] = "dotmac.io/product-observation/v1"
INVOICE_ACCOUNTING_SYNC_CAPABILITY: Final[
    Literal["invoices.accounting_sync.observation.v1"]
] = "invoices.accounting_sync.observation.v1"
INVOICE_ACCOUNTING_SYNC_FEED_CONTRACT_VERSION: Final[
    Literal["invoice-accounting-sync.v2"]
] = "invoice-accounting-sync.v2"


class IntegratorDestinationScope(BaseModel):
    """ERP's opaque local stream name, carried but never interpreted upstream."""

    model_config = ConfigDict(extra="forbid")

    kind: str = Field(min_length=1, max_length=60)
    ref: str = Field(min_length=1, max_length=160)


class IntegratorObservationSource(BaseModel):
    """Durable engine provenance, never a connector-payload field."""

    model_config = ConfigDict(extra="forbid")

    installation_id: UUID
    connector_key: str = Field(min_length=1, max_length=160)


class InvoiceAccountingSyncIssue(BaseModel):
    """One piece of blocked-disposition evidence.

    Only ``code`` is guaranteed present. Sub's real
    ``InvoiceAccountingSyncIssueRead`` (and the connector's ``map_item``,
    which forwards it) declares ``source_line_id``/``expected_amount``/
    ``actual_amount`` as genuinely optional — a header-level issue such as
    an unallocated discount has no specific line and no expected amount,
    only an actual one. The connector OMITS an absent optional field from
    the wire payload rather than sending it as explicit ``null``; a present
    field that fails to parse is still rejected at the connector before it
    is ever sent. These are typed ``| None`` here both because the wire
    payload can genuinely omit them and to match ERP's own
    ``InvoiceSyncIssueEvidence`` persistence shape (that dataclass is frozen
    for this task). Confirmed against the connector's real, shared-fixture
    contract test (dotmac_connector_sub_accounting,
    ``tests/test_shared_fixture_contract.py``) — a prior version of this
    docstring incorrectly claimed all four fields are always populated.
    """

    model_config = ConfigDict(extra="forbid")

    code: InvoiceSyncIssueCode
    source_line_id: UUID | None = None
    expected_amount: Decimal | None = None
    actual_amount: Decimal | None = None


class InvoiceAccountingSyncObservation(BaseModel):
    """ERP's explicitly published inner contract — Sub's invoice-accounting-sync
    feed item, exactly as ``dotmac_connector_sub_accounting.mapping.map_item``
    produces it. See the module docstring for the ``contract_version`` naming
    collision with the outer envelope, and for the five discarded fields.
    """

    model_config = ConfigDict(extra="forbid")

    capability_id: Literal["invoices.accounting_sync.observation.v1"]
    contract_version: Literal["invoice-accounting-sync.v2"]
    source_invoice_id: UUID
    source_account_id: UUID
    source_updated_at: datetime
    source_kind: InvoiceSyncSourceKind
    disposition: InvoiceSyncDisposition
    source_total_amount: Decimal
    source_currency: str = Field(pattern=r"^[A-Z]{3}$")
    source_issued_at: datetime | None = None
    source_due_at: datetime | None = None
    digest_version: int = Field(strict=True)
    projection_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    issues: tuple[InvoiceAccountingSyncIssue, ...] = ()


class IntegratorInvoiceSyncEnvelope(BaseModel):
    """Generic ProductObservation v1 carrying ERP's typed invoice-sync body.

    ``capability_id``/``event_type`` are pinned to the same literal as the
    inner observation's own ``capability_id`` — a free consistency check that
    needs no extra validator, mirroring Sub's identical shipped pattern.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["dotmac.io/product-observation/v1"]
    capability_id: Literal["invoices.accounting_sync.observation.v1"]
    contract_version: Literal[1]
    source: IntegratorObservationSource
    provider_event_id: str = Field(min_length=1, max_length=255)
    event_type: Literal["invoices.accounting_sync.observation.v1"]
    scope: IntegratorDestinationScope
    observation: InvoiceAccountingSyncObservation


class IntegratorInvoiceSyncReceipt(BaseModel):
    """The write route's success response.

    ``observation_id``/``outcome``/``processing_status``/``replayed`` are the
    four keys the real Integrator client (``ObservationPortClient._outcome``)
    actually reads. ``occurrence_count``/``resolved_prior_count`` are extra,
    informational-only keys the client ignores on a 200 — kept for operators
    and for step 5's own use.
    """

    observation_id: str
    outcome: str
    processing_status: str
    replayed: bool
    occurrence_count: int
    resolved_prior_count: int


class IntegratorInvoiceSyncMirrorDisagreement(BaseModel):
    field: str
    integrator: str | None
    erp: str | None


class IntegratorInvoiceSyncMirrorReport(BaseModel):
    """Read-only cutover evidence; a mirror request never writes a row."""

    verdict: Literal["match", "missing", "blocked"]
    agrees: bool
    identity: str
    counterpart_identity: str | None = None
    blocking_reasons: tuple[str, ...] = ()
    disagreements: tuple[IntegratorInvoiceSyncMirrorDisagreement, ...] = ()


class ProductPortDescriptorV2(BaseModel):
    """ERP's authenticated v2 declaration for the generic observation wire."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["dotmac.io/product-port-descriptor/v2"]
    application: Literal["erp"]
    owner_module: str = Field(min_length=1, max_length=160)
    capability_id: Literal["invoices.accounting_sync.observation.v1"]
    capability_summary: str = Field(min_length=1, max_length=500)
    contract_version: Literal[1]
    destination_binding_id: UUID
    delivery_path: str = Field(pattern=r"^/")
    mirror_path: str = Field(pattern=r"^/")
    destination_scope: IntegratorDestinationScope
    activation_state: Literal[
        "configured_disabled", "enabled", "quarantined", "retired"
    ]
    source_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    descriptor_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
