"""
SagaFactory - Registry for saga orchestrators.

Provides a central registry for saga types, enabling dynamic
lookup of orchestrators by saga type name.
"""

from __future__ import annotations

import logging

from app.services.finance.platform.saga_orchestrator import SagaOrchestrator

logger = logging.getLogger(__name__)


class SagaFactory:
    """
    Factory/registry for saga orchestrators.

    Saga types register themselves with the factory, enabling
    dynamic lookup and recovery of sagas.
    """

    def __init__(self):
        self._orchestrators: dict[str, SagaOrchestrator] = {}

    def register(self, orchestrator: SagaOrchestrator) -> None:
        """
        Register a saga orchestrator.

        Args:
            orchestrator: The orchestrator instance to register
        """
        saga_type = orchestrator.saga_type
        if saga_type in self._orchestrators:
            logger.warning(
                "Replacing existing orchestrator for saga type: %s", saga_type
            )
        self._orchestrators[saga_type] = orchestrator
        logger.info("Registered saga orchestrator: %s", saga_type)

    def get_orchestrator(self, saga_type: str) -> SagaOrchestrator | None:
        """
        Get an orchestrator by saga type.

        Args:
            saga_type: The saga type identifier

        Returns:
            The registered orchestrator or None
        """
        return self._orchestrators.get(saga_type)

    def list_types(self) -> list[str]:
        """List all registered saga types."""
        return list(self._orchestrators.keys())


# Global factory instance
saga_factory = SagaFactory()


def register_saga(orchestrator: SagaOrchestrator) -> SagaOrchestrator:
    """
    Decorator/helper to register a saga orchestrator.

    Usage:
        orchestrator = register_saga(MyPostingSaga())
    """
    saga_factory.register(orchestrator)
    return orchestrator
