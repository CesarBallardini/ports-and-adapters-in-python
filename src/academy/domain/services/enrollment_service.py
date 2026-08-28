"""Enrollment rule enforcement across the student, plan, and section aggregates."""

from __future__ import annotations

from collections.abc import Set

from academy.domain.academics.course_section import CourseSection
from academy.domain.academics.plan import Plan
from academy.domain.academics.term import Term
from academy.domain.people.person import Person
from academy.domain.people.role import Role
from academy.domain.shared.errors import DomainError
from academy.domain.shared.ids import SubjectId


class NotAStudentError(DomainError):
    """Raised when enrolling a person who does not hold the student role."""


class SubjectNotInPlanError(DomainError):
    """Raised when the section's subject is not part of the student's plan."""


class WrongTermError(DomainError):
    """Raised when the section is not offered in the current term."""


class DuplicateSubjectEnrollmentError(DomainError):
    """Raised when the student is already enrolled in a section of that subject."""


class EnrollmentService:
    """Validates the enrollment rule, then enrolls the student in the section."""

    def enroll(
        self,
        section: CourseSection,
        student: Person,
        plan: Plan,
        current_term: Term,
        already_enrolled_subject_ids: Set[SubjectId],
    ) -> None:
        """Enroll ``student`` in ``section`` if every enrollment rule is satisfied.

        Args:
            section: The section to enroll into.
            student: The student to enroll.
            plan: The student's degree plan.
            current_term: The term currently open for enrollment.
            already_enrolled_subject_ids: Subjects the student already has a section for.

        Raises:
            NotAStudentError: If ``student`` does not hold the student role.
            SubjectNotInPlanError: If the section's subject is not in the plan.
            WrongTermError: If the section is not offered in ``current_term``.
            DuplicateSubjectEnrollmentError: If the student already has that subject.
        """
        if not student.has_role(Role.STUDENT):
            raise NotAStudentError(str(student.id))
        if not plan.has_subject(section.subject_id):
            raise SubjectNotInPlanError(str(section.subject_id))
        if section.term != current_term:
            raise WrongTermError(section.term.label())
        if section.subject_id in already_enrolled_subject_ids:
            raise DuplicateSubjectEnrollmentError(str(section.subject_id))
        section.enroll(student.id)
