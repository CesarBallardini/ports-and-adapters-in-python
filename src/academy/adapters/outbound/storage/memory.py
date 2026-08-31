"""In-process file storage: a dictionary that satisfies the blob port.

A production-grade adapter (ADR-0014), not a stand-in. It is what the test suite, a CLI
invocation and a single-process demo want, and its limitation is exactly the one its name
states: the bytes live as long as the process.
"""

from __future__ import annotations

from academy.application.errors import NotFoundError


class MemoryFileStorage:
    """Bytes held in a dictionary, keyed by the application's opaque keys.

    Satisfies :class:`~academy.application.ports.outbound.file_storage.FileStorage`.
    """

    def __init__(self) -> None:
        """Start empty."""
        self._blobs: dict[str, bytes] = {}

    async def put(self, key: str, data: bytes) -> None:
        """Store ``data`` under ``key``, replacing anything already there."""
        self._blobs[key] = data

    async def get(self, key: str) -> bytes:
        """Retrieve the bytes stored under ``key``.

        Raises:
            NotFoundError: If nothing is stored there.
        """
        blob = self._blobs.get(key)
        if blob is None:
            raise NotFoundError('stored file', key)
        return blob

    async def delete(self, key: str) -> None:
        """Remove whatever is stored under ``key``. Idempotent."""
        self._blobs.pop(key, None)
