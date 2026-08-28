"""Unit tests for the academics bounded context."""

from uuid import UUID

import pytest

from academy.domain.academics.course_section import AlreadyEnrolledError, CourseSection
from academy.domain.academics.degree_program import (
    DegreeProgram,
    DuplicatePlanError,
    PlanNotFoundError,
)
from academy.domain.academics.plan import Plan
from academy.domain.academics.subject import InvalidSubjectError, Subject
from academy.domain.academics.term import InvalidTermError, Term
from academy.domain.shared.ids import PersonId, PlanId, ProgramId, SectionId, SubjectId


def subject(n: int) -> Subject:
    return Subject(SubjectId(UUID(int=n)), f'Subject {n}')


@pytest.mark.unit
@pytest.mark.parametrize('year,number', [(0, 1), (-1, 1), (2026, 0), (2026, 3)])
def test_invalid_term_raises(year: int, number: int) -> None:
    with pytest.raises(InvalidTermError):
        Term(year, number)


@pytest.mark.unit
def test_term_label() -> None:
    assert Term(2026, 1).label() == '2026-T1'


@pytest.mark.unit
def test_terms_order_by_year_then_number() -> None:
    assert Term(2026, 1) < Term(2026, 2) < Term(2027, 1)


@pytest.mark.unit
def test_subject_rejects_empty_name() -> None:
    with pytest.raises(InvalidSubjectError):
        Subject(SubjectId(UUID(int=1)), '  ')


@pytest.mark.unit
def test_plan_add_subject_is_idempotent() -> None:
    plan = Plan(PlanId(UUID(int=1)))
    plan.add_subject(subject(1))
    plan.add_subject(subject(1))

    assert plan.subject_ids() == {SubjectId(UUID(int=1))}


@pytest.mark.unit
def test_program_keeps_at_most_one_active_plan() -> None:
    program = DegreeProgram(ProgramId(UUID(int=1)), 'Engineering')
    plan_a = Plan(PlanId(UUID(int=10)), [subject(1)])
    plan_b = Plan(PlanId(UUID(int=11)), [subject(1)])
    program.add_plan(plan_a)
    program.add_plan(plan_b)

    program.activate_plan(plan_a.id)
    assert program.active_plan() == plan_a

    program.activate_plan(plan_b.id)
    assert program.active_plan() == plan_b
    assert not plan_a.active


@pytest.mark.unit
def test_adding_an_active_plan_deactivates_the_others() -> None:
    program = DegreeProgram(ProgramId(UUID(int=1)), 'Engineering')
    active_first = Plan(PlanId(UUID(int=10)), [subject(1)], active=True)
    active_second = Plan(PlanId(UUID(int=11)), [subject(1)], active=True)
    program.add_plan(active_first)
    program.add_plan(active_second)

    assert program.active_plan() == active_second
    assert not active_first.active


@pytest.mark.unit
def test_program_rejects_duplicate_plan_id() -> None:
    program = DegreeProgram(ProgramId(UUID(int=1)), 'Engineering')
    program.add_plan(Plan(PlanId(UUID(int=10))))

    with pytest.raises(DuplicatePlanError):
        program.add_plan(Plan(PlanId(UUID(int=10))))


@pytest.mark.unit
def test_unknown_plan_raises() -> None:
    program = DegreeProgram(ProgramId(UUID(int=1)), 'Engineering')

    with pytest.raises(PlanNotFoundError):
        program.plan(PlanId(UUID(int=99)))


@pytest.mark.unit
def test_course_section_enrollment() -> None:
    section = CourseSection(
        SectionId(UUID(int=1)),
        SubjectId(UUID(int=1)),
        Term(2026, 1),
        PersonId(UUID(int=100)),
    )
    student = PersonId(UUID(int=200))

    section.enroll(student)

    assert section.is_enrolled(student)
    assert section.students() == {student}


@pytest.mark.unit
def test_double_enrollment_in_same_section_raises() -> None:
    section = CourseSection(
        SectionId(UUID(int=1)),
        SubjectId(UUID(int=1)),
        Term(2026, 1),
        PersonId(UUID(int=100)),
    )
    student = PersonId(UUID(int=200))
    section.enroll(student)

    with pytest.raises(AlreadyEnrolledError):
        section.enroll(student)
