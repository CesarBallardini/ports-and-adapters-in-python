"""Grade value object."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from academy.domain.shared.errors import DomainError


class InvalidGradeError(DomainError):
    """Raised when a grade is outside the allowed range."""


@dataclass(frozen=True, slots=True, order=True)
class Grade:
    """An integer grade on a 0..10 scale; a subject is passed at 6 or above."""

    MIN: ClassVar[int] = 0
    MAX: ClassVar[int] = 10
    PASS_THRESHOLD: ClassVar[int] = 6

    value: int

    def __post_init__(self) -> None:
        """Validate the grade is within ``MIN..MAX``."""
        if not self.MIN <= self.value <= self.MAX:
            raise InvalidGradeError(f'grade must be in {self.MIN}..{self.MAX}: {self.value}')

    def is_passing(self) -> bool:
        """Return whether the grade meets the passing threshold."""
        return self.value >= self.PASS_THRESHOLD
