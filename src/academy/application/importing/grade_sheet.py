"""Importing a term's grades for one course section (UC-40).

Every rule the use case has is in this file, and none of them is in an adapter. That is the
claim ``tests/acceptance/features/grade_import.feature`` will check by running the same
scenarios through the CSV adapter and the XLSX adapter and demanding identical outcomes: if a
rule had leaked downwards, the two runs would diverge.

The rules, in the order a row meets them (``docs/02-actors-and-use-cases.md`` UC-40 §6):

1. the student is named by an email the system knows;
2. that student is not already named by an earlier row in this file;
3. the grade is an integer the domain accepts;
4. the student is enrolled in *this* section.

Only the last two are the domain's, and both are asked by calling it rather than by restating
it here -- ``Grade`` validates the value and ``GradingService`` owns the enrollment rule. A
fourth copy of "a grade is 0 to 10" is exactly what a repository like this exists to avoid.
"""

from __future__ import annotations

from collections.abc import Sequence

from academy.application.authorization import AccessGuard
from academy.application.dtos import Actor, ImportResultDto, RowError
from academy.application.errors import NotFoundError
from academy.application.importing.rows import Template, numbered
from academy.application.jobs import ImportContext
from academy.application.ports.outbound.repositories import (
    AcademicHistoryRepository,
    PersonRepository,
    SectionRepository,
)
from academy.application.ports.outbound.spreadsheet import Row
from academy.domain.academics.course_section import CourseSection
from academy.domain.authorization.models import Action, ResourceType
from academy.domain.grades.grade import Grade, InvalidGradeError
from academy.domain.people.person import Person
from academy.domain.services.grading_service import GradingService, StudentNotEnrolledError
from academy.domain.shared.errors import DomainError
from academy.domain.shared.ids import PersonId, SectionId

# The columns of a grade sheet, in the order the template writes them. `student_name` is
# there for the human filling the file in and is ignored on the way back: the email is the
# identifier, and a teacher correcting a spelling must not thereby create a person.
#
# Comments rather than attribute docstrings: the check-docstring-first hook reads a string
# literal after a module-level assignment as a second module docstring.
STUDENT_EMAIL = 'student_email'
STUDENT_NAME = 'student_name'
GRADE = 'grade'
HEADERS = [STUDENT_EMAIL, STUDENT_NAME, GRADE]


class GradeSheetImporter:
    """Records one grade attempt per row of a section's grade sheet.

    Satisfies :class:`~academy.application.importing.rows.RowImporter`. It owns no transaction
    and no file: :class:`~academy.application.importing.service.ImportService` opens the unit
    of work around ``import_rows`` and rolls it back for a dry run, so this class can be read
    as "what a row means" with nothing else in the way.
    """

    def __init__(
        self,
        sections: SectionRepository,
        histories: AcademicHistoryRepository,
        people: PersonRepository,
        guard: AccessGuard,
        grading: GradingService | None = None,
    ) -> None:
        """Wire the importer to its collaborators.

        Args:
            sections: Source of the section being graded and its roster.
            histories: Where the grades land.
            people: Resolves the emails in the file, and the acting teacher.
            guard: Enforces that the actor may write grades for this section.
            grading: The domain service that owns the cross-aggregate grading rules.
                Injectable so a test can observe the delegation, never so a deployment can
                replace the rules.
        """
        self._sections = sections
        self._histories = histories
        self._people = people
        self._guard = guard
        self._grading = grading or GradingService()

    async def template(self, context: ImportContext) -> Template:
        """Build the section's grade sheet, pre-filled with the enrolled students (UC-36).

        Pre-filled rather than blank, because that is what makes the round trip a workflow:
        the teacher fills in a grade column against names that are already correct, and the
        emails that come back are ones the system can certainly resolve.

        Raises:
            NotFoundError: If ``context`` names no section, or one that does not exist.
        """
        section = await self._section(context)
        roster = await self._people.by_ids(sorted(section.students(), key=str))
        roster.sort(key=lambda person: (person.personal.full_name, str(person.id)))
        return Template(
            headers=list(HEADERS),
            rows=[[person.email.value, person.personal.full_name, ''] for person in roster],
        )

    async def import_rows(self, actor: Actor, rows: Sequence[Row], context: ImportContext) -> ImportResultDto:
        """Record a grade for every row that survives the four rules.

        Authorization is checked **once, for the whole file**, not per row: the actor either
        may grade this section or may not, and refusing row by row would report a permissions
        problem as ninety-nine data problems. The check is the conjunction ADR-0016 uses for
        reading a sheet, in its write direction -- the actor must be allowed to write grades
        for every student on the roster.

        Raises:
            AuthorizationError: If the actor may not write grades for this section.
            NotFoundError: If ``context`` names no section, or the acting teacher is unknown.
        """
        section = await self._section(context)
        await self._require_gradable(actor, section)
        teacher = await self._require_person(actor.person_id, 'teacher')

        created = 0
        errors: list[RowError] = []
        seen: set[PersonId] = set()

        for line, row in numbered(rows):
            error = await self._apply(row, line, section, teacher, seen)
            if error is not None:
                errors.append(error)
            else:
                created += 1

        return ImportResultDto(created=created, errors=tuple(errors))

    async def _apply(
        self,
        row: Row,
        line: int,
        section: CourseSection,
        teacher: Person,
        seen: set[PersonId],
    ) -> RowError | None:
        """Apply one row, or say why it was rejected.

        Returns ``None`` when the row was recorded. Every failure here is a *data* failure and
        so becomes a report entry: one bad row in a hundred must not cost the other
        ninety-nine (UC-40 §6a).
        """
        email = row.get(STUDENT_EMAIL, '').strip()
        raw_grade = row.get(GRADE, '').strip()
        values = (email, raw_grade)

        student = await self._people.by_email(email) if email else None
        if student is None:
            return RowError(line=line, reason=f'no student with email {email!r}', values=values)

        if student.id in seen:
            # The later row loses. Taking the last would make the outcome depend on the order
            # a teacher happened to paste rows in, and neither row is more authoritative.
            return RowError(line=line, reason='this student already has a row in this file', values=values)

        try:
            grade = Grade(int(raw_grade))
        except (ValueError, InvalidGradeError) as error:
            return RowError(line=line, reason=f'{raw_grade!r} is not a grade: {error}', values=values)

        history = await self._histories.get_or_create(student.id)
        try:
            self._grading.record_grade(section, teacher, student.id, grade, history)
        except StudentNotEnrolledError:
            return RowError(line=line, reason='this student is not enrolled in this section', values=values)
        except DomainError as error:
            # Any other rule the domain enforces about this attempt. Caught as a row failure
            # rather than allowed to abort the run, because it is a fact about this row.
            return RowError(line=line, reason=str(error), values=values)

        await self._histories.save(history)
        seen.add(student.id)
        return None

    async def _section(self, context: ImportContext) -> CourseSection:
        """Resolve the section this import is for.

        Raises:
            NotFoundError: If the context carries no section id, or an unparseable one, or
                one that names no section. All three name nothing, which is what the error
                says -- and an import with no target is not a partially successful import.
        """
        raw = context.get('section_id', '')
        try:
            section_id = SectionId.from_str(raw)
        except ValueError as error:
            raise NotFoundError('course section', raw) from error

        section = await self._sections.get(section_id)
        if section is None:
            raise NotFoundError('course section', section_id)
        return section

    async def _require_gradable(self, actor: Actor, section: CourseSection) -> None:
        """Require write access to the grades of everyone on the roster."""
        for student_id in sorted(section.students(), key=str):
            await self._guard.require(actor, Action.WRITE, ResourceType.GRADES, student_id)

    async def _require_person(self, person_id: PersonId, entity: str) -> Person:
        """Fetch a person the import cannot proceed without.

        Raises:
            NotFoundError: If no such person is stored.
        """
        person = await self._people.get(person_id)
        if person is None:
            raise NotFoundError(entity, person_id)
        return person
