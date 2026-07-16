"""Shared auth/path security helpers."""

import os
import stat
import tempfile
from pathlib import Path


class SecurityError(Exception):
    """Raised when a security check fails."""


def validate_no_symlink(path: Path) -> None:
    """Refuse to operate on symlinks anywhere in the path chain."""
    resolved = path.resolve()
    expected_prefixes = (
        Path.home() / ".local" / "share" / "tuneshift",
        Path.home() / ".local" / "share" / "tidal-importer",
        Path.home() / "dotfiles" / "tools" / "tuneshift",
    )
    if not any(str(resolved).startswith(str(p)) for p in expected_prefixes):
        raise SecurityError(f"Path resolves outside expected directories: {resolved}")
    if path.exists() and path.is_symlink():
        raise SecurityError(f"Refusing to use symlinked path: {path}")
    for parent in path.parents:
        if parent == Path.home():
            break
        if parent.exists() and parent.is_symlink():
            raise SecurityError(f"Refusing to use symlinked ancestor: {parent}")


def secure_write(path: Path, content: str) -> None:
    """Write content atomically with restrictive permissions (0600).

    Uses temp file + rename to prevent TOCTOU races and partial writes.
    """
    validate_no_symlink(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, stat.S_IRWXU)

    fd = None
    tmp_path = None
    try:
        fd = tempfile.mkstemp(dir=path.parent, prefix=".tmp_token_")
        tmp_path = Path(fd[1])
        os.fchmod(fd[0], stat.S_IRUSR | stat.S_IWUSR)
        os.write(fd[0], content.encode("utf-8"))
        os.fsync(fd[0])
        os.close(fd[0])
        fd = None
        os.replace(tmp_path, path)
    except OSError:
        if fd is not None:
            os.close(fd[0])
        if tmp_path and tmp_path.exists():
            tmp_path.unlink()
        raise
