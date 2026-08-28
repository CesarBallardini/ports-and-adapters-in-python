# ADR-0008 — One spreadsheet port, CSV and XLSX adapters

- **Status** Accepted
- **Date** 2026-08-28

## Context

Bulk loading is the centrepiece use case (UC-36 to UC-43). Administrators have CSV exports from
other systems; teachers have `.xlsx` files from Excel. Both must reach the same import rules.

`bluedoter-tng` shows the shape: an `ExcelEngine` port of two methods, an openpyxl adapter that
normalises the library's exception grab-bag into a single `ValueError`, and the actual business
rules — header normalisation, deduplication, replace mode, size caps — living in the use case.

## Decision

Declare `SpreadsheetReader` and `SpreadsheetWriter` ports whose contract is deliberately narrow:

- `read_rows(data: bytes) -> list[dict[str, str]]`
- `write_sheet(headers, rows) -> bytes`

Two adapters implement each: stdlib `csv`, and `openpyxl`. Adapters do **nothing** but convert
between bytes and rows of strings, and normalise every parse failure into one `ValueError`.
Every import rule lives in the use case.

Rows are `dict[str, str]`. The adapter does not coerce types: deciding that "8" is a valid grade
and "eight" is not is a domain rule, and a CSV adapter that guessed types would guess
differently from an XLSX adapter that gets them from the file.

## Consequences

- Supporting a new format is one new adapter and zero changes to any rule.
- The claim is *verified*, not asserted: the acceptance suite runs the same Gherkin scenarios
  against both adapters and requires identical outcomes. A rule leaking into an adapter makes
  the two runs diverge, and the suite reports it.
- Everything is held in memory as `bytes`, which is why the size cap and the job queue
  (ADR-0009) exist.
- Returning strings costs a parsing step in the use case that a type-aware adapter would have
  saved. That cost buys the format-independence, and it is the right trade here.

## Alternatives considered

- **CSV only.** One adapter, no dependency, and it discards the clearest demonstration of what a
  port buys, at exactly the place it lands best.
- **XLSX only.** Matches the reference application most closely, but leaves the port with a
  single implementation — which means it has never been tested as an abstraction.
- **A streaming, row-iterator port.** Handles arbitrarily large files, and complicates the
  contract, the dry-run rollback, and every test, for file sizes this application will not see.
