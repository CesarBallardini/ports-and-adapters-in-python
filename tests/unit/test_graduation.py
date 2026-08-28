"""Unit tests for the graduation bounded context."""

from datetime import date
from uuid import UUID

import pytest

from academy.domain.graduation.graduation import (
    Graduation,
    GraduationStateError,
    GraduationStatus,
)
from academy.domain.shared.ids import CredentialId, GraduationId, PersonId, ProgramId


def make_graduation() -> Graduation:
    return Graduation(
        GraduationId(UUID(int=1)),
        PersonId(UUID(int=2)),
        ProgramId(UUID(int=3)),
        CredentialId(UUID(int=4)),
        date(2026, 3, 1),
    )


@pytest.mark.unit
def test_graduation_starts_active() -> None:
    assert make_graduation().is_active()


@pytest.mark.unit
def test_revoke_then_reissue() -> None:
    graduation = make_graduation()

    graduation.revoke()
    assert graduation.status is GraduationStatus.REVOKED

    graduation.reissue(date(2026, 9, 1))
    assert graduation.is_active()
    assert graduation.conferred_on == date(2026, 9, 1)


@pytest.mark.unit
def test_revoking_twice_raises() -> None:
    graduation = make_graduation()
    graduation.revoke()

    with pytest.raises(GraduationStateError):
        graduation.revoke()


@pytest.mark.unit
def test_reissuing_an_active_graduation_raises() -> None:
    graduation = make_graduation()

    with pytest.raises(GraduationStateError):
        graduation.reissue(date(2026, 9, 1))
