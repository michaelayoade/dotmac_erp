"""
IFRS Accounting Services.

This package contains service implementations for the IFRS accounting system.
"""

from app.services.ifrs import platform
from app.services.ifrs import gl
from app.services.ifrs import ap
from app.services.ifrs import ar
from app.services.ifrs import fa
from app.services.ifrs import inv
from app.services.ifrs import lease
from app.services.ifrs import fin_inst
from app.services.ifrs import tax
from app.services.ifrs import cons
from app.services.ifrs import rpt

__all__ = ["platform", "gl", "ap", "ar", "fa", "inv", "lease", "fin_inst", "tax", "cons", "rpt"]
