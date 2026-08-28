"""Recording grades into a student's academic history."""

from __future__ import annotations

from academy.domain.academics.course_section import CourseSection
from academy.domain.grades.academic_history import AcademicHistory
from academy.domain.grades.grade import Grade
from academy.domain.grades.grade_entry import GradeEntry
from academy.domain.people.person import Person
from academy.domain.shared.errors import DomainError
from academy.domain.shared.ids import PersonId


class NotTeacherOfSectionError(DomainError):
    """Raised when a teacher tries to grade a section they do not teach."""


class StudentNotEnrolledError(DomainError):
    """Raised when grading a student who is not enrolled in the section."""


class HistoryOwnerMismatchError(DomainError):
    """Raised when the academic history does not belong to the graded student."""


class GradingService:
    """Records a grade for a student, enforcing teaching and enrollment relationships."""

    def record_grade(
        self,
        section: CourseSection,
        teacher: Person,
        student_id: PersonId,
        grade: Grade,
        history: AcademicHistory,
    ) -> GradeEntry:
        """Record ``grade`` for a student enrolled in a section taught by ``teacher``.

        Args:
            section: The section in which the grade is recorded.
            teacher: The teacher recording the grade.
            student_id: The student being graded.
            grade: The grade to record.
            history: The graded student's academic history.

        Returns:
            The grade entry that was appended to the history.

        Raises:
            NotTeacherOfSectionError: If ``teacher`` does not teach ``section``.
            StudentNotEnrolledError: If the student is not enrolled in ``section``.
            HistoryOwnerMismatchError: If ``history`` is not the student's history.
        """
        if section.teacher_id != teacher.id:
            raise NotTeacherOfSectionError(str(teacher.id))
        if not section.is_enrolled(student_id):
            raise StudentNotEnrolledError(str(student_id))
        if history.student_id != student_id:
            raise HistoryOwnerMismatchError(str(history.student_id))
        entry = GradeEntry(
            subject_id=section.subject_id,
            term=section.term,
            grade=grade,
            source_section_id=section.id,
        )
        history.record(entry)
        return entry
