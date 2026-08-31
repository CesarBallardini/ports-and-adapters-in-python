"""File storage adapters: bytes in memory, and bytes on a disk.

Two implementations of the port ADR-0005 calls the clearest case of an adapter: they differ in
every operational respect -- durability, capacity, what happens when the process dies -- and in
nothing the application can observe. The contract suite runs the same assertions against both,
which is what that claim has to mean to be worth making.

The S3 adapter belongs in this package too and is not written; ``boto3`` is in no extra yet.
"""

from academy.adapters.outbound.storage.local import LocalFileStorage
from academy.adapters.outbound.storage.memory import MemoryFileStorage

__all__ = ['LocalFileStorage', 'MemoryFileStorage']
