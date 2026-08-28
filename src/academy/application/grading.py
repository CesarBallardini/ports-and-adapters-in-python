"""Recording and reading the grades of a course section (UC-21, UC-22).

The implementation behind :class:`~academy.application.ports.inbound.grading.ManageGrades`.
One class per port rather than one per use case (``docs/06-class-diagram.md`` §2): a port is
an interface, and a class that carried only ``record_grade`` would not satisfy it.

Both methods are Controllers in the GRASP sense -- they authorize, load, delegate, save and
project, and hold no rules of their own. Notably absent from ``record_grade`` is any check
that the teacher teaches the section or that the student is enrolled in it: those invariants
span two aggregates, so :class:`~academy.domain.services.grading_service.GradingService` owns
them (``docs/03-sequence-diagrams.md`` §3). A rule that appeared here as well as there would
eventually disagree with itself.
"""

from __future__ import annotations

from academy.application.authorization import AccessGuard
from academy.application.commands import ListSectionGradesCommand, RecordGradeCommand
from academy.application.dtos import Actor, GradeRecordedDto, SectionGradeRowDto, SectionGradesDto
from academy.application.errors import NotFoundError
from academy.application.ports.outbound.repositories import (
    AcademicHistoryRepository,
    PersonRepository,
    SectionRepository,
)
from academy.application.ports.outbound.unit_of_work import UnitOfWork
from academy.domain.academics.course_section import CourseSection
from academy.domain.authorization.models import Action, ResourceType
from academy.domain.grades.academic_history import AcademicHistory
from academy.domain.grades.grade import Grade
from academy.domain.grades.grade_entry import GradeEntry
from academy.domain.people.person import Person
from academy.domain.services.grading_service import GradingService
from academy.domain.shared.ids import PersonId, SectionId, SubjectId


class GradeManagement:
    """Recording and reading the grades of a course section.

    Satisfies :class:`~academy.application.ports.inbound.grading.ManageGrades`. The
    conformance is structural, so nothing here declares it; the contract that matters is the
    port's docstrings, and the test suite asserts them against this class.
    """

    def __init__(
        self,
        sections: SectionRepository,
        histories: AcademicHistoryRepository,
        people: PersonRepository,
        uow: UnitOfWork,
        guard: AccessGuard,
        grading: GradingService | None = None,
    ) -> None:
        """Wire the use cases to their collaborators.

        Args:
            sections: Source of the graded section and its roster.
            histories: Where grades are durably recorded.
            people: Needed to resolve the acting teacher into the ``Person`` the domain
                service demands, and to put names on a roster listing.
            uow: The transaction boundary for the one write path.
            guard: Resolves relations and enforces what they grant.
            grading: The domain service that owns the cross-aggregate grading rules.
                Defaults to the standard :class:`GradingService`, since those rules are the
                domain's and not a deployment choice -- it is injectable so a test can
                observe the delegation, never so a deployment can replace the rules.
        """
        self._sections = sections
        self._histories = histories
        self._people = people
        self._uow = uow
        self._guard = guard
        self._grading = grading or GradingService()

    async def record_grade(self, command: RecordGradeCommand) -> GradeRecordedDto:
        """Record one grade attempt (UC-22).

        Authorization comes first and is asked about the *student*: writing a grade touches
        the student's record, and ``Action.WRITE`` on ``GRADES`` is granted by exactly one
        relation, ``TEACHER_OF_SECTION``. Being a teacher of *some* section the student
        attends is therefore all the guard establishes; that it is *this* section is the
        domain service's check, not a second copy of the same rule here.

        Args:
            command: The section, the student, the grade, and who is recording it.

        Returns:
            The resulting standing for the section's subject.

        Raises:
            AuthorizationError: If the actor does not teach a section this student is in.
            NotFoundError: If the section, the student or the acting teacher does not exist.
            InvalidGradeError: If the grade is outside 0..10.
            NotTeacherOfSectionError: If the actor does not teach *this* section.
            StudentNotEnrolledError: If the student is not enrolled in this section.
        """
        student_id = _student_id(command.student_id)
        section_id = _section_id(command.section_id)

        await self._guard.require(command.actor, Action.WRITE, ResourceType.GRADES, student_id)

        # After the guard, so an unauthorized caller cannot use the difference between a
        # rejected grade and a rejected enrollment to probe a roster they may not read.
        grade = Grade(command.grade)

        async with self._uow:
            section = await self._sections.get(section_id)
            if section is None:
                raise NotFoundError('course section', section_id)
            teacher = await self._require_person(command.actor.person_id, 'teacher')
            # The student is looked up for existence alone. Without it a grade for a person
            # who does not exist would surface as StudentNotEnrolledError -- true, but not
            # the answer the port promises, and not the one that tells a caller what to fix.
            await self._require_person(student_id, 'student')
            history = await self._histories.get_or_create(student_id)
            entry = self._grading.record_grade(section, teacher, student_id, grade, history)
            await self._histories.save(history)
            await self._uow.commit()

        return _recorded(entry, history)

    async def list_section_grades(self, command: ListSectionGradesCommand) -> SectionGradesDto:
        """List a section's students with their standing in its subject (UC-21).

        Read-only, and therefore outside a unit of work: the transaction boundary exists to
        make writes atomic, and opening one here would claim a guarantee -- a consistent
        snapshot across three repositories -- that the port does not promise and that a
        grade sheet does not need.

        The section is loaded before the guard runs, because who may read the sheet is
        derived from the section itself. A caller therefore learns whether a section id
        exists before learning they may not read it; that is the lesser leak, and the
        alternative is an authorization check with nothing to check against.

        Args:
            command: The section to list, and who is asking.

        Returns:
            The roster, ordered by student name, each row carrying the standing derived from
            that student's transcript. Students enrolled in the section but absent from the
            people repository are omitted -- a dangling roster entry is a data problem, and
            failing the whole listing over one would help nobody.

        Raises:
            AuthorizationError: If the actor holds no relation granting read on grades.
            NotFoundError: If the section does not exist.
        """
        section_id = _section_id(command.section_id)
        section = await self._sections.get(section_id)
        if section is None:
            raise NotFoundError('course section', section_id)

        await self._require_readable(command.actor, section)

        student_ids = sorted(section.students(), key=str)
        people = {person.id: person for person in await self._people.by_ids(student_ids)}
        histories = {history.student_id: history for history in await self._histories.for_students(student_ids)}

        rows = [
            _row(person, histories.get(student_id), section.subject_id)
            for student_id in student_ids
            if (person := people.get(student_id)) is not None
        ]
        rows.sort(key=lambda row: (row.full_name, row.student_id))

        return SectionGradesDto(
            section_id=str(section.id),
            subject_id=str(section.subject_id),
            term=section.term.label(),
            rows=tuple(rows),
        )

    async def _require_readable(self, actor: Actor, section: CourseSection) -> None:
        """Require read access to every person the grade sheet names.

        The sheet names its teacher and its students, and the actor must be allowed to read
        the grades of all of them. Stating it that way -- rather than as a single check --
        is what makes the two obvious shortcuts wrong:

        * Checking only the students lets the guardian of the sole student on a one-student
          roster read the whole sheet, and says nothing at all about an empty section.
        * Checking only the teacher lets a teacher read any section taught by someone who
          happens to be enrolled as a student in one of *their* sections -- and the spec is
          explicit that one human may be a teacher and a student at once.

        The conjunction has neither hole, and for the two actors UC-21 actually names it is
        satisfied by one relation each: ``SELF`` for the section's teacher, ``ADMINISTRATOR``
        for a registrar. The cost is one resolution per person, which a roster bounds.
        """
        for owner_id in (section.teacher_id, *sorted(section.students(), key=str)):
            await self._guard.require(actor, Action.READ, ResourceType.GRADES, owner_id)

    async def _require_person(self, person_id: PersonId, entity: str) -> Person:
        """Fetch a person that the operation cannot proceed without.

        Args:
            person_id: Who to fetch.
            entity: The role the person plays here, used in the error message. ``'student'``
                and ``'teacher'`` say more to whoever reads the failure than ``'person'``.

        Raises:
            NotFoundError: If no such person is stored.
        """
        person = await self._people.get(person_id)
        if person is None:
            raise NotFoundError(entity, person_id)
        return person


def _recorded(entry: GradeEntry, history: AcademicHistory) -> GradeRecordedDto:
    """Project the outcome of a recording into its DTO."""
    best = history.best_grade(entry.subject_id)
    return GradeRecordedDto(
        student_id=str(history.student_id),
        subject_id=str(entry.subject_id),
        recorded_grade=entry.grade.value,
        # ``best_grade`` cannot be None here -- the entry just recorded is in this history --
        # but saying so with ``assert`` would be a lie under ``python -O``, which strips it.
        best_grade=best.value if best is not None else entry.grade.value,
        passed=history.has_passed(entry.subject_id),
    )


def _row(person: Person, history: AcademicHistory | None, subject_id: SubjectId) -> SectionGradeRowDto:
    """Project one student's standing in one subject into a grade-sheet row.

    A student with no transcript at all is a student who has not been graded yet, and reads
    as an ungraded row rather than a missing one: the sheet's job is to show the teacher who
    still needs a grade.
    """
    if history is None:
        return SectionGradeRowDto(
            student_id=str(person.id),
            full_name=person.personal.full_name,
            best_grade=None,
            passed=False,
            attempts=0,
        )
    best = history.best_grade(subject_id)
    return SectionGradeRowDto(
        student_id=str(person.id),
        full_name=person.personal.full_name,
        best_grade=best.value if best is not None else None,
        passed=history.has_passed(subject_id),
        attempts=len(history.entries_for(subject_id)),
    )


def _section_id(raw: str) -> SectionId:
    """Parse a section identifier, treating an unparseable one as a section that is not there.

    A malformed id names nothing, which is what ``NotFoundError`` says. Letting the
    ``ValueError`` escape instead would reach an inbound adapter that has no entry for it in
    the status table (ADR-0012) and become a 500 -- a server error for what is plainly a bad
    request.
    """
    try:
        return SectionId.from_str(raw)
    except ValueError as error:
        raise NotFoundError('course section', raw) from error


def _student_id(raw: str) -> PersonId:
    """Parse a student identifier, treating an unparseable one as a student that is not there."""
    try:
        return PersonId.from_str(raw)
    except ValueError as error:
        raise NotFoundError('student', raw) from error
