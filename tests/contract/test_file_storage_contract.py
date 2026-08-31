"""What every file-storage adapter must do (ADR-0014).

Two implementations that share nothing operationally -- a dictionary and a directory -- and
must be indistinguishable to the application. That is the claim the port's own docstring makes
("differ in every operational respect and in none that the application can observe"), and this
is where it is checked rather than asserted.

`unit` tier: the local adapter writes to a pytest temporary directory, which is fast and needs
no service. When an S3 adapter joins ``BACKENDS`` its parameter moves to the integration tier,
and these assertions come with it unchanged.
"""

from collections.abc import Callable
from pathlib import Path

import pytest

from academy.adapters.outbound.storage import LocalFileStorage, MemoryFileStorage
from academy.application.errors import NotFoundError
from academy.application.ports.outbound.file_storage import FileStorage

KEY = 'imports/job-1'
PAYLOAD = b'student_email,grade\r\nada@academy.test,8\r\n'


# Add the S3 adapter here and every test below runs against it too.
BACKENDS = [
    pytest.param(lambda _root: MemoryFileStorage(), id='memory'),
    pytest.param(LocalFileStorage, id='local'),
]


@pytest.fixture(params=BACKENDS)
def storage(request: pytest.FixtureRequest, tmp_path: Path) -> FileStorage:
    """One storage adapter, empty."""
    build: Callable[[Path], FileStorage] = request.param
    return build(tmp_path)


@pytest.mark.unit
async def test_what_was_put_can_be_got(storage: FileStorage) -> None:
    await storage.put(KEY, PAYLOAD)

    assert await storage.get(KEY) == PAYLOAD


@pytest.mark.unit
async def test_putting_again_replaces_what_was_there(storage: FileStorage) -> None:
    await storage.put(KEY, PAYLOAD)
    await storage.put(KEY, b'replaced')

    assert await storage.get(KEY) == b'replaced'


@pytest.mark.unit
async def test_getting_what_was_never_stored_is_not_an_empty_file(storage: FileStorage) -> None:
    # The failure a worker meets when a sweep removed a payload between submission and
    # execution. Returning b'' would make it import nothing and report success.
    with pytest.raises(NotFoundError):
        await storage.get(KEY)


@pytest.mark.unit
async def test_a_deleted_key_is_gone(storage: FileStorage) -> None:
    await storage.put(KEY, PAYLOAD)
    await storage.delete(KEY)

    with pytest.raises(NotFoundError):
        await storage.get(KEY)


@pytest.mark.unit
async def test_deleting_twice_is_not_an_error(storage: FileStorage) -> None:
    # Cleanup runs after a job reaches a terminal state, and must not fail merely because it
    # is running for the second time.
    await storage.put(KEY, PAYLOAD)
    await storage.delete(KEY)
    await storage.delete(KEY)


@pytest.mark.unit
async def test_keys_that_look_like_paths_are_still_just_keys(storage: FileStorage) -> None:
    # The port says keys are opaque. This is the assertion that keeps the local adapter honest
    # about it: '../' in a key must reach nothing outside the directory it was given, and two
    # keys that differ must not collide however they are spelled.
    await storage.put('../escape', b'one')
    await storage.put('nested/deep/key', b'two')

    assert await storage.get('../escape') == b'one'
    assert await storage.get('nested/deep/key') == b'two'


@pytest.mark.unit
async def test_an_empty_payload_is_a_payload(storage: FileStorage) -> None:
    # Distinct from an absent one, and the distinction matters: an empty upload is a file the
    # registrar really sent, and it should import zero rows rather than fail the job.
    await storage.put(KEY, b'')

    assert await storage.get(KEY) == b''


@pytest.mark.unit
def test_every_backend_satisfies_the_port(storage: FileStorage) -> None:
    assert isinstance(storage, FileStorage)
