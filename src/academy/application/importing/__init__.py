"""Bulk import: the feature this repository exists to demonstrate.

Three things meet here, and each is a claim the repository makes:

* **The rules live above the port.** A spreadsheet adapter turns bytes into rows of strings and
  does nothing else; header normalisation, validation, duplicate detection and partial success
  are in :mod:`~academy.application.importing.rows` and in the importer for each kind. Swapping
  CSV for XLSX therefore cannot change an outcome — which is exactly what the acceptance suite
  checks by running the same scenarios through both.
* **A use case does not know who called it.** The same importer serves an htmx upload, a JSON
  API call, a CLI command and a background worker.
* **Plumbing and rules are separable.** :class:`~academy.application.importing.service.ImportService`
  owns the transaction, the dry-run rollback and the choice of adapter; a
  :class:`~academy.application.importing.rows.RowImporter` owns what a row means. Neither
  mentions the other's concerns.
"""

from academy.application.importing.formats import SpreadsheetFormats
from academy.application.importing.grade_sheet import GradeSheetImporter
from academy.application.importing.rows import RowImporter, Template, normalise, numbered
from academy.application.importing.service import ImportService

__all__ = [
    'GradeSheetImporter',
    'ImportService',
    'RowImporter',
    'SpreadsheetFormats',
    'Template',
    'normalise',
    'numbered',
]
