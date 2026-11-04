"""Versioned Sub vendor to ERP supplier contract."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SubSupplierCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: int = Field(default=1, ge=1, le=1)
    source_vendor_id: UUID
    name: str = Field(min_length=1, max_length=160)
    code: str | None = Field(default=None, max_length=60)
    contact_name: str | None = Field(default=None, max_length=160)
    contact_email: str | None = Field(default=None, max_length=255)
    contact_phone: str | None = Field(default=None, max_length=40)
    is_active: bool = True


class SubSupplierRead(BaseModel):
    contract_version: int = 1
    source_vendor_id: UUID
    supplier_id: UUID
    supplier_code: str
    created: bool
