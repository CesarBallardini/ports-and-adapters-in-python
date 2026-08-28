"""Driving adapters: the ways into the application.

Each translates one protocol -- HTTP form posts, JSON, argv, a queue message -- into a
call on a driving port, and translates domain errors back out through the single
status table in :mod:`academy.adapters.inbound.error_status` (ADR-0012).

None of them holds business logic. If a rule appears here, it is in the wrong layer.
"""
