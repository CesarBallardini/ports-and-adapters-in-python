"""Guardianship aggregate root."""

from __future__ import annotations

from datetime import date

from academy.domain.people.age_of_majority import AgeOfMajority
from academy.domain.people.person import Person
from academy.domain.shared.entity import Entity
from academy.domain.shared.errors import DomainError
from academy.domain.shared.ids import GuardianshipId, PersonId


class SelfGuardianshipError(DomainError):
    """Raised when a guardianship links a person to themselves."""


class WrongWardError(DomainError):
    """Raised when checking guardianship against a person who is not the ward."""


class Guardianship(Entity[GuardianshipId]):
    """A stored link between a guardian and a ward.

    Whether the guardianship *applies* is computed on read: it holds only while the ward
    is a minor. Once the ward reaches the age of majority, it no longer applies.
    """

    def __init__(self, id: GuardianshipId, guardian_id: PersonId, ward_id: PersonId) -> None:
        """Initialize a guardianship.

        Args:
            id: The guardianship's identifier.
            guardian_id: The guardian.
            ward_id: The ward.

        Raises:
            SelfGuardianshipError: If guardian and ward are the same person.
        """
        if guardian_id == ward_id:
            raise SelfGuardianshipError(str(guardian_id))
        self.id = id
        self.guardian_id = guardian_id
        self.ward_id = ward_id

    def applies(self, ward: Person, age_of_majority: AgeOfMajority, today: date) -> bool:
        """Return whether this guardianship currently applies.

        Args:
            ward: The ward person (must be this guardianship's ward).
            age_of_majority: The global age of majority.
            today: The reference date.

        Returns:
            ``True`` while the ward is a minor as of ``today``; ``False`` otherwise.

        Raises:
            WrongWardError: If ``ward`` is not this guardianship's ward.
        """
        if ward.id != self.ward_id:
            raise WrongWardError(str(ward.id))
        return not ward.is_of_legal_age(age_of_majority, today)
