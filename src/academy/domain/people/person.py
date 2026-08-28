"""Person aggregate root."""

from __future__ import annotations

from datetime import date

from academy.domain.people.age_of_majority import AgeOfMajority
from academy.domain.people.email import Email
from academy.domain.people.personal_data import PersonalData
from academy.domain.people.role import Role
from academy.domain.shared.entity import Entity
from academy.domain.shared.ids import CredentialId, PersonId


class Person(Entity[PersonId]):
    """A person: a single record carrying every role and credential the person holds."""

    def __init__(
        self,
        id: PersonId,
        email: Email,
        personal: PersonalData,
        roles: set[Role] | None = None,
        held_credentials: set[CredentialId] | None = None,
    ) -> None:
        """Initialize a person.

        Args:
            id: The person's identifier.
            email: The person's unique email address.
            personal: The person's personal data.
            roles: The roles the person holds.
            held_credentials: Ids of the credentials the person holds.
        """
        self.id = id
        self.email = email
        self.personal = personal
        self._roles: set[Role] = set(roles or set())
        self._held_credentials: set[CredentialId] = set(held_credentials or set())

    def age(self, today: date) -> int:
        """Return the person's age in completed years as of ``today``."""
        return self.personal.age(today)

    def is_of_legal_age(self, age_of_majority: AgeOfMajority, today: date) -> bool:
        """Return whether the person is of legal age as of ``today``."""
        return age_of_majority.is_reached_at(self.age(today))

    def has_role(self, role: Role) -> bool:
        """Return whether the person holds ``role``."""
        return role in self._roles

    def grant_role(self, role: Role) -> None:
        """Grant ``role`` to the person (idempotent)."""
        self._roles.add(role)

    def revoke_role(self, role: Role) -> None:
        """Revoke ``role`` from the person (idempotent)."""
        self._roles.discard(role)

    def hold_credential(self, credential_id: CredentialId) -> None:
        """Record that the person holds the credential ``credential_id`` (idempotent)."""
        self._held_credentials.add(credential_id)

    def holds_credential(self, credential_id: CredentialId) -> bool:
        """Return whether the person holds the credential ``credential_id``."""
        return credential_id in self._held_credentials

    @property
    def roles(self) -> frozenset[Role]:
        """The roles the person holds (read-only view)."""
        return frozenset(self._roles)

    @property
    def held_credentials(self) -> frozenset[CredentialId]:
        """The ids of the credentials the person holds (read-only view)."""
        return frozenset(self._held_credentials)
