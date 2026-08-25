"""The Person-to-Party projection decides one row, and refuses what it cannot."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest

from app.services.party_projection import (
    MAX_DISPLAY_NAME_LENGTH,
    MAX_PERSON_NAME_LENGTH,
    PERSON_PARTY_TYPE,
    PartyProjectionError,
    expected_party,
    party_display_name,
    person_party_drift,
)


@dataclass
class _Person:
    id: UUID
    organization_id: UUID
    first_name: str
    last_name: str
    display_name: str | None = None
    is_active: bool = True


def _person(**overrides: object) -> _Person:
    values: dict[str, object] = {
        "id": uuid4(),
        "organization_id": uuid4(),
        "first_name": "Ada",
        "last_name": "Lovelace",
    }
    values.update(overrides)
    return _Person(**values)  # type: ignore[arg-type]


def test_display_name_prefers_the_explicit_one() -> None:
    person = _person(display_name="Countess Lovelace")
    assert party_display_name(person) == "Countess Lovelace"


def test_display_name_falls_back_to_the_name_parts() -> None:
    """ERP allows a blank display_name, so a copied column is often empty."""
    for blank in (None, "", "   "):
        assert party_display_name(_person(display_name=blank)) == "Ada Lovelace"


def test_a_person_with_no_name_at_all_is_refused() -> None:
    person = _person(first_name="  ", last_name="", display_name=" ")
    with pytest.raises(PartyProjectionError, match="no name"):
        party_display_name(person)


def test_an_oversized_name_part_is_refused_before_the_database_sees_it() -> None:
    person = _person(first_name="A" * (MAX_PERSON_NAME_LENGTH + 1))
    with pytest.raises(PartyProjectionError, match="PartyPerson limit"):
        expected_party(person)


def test_an_oversized_display_name_is_refused() -> None:
    person = _person(display_name="D" * (MAX_DISPLAY_NAME_LENGTH + 1))
    with pytest.raises(PartyProjectionError, match="display_name limit"):
        party_display_name(person)


def test_the_projected_row_shares_the_person_identity() -> None:
    person = _person()
    row = expected_party(person)
    assert row["id"] == person.id
    assert row["tenant_id"] == person.organization_id
    assert row["party_type"] == PERSON_PARTY_TYPE
    assert row["display_name"] == "Ada Lovelace"
    assert row["first_name"] == "Ada"
    assert row["last_name"] == "Lovelace"


def test_names_are_trimmed_on_the_way_into_the_catalogue() -> None:
    row = expected_party(_person(first_name="  Ada  ", last_name=" Lovelace "))
    assert row["first_name"] == "Ada"
    assert row["last_name"] == "Lovelace"


def test_drift_names_every_field_that_disagrees() -> None:
    person = _person()
    observed = dict(expected_party(person))
    assert person_party_drift(person, observed) == ()

    observed["display_name"] = "Someone Else"
    observed["is_active"] = False
    assert set(person_party_drift(person, observed)) == {"display_name", "is_active"}


def test_drift_ignores_fields_the_catalogue_does_not_carry() -> None:
    """A partial read is not evidence of drift in what it did not select."""
    person = _person()
    assert person_party_drift(person, {"id": person.id}) == ()


def test_the_drift_detector_is_sensitive_to_a_moved_tenant() -> None:
    person = _person()
    observed = dict(expected_party(person))
    observed["tenant_id"] = uuid4()
    assert person_party_drift(person, observed) == ("tenant_id",)
