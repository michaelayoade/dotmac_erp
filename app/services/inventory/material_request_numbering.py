"""
Material Request numbering service.

Generates MAT-MR-YYYY-##### numbers with a yearly reset and
bootstrap from existing request_number values.
"""

from datetime import date
from uuid import UUID

from sqlalchemy import Integer, cast, func, select
from sqlalchemy.orm import Session

from app.models.finance.core_config.numbering_sequence import NumberingSequence, ResetFrequency, SequenceType
from app.models.finance.inv.material_request import MaterialRequest


class MaterialRequestNumberingService:
    """Generate material request numbers with yearly resets."""

    @staticmethod
    def _get_or_create_sequence(db: Session, organization_id: UUID) -> NumberingSequence:
        sequence = (
            db.query(NumberingSequence)
            .filter(
                NumberingSequence.organization_id == organization_id,
                NumberingSequence.sequence_type == SequenceType.MATERIAL_REQUEST,
            )
            .with_for_update()
            .first()
        )
        if sequence:
            return sequence

        sequence = NumberingSequence(
            organization_id=organization_id,
            sequence_type=SequenceType.MATERIAL_REQUEST,
            prefix="MAT-MR-",
            suffix="",
            separator="-",
            min_digits=5,
            include_year=True,
            include_month=False,
            year_format=4,
            current_number=0,
            current_year=None,
            current_month=None,
            reset_frequency=ResetFrequency.YEARLY,
        )
        db.add(sequence)
        db.flush()
        return sequence

    @staticmethod
    def _max_existing_for_year(
        db: Session,
        organization_id: UUID,
        year: int,
    ) -> int:
        prefix = f"MAT-MR-{year}-"
        stmt = (
            select(
                func.max(
                    cast(
                        func.substring(MaterialRequest.request_number, r"([0-9]+)$"),
                        Integer,
                    )
                )
            )
            .where(
                MaterialRequest.organization_id == organization_id,
                MaterialRequest.request_number.like(f"{prefix}%"),
            )
        )
        return int(db.scalar(stmt) or 0)

    @classmethod
    def get_next_number(cls, db: Session, organization_id: UUID) -> str:
        year = date.today().year
        sequence = cls._get_or_create_sequence(db, organization_id)

        max_existing = None
        if sequence.current_year != year:
            max_existing = cls._max_existing_for_year(db, organization_id, year)
            sequence.current_year = year
            sequence.current_number = max_existing
        elif sequence.current_number == 0:
            max_existing = cls._max_existing_for_year(db, organization_id, year)
            if max_existing > sequence.current_number:
                sequence.current_number = max_existing

        if not sequence.min_digits or sequence.min_digits < 5:
            sequence.min_digits = 5

        sequence.current_number += 1
        db.flush()

        number_str = str(sequence.current_number).zfill(sequence.min_digits or 5)
        return f"MAT-MR-{year}-{number_str}"


material_request_numbering_service = MaterialRequestNumberingService()
