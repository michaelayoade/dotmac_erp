"""Single writer for ERP Person projections into the kernel party catalogue.

``public.people`` remains authoritative. ``public.parties`` and
``public.party_persons`` are assembly-owned projections that supply
``party_person_catalog.v1`` — the identity reference a composed module such as
``dotmac-people`` points its employee row at. This service is the only runtime
writer of those rows.

The service mutates but never commits or rolls back. The ERP entry point that
changed the Person owns the transaction, so source and projection succeed or
fail together — the same rule ``app.services.tenant_projection`` follows for
the Organization catalogue, and for the same reason: a projection written in a
second transaction is a projection that can be permanently wrong after a
partial failure.

Identity is deliberately shared. ``parties.id`` IS ``people.id`` rather than a
new surrogate, so the projection needs no mapping table, cannot drift into a
duplicate, and can be rebuilt from the source alone at any time.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

PERSON_PARTY_TYPE = "person"
ORGANIZATION_PARTY_TYPE = "organization"
MAX_DISPLAY_NAME_LENGTH = 200
MAX_PERSON_NAME_LENGTH = 100


class PersonProjectionSource(Protocol):
    """The product-owned facts the kernel party catalogue projects."""

    id: UUID
    organization_id: UUID
    first_name: str
    last_name: str
    display_name: str | None
    is_active: bool


class PartyProjectionError(RuntimeError):
    """A Person cannot be represented truthfully as a kernel Party."""


def _name_part(value: str | None, label: str) -> str:
    text_value = (value or "").strip()
    if len(text_value) > MAX_PERSON_NAME_LENGTH:
        raise PartyProjectionError(
            f"person {label} exceeds the kernel PartyPerson limit of "
            f"{MAX_PERSON_NAME_LENGTH} characters"
        )
    return text_value


def party_display_name(person: PersonProjectionSource) -> str:
    """Return the display name the catalogue must carry for one Person.

    ERP lets ``display_name`` be blank and falls back to the name parts, so the
    projection has to make the same choice the ORM's ``name_expr`` makes rather
    than copying a column that is often empty.
    """
    explicit = (person.display_name or "").strip()
    if not explicit:
        first = _name_part(person.first_name, "first_name")
        last = _name_part(person.last_name, "last_name")
        explicit = f"{first} {last}".strip()
    if not explicit:
        raise PartyProjectionError(
            "person has no name to project as Party.display_name"
        )
    if len(explicit) > MAX_DISPLAY_NAME_LENGTH:
        raise PartyProjectionError(
            "person display name exceeds the kernel Party.display_name limit of "
            f"{MAX_DISPLAY_NAME_LENGTH} characters"
        )
    return explicit


def expected_party(person: PersonProjectionSource) -> dict[str, object]:
    """The exact row the catalogue must hold for one Person."""
    return {
        "id": person.id,
        "tenant_id": person.organization_id,
        "party_type": PERSON_PARTY_TYPE,
        "display_name": party_display_name(person),
        "is_active": bool(person.is_active),
        "first_name": _name_part(person.first_name, "first_name"),
        "last_name": _name_part(person.last_name, "last_name"),
    }


def person_party_drift(
    person: PersonProjectionSource,
    observed: dict[str, object],
) -> tuple[str, ...]:
    """Name every projected field that differs from its authoritative source."""
    expected = expected_party(person)
    return tuple(
        field
        for field, value in expected.items()
        if field in observed and observed[field] != value
    )


def reconcile_person_party(db: Session, person: PersonProjectionSource) -> None:
    """Make the catalogue agree with one Person, in the caller's transaction.

    Idempotent by construction: both statements are upserts keyed on the shared
    identity, so a repeat call, a retry and a repair sweep are the same
    operation.
    """
    row = expected_party(person)
    db.execute(
        text(
            """
            INSERT INTO public.parties
                (id, tenant_id, party_type, display_name, is_active)
            VALUES (:id, :tenant_id, :party_type, :display_name, :is_active)
            ON CONFLICT (id) DO UPDATE
               SET tenant_id = EXCLUDED.tenant_id,
                   party_type = EXCLUDED.party_type,
                   display_name = EXCLUDED.display_name,
                   is_active = EXCLUDED.is_active
            """
        ),
        {
            "id": row["id"],
            "tenant_id": row["tenant_id"],
            "party_type": row["party_type"],
            "display_name": row["display_name"],
            "is_active": row["is_active"],
        },
    )
    db.execute(
        text(
            """
            INSERT INTO public.party_persons (party_id, first_name, last_name)
            VALUES (:party_id, :first_name, :last_name)
            ON CONFLICT (party_id) DO UPDATE
               SET first_name = EXCLUDED.first_name,
                   last_name = EXCLUDED.last_name
            """
        ),
        {
            "party_id": row["id"],
            "first_name": row["first_name"],
            "last_name": row["last_name"],
        },
    )


def retire_person_party(db: Session, person_id: UUID) -> None:
    """Remove one person's catalogue rows in the caller's transaction.

    ``party_persons`` follows by cascade; deleting it separately would be a
    second writer of a fact the foreign key already owns.
    """
    db.execute(
        text("DELETE FROM public.parties WHERE id = :id AND party_type = :party_type"),
        {"id": person_id, "party_type": PERSON_PARTY_TYPE},
    )
