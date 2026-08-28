"""Unit tests for the people bounded context."""

from datetime import date
from uuid import UUID

import pytest

from academy.domain.people.age_of_majority import AgeOfMajority, InvalidAgeOfMajorityError
from academy.domain.people.credential import Credential
from academy.domain.people.email import Email, InvalidEmailError
from academy.domain.people.person import Person
from academy.domain.people.personal_data import InvalidPersonalDataError, PersonalData
from academy.domain.people.role import Role
from academy.domain.shared.ids import CredentialId, PersonId, SubjectId


def make_person(roles: set[Role] | None = None, birth: date = date(2000, 6, 15)) -> Person:
    return Person(
        PersonId(UUID(int=1)),
        Email('ann@example.com'),
        PersonalData('Ann', birth),
        roles=roles,
    )


@pytest.mark.unit
def test_email_is_normalized_to_lower_case_and_trimmed() -> None:
    assert Email('  Ann@Example.COM ').value == 'ann@example.com'


@pytest.mark.unit
@pytest.mark.parametrize('bad', ['noatsign', '@no-local.com', 'no-domain@', 'no@dot', 'a@.com', 'a@b.'])
def test_invalid_email_raises(bad: str) -> None:
    with pytest.raises(InvalidEmailError):
        Email(bad)


@pytest.mark.unit
def test_age_counts_completed_years() -> None:
    data = PersonalData('Ann', date(2000, 6, 15))

    assert data.age(date(2018, 6, 15)) == 18
    assert data.age(date(2018, 6, 14)) == 17


@pytest.mark.unit
def test_personal_data_rejects_empty_name() -> None:
    with pytest.raises(InvalidPersonalDataError):
        PersonalData('   ', date(2000, 1, 1))


@pytest.mark.unit
def test_age_rejects_date_before_birth() -> None:
    data = PersonalData('Ann', date(2000, 1, 1))

    with pytest.raises(InvalidPersonalDataError):
        data.age(date(1999, 12, 31))


@pytest.mark.unit
def test_age_of_majority_must_be_positive() -> None:
    with pytest.raises(InvalidAgeOfMajorityError):
        AgeOfMajority(0)


@pytest.mark.unit
def test_is_of_legal_age_on_the_boundary() -> None:
    person = make_person(birth=date(2000, 6, 15))
    majority = AgeOfMajority(18)

    assert person.is_of_legal_age(majority, date(2018, 6, 15)) is True
    assert person.is_of_legal_age(majority, date(2018, 6, 14)) is False


@pytest.mark.unit
def test_roles_can_be_granted_and_revoked() -> None:
    person = make_person()

    person.grant_role(Role.TEACHER)
    assert person.has_role(Role.TEACHER)

    person.revoke_role(Role.TEACHER)
    assert not person.has_role(Role.TEACHER)


@pytest.mark.unit
def test_person_roles_view_is_readonly_snapshot() -> None:
    person = make_person(roles={Role.STUDENT})

    assert person.roles == frozenset({Role.STUDENT})


@pytest.mark.unit
def test_credential_qualification() -> None:
    subject = SubjectId(UUID(int=7))
    credential = Credential(CredentialId(UUID(int=2)), 'Maths', {subject})

    assert credential.qualifies_for(subject)
    assert not credential.qualifies_for(SubjectId(UUID(int=8)))


@pytest.mark.unit
def test_person_holds_credentials() -> None:
    person = make_person()
    cid = CredentialId(UUID(int=2))

    person.hold_credential(cid)

    assert person.holds_credential(cid)
