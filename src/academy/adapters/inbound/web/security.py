"""Where a credential becomes a person id, and nothing further.

Two mechanisms, one outcome (ADR-0010). A browser sends a signed, HTTP-only cookie; a script
sends ``Authorization: Bearer``. Both carry a person id and nothing else, and both hand that id
to :class:`~academy.application.ports.outbound.identity.ActorIdentity`, which produces the
:class:`~academy.application.dtos.Actor` every use case takes.

That is the whole of ADR-0010's claim made mechanical: the two paths meet here, the type that
leaves carries no trace of which one it came from, and no use case could branch on the
difference even if it wanted to. ``tests/integration/test_web.py`` asserts it by driving the same
operation both ways and demanding the same answer.

**CSRF applies to the cookie and not to the header**, and the asymmetry is the reason rather than
an oversight: a browser attaches a cookie to a cross-site request all by itself, which is what
makes a forged request possible at all. Nothing attaches an ``Authorization`` header without the
caller asking for it, so a token-authenticated request cannot be forged by a third-party page and
a CSRF check on it would be ceremony.

-------------------------------------------------------------------------------------------------
CREDENTIAL VERIFICATION HERE IS A PLACEHOLDER AND IS NOT FIT TO DEPLOY.

:func:`verify_credentials` checks that a person exists and does not check a password, because
this repository has no password to check: ``Person`` carries none, and adding one would mean
modifying the copied domain (ADR-0002). ADR-0010 scoped password hashing and token rotation out
deliberately -- they are a large amount of machinery that teaches nothing about ports and
adapters -- and recorded the consequence that the placeholder must be unmistakably labelled or
someone will ship it. This banner is that label.

Everything *below* the sign-in step is real: the session is genuinely signed and genuinely
expires, the actor is genuinely resolved, and authorization is genuinely enforced. What is
missing is the proof that the person at the keyboard is who they say they are.
-------------------------------------------------------------------------------------------------
"""

from __future__ import annotations

import hmac
import secrets
from dataclasses import dataclass
from typing import Final

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from academy.application.ports.outbound.repositories import PersonRepository
from academy.domain.people.person import Person
from academy.domain.shared.ids import PersonId

# The cookie a browser gets. `__Host-` is not used: it requires Secure, which requires HTTPS,
# which would make the adapter unusable on `http://localhost:8000` -- the one deployment this
# repository actually ships. A real deployment terminates TLS and should rename it.
SESSION_COOKIE: Final = 'academy_session'
CSRF_COOKIE: Final = 'academy_csrf'
CSRF_FIELD: Final = 'csrf_token'
CSRF_HEADER: Final = 'X-CSRF-Token'

# Eight hours: a working day, after which a shared machine stops being signed in. Enforced by
# `itsdangerous` at read time, so an old cookie is refused rather than merely ignored.
SESSION_MAX_AGE_SECONDS: Final = 8 * 60 * 60

# Distinct salts, so a value signed as a session cannot be replayed as a bearer token or the
# reverse. Same key, different derived signing key -- which is what `salt` is for and is cheaper
# than configuring two secrets a deployment could set to the same thing anyway.
_SESSION_SALT: Final = 'academy.session.v1'
_BEARER_SALT: Final = 'academy.bearer.v1'

_BEARER_PREFIX: Final = 'Bearer '


class NotAuthenticatedError(Exception):
    """No usable credential was presented.

    Deliberately **not** an ``ApplicationError`` and not in ``error_status``'s table (ADR-0012).
    That table classifies a *use case* failing, and nothing here has reached a use case: the
    request never established who was asking, which is a different question from whether who was
    asking is allowed. 401 against 403, and conflating them is how a signed-out user gets told
    they lack permission and goes looking for an administrator instead of a sign-in link.
    """


class CsrfFailedError(Exception):
    """An unsafe browser request arrived without a token matching its cookie.

    Also outside the table, for the same reason: the request was rejected before anyone asked
    what it wanted to do.
    """


class Credentials:
    """Issues and reads the two credentials, over one signing key.

    Holds no request state and is built once, at startup, from
    :attr:`~academy.config.container.Container.secret_key`.
    """

    def __init__(self, secret_key: str) -> None:
        """Build the signers.

        Args:
            secret_key: The deployment's signing key. Already resolved -- the composition root
                has turned "unset" into either a generated key or a refusal to start, so there
                is no ``None`` to handle here.
        """
        self._session = URLSafeTimedSerializer(secret_key, salt=_SESSION_SALT)
        self._bearer = URLSafeTimedSerializer(secret_key, salt=_BEARER_SALT)

    def issue_session(self, person_id: PersonId) -> str:
        """Sign a session naming this person.

        The payload is the id and nothing else. Not the roles -- caching those in the session is
        exactly what the identity port forbids, because it would keep a revoked teacher writing
        grades until their cookie expired.
        """
        return self._session.dumps(str(person_id))

    def issue_token(self, person_id: PersonId) -> str:
        """Sign a bearer token naming this person.

        Same shape as a session and deliberately not the same value: a token pasted into a
        cookie, or a cookie sent as a token, fails its signature rather than working.
        """
        return self._bearer.dumps(str(person_id))

    def read_session(self, cookie: str | None) -> PersonId | None:
        """Recover the person id a session names.

        Returns:
            The id, or ``None`` if the cookie is absent, unsigned, tampered with, or older than
            :data:`SESSION_MAX_AGE_SECONDS`. One answer for every one of those, because the
            caller's response to all of them is the same and telling them apart would only tell
            an attacker which part they got right.
        """
        return self._read(self._session, cookie)

    def read_token(self, header: str | None) -> PersonId | None:
        """Recover the person id an ``Authorization: Bearer`` header names.

        Returns:
            The id, or ``None`` if the header is absent, is not a bearer header, or carries a
            value that does not verify.
        """
        if header is None or not header.startswith(_BEARER_PREFIX):
            return None
        return self._read(self._bearer, header.removeprefix(_BEARER_PREFIX).strip())

    @staticmethod
    def _read(serializer: URLSafeTimedSerializer, raw: str | None) -> PersonId | None:
        """Verify a signed value and parse the id inside it.

        ``loads`` returns whatever was serialised, which the type checker knows only as
        ``Any`` -- so the ``isinstance`` below is a real check at the real boundary and not a
        formality. A value that verifies but is not a string, or is a string that is not a UUID,
        is treated as no credential at all: it cannot have been issued by
        :meth:`issue_session`, so something is wrong in a way the caller cannot fix.
        """
        if not raw:
            return None

        try:
            payload = serializer.loads(raw, max_age=SESSION_MAX_AGE_SECONDS)
        except BadSignature, SignatureExpired:
            return None

        if not isinstance(payload, str):
            return None

        try:
            return PersonId.from_str(payload)
        except ValueError:
            return None


@dataclass(frozen=True, slots=True)
class CsrfToken:
    """A double-submit token: the same value in a cookie and in the submitted form."""

    value: str

    @classmethod
    def issue(cls) -> CsrfToken:
        """Mint a fresh token."""
        return cls(secrets.token_urlsafe(32))


def csrf_matches(cookie: str | None, submitted: str | None) -> bool:
    """Whether an unsafe request carried a token matching its cookie.

    The double-submit pattern: a page is served with a random token in both a cookie and its
    form, and only a page served from this origin can read the cookie to put the value in the
    form. A cross-site form post carries the cookie -- the browser sends it -- but cannot know
    what to put in the body.

    Compared with :func:`hmac.compare_digest` rather than ``==``: the comparison is on a secret,
    and a short-circuiting comparison leaks how much of a guess was right.

    Returns:
        ``True`` only if both are present and equal. A missing cookie is a failure and not a
        pass, which is the direction a mistake here has to fall.
    """
    if not cookie or not submitted:
        return False
    return hmac.compare_digest(cookie, submitted)


async def verify_credentials(people: PersonRepository, email: str, password: str) -> Person | None:
    """Check that whoever is signing in is who they claim to be -- **except that it does not**.

    See the banner at the top of this module. This looks the person up by email and returns
    them; ``password`` is accepted, ignored, and named so that the call site reads like the real
    thing it is standing in for. Replacing this function with one that verifies a hash is the
    whole of what a deployment would have to do, which is the point of isolating it here.

    Args:
        people: The person repository, used only to read.
        email: What was typed into the email field.
        password: What was typed into the password field. **Not checked.**

    Returns:
        The person, or ``None`` if no person has that address.
    """
    del password  # Named for the signature this is standing in for; deliberately unexamined.
    return await people.by_email(email)
