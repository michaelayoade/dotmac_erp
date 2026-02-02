"""
Remita Integration Models.

Models for Remita RRR (Remita Retrieval Reference) generation and payment tracking
for government-related transactions: PAYE, NHF, Pension, taxes, fees, etc.
"""

from app.models.finance.remita.rrr import RemitaRRR, RRRStatus

__all__ = [
    "RemitaRRR",
    "RRRStatus",
]
