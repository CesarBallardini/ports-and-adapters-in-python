"""Job queue adapters: hand the work to somebody else, or do it yourself.

Two implementations of a one-method port, and they are further apart than any other pair here:
one defers the work to a worker process, the other does it before returning. What they share
is all the port promises -- an id goes in, and the job runs -- which is exactly the point. A
single-process deployment runs the inline queue and needs no worker at all; nothing above the
port can tell.
"""

from academy.adapters.outbound.queue.inline import InlineJobQueue
from academy.adapters.outbound.queue.memory import MemoryJobQueue

__all__ = ['InlineJobQueue', 'MemoryJobQueue']
