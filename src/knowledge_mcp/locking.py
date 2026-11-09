"""A process-shared write lock for one local vault (Unix and Windows)."""

from contextlib import contextmanager
import errno
import os
from pathlib import Path
import stat
import time

if os.name == "nt":
    import msvcrt
else:
    import fcntl


class WriteLockTimeout(TimeoutError):
    pass


@contextmanager
def vault_write_lock(root: Path, *, timeout: float = 10.0):
    # Never unlink this file: another process may already have it open, and
    # replacing its inode would allow two independent locks for the same vault.
    path = root / ".knowledge-mcp.lock"
    if path.is_symlink():
        raise ValueError("Vault lock must be a regular file")
    descriptor = os.open(
        path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600
    )
    acquired = False
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("Vault lock must be a regular file")
        deadline = time.monotonic() + timeout
        while True:
            try:
                if os.name == "nt":
                    # Windows permits locking a byte beyond EOF; no content
                    # write (and therefore no initialization race) is needed.
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                    raise
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise WriteLockTimeout("Vault is busy; retry the write after the other operation finishes") from None
                time.sleep(min(0.05, remaining))
        yield
    finally:
        try:
            if acquired:
                if os.name == "nt":
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)
