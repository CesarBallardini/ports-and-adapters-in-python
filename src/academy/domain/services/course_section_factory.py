"""Factory that enforces teacher qualification when creating a course section."""

from __future__ import annotations

from collections.abc import Iterable

from academy.domain.academics.course_section import CourseSection
from academy.domain.academics.term import Term
from academy.domain.people.credential import Credential
from academy.domain.people.person import Person
from academy.domain.people.role import Role
from academy.domain.shared.errors import DomainError
from academy.domain.shared.ids import SectionId, SubjectId


class NotATeacherError(DomainError):
    """Raised when the person assigned to a section does not hold the teacher role."""


class TeacherNotQualifiedError(DomainError):
    """Raised when the teacher holds no credential qualifying for the subject."""


class CourseSectionFactory:
    """Creates course sections, hard-enforcing that the teacher is qualified."""

    def create(
        self,
        id: SectionId,
        subject_id: SubjectId,
        term: Term,
        teacher: Person,
        held_credentials: Iterable[Credential],
    ) -> CourseSection:
        """Create a course section for a qualified teacher.

        Args:
            id: The new section's identifier.
            subject_id: The subject to be taught.
            term: The term during which the section runs.
            teacher: The teacher to assign.
            held_credentials: The credentials the teacher holds.

        Returns:
            The new course section.

        Raises:
            NotATeacherError: If ``teacher`` does not hold the teacher role.
            TeacherNotQualifiedError: If no held credential qualifies for ``subject_id``.
        """
        if not teacher.has_role(Role.TEACHER):
            raise NotATeacherError(str(teacher.id))
        qualified = any(
            teacher.holds_credential(credential.id) and credential.qualifies_for(subject_id)
            for credential in held_credentials
        )
        if not qualified:
            raise TeacherNotQualifiedError(f'{teacher.id} is not qualified for {subject_id}')
        return CourseSection(id=id, subject_id=subject_id, term=term, teacher_id=teacher.id)
