"""Credential entity: a qualification that lets its holder teach certain subjects."""

from __future__ import annotations

from academy.domain.shared.entity import Entity
from academy.domain.shared.ids import CredentialId, SubjectId


class Credential(Entity[CredentialId]):
    """A credential (the "titulo"): qualifies its holder to teach a set of subjects.

    The same credential concept is what a degree program issues to a graduate, so a
    graduate's credential can later qualify them to teach.
    """

    def __init__(
        self,
        id: CredentialId,
        name: str,
        qualifying_subjects: set[SubjectId] | None = None,
    ) -> None:
        """Initialize a credential.

        Args:
            id: The credential's identifier.
            name: Human-readable credential name.
            qualifying_subjects: Subjects this credential qualifies its holder to teach.
        """
        self.id = id
        self.name = name
        self._qualifying_subjects: set[SubjectId] = set(qualifying_subjects or set())

    def qualifies_for(self, subject_id: SubjectId) -> bool:
        """Return whether this credential qualifies its holder to teach ``subject_id``."""
        return subject_id in self._qualifying_subjects

    def add_subject(self, subject_id: SubjectId) -> None:
        """Associate a subject with this credential."""
        self._qualifying_subjects.add(subject_id)

    @property
    def qualifying_subjects(self) -> frozenset[SubjectId]:
        """The subjects this credential qualifies for (read-only view)."""
        return frozenset(self._qualifying_subjects)
