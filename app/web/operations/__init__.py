"""
Operations web module placeholder.

Operations routes were split into standalone modules. This router is intentionally
empty to avoid importing deprecated paths.
"""

from fastapi import APIRouter

router = APIRouter(prefix="/operations", tags=["operations-web"])
