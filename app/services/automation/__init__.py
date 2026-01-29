"""
Automation Services.

Cross-cutting services for document generation, workflows, and templates.
"""

from app.services.automation.document_generator import DocumentGeneratorService

__all__ = [
    "DocumentGeneratorService",
]
