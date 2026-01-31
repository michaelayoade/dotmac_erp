"""
PFA Directory Model - Core Org.

System-wide directory of Nigerian Pension Fund Administrators (PFAs).
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, String, func, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class PFADirectory(Base):
    """
    System-wide directory of Pension Fund Administrators (PFAs).

    Used for looking up PFA codes for pension exports (Paypen format)
    and validating employee RSA PIN assignments.
    """

    __tablename__ = "pfa_directory"
    __table_args__ = {"schema": "core_org"}

    # PenCom-assigned PFA code
    pfa_code: Mapped[str] = mapped_column(
        String(10),
        primary_key=True,
        comment="PenCom-assigned PFA code",
    )

    # Official PFA name
    pfa_name: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
        unique=True,
        comment="Official PFA name",
    )

    # Short name/abbreviation
    short_name: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
        comment="Common short name or abbreviation",
    )

    # Alternative names for matching
    aliases: Mapped[Optional[list[str]]] = mapped_column(
        ARRAY(String(100)),
        nullable=True,
        comment="Alternative names for fuzzy matching",
    )

    # Contact info (optional)
    website: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
    )
    email: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
    )
    phone: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
