"""Unit tests for the multi-aggregate domain services."""

from datetime import date
from uuid import UUID

import pytest

from academy.domain.academics.course_section import CourseSection
from academy.domain.academics.plan import Plan
from academy.domain.academics.subject import Subject
from academy.domain.academics.term import Term
from academy.domain.grades.academic_history import AcademicHistory
from academy.domain.grades.grade import Grade
from academy.domain.people.credential import Credential
from academy.domain.people.email import Email
from academy.domain.people.person import Person
from academy.domain.people.personal_data import PersonalData
from academy.domain.people.role import Role
from academy.domain.services.course_section_factory import (
    CourseSectionFactory,
    NotATeacherError,
    TeacherNotQualifiedError,
)
from academy.domain.services.enrollment_service import (
    DuplicateSubjectEnrollmentError,
    EnrollmentService,
    NotAStudentError,
    SubjectNotInPlanError,
    WrongTermError,
)
from academy.domain.services.grading_service import (
    GradingService,
    HistoryOwnerMismatchError,
    NotTeacherOfSectionError,
    StudentNotEnrolledError,
)
from academy.domain.services.graduation_service import (
    EmptyPlanError,
    GraduationService,
    NotEligibleForGraduationError,
)
from academy.domain.shared.ids import (
    CredentialId,
    GraduationId,
    PersonId,
    PlanId,
    ProgramId,
    SectionId,
    SubjectId,
)

TERM = Term(2026, 1)
SUBJECT_ID = SubjectId(UUID(int=1))
OTHER_SUBJECT_ID = SubjectId(UUID(int=2))
CREDENTIAL_ID = CredentialId(UUID(int=5))


def person(n: int, roles: set[Role]) -> Person:
    return Person(
        PersonId(UUID(int=n)), Email(f'p{n}@example.com'), PersonalData(f'P{n}', date(2000, 1, 1)), roles=roles
    )


def qualified_teacher() -> Person:
    teacher = person(100, {Role.TEACHER})
    teacher.hold_credential(CREDENTIAL_ID)
    return teacher


def maths_credential() -> Credential:
    return Credential(CREDENTIAL_ID, 'Maths', {SUBJECT_ID})


def section_for(teacher_id: PersonId) -> CourseSection:
    return CourseSection(SectionId(UUID(int=50)), SUBJECT_ID, TERM, teacher_id)


# --- CourseSectionFactory ---------------------------------------------------


@pytest.mark.unit
def test_factory_rejects_non_teacher() -> None:
    not_teacher = person(100, {Role.STUDENT})

    with pytest.raises(NotATeacherError):
        CourseSectionFactory().create(SectionId(UUID(int=50)), SUBJECT_ID, TERM, not_teacher, [maths_credential()])


@pytest.mark.unit
def test_factory_rejects_teacher_without_qualifying_credential() -> None:
    teacher = qualified_teacher()
    unrelated = Credential(CREDENTIAL_ID, 'History', {OTHER_SUBJECT_ID})

    with pytest.raises(TeacherNotQualifiedError):
        CourseSectionFactory().create(SectionId(UUID(int=50)), SUBJECT_ID, TERM, teacher, [unrelated])


@pytest.mark.unit
def test_factory_creates_section_for_qualified_teacher() -> None:
    teacher = qualified_teacher()

    section = CourseSectionFactory().create(SectionId(UUID(int=50)), SUBJECT_ID, TERM, teacher, [maths_credential()])

    assert section.teacher_id == teacher.id
    assert section.subject_id == SUBJECT_ID
    assert section.term == TERM


# --- EnrollmentService ------------------------------------------------------


def plan_with_subject() -> Plan:
    return Plan(PlanId(UUID(int=10)), [Subject(SUBJECT_ID, 'Maths')])


@pytest.mark.unit
def test_enroll_rejects_non_student() -> None:
    section = section_for(PersonId(UUID(int=100)))

    with pytest.raises(NotAStudentError):
        EnrollmentService().enroll(section, person(200, set()), plan_with_subject(), TERM, set())


@pytest.mark.unit
def test_enroll_rejects_subject_not_in_plan() -> None:
    section = section_for(PersonId(UUID(int=100)))
    empty_plan = Plan(PlanId(UUID(int=10)), [Subject(OTHER_SUBJECT_ID, 'Other')])

    with pytest.raises(SubjectNotInPlanError):
        EnrollmentService().enroll(section, person(200, {Role.STUDENT}), empty_plan, TERM, set())


@pytest.mark.unit
def test_enroll_rejects_wrong_term() -> None:
    section = section_for(PersonId(UUID(int=100)))

    with pytest.raises(WrongTermError):
        EnrollmentService().enroll(section, person(200, {Role.STUDENT}), plan_with_subject(), Term(2026, 2), set())


@pytest.mark.unit
def test_enroll_rejects_duplicate_subject() -> None:
    section = section_for(PersonId(UUID(int=100)))

    with pytest.raises(DuplicateSubjectEnrollmentError):
        EnrollmentService().enroll(section, person(200, {Role.STUDENT}), plan_with_subject(), TERM, {SUBJECT_ID})


@pytest.mark.unit
def test_enroll_succeeds_when_rules_pass() -> None:
    section = section_for(PersonId(UUID(int=100)))
    student = person(200, {Role.STUDENT})

    EnrollmentService().enroll(section, student, plan_with_subject(), TERM, set())

    assert section.is_enrolled(student.id)


# --- GradingService ---------------------------------------------------------


@pytest.mark.unit
def test_grading_rejects_non_teacher_of_section() -> None:
    section = section_for(PersonId(UUID(int=100)))
    other_teacher = person(101, {Role.TEACHER})
    student = person(200, {Role.STUDENT})
    section.enroll(student.id)

    with pytest.raises(NotTeacherOfSectionError):
        GradingService().record_grade(section, other_teacher, student.id, Grade(8), AcademicHistory(student.id))


@pytest.mark.unit
def test_grading_rejects_unenrolled_student() -> None:
    teacher = qualified_teacher()
    section = section_for(teacher.id)
    student = person(200, {Role.STUDENT})

    with pytest.raises(StudentNotEnrolledError):
        GradingService().record_grade(section, teacher, student.id, Grade(8), AcademicHistory(student.id))


@pytest.mark.unit
def test_grading_rejects_history_of_a_different_student() -> None:
    teacher = qualified_teacher()
    section = section_for(teacher.id)
    student = person(200, {Role.STUDENT})
    section.enroll(student.id)
    wrong_history = AcademicHistory(PersonId(UUID(int=999)))

    with pytest.raises(HistoryOwnerMismatchError):
        GradingService().record_grade(section, teacher, student.id, Grade(8), wrong_history)


@pytest.mark.unit
def test_grading_records_entry_into_history() -> None:
    teacher = qualified_teacher()
    section = section_for(teacher.id)
    student = person(200, {Role.STUDENT})
    section.enroll(student.id)
    history = AcademicHistory(student.id)

    entry = GradingService().record_grade(section, teacher, student.id, Grade(8), history)

    assert entry.subject_id == SUBJECT_ID
    assert entry.term == TERM
    assert entry.grade == Grade(8)
    assert entry.source_section_id == section.id
    assert history.best_grade(SUBJECT_ID) == Grade(8)


# --- GraduationService ------------------------------------------------------


@pytest.mark.unit
def test_eligibility_rejects_empty_plan() -> None:
    history = AcademicHistory(PersonId(UUID(int=200)))

    with pytest.raises(EmptyPlanError):
        GraduationService().is_eligible(history, Plan(PlanId(UUID(int=10))))


@pytest.mark.unit
def test_confer_rejects_ineligible_student() -> None:
    student = person(200, {Role.STUDENT})
    history = AcademicHistory(student.id)  # no passing grades

    with pytest.raises(NotEligibleForGraduationError):
        GraduationService().confer(
            GraduationId(UUID(int=1)),
            student,
            ProgramId(UUID(int=3)),
            CREDENTIAL_ID,
            history,
            plan_with_subject(),
            date(2026, 3, 1),
        )


@pytest.mark.unit
def test_confer_issues_graduation_for_eligible_student() -> None:
    from academy.domain.grades.grade_entry import GradeEntry

    student = person(200, {Role.STUDENT})
    history = AcademicHistory(student.id)
    history.record(GradeEntry(SUBJECT_ID, TERM, Grade(9)))

    graduation = GraduationService().confer(
        GraduationId(UUID(int=1)),
        student,
        ProgramId(UUID(int=3)),
        CREDENTIAL_ID,
        history,
        plan_with_subject(),
        date(2026, 3, 1),
    )

    assert graduation.student_id == student.id
    assert graduation.program_id == ProgramId(UUID(int=3))
    assert graduation.credential_id == CREDENTIAL_ID
    assert graduation.conferred_on == date(2026, 3, 1)
    assert graduation.is_active()
