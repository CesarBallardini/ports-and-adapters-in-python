"""Personal data value object."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from academy.domain.shared.errors import DomainError


class InvalidPersonalDataError(DomainError):
    """Raised when personal data is invalid (empty name or an impossible birth date)."""


@dataclass(frozen=True, slots=True)
class PersonalData:
    """A person's personal data: full name and date of birth."""

    full_name: str
    birth_date: date

    def __post_init__(self) -> None:
        """Validate that the full name is not empty."""
        if not self.full_name.strip():
            raise InvalidPersonalDataError('full_name must not be empty')

    def age(self, today: date) -> int:
        """Return the age in completed years as of ``today``.

        Args:
            today: The reference date to compute the age against.

        Returns:
            The age in whole (completed) years.
        """
        if today < self.birth_date:
            raise InvalidPersonalDataError('today must not be before birth_date')
        years = today.year - self.birth_date.year
        had_birthday_this_year = (today.month, today.day) >= (self.birth_date.month, self.birth_date.day)
        return years if had_birthday_this_year else years - 1
