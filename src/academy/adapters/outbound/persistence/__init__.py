"""Persistence adapters.

Two backends behind the same repository ports: ``memory`` and ``sqlalchemy``. Neither is a
test double -- the in-memory one is a production-grade adapter that happens to forget
everything when the process ends (ADR-0014), and both are held to the same contract suite.
"""
