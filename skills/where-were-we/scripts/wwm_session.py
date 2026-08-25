# skills/where-were-we/scripts/wwm_session.py
"""Session location and runtime gating for the where-were-we skill."""

from __future__ import annotations

import os
import re
from pathlib import Path

ENV_SESSION = "COPILOT_AGENT_SESSION_ID"
# Session ids are opaque handles that land in a filesystem path. Anything that
# can traverse or escape is rejected outright rather than sanitized, because a
# "cleaned" id would silently read or write a different session's ledger, and
# reading the wrong session is exactly the confidently-wrong answer this skill
# exists to prevent. Must start alphanumeric so `..` and `.hidden` cannot match.
SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class SessionUnknown(Exception):
    """Raised when no session ID is available from any source."""


class InvalidSessionId(Exception):
    """Raised when a session ID could address a path outside session-state."""


class UnsupportedRuntime(Exception):
    """Raised when invoked outside Copilot CLI."""


def copilot_home() -> Path:
    return Path(os.environ["HOME"]) / ".copilot"


def store_path() -> Path:
    return copilot_home() / "session-store.db"


def resolve_session_id(explicit: str | None = None) -> str:
    """Return the session ID to operate on.

    An explicit --session argument wins. The environment variable is the
    default. Nothing is guessed: if neither is available we refuse, because
    guessing which session the user means is the one failure that produces a
    confidently wrong summary.
    """
    if explicit:
        return explicit
    from_env = os.environ.get(ENV_SESSION)
    if from_env:
        return from_env
    raise SessionUnknown(
        "No session ID available. Pass --session <id>, or run inside a "
        f"Copilot CLI session where {ENV_SESSION} is set."
    )


def session_dir(session_id: str) -> Path:
    """Return the session-state directory for `session_id`, or refuse.

    Two independent checks, because either alone has been enough to fail
    elsewhere: the pattern rejects anything that could traverse, and the
    resolved path is then re-confirmed to sit under session-state so a symlink
    or an unusual platform rule cannot get past the pattern.

    Raises:
        InvalidSessionId: If the id is malformed or escapes session-state.
    """
    if not SESSION_ID.match(session_id or ""):
        raise InvalidSessionId(
            f"Refusing to use session id {session_id!r}: session ids may "
            "contain only letters, digits, dot, dash and underscore, and must "
            "start with a letter or digit."
        )
    root = copilot_home() / "session-state"
    path = root / session_id
    if root.resolve() not in path.resolve().parents:
        raise InvalidSessionId(
            f"Refusing to use session id {session_id!r}: it resolves outside {root}."
        )
    return path


def ledger_path(session_id: str) -> Path:
    return session_dir(session_id) / "files" / "ledger.md"


def is_copilot_runtime() -> bool:
    """True when this looks like a Copilot CLI session.

    Deliberately NOT `store_path().exists()`. The store being absent and the
    runtime being foreign are different failures with different correct
    responses:

      - foreign runtime  -> unsupported, refuse
      - store missing    -> degrade to ledger-only, say the history is missing

    Conflating them threw away every recorded fact precisely when the ledger was
    the only surviving source. Runtime is judged by the session-state layout and
    the environment variable, not by the database file.
    """
    return (
        bool(os.environ.get(ENV_SESSION)) or (copilot_home() / "session-state").is_dir()
    )


def has_store() -> bool:
    """Whether history is readable. Independent of runtime support."""
    return store_path().exists()
