"""Choosing which spreadsheet adapter reads or writes a given file.

The one place in the import feature that knows file formats exist. Everything above it works
in rows, and everything below it works in one format; this is the seam, and keeping it to a
single small object is what stops ``if content_type == 'text/csv'`` appearing in a use case.

It holds adapters it did not build. The composition root decides which implementations exist
(ADR-0015); this decides which of them a given upload wants.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import PurePosixPath

from academy.application.errors import MalformedSpreadsheetError
from academy.application.ports.outbound.spreadsheet import SpreadsheetReader, SpreadsheetWriter

# What a browser, a curl invocation and a CLI actually send. The `application/octet-stream`
# entry is not sloppiness: it is what most clients send for a file they did not sniff, and it
# is why the filename is consulted as well.
#
# Comments rather than attribute docstrings: the check-docstring-first hook reads a string
# literal after a module-level assignment as a second module docstring.
CONTENT_TYPES = {
    'text/csv': 'csv',
    'application/csv': 'csv',
    'text/plain': 'csv',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': 'xlsx',
    'application/vnd.ms-excel': 'xlsx',
}
EXTENSIONS = {'.csv': 'csv', '.xlsx': 'xlsx', '.xlsm': 'xlsx'}

# The extension to *write* for a format, derived from the table above rather than restated, so
# the two cannot disagree about what `.xlsm` is. Reversed because a later entry wins in a dict
# comprehension and the first extension listed for a format is its canonical one -- `.xlsx`, not
# `.xlsm`.
CANONICAL_EXTENSIONS = {name: extension for extension, name in reversed(EXTENSIONS.items())}


class SpreadsheetFormats:
    """The formats this deployment can read and write, and how an upload picks one."""

    def __init__(
        self,
        readers: Mapping[str, SpreadsheetReader],
        writers: Mapping[str, SpreadsheetWriter],
        default: str = 'csv',
    ) -> None:
        """Bind the available adapters.

        Args:
            readers: Format name to reader, e.g. ``{'csv': ..., 'xlsx': ...}``.
            writers: Format name to writer, over the same names.
            default: The format to assume when an upload says nothing useful about itself.
                CSV, because it is the one a file of unknown provenance is most likely to be
                and the one whose failure message is clearest when it is not.

        Raises:
            ValueError: If a format has a reader but no writer or the other way round. A
                half-supported format is a template a registrar can download and not upload
                back, and the composition root is where that is cheap to notice.
        """
        if set(readers) != set(writers):
            raise ValueError(
                f'formats must have both a reader and a writer; got {sorted(readers)} and {sorted(writers)}'
            )
        if default not in readers:
            raise ValueError(f'the default format {default!r} has no adapter')

        self._readers = dict(readers)
        self._writers = dict(writers)
        self._default = default

    def reader_for(self, content_type: str = '', filename: str = '') -> SpreadsheetReader:
        """Pick the reader for an upload.

        The declared content type wins, and the filename's extension is the fallback, because
        a client that sends ``application/octet-stream`` has told us nothing while its user
        was quite clear when they named the file ``grades.xlsx``.

        Neither is trusted beyond this choice: a file that claims to be XLSX and is not gets a
        ``MalformedSpreadsheetError`` from the adapter, which is the same answer it would get
        for any other unreadable file.

        Returns:
            The reader for the chosen format, or the default reader if nothing identified it.
        """
        return self._readers[self._format_of(content_type, filename)]

    def extension_for(self, content_type: str = '', filename: str = '') -> str:
        """The canonical extension of the format :meth:`reader_for` would choose for an upload.

        Exists so a *queued* import reads back through the same adapter the inline path would
        have used. The job carries a storage key and no MIME type, so the key is given this
        extension when the payload is stored and the worker resolves the format from the key --
        which means the choice is made once, at submission, rather than guessed again later by a
        process holding less information.

        A payload nothing identified is stored under the *default* format's extension, so reading
        it back reaches the same reader that would have rejected it inline rather than a
        different one that might not.

        Returns:
            The extension, leading dot included, e.g. ``'.csv'``. Empty if this deployment wired
            a format under a name no extension maps to -- in which case the key carries no
            extension and the worker falls back to the default, exactly as an upload with no
            filename does.
        """
        return CANONICAL_EXTENSIONS.get(self._format_of(content_type, filename), '')

    def writer_for(self, file_format: str) -> SpreadsheetWriter:
        """Pick the writer for a requested template format.

        Raises:
            MalformedSpreadsheetError: If this deployment has no adapter for that format. The
                caller asked for a file this system cannot produce, and naming the formats it
                can is more use than a bare failure.
        """
        wanted = file_format.strip().lower().lstrip('.')
        if wanted not in self._writers:
            raise MalformedSpreadsheetError(f'no {wanted!r} writer; this system writes {sorted(self._writers)}')
        return self._writers[wanted]

    def _format_of(self, content_type: str, filename: str) -> str:
        """Resolve an upload to a format name, falling back to the default."""
        declared = CONTENT_TYPES.get(content_type.split(';')[0].strip().lower())
        if declared in self._readers:
            return declared

        suffix = EXTENSIONS.get(PurePosixPath(filename).suffix.lower())
        if suffix in self._readers:
            return suffix

        return self._default
