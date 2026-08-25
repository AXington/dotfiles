# skills/where-were-we/scripts/wwm_render.py
"""Exact 72-character rendering.

Every visual guarantee lives here and nowhere else. The model contributes prose
but never formatting, because format consistency is what lets the eye find
`Next` without re-reading.
"""

from __future__ import annotations

import re
import textwrap
import unicodedata

TOTAL = 72
INDENT = 2
LABEL_W = 9
TEXT_COL = INDENT + LABEL_W + 1
TEXT_W = TOTAL - TEXT_COL
TAG_W = 5
TIGHT_W = TEXT_W - TAG_W - 1
MENU = "more? [decisions] [threads] [timeline] [files] [full]"


def display_width(text: str) -> int:
    """Columns a terminal will actually use to draw `text`.

    len() counts codepoints, not columns. CJK and emoji are drawn two columns
    wide, and combining marks are drawn in zero, so a "72-character" line of
    Japanese occupied 132 terminal columns and wrapped into an unreadable
    ragged block. The whole layout guarantee is about columns, so every width
    decision has to be measured in columns.
    """
    total = 0
    for char in text:
        if unicodedata.combining(char):
            continue
        total += 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
    return total


def _truncate_to_width(text: str, width: int) -> str:
    """Cut `text` down to at most `width` columns."""
    out: list[str] = []
    used = 0
    for char in text:
        step = 0 if unicodedata.combining(char) else display_width(char)
        if used + step > width:
            break
        out.append(char)
        used += step
    return "".join(out)


def _wrap(text: str, width: int) -> list[str]:
    wrapped = textwrap.wrap(
        text,
        width=width,
        break_long_words=True,
        break_on_hyphens=False,
    )
    # textwrap measures in codepoints, so a wide-character line it believes is
    # `width` long can be up to twice that on screen. Re-break anything that
    # overflows in real columns; ASCII is untouched, since for ASCII
    # display_width equals len and this loop never fires.
    out: list[str] = []
    for line in wrapped:
        while display_width(line) > width:
            head = _truncate_to_width(line, width)
            if not head:
                # A single character wider than the entire column. Emit it
                # whole rather than dropping it or looping forever; one
                # over-wide line beats a hang or a silent deletion.
                break
            out.append(head)
            line = line[len(head) :]
        out.append(line)
    return out or [""]


def _clean(text: str) -> str:
    """Strip markdown noise before wrapping.

    Checkpoint and turn text is written for a markdown renderer, so it arrives
    full of `**`, backticks, and bullet leaders. Inside a fixed-width label
    block none of that renders; it just costs characters and adds visual noise
    to something whose entire purpose is being easy to scan.
    """
    text = re.sub(r"`+", "", str(text))
    text = re.sub(r"\*{1,3}", "", text)
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*#{1,6}\s*", "", text, flags=re.MULTILINE)
    return " ".join(text.split())


def row(label: str, text: str, tag: str) -> list[str]:
    """Render one labeled row with its source tag right-aligned to column 72.

    Args:
        label: Short row label, truncated to the label column width.
        text: Body text. Markdown noise is stripped before wrapping.
        tag: Provenance marker, rendered as `[tag]` at the far right.

    Returns:
        Lines that are each at most TOTAL characters wide.
    """
    marker = f"[{tag}]"
    text = _clean(text)

    lines = _wrap(text, TEXT_W)
    if display_width(lines[-1]) + 1 + TAG_W > TEXT_W:
        lines = _wrap(text, TIGHT_W)

    out: list[str] = []
    head = f"{' ' * INDENT}{label[:LABEL_W].ljust(LABEL_W)} "
    for index, line in enumerate(lines):
        prefix = head if index == 0 else " " * TEXT_COL
        out.append(prefix + line)

    last = out[-1]
    # Padded by columns, not codepoints. ljust() counts codepoints, so a line
    # of wide characters was padded as though it were half its drawn width and
    # pushed the tag past column 72.
    if display_width(last) + 1 + TAG_W <= TOTAL:
        out[-1] = last + " " * (TOTAL - TAG_W - display_width(last)) + marker
    else:  # pragma: no cover - unreachable with the current widths
        # Structurally dead today: TIGHT_W is TOTAL - TEXT_COL - TAG_W - 1, so
        # the re-wrap above always leaves room for the tag. Kept because the
        # "no line exceeds TOTAL" invariant would otherwise depend silently on
        # that arithmetic holding; if someone retunes the widths this catches
        # it instead of shipping a ragged table. Verified unreachable across
        # 60,000 randomized inputs rather than assumed.
        out.append(" " * (TOTAL - TAG_W) + marker)
    return out


LEVELS = ("tldr", "summary", "full")
SECTIONS = {
    "decisions": ("Decided",),
    "threads": ("Thread",),
    "timeline": ("Discussed", "Direction", "Started"),
    "files": ("Files",),
    "blockers": ("Blocked",),
}
TLDR_PRIORITY = (
    # Goal first. "Where were we" is answered by what this session is FOR
    # before it is answered by what happened in it.
    "Goal",
    "Decided",
    # State before Next: the question this skill is named after is "where am
    # I", and orienting has to come before acting. Chosen by the user after
    # seeing the same live session rendered both ways.
    "State",
    "Next",
    "Blocked",
    "Thread",
    # Recorded labels win, but history-only labels MUST remain eligible.
    # Omitting them rendered an empty tldr (prose plus menu, zero facts) for
    # every session with no ledger and no checkpoint, which is precisely the
    # case the history fallback exists to serve.
    "Discussed",
    "Direction",
    # Last resort. On a session with no ledger and no checkpoint, where it
    # began is the only orienting fact there is, so it must stay eligible;
    # ranked last so it never displaces a fact the user recorded themselves.
    "Started",
)
TLDR_MAX_LINES = 8
PROSE_MAX_LINES = 2
ITEM_MAX_LINES = 2


class UnknownSection(Exception):
    """Raised when a section name does not exist. Never guessed."""


def _wrap_note(note: str) -> list[str]:
    """Wrap one status note to TOTAL, indenting continuation lines.

    Notes quote hand-typed heading and metadata key names straight out of the
    user's ledger, so their length is bounded by nothing. Emitting them raw
    broke the fixed-width layout: one invented heading produced a
    109-character line.
    """
    body = _wrap(_clean(note), TOTAL - INDENT)
    return [" " * INDENT + line for line in body]


def _prose_lines(prose: str, cap: int = PROSE_MAX_LINES) -> list[str]:
    """Wrap the prose opening and cap it.

    The cap exists because prose competes with the label block for the same 8
    lines, and the label block is the part that carries sourced facts. A
    runaway sentence must lose, not the evidence.
    """
    prose = " ".join(str(prose).split())
    if not prose:
        return []
    wrapped = textwrap.wrap(prose, width=TOTAL)
    if len(wrapped) > cap:
        wrapped = wrapped[:cap]
        wrapped[-1] = wrapped[-1][: TOTAL - 3].rstrip() + "..."
    return wrapped + [""]


def _fit(item: dict, budget: int) -> list[str]:
    """Render one item shortened to fit `budget` lines. Never returns empty."""
    # A caller that has run out of budget still needs *something* back: this
    # helper exists because an empty tldr is the worst possible output, so
    # honouring a zero budget by returning nothing would defeat its purpose.
    budget = max(1, budget)
    text = item["text"]
    while text:
        rendered = row(item["label"], text, item["source"])
        if len(rendered) <= budget:
            return rendered
        text = text[: max(0, len(text) - max(8, len(text) // 8))].rstrip()
        if len(text) > 3:
            text = text[:-3].rstrip() + "..."
    return row(item["label"], "...", item["source"])[:budget]


def _select(items: list[dict], level: str, section: str | None) -> list[dict]:
    # Superseded goals are history, not current state. They belong in `full`,
    # where the user has deliberately gone looking for them, and nowhere that
    # competes with the current goal for the eight lines of a tldr.
    if level != "full":
        items = [i for i in items if i["label"] != "was"]
    if section is not None:
        if section not in SECTIONS:
            raise UnknownSection(
                f"No section named '{section}'. Known: {', '.join(sorted(SECTIONS))}"
            )
        wanted = SECTIONS[section]
        return [i for i in items if i["label"] in wanted]
    if level == "full":
        return items
    if level == "summary":
        return items[:12]
    ordered: list[dict] = []
    for label in TLDR_PRIORITY:
        ordered += [i for i in items if i["label"] == label]
    return ordered


def render(
    bundle: dict,
    level: str = "tldr",
    section: str | None = None,
    prose: str = "",
) -> str:
    """Assemble the final fixed-width output.

    Args:
        bundle: Collected session facts.
        level: One of LEVELS.
        section: Optional single section to render alone.
        prose: Optional opening sentence, bounded by PROSE_MAX_LINES.

    Returns:
        The rendered block, every line at most TOTAL characters.

    Raises:
        ValueError: If `level` is not a known level.
        UnknownSection: If `section` is not a known section.
    """
    if level not in LEVELS:
        raise ValueError(f"unknown level: {level}")

    if bundle.get("insufficient"):
        return (
            "Not enough here to tell you where we were. No ledger entries and\n"
            "no usable history for this session."
        )

    lines = _prose_lines(prose)

    notes: list[str] = []
    if bundle.get("adopted_from"):
        notes.append(f"  (ledger adopted from session {bundle['adopted_from'][:8]})")
    if bundle.get("stale"):
        notes.append("  (ledger was behind; newer turns folded in below)")
    if bundle.get("has_history") is False:
        notes.append("  (session history unreadable; recorded notes only)")
    if bundle.get("ledger_damaged"):
        damaged = ", ".join(bundle["ledger_damaged"])
        notes.append(f"  (unreadable in ledger: {damaged})")
    if bundle.get("ledger_omitted"):
        notes.append(
            f"  ({bundle['ledger_omitted']} older recorded items omitted; "
            "the ledger is larger than one answer can carry)"
        )

    # Notes carry hand-typed heading and key names straight from the user's
    # ledger, so they are not bounded by anything upstream. Emitting them raw
    # broke the one guarantee the whole layout rests on: a single invented
    # heading produced a 109-character line. Wrap them like every other line.
    notes = [wrapped for note in notes for wrapped in _wrap_note(note)]

    # Notes must never crowd out every fact. With enough of them plus a two
    # line prose the tldr budget went negative, the "at least one fact" guard
    # below was gated on budget > 0, and the answer came back as prose plus
    # apologies with nothing under them, while still claiming "newer turns
    # folded in below". A confident empty answer is the precise failure this
    # skill exists to prevent, so when the notes would leave no room they
    # collapse to a single line that still says how many there were.
    if level == "tldr" and section is None and notes:
        available = TLDR_MAX_LINES - len(lines) - 2
        if available - (len(notes) + 1) < 1:
            notes = [_ellipsize(f"  ({len(notes)} notes; see [full])", TOTAL)]

    if notes:
        lines += notes
        lines.append("")

    selected = _select(bundle["items"], level, section)

    # Subtract two, not one: the blank line before the menu counts. Verified by
    # running it; the naive arithmetic yields 9 lines, not 8.
    budget = (
        TLDR_MAX_LINES - len(lines) - 2 if level == "tldr" and section is None else None
    )
    body: list[str] = []
    omitted = 0
    # In the tldr, cap how tall any single item may be. Without this, one long
    # checkpoint `next_steps` consumes the entire budget and the summary becomes
    # a single truncated paragraph, which is the wall-of-text failure this skill
    # exists to prevent. Verified against the live session: uncapped, one item
    # took all four available lines and hid twelve others.
    per_item = ITEM_MAX_LINES if (level == "tldr" and section is None) else None
    for item in selected:
        rendered = row(item["label"], item["text"], item["source"])
        cap = per_item
        # `Started` is an orienting fragment, not evidence. It carries a raw
        # opening turn, which is routinely a several-hundred-word paste, and
        # at `full` that rendered as a ten-line wall sitting directly under
        # the goal: the exact wall-of-text failure this skill exists to
        # prevent. Capped at every level EXCEPT an explicit `--section
        # timeline`, where asking for the timeline is asking for the whole
        # turn.
        if item["label"] == "Started" and section is None:
            cap = ITEM_MAX_LINES if cap is None else min(cap, ITEM_MAX_LINES)
        if cap is not None and len(rendered) > cap:
            rendered = _fit(item, cap)
        if budget is not None and len(body) + len(rendered) > budget:
            # Never return a tldr with zero facts. An empty body looks like a
            # confident answer and carries nothing, which is the worst possible
            # failure for a memory aid. If the very first item cannot fit, cut
            # the item's text down until it does rather than dropping it.
            #
            # This is deliberately not gated on the budget being positive. It
            # used to be, so a budget driven to zero by status notes skipped the
            # guard entirely and produced exactly the empty answer it exists to
            # stop. _fit floors the budget at one line for this reason.
            if not body:
                rendered = _fit(item, budget)
                body += rendered
                omitted = max(0, len(selected) - 1)
                break
            omitted = len(selected) - selected.index(item)
            break
        body += rendered
        if level == "full" and item.get("why"):
            body += row("why", item["why"], item["source"])
        if level == "full" and item.get("rejected"):
            body += row("rejected", item["rejected"], item["source"])

    lines += body

    if section == "files" or level == "full":
        for path in bundle.get("files", []):
            lines += row("Files", path, "inf")

    lines.append("")
    # Say what was left out. Silent truncation makes an incomplete answer look
    # complete, which the spec forbids.
    lines.append(f"+{omitted} more. {MENU}" if omitted else MENU)
    return "\n".join(line.rstrip() for line in lines).strip("\n")


SESSION_SUMMARY_W = 40
SESSION_REPO_W = 18
SESSION_DATE_W = 10


def sessions_table(rows: list[dict], total: int, days: int) -> str:
    """Render the cross-session list at the same fixed width as everything else.

    Summaries are free text and some of them are multi-line: one real session
    was titled "How to talk to me in this session:\n\n- W...", which under a
    bare slice broke into three ragged lines and destroyed the columns. The
    whole point of this table is being scannable at a glance, so the text is
    collapsed through the same cleaner the rest of the output uses.
    """
    lines: list[str] = []
    for r in rows:
        summary = _clean(r["summary"] or "") or "(no summary)"
        repo = _clean(r["repository"] or "") or "?"
        updated = _clean(r["updated_at"] or "")[:SESSION_DATE_W]
        # Padded by columns, not codepoints, for the same reason as row().
        cell_summary = _ellipsize(summary, SESSION_SUMMARY_W)
        cell_repo = _ellipsize(repo, SESSION_REPO_W)
        lines.append(
            "  "
            + cell_summary
            + " " * (SESSION_SUMMARY_W - display_width(cell_summary) + 1)
            + cell_repo
            + " " * (SESSION_REPO_W - display_width(cell_repo) + 1)
            + f"{updated}".rstrip()
        )
    if not lines:
        lines.append(f"  No sessions in the last {days} days.")
    if total > len(rows):
        lines.append("")
        lines.append(f"  {total - len(rows)} more in the last {days} days")
    return "\n".join(lines)


def _ellipsize(text: str, width: int) -> str:
    """Truncate to `width` columns, spending three of them saying so."""
    if display_width(text) <= width:
        return text
    if width <= 3:
        return _truncate_to_width(text, width)
    return _truncate_to_width(text, width - 3).rstrip() + "..."
