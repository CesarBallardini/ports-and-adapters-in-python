"""Adapters for the two ambient facts the domain must not read for itself: time and identity."""

from academy.adapters.outbound.system.clock import FixedClock, SystemClock

__all__ = ['FixedClock', 'SystemClock']
