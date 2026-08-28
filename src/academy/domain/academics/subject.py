"""Subject entity."""

from __future__ import annotations

from academy.domain.shared.entity import Entity
from academy.domain.shared.errors import DomainError
from academy.domain.shared.ids import SubjectId


class InvalidSubjectError(DomainError):
    """Raised when a subject has an empty name."""


class Subject(Entity[SubjectId]):
    """A subject that belongs to a study plan."""

    def __init__(self, id: SubjectId, name: str) -> None:
        """Initialize a subject.

        Args:
            id: The subject's identifier.
            name: The subject's name (must not be empty).
        """
        if not name.strip():
            raise InvalidSubjectError('name must not be empty')
        self.id = id
        self.name = name
