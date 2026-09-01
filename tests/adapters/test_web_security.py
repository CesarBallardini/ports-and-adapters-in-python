"""Sessions, tokens and CSRF, tested as values rather than through HTTP.

Every assertion here is about a credential in isolation: sign it, tamper with it, expire it, and
ask what comes back. That is the level at which the interesting failures live -- a signature that
verifies when it should not, a session that outlives its expiry, a CSRF comparison that says yes
to a missing cookie -- and none of them is easier to see through a request.

The one thing deliberately *not* tested here is that any of it authenticates anybody.
:func:`~academy.adapters.inbound.web.security.verify_credentials` is a labelled placeholder
(ADR-0010) and the test below pins the label rather than the behaviour, so that replacing it with
a real check does not have to fight a test asserting that it accepts everyone.
"""

from __future__ import annotations

import time
from datetime import date
from uuid import UUID

import pytest

from academy.adapters.inbound.web import security
from academy.adapters.inbound.web.security import (
    SESSION_MAX_AGE_SECONDS,
    Credentials,
    CsrfToken,
    csrf_matches,
    verify_credentials,
)
from academy.adapters.outbound.persistence.memory import MemoryPersonRepository, MemoryStore
from academy.domain.people.email import Email
from academy.domain.people.person import Person
from academy.domain.people.personal_data import PersonalData
from academy.domain.people.role import Role
from academy.domain.shared.ids import PersonId

pytestmark = pytest.mark.unit

SOMEONE = PersonId(UUID(int=7))
KEY = 'a-signing-key-for-tests'


@pytest.fixture
def signers() -> Credentials:
    return Credentials(KEY)


def test_a_session_round_trips(signers: Credentials) -> None:
    assert signers.read_session(signers.issue_session(SOMEONE)) == SOMEONE


def test_a_bearer_token_round_trips(signers: Credentials) -> None:
    assert signers.read_token(f'Bearer {signers.issue_token(SOMEONE)}') == SOMEONE


def test_a_session_carries_the_person_and_nothing_else(signers: Credentials) -> None:
    """Not the roles. Caching those is exactly what the identity port forbids.

    Asserted on the wire rather than in a docstring: the signed payload is the id's string form,
    so there is nowhere for a role to be hiding.
    """
    from itsdangerous import URLSafeTimedSerializer

    payload = URLSafeTimedSerializer(KEY, salt='academy.session.v1').loads(signers.issue_session(SOMEONE))

    assert payload == str(SOMEONE)


def test_a_session_whose_payload_was_edited_is_refused(signers: Credentials) -> None:
    """The whole point of signing it: becoming somebody else has to fail.

    The *payload* segment is edited rather than the signature, because that is both the attack
    anyone would actually attempt and the unambiguous test. Flipping the final character of the
    base64url signature is not: the last character carries fewer significant bits than a full
    byte, so some flips decode to the identical signature and verify correctly. A test written
    that way passes or fails depending on the cookie it happened to get.
    """
    # `rsplit`, because a compressed payload itself begins with a `.` -- so the value has four
    # dot-separated pieces and only the last two are the timestamp and the signature.
    payload, timestamp, signature = signers.issue_session(SOMEONE).rsplit('.', 2)
    edited = payload[:-2] + ('AA' if payload[-2:] != 'AA' else 'BB')

    assert signers.read_session(f'{edited}.{timestamp}.{signature}') is None


def test_a_session_whose_signature_was_dropped_is_refused(signers: Credentials) -> None:
    """An unsigned value shaped like a signed one is not a credential."""
    payload, _, _ = signers.issue_session(SOMEONE).rsplit('.', 2)

    assert signers.read_session(payload) is None


def test_a_session_signed_with_another_key_is_refused() -> None:
    """Two deployments, or one deployment before and after a key rotation."""
    theirs = Credentials('a-different-key').issue_session(SOMEONE)

    assert Credentials(KEY).read_session(theirs) is None


def test_a_session_cannot_be_replayed_as_a_bearer_token(signers: Credentials) -> None:
    """Distinct salts, so the two credentials are not interchangeable.

    They carry the same payload and are signed with the same key, so without separate salts a
    cookie stolen by an XSS would be a working API token and the reverse.
    """
    assert signers.read_token(f'Bearer {signers.issue_session(SOMEONE)}') is None
    assert signers.read_session(signers.issue_token(SOMEONE)) is None


def test_an_expired_session_is_refused(signers: Credentials, monkeypatch: pytest.MonkeyPatch) -> None:
    """Eight hours and one second later, the cookie stops working.

    Time is moved rather than waited for. ``itsdangerous`` timestamps at signing time and checks
    against ``time.time`` at read time, so advancing the clock is the whole of the simulation.
    """
    cookie = signers.issue_session(SOMEONE)
    later = time.time() + SESSION_MAX_AGE_SECONDS + 1
    monkeypatch.setattr(time, 'time', lambda: later)

    assert signers.read_session(cookie) is None


def test_a_session_just_inside_its_lifetime_still_works(signers: Credentials, monkeypatch: pytest.MonkeyPatch) -> None:
    """The other side of the boundary, so the expiry test cannot pass by refusing everything."""
    cookie = signers.issue_session(SOMEONE)
    later = time.time() + SESSION_MAX_AGE_SECONDS - 60
    monkeypatch.setattr(time, 'time', lambda: later)

    assert signers.read_session(cookie) == SOMEONE


@pytest.mark.parametrize('cookie', [None, '', 'not-signed-at-all', 'a.b.c'])
def test_a_missing_or_malformed_session_is_refused(signers: Credentials, cookie: str | None) -> None:
    """One answer for every way of being unusable; see the port docstring for why."""
    assert signers.read_session(cookie) is None


@pytest.mark.parametrize('header', [None, '', 'Basic abc', 'bearer lowercase', 'Bearer', 'Token abc'])
def test_only_a_bearer_header_is_read(signers: Credentials, header: str | None) -> None:
    """A scheme this adapter does not speak is no credential, not a malformed one."""
    assert signers.read_token(header) is None


def test_a_signed_value_that_is_not_a_uuid_is_refused() -> None:
    """Verified, and still not a credential this adapter could have issued.

    ``loads`` returns ``Any``, so the narrowing in ``_read`` is a real check at a real boundary.
    Someone holding the key could sign anything; only something that parses as a person id counts.
    """
    from itsdangerous import URLSafeTimedSerializer

    forged = URLSafeTimedSerializer(KEY, salt='academy.session.v1').dumps('not-a-uuid')

    assert Credentials(KEY).read_session(forged) is None


def test_a_signed_value_of_the_wrong_type_is_refused() -> None:
    """The same boundary, for a payload that is not even a string."""
    from itsdangerous import URLSafeTimedSerializer

    forged = URLSafeTimedSerializer(KEY, salt='academy.session.v1').dumps({'person_id': str(SOMEONE)})

    assert Credentials(KEY).read_session(forged) is None


def test_two_sessions_for_the_same_person_are_both_valid(signers: Credentials) -> None:
    """Two browsers, or a browser and a phone. Nothing here is a single-session scheme."""
    first, second = signers.issue_session(SOMEONE), signers.issue_session(SOMEONE)

    assert signers.read_session(first) == signers.read_session(second) == SOMEONE


def test_a_csrf_token_matches_itself() -> None:
    token = CsrfToken.issue()

    assert csrf_matches(token.value, token.value)


def test_two_csrf_tokens_do_not_match() -> None:
    assert not csrf_matches(CsrfToken.issue().value, CsrfToken.issue().value)


@pytest.mark.parametrize(
    ('cookie', 'submitted'),
    [(None, 'x'), ('x', None), (None, None), ('', 'x'), ('x', ''), ('x', 'y')],
)
def test_csrf_fails_closed(cookie: str | None, submitted: str | None) -> None:
    """A missing half is a failure, not a pass.

    The direction a mistake here has to fall: the alternative is a check that silently stops
    checking the moment a cookie fails to be set.
    """
    assert not csrf_matches(cookie, submitted)


def test_csrf_tokens_are_not_predictable() -> None:
    """Minted from ``secrets``; a token anyone can guess is not a token."""
    minted = {CsrfToken.issue().value for _ in range(50)}

    assert len(minted) == 50
    assert all(len(value) >= 32 for value in minted)


async def test_the_placeholder_credential_check_finds_a_person_by_email() -> None:
    """What the placeholder does do -- pinned so its replacement has a starting point."""
    people = MemoryPersonRepository(MemoryStore())
    await people.add(
        Person(
            id=SOMEONE,
            email=Email('dana@academy.test'),
            personal=PersonalData(full_name='Dana', birth_date=date(1990, 1, 1)),
            roles={Role.TEACHER},
        )
    )

    found = await verify_credentials(people, 'dana@academy.test', 'any password at all')

    assert found is not None
    assert found.id == SOMEONE


async def test_the_placeholder_credential_check_refuses_an_unknown_address() -> None:
    """Unknown is still refused, which is why a wrong email cannot sign anybody in."""
    people = MemoryPersonRepository(MemoryStore())

    assert await verify_credentials(people, 'nobody@academy.test', 'x') is None


def test_the_placeholder_is_labelled_where_someone_would_look() -> None:
    """ADR-0010's own consequence: "the placeholder credential check must be unmistakably
    labelled, or someone will ship it".

    A test on a comment looks odd until you consider what it is protecting. The label is the only
    thing standing between this and a deployment, and a tidy-up that removed it would otherwise
    be invisible in review.
    """
    module_doc = security.__doc__ or ''
    function_doc = verify_credentials.__doc__ or ''

    assert 'NOT FIT TO DEPLOY' in module_doc
    assert 'PLACEHOLDER' in module_doc
    assert 'does not' in function_doc
