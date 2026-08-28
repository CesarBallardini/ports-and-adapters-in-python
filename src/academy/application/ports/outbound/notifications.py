"""The outbound notification port.

**Async** (ADR-0005): SMTP is I/O.

Its shape is the lesson. The methods are named after **intents**, not after email: a use
case says "a graduation was conferred", never "send this subject and this HTML body". The
adapter owns the templates, the wording, the localisation and the transport, so none of that
can leak into the application -- and a use case's test asserts that the right thing was
announced rather than that the right string was formatted.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from academy.application.dtos import ImportResultDto
from academy.domain.graduation.graduation import Graduation
from academy.domain.shared.ids import PersonId


@runtime_checkable
class Notifier(Protocol):
    """Announces that something happened, to whoever should hear it.

    **Implementations must not raise.** A notification is a side channel: failing to send one
    is not a reason to fail the use case that caused it. Swallowing and logging the failure is
    the adapter's responsibility precisely so that no use case has to wrap these calls in a
    try/except and decide, over and over, that email is not important enough to roll back a
    conferred degree.
    """

    async def graduation_conferred(self, graduation: Graduation) -> None:
        """Tell the student their degree has been conferred."""
        ...

    async def graduation_revoked(self, graduation: Graduation) -> None:
        """Tell the student their degree has been revoked."""
        ...

    async def import_finished(self, submitted_by: PersonId, result: ImportResultDto) -> None:
        """Tell the submitter that a queued import has finished.

        Only queued imports notify. An inline import already returned its report in the
        response, and emailing that too would be noise.
        """
        ...

    async def grade_recorded(self, student_id: PersonId, subject_name: str, grade: int) -> None:
        """Tell a student, and any guardian who currently applies, about a new grade.

        Resolving *who* currently has guardianship is the application's job, not this port's
        -- it is a domain rule evaluated against the ward's age. The adapter is told the
        student and delivers accordingly.
        """
        ...
