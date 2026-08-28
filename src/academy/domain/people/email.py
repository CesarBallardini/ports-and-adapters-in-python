"""Email value object."""

from __future__ import annotations

from dataclasses import dataclass

from academy.domain.shared.errors import DomainError


class InvalidEmailError(DomainError):
    """Raised when an email address is not well formed."""


@dataclass(frozen=True, slots=True)
class Email:
    """A syntactically valid, case-normalized email address."""

    value: str

    def __post_init__(self) -> None:
        """Validate the address and normalize it to lower case."""
        normalized = self.value.strip().lower()
        local, sep, domain = normalized.partition('@')
        if not sep or not local or not domain:
            raise InvalidEmailError(self.value)
        if '.' not in domain or domain.startswith('.') or domain.endswith('.'):
            raise InvalidEmailError(self.value)
        object.__setattr__(self, 'value', normalized)
