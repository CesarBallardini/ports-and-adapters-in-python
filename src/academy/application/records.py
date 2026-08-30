"""Reading a student's transcript, as the student or as their guardian (UC-26, UC-28, UC-30).

The implementation behind :class:`~academy.application.ports.inbound.records.ViewStudentRecords`.
One class per port rather than one per use case (``docs/06-class-diagram.md`` §2).

Read-only, and it shows: there is no :class:`~academy.application.ports.outbound.unit_of_work.UnitOfWork`
here at all. A transaction is a write boundary, and opening one around a read would claim a
consistent snapshot across three repositories that the port does not promise and that a
transcript does not need.

The interesting half is not the transcript, it is *who may see it*. Both use cases turn on a
guardianship that expires without anybody acting on it -- a ward has a birthday and the
guardian's access is gone the next morning, with nothing written and no job run
(``docs/04-state-diagrams.md`` §6). ``view_academic_history`` gets that for free from the
guard, because :class:`~academy.application.authorization.RelationshipResolver` already
computes the relation on every check. ``list_my_wards`` has to do the same filtering itself,
since it needs the *list* rather than a yes or no; both call the same domain rule,
``Guardianship.applies``, so the two answers cannot drift apart.
"""

from __future__ import annotations

from academy.application.authorization import AccessGuard
from academy.application.commands import ListMyWardsCommand, ViewAcademicHistoryCommand
from academy.application.dtos import AcademicHistoryDto, PersonDto
from academy.application.errors import NotFoundError
from academy.application.ports.outbound.repositories import (
    AcademicHistoryRepository,
    ConfigurationRepository,
    GuardianshipRepository,
    PersonRepository,
)
from academy.application.ports.outbound.system import Clock
from academy.domain.authorization.models import Action, ResourceType
from academy.domain.grades.academic_history import AcademicHistory
from academy.domain.people.person import Person
from academy.domain.shared.ids import PersonId


class StudentRecords:
    """Reading transcripts, as the student themselves or as their guardian.

    Satisfies :class:`~academy.application.ports.inbound.records.ViewStudentRecords`. The
    conformance is structural, so nothing here declares it; the contract that matters is the
    port's docstrings, and the test suite asserts them against this class.
    """

    def __init__(
        self,
        histories: AcademicHistoryRepository,
        people: PersonRepository,
        guardianships: GuardianshipRepository,
        configuration: ConfigurationRepository,
        clock: Clock,
        guard: AccessGuard,
    ) -> None:
        """Wire the use cases to their collaborators.

        Args:
            histories: Where transcripts are read from.
            people: Resolves a student id into the person whose existence the port promises
                to check, and puts names on a ward listing.
            guardianships: The stored guardian-to-ward links.
            configuration: Source of the global age of majority.
            clock: Supplies ``today`` to the domain, which never reads it itself.
            guard: Resolves relations and enforces what they grant.
        """
        self._histories = histories
        self._people = people
        self._guardianships = guardianships
        self._configuration = configuration
        self._clock = clock
        self._guard = guard

    async def view_academic_history(self, command: ViewAcademicHistoryCommand) -> AcademicHistoryDto:
        """Read a student's full transcript and per-subject standing (UC-26, UC-30).

        Authorization comes first, before the student is even looked up. Who may read a
        transcript is derived from the student's id alone -- they are the owner of the record
        -- so nothing has to be loaded to decide it, and checking first means an unauthorized
        caller cannot use the difference between "forbidden" and "no such student" to discover
        which ids exist. That is the opposite order from ``list_section_grades``, and for the
        opposite reason: a section's readers are derived from the section, so it has to be
        loaded before the question can be asked at all.

        Args:
            command: The student whose transcript is wanted, and who is asking.

        Returns:
            The transcript, with a standing per subject attempted. A student who exists but
            has never been graded reads as an **empty** transcript rather than a missing one:
            "no grades yet" is a normal state, and the alternative would make every caller
            handle an error for the first day of every student's enrollment.

        Raises:
            AuthorizationError: If the actor is neither the student, nor a guardian to whom
                the guardianship still applies, nor an administrator.
            NotFoundError: If no such student exists.
        """
        student_id = _student_id(command.student_id)

        await self._guard.require(command.actor, Action.READ, ResourceType.ACADEMIC_HISTORY, student_id)

        if await self._people.get(student_id) is None:
            raise NotFoundError('student', student_id)

        history = await self._histories.get(student_id)
        # Built rather than stored: `get_or_create` would be a write on a read path, and one
        # taken outside any transaction at that. An empty transcript is a projection, not a
        # record, until the first grade makes it one.
        return AcademicHistoryDto.of(history if history is not None else AcademicHistory(student_id))

    async def list_my_wards(self, command: ListMyWardsCommand) -> list[PersonDto]:
        """List the students currently in the actor's care (UC-28).

        "Currently" is the whole difficulty. The links are stored; whether one *applies* is
        computed here on every read, from the ward's age against the global age of majority.
        A ward who had a birthday overnight is simply absent the next morning, with nothing
        having been written and no job having run.

        No guard call, and none missing: the subject of the question is the actor asking it,
        so there is no second party whose access could be in doubt. What makes that safe is
        the *type* -- the actor arrives inside the command, and an ``Actor`` can only come
        from authentication (ADR-0010). A person-id parameter would have had to come from
        somewhere, and an inbound adapter reaching into the request for it would hand anyone
        the ability to enumerate anyone else's wards.

        Args:
            command: Who is asking. That is the whole request.

        Returns:
            The wards whose guardianship applies today, ordered by name then id, each ward
            appearing once even if two links connect the same pair. An actor with no wards,
            or whose wards have all come of age, gets an empty list rather than an error:
            having no wards is a normal answer to this question.
        """
        links = await self._guardianships.wards_of(command.actor.person_id)
        if not links:
            return []

        age_of_majority = await self._configuration.age_of_majority()
        today = self._clock.today()

        wards: dict[PersonId, Person] = {}
        for link in links:
            if link.ward_id in wards:
                continue
            ward = await self._people.get(link.ward_id)
            # A link can outlive the person it names. That is a data problem for elsewhere;
            # here it is one ward missing from a listing, not a failed request.
            if ward is not None and link.applies(ward, age_of_majority, today):
                wards[link.ward_id] = ward

        return [PersonDto.of(ward) for ward in sorted(wards.values(), key=_by_name)]


def _by_name(person: Person) -> tuple[str, str]:
    """Order a listing by name, with the id breaking ties into a total order.

    The port promises no ordering, so this is the implementation being stricter than its
    contract rather than satisfying it. A listing without a stable order is a paginated view
    that shuffles under the reader, and two people share a name often enough to matter.
    """
    return (person.personal.full_name, str(person.id))


def _student_id(raw: str) -> PersonId:
    """Parse a student identifier, treating an unparseable one as a student that is not there.

    A malformed id names nothing, which is what ``NotFoundError`` says. Letting the
    ``ValueError`` escape instead would reach an inbound adapter that has no entry for it in
    the status table (ADR-0012) and become a 500 -- a server error for what is plainly a bad
    request.
    """
    try:
        return PersonId.from_str(raw)
    except ValueError as error:
        raise NotFoundError('student', raw) from error
