from __future__ import annotations

from datetime import date
from uuid import UUID

SYSTEM_USER_ID = UUID("00000000-0000-0000-0000-000000000000")

DOTMAC_SUB_SYNC_MIN_DATE = date(2026, 1, 1)

_PRE_CUTOFF_SENTINEL = UUID("00000000-0000-0000-0000-000000000001")

DEFAULT_BANK_NAME_MAPPING: dict[str, str | None] = {
    "zenith 461": "zenith 461",
    "zenith 523": "zenith 523",
    "paystack": "paystack",
    "pay stack": "paystack",
    "flutterwave": "flutterwave",
    "flutter wave": "flutterwave",
    "uba": "uba 96",
    "dotmac usd": "zenith usd",
    "cash": None,
}
