#!/usr/bin/env python3
"""agentStop hook: keep the where-were-we ledger from going stale.

The ledger only ever updated when someone remembered to update it, which is
the habit it exists because nobody keeps. Observed in the session that
prompted this: the ledger sat three weeks and twenty-five turns behind while
the work it described had moved on entirely.

So the reminder stops depending on recall. At the end of a turn this compares
what the ledger claims is folded in against what the session store actually
holds, and when the gap is wide enough it hands the agent a short instruction
to bring the record up to date.

Reads only. It never writes the ledger, because what belongs in `state` and
`next` is a judgement about the work and this process has no way to make one.
It asks the agent to write, and the agent has to have read the turns to do it.

Any failure prints an empty object and exits 0. A hook that cannot decide must
let the turn end normally; a stale ledger is a nuisance, a wedged session is
not.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

# Turns the ledger may fall behind before the reminder fires. Low enough that
# a drift is caught while the turns are still reconstructable, high enough
# that ordinary back-and-forth never triggers it.
MAX_DRIFT = int(os.environ.get("COPILOT_LEDGER_MAX_DRIFT", "10"))

REMINDER = """The session ledger is {gap} turns behind. Bring it up to date
before stopping.

Record the current state and the next step, and record any commitment
that was made and deferred as a separate todo, so it survives this
session:

  S=~/.copilot/skills/where-were-we/scripts
  python3 $S/wwm.py record --kind state --text "..."
  python3 $S/wwm.py record --kind next  --text "..."
  python3 $S/wwm.py record --kind todo  --text "..."

Recording state or next is what marks the ledger current, so do those
two even if there is nothing to defer. Then answer normally. Keep this
brief and do not ask Ali to confirm it; it is bookkeeping, not a
decision."""


def emit(payload: dict | None = None) -> None:
    """Print hook output and exit. No payload means let the turn end."""
    print(json.dumps(payload or {}))
    sys.exit(0)


def read_payload() -> dict:
    try:
        return json.loads(sys.stdin.read() or "{}")
    except (json.JSONDecodeError, ValueError):
        return {}


def ledger_path(home: Path, session_id: str) -> Path | None:
    """The ledger belonging to this session, if it has one.

    Deliberately this session only. The sessionStart card may borrow another
    session's ledger to orient from, but a reminder to update a record must
    never point at a record this session does not own.
    """
    if not session_id:
        return None
    path = home / "session-state" / session_id / "files" / "ledger.md"
    return path if path.is_file() else None


def last_synced(path: Path) -> int | None:
    """The turn the ledger claims to be current as of, or None if unreadable.

    Read by hand rather than through the skill's parser so the hook keeps
    working if the skill is absent, moved or mid-edit.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        if line.startswith("last_synced_turn:"):
            try:
                return int(line.split(":", 1)[1].strip())
            except ValueError:
                return None
        if line.startswith("## "):
            break
    return None


def max_turn(store: Path, session_id: str) -> int | None:
    """Highest committed turn index for this session, or None if unknown.

    Opened read-only by URI with a short timeout: the live session owns this
    database, and a hook must never be the reason a write to it blocks.
    """
    if not store.exists():
        return None
    try:
        con = sqlite3.connect(f"file:{store}?mode=ro", uri=True, timeout=2.0)
    except sqlite3.Error:
        return None
    try:
        row = con.execute(
            "SELECT MAX(turn_index) FROM turns WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    except sqlite3.Error:
        return None
    finally:
        con.close()
    return None if not row or row[0] is None else int(row[0])


def drift(home: Path, session_id: str) -> int | None:
    """Turns the ledger is behind, or None when that cannot be established.

    None and 0 are different answers and are kept apart on purpose. A missing
    ledger, an unreadable store or a damaged marker all mean the question has
    no answer, and a reminder fired on a guess would train the reader to
    ignore it.
    """
    path = ledger_path(home, session_id)
    if path is None:
        return None
    synced = last_synced(path)
    if synced is None:
        return None
    newest = max_turn(home / "session-store.db", session_id)
    if newest is None:
        return None
    return max(0, newest - synced)


def main() -> None:
    payload = read_payload()

    # Already blocked once for this stop. Blocking again would talk over the
    # agent's own attempt to comply, and the runtime's cap is a backstop, not
    # a licence to lean on it.
    if payload.get("stopHookActive"):
        emit()

    home = Path(os.environ.get("COPILOT_HOME") or Path.home() / ".copilot")
    session_id = str(payload.get("sessionId") or payload.get("session_id") or "")

    gap = drift(home, session_id)
    if gap is None or gap < MAX_DRIFT:
        emit()

    emit({"decision": "block", "reason": REMINDER.format(gap=gap)})


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except BaseException:  # noqa: BLE001
        # Deliberately total, for the same reason as the sessionStart hook.
        # This runs at the end of every turn, so an escaping exception would
        # break every turn rather than one.
        print("{}")
        sys.exit(0)
