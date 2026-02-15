from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from uuid import UUID

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class DataCollector(ABC):
    """
    Base class for coach data collectors.

    Collectors read from existing services/models and return analysis-ready, non-PII
    bundles. Analyzers should keep math/deterministic computations in Python and
    reserve LLM calls for narration/interpretation.
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    @abstractmethod
    def collect(self, organization_id: UUID) -> dict:
        raise NotImplementedError
