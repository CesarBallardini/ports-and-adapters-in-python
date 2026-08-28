"""Course section aggregate root and its enrollment value object."""

from __future__ import annotations

from dataclasses import dataclass

from academy.domain.academics.term import Term
from academy.domain.shared.entity import Entity
from academy.domain.shared.errors import DomainError
from academy.domain.shared.ids import PersonId, SectionId, SubjectId


class AlreadyEnrolledError(DomainError):
    """Raised when enrolling a student who is already enrolled in this section."""


@dataclass(frozen=True, slots=True)
class Enrollment:
    """A student's enrollment in a course section."""

    student_id: PersonId


class CourseSection(Entity[SectionId]):
    """A section formed to teach one subject in one term, taught by one teacher.

    Teacher qualification is enforced when the section is created (see
    ``CourseSectionFactory``); this aggregate assumes the teacher is already qualified.
    """

    def __init__(
        self,
        id: SectionId,
        subject_id: SubjectId,
        term: Term,
        teacher_id: PersonId,
        enrollments: list[Enrollment] | None = None,
    ) -> None:
        """Initialize a course section.

        Args:
            id: The section's identifier.
            subject_id: The subject taught in the section.
            term: The term during which the section runs.
            teacher_id: The teacher who teaches the section.
            enrollments: Initial enrollments.
        """
        self.id = id
        self.subject_id = subject_id
        self.term = term
        self.teacher_id = teacher_id
        self._enrollments: list[Enrollment] = list(enrollments or [])

    def enroll(self, student_id: PersonId) -> None:
        """Enroll a student in this section.

        Raises:
            AlreadyEnrolledError: If the student is already enrolled in this section.
        """
        if self.is_enrolled(student_id):
            raise AlreadyEnrolledError(str(student_id))
        self._enrollments.append(Enrollment(student_id))

    def is_enrolled(self, student_id: PersonId) -> bool:
        """Return whether ``student_id`` is enrolled in this section."""
        return any(enrollment.student_id == student_id for enrollment in self._enrollments)

    def students(self) -> frozenset[PersonId]:
        """Return the ids of the students enrolled in this section."""
        return frozenset(enrollment.student_id for enrollment in self._enrollments)

    @property
    def enrollments(self) -> tuple[Enrollment, ...]:
        """The section's enrollments (read-only view)."""
        return tuple(self._enrollments)
