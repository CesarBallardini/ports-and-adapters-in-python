"""Typed identifiers for domain aggregates and entities.

Each identifier is a distinct type wrapping a ``UUID`` so the type checker can tell a
``PersonId`` from a ``SubjectId``. Identifiers are pure value objects; generating new
UUIDs is an application concern (an id-generator port), never the domain's.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Self
from uuid import UUID


@dataclass(frozen=True, slots=True)
class _Id:
    """Base typed identifier wrapping a ``UUID``; subclasses are distinct id types."""

    value: UUID

    @classmethod
    def from_str(cls, raw: str) -> Self:
        """Build the identifier from the canonical string form of a UUID."""
        return cls(UUID(raw))

    def __str__(self) -> str:
        """Return the canonical string form of the underlying UUID."""
        return str(self.value)


@dataclass(frozen=True, slots=True)
class PersonId(_Id):
    """Identifier for a Person."""


@dataclass(frozen=True, slots=True)
class CredentialId(_Id):
    """Identifier for a Credential."""


@dataclass(frozen=True, slots=True)
class ProgramId(_Id):
    """Identifier for a DegreeProgram."""


@dataclass(frozen=True, slots=True)
class PlanId(_Id):
    """Identifier for a Plan."""


@dataclass(frozen=True, slots=True)
class SubjectId(_Id):
    """Identifier for a Subject."""


@dataclass(frozen=True, slots=True)
class SectionId(_Id):
    """Identifier for a CourseSection."""


@dataclass(frozen=True, slots=True)
class GraduationId(_Id):
    """Identifier for a Graduation."""


@dataclass(frozen=True, slots=True)
class GuardianshipId(_Id):
    """Identifier for a Guardianship."""
