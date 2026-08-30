"""Unit tests for the clock adapters.

`unit` tier per ADR-0013. Small, and they were missing: every other test in the suite injects
``FixedClock``, so ``SystemClock`` -- the adapter that actually runs in production -- had never
been executed, and ``FixedClock``'s one guard had never been tripped.
"""

from datetime import UTC, date, datetime, timedelta

import pytest

from academy.adapters.outbound.system import FixedClock, SystemClock
from academy.application.ports.outbound.system import Clock


@pytest.mark.unit
def test_the_system_clock_reports_an_aware_utc_instant() -> None:
    # The port refuses naive datetimes: they compare and serialise inconsistently, and the
    # difference only ever surfaces in production.
    now = SystemClock().now()

    assert now.tzinfo is not None
    assert now.utcoffset() == timedelta(0)


@pytest.mark.unit
def test_the_system_clocks_date_is_the_date_of_its_own_instant() -> None:
    # Derived rather than read separately, so the two cannot straddle midnight within a
    # single call. Comparing to a second reading is the closest a test can get without
    # freezing the machine's clock.
    clock = SystemClock()

    assert clock.today() == clock.now().date()


@pytest.mark.unit
def test_a_fixed_clock_reports_exactly_the_instant_it_was_given() -> None:
    instant = datetime(2026, 8, 30, 9, 30, tzinfo=UTC)
    clock = FixedClock(instant)

    assert clock.now() == instant
    assert clock.today() == date(2026, 8, 30)


@pytest.mark.unit
def test_a_fixed_clock_at_a_day_stops_at_midnight_utc() -> None:
    clock = FixedClock.at(date(2026, 8, 30))

    assert clock.now() == datetime(2026, 8, 30, tzinfo=UTC)


@pytest.mark.unit
def test_a_fixed_clock_refuses_a_naive_instant() -> None:
    # The guard exists so a fixed clock cannot let a naive value into code that only ever
    # meets aware ones in production -- which would make the test suite the one place the
    # bug is invisible.
    with pytest.raises(ValueError, match='timezone-aware'):
        FixedClock(datetime(2026, 8, 30, 9, 30))


@pytest.mark.unit
def test_both_clocks_satisfy_the_port() -> None:
    assert isinstance(SystemClock(), Clock)
    assert isinstance(FixedClock.at(date(2026, 8, 30)), Clock)
