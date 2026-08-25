# skills/where-were-we/scripts/wwm_history.py
"""Bounded, read-only reads of the Copilot session store.

Every query is parameterized. The connection is read-only by URI so a bug here
cannot corrupt the user's session history.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

import wwm_session

BUDGET_TOTAL = 6000
BUDGET_ORIGIN = 1000
BUDGET_CHECKPOINT = 2500
BUDGET_CP_OVERVIEW = 1000
BUDGET_CP_NEXT = 1500
BUDGET_RECENT = 2500
# Recorded ledger content gets its own ceiling rather than a share of the
# history budget. It is the highest-value material in the bundle, being the
# only part the user wrote deliberately, so it is not made to compete with
# inferred history for room. The three history slices above already sum to
# BUDGET_TOTAL, so this is the second and last unbounded surface closed.
BUDGET_LEDGER = BUDGET_TOTAL
RECENT_COUNT = 12
RECENT_PER_TURN = 250
ORIGIN_TURNS = 2
SESSION_WINDOW_DAYS = 14
SESSION_LIMIT = 10


class StoreUnavailable(Exception):
    """Raised when the session store is missing or unreadable."""


def connect() -> sqlite3.Connection:
    """Open the session store read-only.

    Returns:
        An open read-only connection with a row factory set.

    Raises:
        StoreUnavailable: If the store is missing or cannot be opened.
    """
    path = wwm_session.store_path()
    if not path.exists():
        raise StoreUnavailable(f"No session store at {path}")
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error as err:
        raise StoreUnavailable(str(err)) from err
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def _open() -> Iterator[sqlite3.Connection]:
    """Yield a read-only connection, turning any sqlite failure into
    StoreUnavailable.

    Wrapping the whole block rather than just the connect is the point.
    sqlite only reports a truncated, corrupt or foreign database when a
    statement actually runs, so guarding connect() alone let `no such table:
    turns` escape as a raw traceback. That took the skill down at exactly
    the moment the ledger was the only surviving record of intent, which is
    the failure this whole design exists to prevent. Callers degrade on
    StoreUnavailable, so every sqlite error must arrive wearing that type.
    """
    conn = connect()
    try:
        yield conn
    except sqlite3.Error as err:
        raise StoreUnavailable(str(err)) from err
    finally:
        conn.close()


def _cap(text: str | None, limit: int) -> str:
    """Collapse whitespace and truncate to `limit`, ellipsis included in it."""
    text = " ".join((text or "").split())
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    return text[: limit - 3].rstrip() + "..."


def _turn_text(row: sqlite3.Row, cap: int | None) -> str:
    """Both halves of the exchange, not just the user's half.

    A turn is an instruction AND what the assistant actually did with it.
    Keeping only the user message made the fallback blind to every conclusion,
    command, and correction that came from the assistant side, and broke
    dedup for events that only ever appeared in a reply.
    """
    user = " ".join((row["user_message"] or "").split())
    reply = " ".join((row["assistant_response"] or "").split())
    if user and reply:
        text = f"{user} -> {reply}"
    else:
        text = user or reply
    return _cap(text, cap) if cap is not None else text


def _collect(rows: list[sqlite3.Row], budget: int, per_turn: int | None) -> list[dict]:
    """Fill one reserved slice. Stops at the slice cap, never borrows."""
    out: list[dict] = []
    spent = 0
    for row in rows:
        text = _turn_text(row, per_turn)
        if not text:
            continue
        if spent + len(text) > budget:
            break
        out.append({"turn_index": row["turn_index"], "text": text})
        spent += len(text)
    return out


def max_turn_index(session_id: str) -> int:
    """Return the highest turn index for a session, or 0 if it has no turns."""
    with _open() as conn:
        row = conn.execute(
            "SELECT MAX(turn_index) AS m FROM turns WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    return row["m"] or 0


def session_exists(session_id: str) -> bool:
    """Existence is a separate question from turn count.

    `turn_index` is 0-based in the real store (verified: MIN(turn_index) = 0),
    so a session with exactly one turn has max index 0, which is
    indistinguishable from "no turns at all". Testing existence via
    `max_turn_index(...) == 0` therefore rejects real single-turn sessions as
    unknown. A real example exists in the store today.

    Both tables are consulted. 29 sessions in the real store have a row in
    `sessions` and no rows in `turns`, so asking `turns` alone reported
    "not in the store" about ids that are demonstrably in it, and made a
    freshly created session impossible to target with --session before its
    first turn was committed.
    """
    with _open() as conn:
        row = conn.execute(
            "SELECT EXISTS(SELECT 1 FROM turns WHERE session_id = ?)"
            " OR EXISTS(SELECT 1 FROM sessions WHERE id = ?)",
            (session_id, session_id),
        ).fetchone()
    return bool(row[0])


def earliest_turns(session_id: str, budget: int = BUDGET_ORIGIN) -> list[dict]:
    """Return the opening turns of a session, bounded by `budget`."""
    with _open() as conn:
        rows = conn.execute(
            "SELECT turn_index, user_message, assistant_response FROM turns"
            " WHERE session_id = ? ORDER BY turn_index ASC LIMIT ?",
            (session_id, ORIGIN_TURNS),
        ).fetchall()
    return _collect(rows, budget, per_turn=budget // max(1, ORIGIN_TURNS))


def recent_turns(
    session_id: str,
    budget: int = BUDGET_RECENT,
    count: int = RECENT_COUNT,
    per_turn: int = RECENT_PER_TURN,
) -> list[dict]:
    """Return the newest turns of a session, oldest-first, bounded by `budget`."""
    with _open() as conn:
        rows = conn.execute(
            "SELECT turn_index, user_message, assistant_response FROM turns"
            " WHERE session_id = ? ORDER BY turn_index DESC LIMIT ?",
            (session_id, count),
        ).fetchall()
    return list(reversed(_collect(rows, budget, per_turn)))


def turns_after(
    session_id: str,
    after: int,
    budget: int = BUDGET_RECENT,
    per_turn: int = RECENT_PER_TURN,
) -> list[dict]:
    """Return turns strictly newer than `after`, oldest-first."""
    with _open() as conn:
        rows = conn.execute(
            "SELECT turn_index, user_message, assistant_response FROM turns"
            " WHERE session_id = ? AND turn_index > ? ORDER BY turn_index ASC",
            (session_id, after),
        ).fetchall()
    return _collect(rows, budget, per_turn)


def latest_checkpoint(session_id: str, budget: int = BUDGET_CHECKPOINT) -> dict | None:
    """Return the newest checkpoint, capped by a JOINT budget.

    `overview` and `next_steps` share one allocation. Applying the budget to
    each field independently lets this slice spend double its reservation:
    measured against the real store, 462 of 581 checkpoints exceed 1500 chars
    combined and the largest reaches 5256, which would consume almost the whole
    6000-char budget from a single row.

    `spent` is returned so the caller can hand any unspent allowance to the
    recent-turns slice. 44% of sessions have no checkpoint at all, and for those
    the recent turns are the only signal available.
    """
    with _open() as conn:
        row = conn.execute(
            "SELECT title, overview, next_steps, important_files FROM checkpoints"
            " WHERE session_id = ? ORDER BY checkpoint_number DESC LIMIT 1",
            (session_id,),
        ).fetchone()
    if row is None:
        return None
    overview = _cap(row["overview"], min(BUDGET_CP_OVERVIEW, budget))
    next_steps = _cap(row["next_steps"], max(0, budget - len(overview)))
    return {
        "title": row["title"],
        "overview": overview,
        "next_steps": next_steps,
        "important_files": row["important_files"] or "",
        "spent": len(overview) + len(next_steps),
    }


def session_files(session_id: str, limit: int = 8) -> list[str]:
    """Return the most recently touched file paths for a session."""
    with _open() as conn:
        rows = conn.execute(
            "SELECT DISTINCT file_path FROM session_files WHERE session_id = ?"
            " ORDER BY turn_index DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
    return [r["file_path"] for r in rows]


def recent_sessions(
    days: int = SESSION_WINDOW_DAYS,
    limit: int = SESSION_LIMIT,
    now: str | None = None,
) -> list[dict]:
    """Return sessions updated within the last `days`, newest first."""
    anchor = f"{now} 00:00:00" if now else "now"
    with _open() as conn:
        rows = conn.execute(
            "SELECT id, repository, cwd, branch, summary, updated_at FROM sessions"
            " WHERE updated_at >= datetime(?, ?) ORDER BY updated_at DESC LIMIT ?",
            (anchor, f"-{int(days)} days", limit),
        ).fetchall()
    return [dict(r) for r in rows]


def count_sessions_in_window(
    days: int = SESSION_WINDOW_DAYS, now: str | None = None
) -> int:
    """Return how many sessions were updated within the last `days`."""
    anchor = f"{now} 00:00:00" if now else "now"
    with _open() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM sessions WHERE updated_at >= datetime(?, ?)",
            (anchor, f"-{int(days)} days"),
        ).fetchone()
    return row["c"]
