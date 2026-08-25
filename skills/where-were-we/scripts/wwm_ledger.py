# skills/where-were-we/scripts/wwm_ledger.py
"""Ledger schema, parsing, and serialization.

The model never writes this file. All writes go through record() and adopt(),
which own every metadata field including last_synced_turn.
"""

from __future__ import annotations

import fcntl
import os
import re
import sqlite3
import tempfile
from contextlib import closing, contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path

import wwm_session

META_KEYS = ("goal", "started", "updated", "last_synced_turn", "adopted_from")
KINDS = ("decision", "thread", "blocker", "state", "next", "goal")
KNOWN_SECTIONS = (
    "state",
    "next",
    "decisions",
    "threads",
    "blockers",
    "goal history",
)
HEADING = re.compile(r"^##\s+(.+?)\s*$")
DECISION = re.compile(r"^-\s+(\d{4}-\d{2}-\d{2})\s+(.*)$")
THREAD = re.compile(r"^-\s+\[( |x)\]\s+(.*)$")
CONTINUATION = re.compile(r"^\s+(why|rejected):\s*(.*)$")


class LedgerRefused(Exception):
    """Raised when writing would destroy content instead of preserving it."""


@dataclass(frozen=True)
class Decision:
    date: str
    text: str
    why: str = ""
    rejected: str = ""


@dataclass(frozen=True)
class Thread:
    done: bool
    text: str


@dataclass(frozen=True)
class Ledger:
    goal: str | None = None
    # A long session changes what it is about, so replacing the goal is
    # intended, not data loss. What would be data loss is forgetting the goals
    # it replaced, because those are the record of how the work arrived here.
    # Oldest first; each entry is dated the day it stopped being the goal.
    goal_history: list[Decision] = field(default_factory=list)
    started: str | None = None
    updated: str | None = None
    # -1, not 0, means nothing is synced. Turn indexes are 0-based and
    # reconciliation asks for turn_index > this value, so 0 claims turn 0
    # is already accounted for and silently hides the session's first turn.
    last_synced_turn: int = -1
    adopted_from: str | None = None
    state: str | None = None
    next: str | None = None
    decisions: list[Decision] = field(default_factory=list)
    threads: list[Thread] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    # Names of sections that failed to parse. The spec requires that a damaged
    # ledger be used for what survives AND that the loss be stated. Dropping
    # sections silently turns corruption into an answer that looks whole.
    damaged: list[str] = field(default_factory=list)
    # Raw lines the parsers could not read, kept verbatim so that recording a
    # new milestone rewrites the file without destroying them. Reporting a
    # section as damaged and then serializing over it was the same data loss
    # with a warning label attached.
    unparsed: dict[str, list[str]] = field(default_factory=dict)
    # Lines under headings this parser does not know, keyed by heading. A
    # hand-written "## scratchpad" is still the user's words.
    unknown_sections: dict[str, list[str]] = field(default_factory=dict)
    # True when the file exists but could not be read at all (bad bytes, bad
    # permissions). Writes are refused in that state: content that cannot be
    # read cannot be preserved, and overwriting it would destroy it.
    unreadable: bool = False


def _split_sections(
    text: str,
) -> tuple[list[str], dict[str, list[str]], dict[str, list[str]]]:
    """Return preamble lines, known sections, and unknown sections verbatim.

    Headings are matched loosely and compared case-insensitively, so a
    hand-edited `## Decisions` still lands in the decisions section. A heading
    that is genuinely not understood keeps its lines rather than discarding
    them: a hand-written `## scratchpad` is still the user's words, and the
    caller both discloses it and writes it back untouched.
    """
    preamble: list[str] = []
    sections: dict[str, list[str]] = {}
    unknown: dict[str, list[str]] = {}
    current: str | None = None
    orphan: str | None = None
    for line in text.splitlines():
        match = HEADING.match(line)
        if match:
            name = match.group(1).strip().lower()
            if name in KNOWN_SECTIONS:
                current = name
                # Merge rather than reset. A duplicated heading used to drop
                # everything above it without a word.
                sections.setdefault(current, [])
                orphan = None
            else:
                current = None
                orphan = match.group(1).strip()
                unknown.setdefault(orphan, [])
            continue
        if orphan is not None:
            unknown[orphan].append(line.rstrip())
            continue
        if current is None:
            preamble.append(line)
        else:
            sections[current].append(line)
    # A heading with nothing under it lost nothing, so it is not a warning.
    # Trailing blanks are trimmed so that re-serializing an unknown section
    # cannot slowly accumulate empty lines across saves.
    trimmed = {k: _rstrip_blank(v) for k, v in unknown.items()}
    return preamble, sections, {k: v for k, v in trimmed.items() if v}


def _rstrip_blank(lines: list[str]) -> list[str]:
    """Drop trailing blank lines, which carry no content and can accumulate."""
    end = len(lines)
    while end and not lines[end - 1].strip():
        end -= 1
    return lines[:end]


def parse(text: str) -> Ledger:
    preamble, sections, unknown = _split_sections(text)

    meta: dict[str, str] = {}
    # A key: value line the parser does not recognise is almost always a typo
    # for one that it does ("gola:" for "goal:"). Skipping it quietly loses
    # whatever the user wrote, so an unrecognised key is disclosed by name and
    # they can see what to correct. Same rule as decisions, threads and
    # headings: nothing readable is discarded without saying so.
    bad_keys: list[str] = []
    stray: list[str] = []
    for line in preamble:
        stripped = line.strip()
        if not stripped or stripped.lower() == "# where-were-we":
            continue
        key, sep, value = line.partition(":")
        key = key.strip()
        if sep and key in META_KEYS:
            meta[key] = value.strip()
            continue
        # Preserved verbatim as well as disclosed. Naming the bad key told the
        # user what to correct and then the next record() deleted the line they
        # were meant to correct.
        stray.append(line.rstrip())
        if sep and key and " " not in key and not key.startswith("#"):
            bad_keys.append(key)

    try:
        synced = int(meta.get("last_synced_turn", "0"))
    except ValueError:
        synced = 0
        damaged = ["last_synced_turn"]
    else:
        damaged = []
    damaged += bad_keys

    parsed: dict[str, object] = {}
    unparsed: dict[str, list[str]] = {}
    if stray:
        unparsed["preamble"] = stray
    for name, parser in (
        ("decisions", _decisions),
        ("threads", _threads),
        ("blockers", _blockers),
        # Same `- YYYY-MM-DD text` shape as decisions, so it reuses the same
        # parser and inherits the same preserve-verbatim behaviour for free.
        ("goal history", _decisions),
    ):
        try:
            result, dropped = parser(sections.get(name, []))
        except Exception:  # noqa: BLE001 - see below
            # Deliberately blind. The ledger is the last surviving record of
            # intent when the store is gone, so no parse failure may take the
            # whole file down with it. This is not a silent swallow: the
            # section name lands in `damaged` and the renderer tells the user
            # what it could not read.
            # Keep the whole section verbatim. A parser that blew up read
            # nothing, so everything under that heading is still unread text
            # that must survive the next write.
            parsed[name] = []
            unparsed[name] = [
                ln.rstrip() for ln in sections.get(name, []) if ln.strip()
            ]
            damaged.append(name)
        else:
            # A section can parse and still lose individual lines to a typo.
            # Dropping those silently is the same data loss wearing a
            # friendlier face, so a partial read is disclosed as damaged too.
            parsed[name] = result
            if dropped:
                unparsed[name] = dropped
                damaged.append(name)

    damaged.extend(unknown)

    return Ledger(
        goal=meta.get("goal"),
        goal_history=parsed["goal history"],
        started=meta.get("started"),
        updated=meta.get("updated"),
        last_synced_turn=max(-1, synced),
        adopted_from=meta.get("adopted_from"),
        state=_block(sections.get("state")),
        next=_block(sections.get("next")),
        decisions=parsed["decisions"],
        threads=parsed["threads"],
        blockers=parsed["blockers"],
        damaged=damaged,
        unparsed=unparsed,
        unknown_sections=unknown,
    )


def _block(lines: list[str] | None) -> str | None:
    if lines is None:
        return None
    text = "\n".join(lines).strip()
    return text or None


def _decisions(lines: list[str]) -> tuple[list[Decision], list[str]]:
    out: list[Decision] = []
    dropped: list[str] = []
    for line in lines:
        if not line.strip():
            continue
        match = DECISION.match(line)
        if match:
            out.append(Decision(date=match.group(1), text=match.group(2).strip()))
            continue
        cont = CONTINUATION.match(line)
        if cont and out:
            key, value = cont.group(1), cont.group(2).strip()
            out[-1] = replace(out[-1], **{key: value})
            continue
        dropped.append(line.rstrip())
    return out, dropped


def _threads(lines: list[str]) -> tuple[list[Thread], list[str]]:
    out: list[Thread] = []
    dropped: list[str] = []
    for line in lines:
        if not line.strip():
            continue
        match = THREAD.match(line)
        if match:
            out.append(Thread(done=match.group(1) == "x", text=match.group(2).strip()))
            continue
        dropped.append(line.rstrip())
    return out, dropped


def _blockers(lines: list[str]) -> tuple[list[str], list[str]]:
    out: list[str] = []
    dropped: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped == "(none)":
            continue
        if stripped.startswith("- "):
            out.append(stripped[2:].strip())
            continue
        dropped.append(line.rstrip())
    return out, dropped


def serialize(led: Ledger) -> str:
    parts = ["# where-were-we", ""]
    for key in META_KEYS:
        value = getattr(led, key)
        if value is None:
            continue
        parts.append(f"{key}: {value}")
    parts += led.unparsed.get("preamble", [])
    parts.append("")

    # Written high in the file, directly under the current goal, so that
    # reading the ledger by eye shows the arc of the session before its
    # details. Emitted only when it holds something: adding an empty heading
    # to every ledger would rewrite every existing file for no content.
    if led.goal_history or led.unparsed.get("goal history"):
        parts.append("## goal history")
        for past in led.goal_history:
            parts.append(f"- {past.date} {past.text}")
        parts += led.unparsed.get("goal history", [])
        parts.append("")

    if led.state:
        parts += ["## state", led.state, ""]
    if led.next:
        parts += ["## next", led.next, ""]

    parts.append("## decisions")
    for dec in led.decisions:
        parts.append(f"- {dec.date} {dec.text}")
        if dec.why:
            parts.append(f"  why: {dec.why}")
        if dec.rejected:
            parts.append(f"  rejected: {dec.rejected}")
    parts += led.unparsed.get("decisions", [])
    parts.append("")

    parts.append("## threads")
    for thread in led.threads:
        box = "x" if thread.done else " "
        parts.append(f"- [{box}] {thread.text}")
    parts += led.unparsed.get("threads", [])
    parts.append("")

    parts.append("## blockers")
    parts += [f"- {b}" for b in led.blockers] if led.blockers else ["(none)"]
    parts += led.unparsed.get("blockers", [])
    parts.append("")

    # Headings this parser does not understand are written back untouched.
    # Reporting a section as unreadable and then serializing over it destroyed
    # the very words the ledger exists to keep.
    for heading, body in led.unknown_sections.items():
        parts.append(f"## {heading}")
        parts += body
        parts.append("")
    return "\n".join(parts)


def load(session_id: str) -> Ledger:
    """Read and parse a session ledger, tolerating a missing or unreadable file.

    Args:
        session_id: The session whose ledger to read.

    Returns:
        The parsed ledger, an empty Ledger when no ledger exists, or a Ledger
        flagged `unreadable` when one exists but could not be read at all.

    A file that cannot be decoded or opened is NOT the same as no file. Both
    used to return a bare Ledger(), so a ledger full of milestones looked
    identical to a fresh session and the next record() overwrote it. The flag
    keeps that difference visible and makes writes refuse.
    """
    path = wwm_session.ledger_path(session_id)
    if not path.exists():
        return Ledger()
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return Ledger(unreadable=True, damaged=["whole file"])
    return parse(text)


def _max_turn_index(session_id: str) -> int:
    """Return the highest committed turn index, or -1 when none is readable.

    Read straight from the store so record() stamps last_synced_turn from
    reality rather than from any caller-supplied value. The connection is
    read-only by URI so a bug here cannot corrupt session history, and it is
    wrapped in contextlib.closing because a bare sqlite3.connect context manager
    commits without closing and would leak the handle.

    -1 rather than 0 on every failure path. Reconciliation selects
    turn_index > this value, so returning 0 for "no store" or "no turns"
    asserted that turn 0 was already folded in and silently hid the first turn
    of the session.
    """
    path = wwm_session.store_path()
    if not path.exists():
        return -1
    try:
        with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as conn:
            row = conn.execute(
                "SELECT MAX(turn_index) AS m FROM turns WHERE session_id = ?",
                (session_id,),
            ).fetchone()
    except sqlite3.Error:
        return -1
    if row is None or row[0] is None:
        return -1
    return int(row[0])


def _today() -> str:
    """Return today's date as an ISO (YYYY-MM-DD) string.

    Uses a timezone-aware UTC clock so the stamp is deterministic and
    unambiguous across machines and timezones.
    """
    return datetime.now(tz=timezone.utc).date().isoformat()


def _atomic_write(path: Path, text: str) -> None:
    """Write text to path atomically, never truncating an existing file.

    The content lands in a sibling temp file first and is moved into place with
    a single os.replace, so a crash mid-write leaves the previous ledger intact.

    The temp name is unique per call. A fixed `.tmp` suffix races when two
    processes record against the same session at once, which is routine here:
    a subagent and its parent both hold the same session id, so the first
    rename pulls the file out from under the second and it dies with
    FileNotFoundError.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


@contextmanager
def _locked(session_id: str):
    """Hold an exclusive interprocess lock for one session's ledger.

    record() is a read-modify-write: it loads the whole ledger, appends one
    item, and replaces the file. Two of those interleaving means the second
    write is computed from a snapshot taken before the first, so the first
    milestone is silently lost. That is not a rare race here: a subagent and
    its parent share a session id, so concurrent recording is the normal case.

    The lock is a sibling file rather than the ledger itself, because the
    ledger is replaced by rename and a lock held on the old inode would
    protect nothing after the first write.
    """
    path = wwm_session.ledger_path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_name(path.name + ".lock")
    with open(lock, "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _single_line(value: str, field_name: str) -> str:
    """Reject text carrying newlines rather than silently truncating it.

    Every milestone is serialized as one line. A newline in the middle of one
    used to end the line early, so the remainder was written back as an
    unparsable stray and everything after the first line vanished from the
    rendered answer. Refusing is the only honest option: the alternative is
    accepting the user's words and then not keeping them.

    Raises:
        LedgerRefused: If the text spans more than one line.
    """
    text = str(value)
    if "\n" in text or "\r" in text:
        raise LedgerRefused(
            f"The {field_name} spans multiple lines, and a ledger entry is one "
            "line. Nothing was recorded. Re-run with the newlines removed, or "
            "record each line as its own entry."
        )
    return text


def record(
    session_id: str,
    kind: str,
    text: str,
    why: str = "",
    rejected: str = "",
    **ignored: object,
) -> Ledger:
    """Append one milestone to the ledger and re-stamp sync metadata.

    Args:
        session_id: The session whose ledger to update.
        kind: One of decision, thread, blocker, state, next, or goal.
        text: The milestone content.
        why: Rationale, recorded only for decisions.
        rejected: Rejected alternatives, recorded only for decisions.
        **ignored: Absorbs any extra keyword arguments so a caller passing
            last_synced_turn cannot set it. The value is read from the store,
            never accepted, because the model has no reliable way to know its
            own turn index and a value that runs ahead of reality would suppress
            real turns.

    Returns:
        The updated ledger that was written to disk.

    Raises:
        ValueError: If kind is not a recognized milestone type.
        LedgerRefused: If the existing ledger could not be read at all.
    """
    if kind not in KINDS:
        raise ValueError(f"unknown kind: {kind}")
    # Validated before the lock is taken: a refusal must not make a concurrent
    # writer wait, and nothing here depends on the ledger's contents.
    text = _single_line(text, "text")
    why = _single_line(why, "why")
    rejected = _single_line(rejected, "rejected")

    # The load and the write are one critical section. Two record() calls
    # interleaving meant the second computed its result from a snapshot taken
    # before the first, so the first milestone was silently lost.
    with _locked(session_id):
        led = load(session_id)
        # Content that cannot be read cannot be preserved. Everywhere else a
        # damaged ledger is kept verbatim and written back, but a file we could
        # not decode has no recoverable form, so writing would simply destroy
        # it.
        if led.unreadable:
            raise LedgerRefused(
                f"The ledger for {session_id} exists but could not be read, so "
                "recording would overwrite it. Move or repair the file first: "
                f"{wwm_session.ledger_path(session_id)}"
            )
        today = _today()

        if kind == "decision":
            led = replace(
                led,
                decisions=[*led.decisions, Decision(today, text, why, rejected)],
            )
        elif kind == "thread":
            led = replace(led, threads=[*led.threads, Thread(False, text)])
        elif kind == "blocker":
            led = replace(led, blockers=[*led.blockers, text])
        elif kind == "state":
            led = replace(led, state=text)
        elif kind == "next":
            led = replace(led, next=text)
        else:
            # Replacing the goal is correct: a session's purpose genuinely
            # changes. Forgetting the goal it replaced is not, because months
            # later that is the only thing that explains why the work looks the
            # way it does. So the old goal moves into history automatically
            # rather than requiring the user to remember to write it down,
            # which is precisely the memory this skill exists to not depend on.
            history = led.goal_history
            if led.goal and led.goal != text:
                history = [*history, Decision(today, led.goal)]
            led = replace(led, goal=text, goal_history=history)

        led = replace(
            led,
            started=led.started or today,
            updated=today,
            last_synced_turn=_max_turn_index(session_id),
        )
        _atomic_write(wwm_session.ledger_path(session_id), serialize(led))
    return led


def adopt(source: str, target: str) -> Ledger:
    """Carry a stranded ledger into the current session.

    Content transfers. Sync state does not: turn_index is per-session, so an
    inherited last_synced_turn is meaningless here and, if it ran high, would
    mark the new ledger current and hide every real turn in it.

    Args:
        source: The session whose ledger content is being carried over.
        target: The current session that adopts the content.

    Returns:
        The adopted ledger that was written to the target session.

    Raises:
        LedgerRefused: If the source has nothing to carry over, or if the
            target already holds content that adoption would erase.
    """
    src = load(source)
    if src.unreadable:
        raise LedgerRefused(f"The ledger for {source} could not be read.")
    if not _has_content(src):
        # A typo in the source id used to load an empty ledger, write it over
        # the target, and report success, erasing the target's own record.
        raise LedgerRefused(
            f"No ledger content found for session '{source}'. Nothing was "
            "changed. Check the session id with `wwm.py sessions`."
        )
    # Same critical section as record(): the target is read to decide whether
    # adoption is safe, then written. Without the lock a record() landing
    # between those two steps is overwritten and lost.
    with _locked(target):
        existing = load(target)
        if _has_content(existing) or existing.unreadable:
            raise LedgerRefused(
                f"Session {target} already has its own ledger. Adopting would "
                "replace it. Move or clear it first if that is really intended."
            )
        adopted = replace(
            src,
            adopted_from=source,
            # Turn indexes are 0-based and reconciliation asks for turn_index >
            # after, so 0 would silently exclude the target's own first turn.
            # -1 is the only value that means "nothing here is synced yet".
            last_synced_turn=-1,
            updated=_today(),
        )
        _atomic_write(wwm_session.ledger_path(target), serialize(adopted))
    return adopted


def _has_content(led: Ledger) -> bool:
    """True when a ledger holds anything worth preserving."""
    return bool(
        led.goal
        or led.goal_history
        or led.state
        or led.next
        or led.decisions
        or led.threads
        or led.blockers
        or led.unparsed
        or led.unknown_sections
    )
