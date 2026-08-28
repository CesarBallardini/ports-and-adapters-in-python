"""Unit tests for the guardianship bounded context."""

from datetime import date
from uuid import UUID

import pytest

from academy.domain.guardianship.guardianship import (
    Guardianship,
    SelfGuardianshipError,
    WrongWardError,
)
from academy.domain.people.age_of_majority import AgeOfMajority
from academy.domain.people.email import Email
from academy.domain.people.person import Person
from academy.domain.people.personal_data import PersonalData
from academy.domain.shared.ids import GuardianshipId, PersonId

MAJORITY = AgeOfMajority(18)
GUARDIAN_ID = PersonId(UUID(int=1))
WARD_ID = PersonId(UUID(int=2))


def ward(birth: date) -> Person:
    return Person(WARD_ID, Email('ward@example.com'), PersonalData('Kid', birth))


@pytest.mark.unit
def test_guardianship_cannot_link_a_person_to_themselves() -> None:
    with pytest.raises(SelfGuardianshipError):
        Guardianship(GuardianshipId(UUID(int=9)), GUARDIAN_ID, GUARDIAN_ID)


@pytest.mark.unit
def test_guardianship_applies_while_ward_is_a_minor() -> None:
    link = Guardianship(GuardianshipId(UUID(int=9)), GUARDIAN_ID, WARD_ID)

    assert link.applies(ward(date(2010, 1, 1)), MAJORITY, date(2026, 1, 1))


@pytest.mark.unit
def test_guardianship_does_not_apply_once_ward_is_an_adult() -> None:
    link = Guardianship(GuardianshipId(UUID(int=9)), GUARDIAN_ID, WARD_ID)

    assert not link.applies(ward(date(2000, 1, 1)), MAJORITY, date(2026, 1, 1))


@pytest.mark.unit
def test_applies_rejects_a_person_who_is_not_the_ward() -> None:
    link = Guardianship(GuardianshipId(UUID(int=9)), GUARDIAN_ID, WARD_ID)
    someone_else = Person(PersonId(UUID(int=3)), Email('x@example.com'), PersonalData('X', date(2010, 1, 1)))

    with pytest.raises(WrongWardError):
        link.applies(someone_else, MAJORITY, date(2026, 1, 1))
