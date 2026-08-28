"""The application layer: use cases, and the ports they are written against.

This layer says *what* the application does. It never says how anything is stored,
presented or delivered -- those live behind the ports in :mod:`academy.application.ports`
and are supplied by adapters at composition time.

Depends on :mod:`academy.domain` and on nothing else (ADR-0003).
"""
