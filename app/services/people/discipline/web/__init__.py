"""
Discipline Web Services.

Provides view-focused operations for discipline web routes.
"""

from app.services.people.discipline.web.discipline_web import DisciplineWebService

discipline_web_service = DisciplineWebService()

__all__ = ["discipline_web_service", "DisciplineWebService"]
