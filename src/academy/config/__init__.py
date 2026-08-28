"""The composition root: settings, and the wiring of adapters into ports.

This is the only module in the codebase permitted to know both a port and the concrete
adapter that satisfies it. Every choice the rest of the application is deliberately
ignorant of -- SQLite or PostgreSQL, CSV or XLSX, local disk or S3, inline or queued --
is made exactly once, here.

It therefore depends on every layer, which is why it sits outside the layers contract in
``.importlinter`` rather than being exempted from it.
"""
