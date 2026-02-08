"""
Bank Directory Model - Core Org.

System-wide directory of Nigerian banks with codes and aliases for lookup.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, func, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class BankDirectory(Base):
    """
    System-wide bank directory for Nigerian banks.

    Used for looking up bank codes from bank names (including aliases)
    for generating bank upload files and statutory exports.
    """

    __tablename__ = "bank_directory"
    __table_args__ = {"schema": "core_org"}

    # CBN/NIBSS bank code (e.g., "057" for Zenith, "058" for GTBank)
    bank_code: Mapped[str] = mapped_column(
        String(10),
        primary_key=True,
        comment="CBN/NIBSS bank code",
    )

    # Official bank name
    bank_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        unique=True,
        comment="Official bank name",
    )

    # NIBSS institution code (may differ from bank_code for some banks)
    nibss_code: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
        comment="NIBSS institution code if different from bank_code",
    )

    # Alternative names for matching (e.g., ["GTBank", "GT Bank", "Guaranty Trust"])
    aliases: Mapped[list[str] | None] = mapped_column(
        ARRAY(String(100)),
        nullable=True,
        comment="Alternative names for fuzzy matching",
    )

    # Sort code prefix (for banks that use it)
    sort_code_prefix: Mapped[str | None] = mapped_column(
        String(10),
        nullable=True,
        comment="Bank sort code prefix",
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
