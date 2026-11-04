"""ERP owned supplier creation from a typed Sub vendor observation."""

import hashlib
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.domain_settings import DomainSetting, SettingDomain
from app.models.finance.ap.supplier import Supplier, SupplierType
from app.schemas.sync.sub_supplier import SubSupplierCreate, SubSupplierRead
from app.services.finance.ap.account_validation import require_ap_control_account
from app.services.finance.ap.supplier import SupplierInput, SupplierService

SETTING_KEY = "sub_supplier_ap_control_account_id"


def _account_id(db: Session, organization_id: UUID) -> UUID:
    setting = db.scalar(
        select(DomainSetting).where(
            DomainSetting.organization_id == organization_id,
            DomainSetting.domain == SettingDomain.gl,
            DomainSetting.key == SETTING_KEY,
            DomainSetting.is_active.is_(True),
        )
    )
    try:
        configured = (
            UUID(str(setting.value_text)) if setting and setting.value_text else None
        )
    except ValueError as exc:
        raise ValueError("Sub supplier AP control account setting is invalid") from exc
    if configured is None:
        raise ValueError("Sub supplier AP control account is not configured")
    require_ap_control_account(db, organization_id, configured)
    return configured


def create_supplier(
    db: Session, *, organization_id: UUID, command: SubSupplierCreate
) -> SubSupplierRead:
    existing = db.scalar(
        select(Supplier).where(
            Supplier.organization_id == organization_id,
            Supplier.sub_vendor_id == command.source_vendor_id,
        )
    )
    if existing is not None:
        return SubSupplierRead(
            source_vendor_id=command.source_vendor_id,
            supplier_id=existing.supplier_id,
            supplier_code=existing.supplier_code,
            created=False,
        )
    account_id = _account_id(db, organization_id)
    code = (command.code or "").strip()
    if not code or len(code) > 30:
        code = (
            "SUB-"
            + hashlib.sha256(command.source_vendor_id.bytes).hexdigest()[:20].upper()
        )
    name = command.name.strip()
    if not name:
        raise ValueError("Vendor name is required")
    supplier = SupplierService.create_supplier(
        db,
        organization_id,
        SupplierInput(
            supplier_code=code,
            supplier_type=SupplierType.VENDOR,
            supplier_name=name,
            trading_name=name,
            default_payable_account_id=account_id,
            primary_contact={
                "name": command.contact_name,
                "email": command.contact_email,
                "phone": command.contact_phone,
            },
        ),
    )
    supplier.sub_vendor_id = command.source_vendor_id
    supplier.is_active = command.is_active
    db.flush()
    return SubSupplierRead(
        source_vendor_id=command.source_vendor_id,
        supplier_id=supplier.supplier_id,
        supplier_code=supplier.supplier_code,
        created=True,
    )


def get_supplier(
    db: Session, *, organization_id: UUID, source_vendor_id: UUID
) -> SubSupplierRead | None:
    supplier = db.scalar(
        select(Supplier).where(
            Supplier.organization_id == organization_id,
            Supplier.sub_vendor_id == source_vendor_id,
        )
    )
    if supplier is None:
        return None
    return SubSupplierRead(
        source_vendor_id=source_vendor_id,
        supplier_id=supplier.supplier_id,
        supplier_code=supplier.supplier_code,
        created=False,
    )
