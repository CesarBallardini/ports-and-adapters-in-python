"""Clock adapters: the real one, and the one that lets a test decide what day it is.

Two implementations of the smallest port in the codebase, and the pair is the point. Every
rule in academy that depends on "now" -- a person's age, whether a guardianship still
applies, which term is open -- becomes an ordinary assertion once ``today`` is an injected
value rather than a syscall.
"""

from __future__ import annotations

from datetime import UTC, date, datetime


class SystemClock:
    """The wall clock, read in UTC.

    Satisfies :class:`~academy.application.ports.outbound.system.Clock`.
    """

    def today(self) -> date:
        """Today's date in UTC.

        Derived from :meth:`now` rather than read separately, so the two cannot straddle
        midnight within a single call.
        """
        return self.now().date()

    def now(self) -> datetime:
        """The current instant, as a timezone-aware UTC datetime."""
        return datetime.now(UTC)


class FixedClock:
    """A clock stopped at a chosen instant.

    Satisfies :class:`~academy.application.ports.outbound.system.Clock`. Not a test double
    in any way that matters: it is the adapter a reproducible batch run wants, as much as
    the one a test wants.
    """

    def __init__(self, instant: datetime) -> None:
        """Stop the clock at ``instant``.

        Args:
            instant: The instant to report. Must be timezone-aware, because the port
                promises an aware datetime and a fixed clock that returned a naive one would
                let a naive value into code that only ever meets aware ones in production.

        Raises:
            ValueError: If ``instant`` is naive.
        """
        if instant.tzinfo is None or instant.tzinfo.utcoffset(instant) is None:
            raise ValueError('FixedClock requires a timezone-aware instant')
        self._instant = instant

    @classmethod
    def at(cls, day: date) -> FixedClock:
        """Stop the clock at midnight UTC on ``day``.

        The convenient constructor for the common case, where a test cares which day it is
        and not which second.
        """
        return cls(datetime(day.year, day.month, day.day, tzinfo=UTC))

    def today(self) -> date:
        """The date component of the fixed instant."""
        return self._instant.date()

    def now(self) -> datetime:
        """The fixed instant."""
        return self._instant
