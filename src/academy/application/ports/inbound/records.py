"""The driving port for reading a student's own record, or a ward's.

One port for two actors, because they ask the identical question and differ only in the
relation that authorizes it. Splitting them into ``ViewOwnRecord`` and ``ViewWardRecord``
would duplicate every method and, worse, invite the second copy to check authorization
slightly differently from the first.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from academy.application.commands import ListMyWardsCommand, ViewAcademicHistoryCommand
from academy.application.dtos import AcademicHistoryDto, PersonDto


@runtime_checkable
class ViewStudentRecords(Protocol):
    """Reading transcripts, as the student themselves or as their guardian."""

    async def view_academic_history(self, command: ViewAcademicHistoryCommand) -> AcademicHistoryDto:
        """Read a student's full transcript and per-subject standing (UC-26, UC-30).

        Raises:
            AuthorizationError: If the actor is neither the student, nor a guardian to whom
                the guardianship still applies, nor an administrator. A guardian whose ward
                has come of age lands here, with nothing having changed in storage.
            NotFoundError: If no such student exists.
        """
        ...

    async def list_my_wards(self, command: ListMyWardsCommand) -> list[PersonDto]:
        """List the students currently in the actor's care (UC-28).

        "Currently" is load-bearing: the list is derived from stored guardianships filtered
        by each ward's age against the global age of majority, computed on read. A ward who
        had a birthday overnight is simply absent the next morning.

        Takes a command carrying the ``Actor`` rather than a person id, like every other use
        case here. The id it needs is the *authenticated* one, and a plain string parameter
        would let an inbound adapter pass one out of the request instead -- handing anyone
        the ability to enumerate anyone else's wards. There is no authorization check beyond
        that, because there is nothing to check: the subject of the question and the person
        asking it are the same, and an actor with no wards gets an empty list rather than a
        refusal.

        Returns:
            The wards whose guardianship applies today, each appearing once however many
            links connect the pair. No ordering is promised.
        """
        ...
