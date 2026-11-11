"""OS file lock, released by the kernel even after process termination."""

from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path
import sys


@contextmanager
def exclusive_database(path: Path) -> Iterator[None]:
    """Acquire before recovery, so a second tracker cannot close live sessions."""
    with path.with_suffix(path.suffix + ".lock").open("a+b") as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise RuntimeError("Another Tocktail instance is using this database") from error
        try:
            yield
        finally:
            handle.seek(0)
            if sys.platform == "win32":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)
