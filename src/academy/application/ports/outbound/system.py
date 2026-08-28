"""Ports for the two ambient facts a pure domain must not read for itself: time and identity.

**Both are sync** (ADR-0005). Reading a clock and generating a UUID are CPU work; nothing
waits on anything outside the process, and an ``async def`` here would tell the reader a lie
about where the code can yield.

These are the smallest ports in the codebase and among the most valuable. Every rule in
academy that depends on "now" -- a person's age, whether guardianship still applies, which
term is open for enrollment -- would otherwise be testable only by changing the system clock.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Protocol, runtime_checkable

from academy.domain.shared.ids import (
    CredentialId,
    GraduationId,
    GuardianshipId,
    PersonId,
    PlanId,
    ProgramId,
    SectionId,
    SubjectId,
)


@runtime_checkable
class Clock(Protocol):
    """The source of "now".

    A port, because reading the wall clock is I/O in the sense that matters: it makes a
    result depend on something outside the process, and therefore not reproducible.

    The domain never calls this. Domain rules that need a date take ``today`` as an argument
    and stay pure; the *application* is what reads the clock and passes the value in. That
    division is why ``docs/04-state-diagrams.md`` §6 can describe a guardianship that ends
    with nobody acting, and still have it be a testable transition.
    """

    def today(self) -> date:
        """The current date.

        Returns:
            Today's date. Implementations must be consistent with :meth:`now`: calling both
            in the same operation must not straddle midnight, so a fixed clock returns the
            date component of its fixed instant rather than an independently chosen day.
        """
        ...

    def now(self) -> datetime:
        """The current instant.

        Returns:
            A timezone-aware datetime in UTC. Naive datetimes are not acceptable: they
            compare and serialise inconsistently, and the difference only ever surfaces in
            production.
        """
        ...


@runtime_checkable
class IdGenerator(Protocol):
    """The source of new identities.

    One method per identifier type rather than a single ``next_id()``, so a caller cannot
    accidentally put a ``SubjectId`` where a ``PersonId`` belongs -- the domain went to the
    trouble of making those distinct types, and a generic generator would hand that back.

    Implementations must never return a value they have returned before, and must not
    require a database round trip: identity is assigned before an entity is stored, which is
    what lets a use case build a complete aggregate and save it once.
    """

    def next_person_id(self) -> PersonId:
        """A fresh person identifier."""
        ...

    def next_credential_id(self) -> CredentialId:
        """A fresh credential identifier."""
        ...

    def next_program_id(self) -> ProgramId:
        """A fresh degree-program identifier."""
        ...

    def next_plan_id(self) -> PlanId:
        """A fresh plan identifier."""
        ...

    def next_subject_id(self) -> SubjectId:
        """A fresh subject identifier."""
        ...

    def next_section_id(self) -> SectionId:
        """A fresh course-section identifier."""
        ...

    def next_graduation_id(self) -> GraduationId:
        """A fresh graduation identifier."""
        ...

    def next_guardianship_id(self) -> GuardianshipId:
        """A fresh guardianship identifier."""
        ...
