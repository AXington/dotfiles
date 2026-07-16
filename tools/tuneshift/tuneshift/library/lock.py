"""Single-flight advisory lock for `resolve` (BUG-2 / FEAT-2).

Concurrent `resolve --all` writers against one SQLite DB cause SQLITE_BUSY and
lost writes. This PID-based lock lets exactly one resolve run at a time: a live
holder makes a new run refuse; a stale holder (dead PID) is reclaimed.
"""

from __future__ import annotations

import os
from pathlib import Path
from types import TracebackType


class ResolveLockHeld(RuntimeError):
    """Another live resolve process holds the lock."""


def _pid_is_live(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by another user
    return True


class ResolveLock:
    """Acquire an exclusive resolve lock next to the database file."""

    def __init__(self, db_path: str | os.PathLike[str]) -> None:
        self._lock_path = Path(db_path).resolve().parent / ".tuneshift" / "resolve.lock"

    def acquire(self) -> None:
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        if self._lock_path.exists():
            raw = self._lock_path.read_text(encoding="utf-8").strip()
            try:
                holder = int(raw)
            except ValueError:
                holder = -1
            if _pid_is_live(holder) and holder != os.getpid():
                raise ResolveLockHeld(
                    f"resolve already running (pid {holder}); lock: {self._lock_path}"
                )
        self._lock_path.write_text(str(os.getpid()), encoding="utf-8")

    def release(self) -> None:
        try:
            if self._lock_path.exists():
                holder = self._lock_path.read_text(encoding="utf-8").strip()
                if holder == str(os.getpid()):
                    self._lock_path.unlink()
        except OSError:
            pass

    def __enter__(self) -> ResolveLock:
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()
