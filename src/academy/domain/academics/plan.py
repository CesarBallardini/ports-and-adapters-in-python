"""Study plan entity (part of the DegreeProgram aggregate)."""

from __future__ import annotations

from academy.domain.academics.subject import Subject
from academy.domain.shared.entity import Entity
from academy.domain.shared.ids import PlanId, SubjectId


class Plan(Entity[PlanId]):
    """A study plan: a flat set of subjects, with no prerequisites between them.

    Activation state is controlled by the owning ``DegreeProgram`` to preserve the
    "exactly one active plan per program" invariant.
    """

    def __init__(self, id: PlanId, subjects: list[Subject] | None = None, *, active: bool = False) -> None:
        """Initialize a plan.

        Args:
            id: The plan's identifier.
            subjects: The subjects that make up the plan.
            active: Whether the plan starts active (defaults to inactive).
        """
        self.id = id
        self._subjects: list[Subject] = list(subjects or [])
        self._active = active

    def add_subject(self, subject: Subject) -> None:
        """Add a subject to the plan (idempotent by subject id)."""
        if not self.has_subject(subject.id):
            self._subjects.append(subject)

    def has_subject(self, subject_id: SubjectId) -> bool:
        """Return whether ``subject_id`` is part of this plan."""
        return any(subject.id == subject_id for subject in self._subjects)

    def subject_ids(self) -> frozenset[SubjectId]:
        """Return the ids of the subjects that make up the plan."""
        return frozenset(subject.id for subject in self._subjects)

    def activate(self) -> None:
        """Mark the plan active. Prefer ``DegreeProgram.activate_plan``."""
        self._active = True

    def deactivate(self) -> None:
        """Mark the plan inactive."""
        self._active = False

    @property
    def active(self) -> bool:
        """Whether the plan is currently active."""
        return self._active

    @property
    def subjects(self) -> tuple[Subject, ...]:
        """The subjects that make up the plan (read-only view)."""
        return tuple(self._subjects)
