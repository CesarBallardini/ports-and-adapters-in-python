"""Graduation aggregate root."""

from __future__ import annotations

from datetime import date
from enum import Enum

from academy.domain.shared.entity import Entity
from academy.domain.shared.errors import DomainError
from academy.domain.shared.ids import CredentialId, GraduationId, PersonId, ProgramId


class GraduationStatus(Enum):
    """The lifecycle status of a graduation record."""

    ACTIVE = 'active'
    REVOKED = 'revoked'


class GraduationStateError(DomainError):
    """Raised on an invalid graduation state transition (e.g. revoking a revoked one)."""


class Graduation(Entity[GraduationId]):
    """A stored conferral event: a dated record that issues a credential to a student."""

    def __init__(
        self,
        id: GraduationId,
        student_id: PersonId,
        program_id: ProgramId,
        credential_id: CredentialId,
        conferred_on: date,
        status: GraduationStatus = GraduationStatus.ACTIVE,
    ) -> None:
        """Initialize a graduation record.

        Args:
            id: The graduation's identifier.
            student_id: The graduating student.
            program_id: The degree program granting the credential.
            credential_id: The credential issued to the student.
            conferred_on: The date the graduation was conferred.
            status: The initial status (defaults to active).
        """
        self.id = id
        self.student_id = student_id
        self.program_id = program_id
        self.credential_id = credential_id
        self.conferred_on = conferred_on
        self._status = status

    def is_active(self) -> bool:
        """Return whether the graduation is currently active."""
        return self._status is GraduationStatus.ACTIVE

    def revoke(self) -> None:
        """Revoke the graduation.

        Raises:
            GraduationStateError: If the graduation is already revoked.
        """
        if self._status is GraduationStatus.REVOKED:
            raise GraduationStateError('graduation is already revoked')
        self._status = GraduationStatus.REVOKED

    def reissue(self, on: date) -> None:
        """Reissue a revoked graduation, dated ``on``.

        Raises:
            GraduationStateError: If the graduation is already active.
        """
        if self._status is GraduationStatus.ACTIVE:
            raise GraduationStateError('graduation is already active')
        self._status = GraduationStatus.ACTIVE
        self.conferred_on = on

    @property
    def status(self) -> GraduationStatus:
        """The graduation's current status."""
        return self._status
