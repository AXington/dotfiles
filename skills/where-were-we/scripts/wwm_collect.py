"""Locate, reconcile, deduplicate, and fall back within budget.

Produces the structured bundle the model judges and the renderer formats. This
module decides what is true and where it came from; it never decides how it
looks.
"""

from __future__ import annotations

import re

import wwm_history
import wwm_ledger
import wwm_session

STOPWORDS = {
    "the",
    "a",
    "an",
    "to",
    "of",
    "and",
    "or",
    "for",
    "we",
    "i",
    "it",
    "is",
    "on",
    "in",
    "that",
    "this",
    "with",
    "be",
    "as",
    "at",
    "so",
    "but",
}


def _fingerprint(text: str) -> frozenset[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return frozenset(w for w in words if w not in STOPWORDS and len(w) > 2)


def _same_event(a: str, b: str, threshold: float = 0.6) -> bool:
    """Cheap overlap test.

    Deliberately not fuzzy string matching: the goal is to catch a decision
    echoing its own turn, not to cluster unrelated topics. A false negative
    shows one extra line; a false positive hides real content, so the threshold
    errs high.
    """
    fa, fb = _fingerprint(a), _fingerprint(b)
    if not fa or not fb:
        return False
    overlap = len(fa & fb) / min(len(fa), len(fb))
    return overlap >= threshold


def _recorded_items(led: wwm_ledger.Ledger) -> list[dict]:
    """Everything the user explicitly recorded, all tagged `rec`."""
    items: list[dict] = []
    # Goal leads. It was collected and then dropped by the renderer, so a
    # ledger holding only a goal rendered a completely blank answer: the user
    # told the skill what they were doing and it showed them nothing.
    if led.goal:
        items.append({"label": "Goal", "text": led.goal, "source": "rec"})
    # Superseded goals, oldest first. Held back to `full` by the renderer so
    # they can never crowd the current goal out of the tldr.
    for past in led.goal_history:
        items.append(
            {
                "label": "was",
                # The date carries the meaning here. A bare list of former
                # goals is trivia; a dated one is the arc of the session.
                "text": f"{past.text} ({past.date})",
                "source": "rec",
                "date": past.date,
            }
        )
    for dec in led.decisions:
        items.append(
            {
                "label": "Decided",
                "text": dec.text,
                "why": dec.why,
                "rejected": dec.rejected,
                "source": "rec",
                "date": dec.date,
            }
        )
    for thread in led.threads:
        if not thread.done:
            items.append({"label": "Thread", "text": thread.text, "source": "rec"})
    for blocker in led.blockers:
        items.append({"label": "Blocked", "text": blocker, "source": "rec"})
    if led.next:
        items.append({"label": "Next", "text": led.next, "source": "rec"})
    if led.state:
        items.append({"label": "State", "text": led.state, "source": "rec"})
    return items


# Labels that hold exactly one value. They cannot grow without bound and they
# are the spine of the answer, so the budget is never allowed to take them.
SINGULAR = ("Goal", "Next", "State")


def _bound(items: list[dict]) -> tuple[list[dict], int]:
    """Hold recorded content to the same budget every other slice obeys.

    History slices are each budgeted; recorded items were not. A ledger grows
    for the life of a session, so a long-running one handed the model a payload
    many times the size the rest of the bundle is held to.

    Repeatable entries are trimmed oldest-first, because a ledger reads
    oldest-first while the newest milestones are the ones that answer "where
    were we". Singular entries are never trimmed: dropping them would silently
    delete the goal, which is the one line the whole answer is built around.

    Returns:
        The surviving items in their original order, and how many were dropped.
    """
    spent = sum(len(i["text"]) for i in items if i["label"] in SINGULAR)
    keep = {id(i) for i in items if i["label"] in SINGULAR}

    dropped = 0
    for item in reversed(items):
        if item["label"] in SINGULAR:
            continue
        spent += len(item["text"])
        if spent > wwm_history.BUDGET_LEDGER:
            dropped += 1
            continue
        keep.add(id(item))

    return [i for i in items if id(i) in keep], dropped


def collect(session_id: str) -> dict:
    led = wwm_ledger.load(session_id)
    items, ledger_omitted = _bound(_recorded_items(led))

    # Branch on whether a ledger exists at all, NOT on whether `newer` came back
    # empty. `turns_after(after=0)` sorts ASC, so on a session with no ledger it
    # happily returns the OLDEST turns and is therefore always truthy. Keying
    # the fallback off `not newer` meant a 461-turn session answered "where were
    # we" with turns 1-29. Verified against the real store.
    #
    # Threads and blockers count. Omitting them meant a ledger holding only an
    # open thread was treated as no ledger at all, which threw away its own
    # last_synced_turn boundary and replayed history the user had already seen.
    has_ledger = bool(
        led.decisions
        or led.state
        or led.goal
        or led.next
        or led.threads
        or led.blockers
    )

    # A file being present is not the same as a readable database: sqlite only
    # reports a truncated, empty or foreign file once a statement actually runs.
    # So every store read sits inside one degradation boundary, and the store is
    # only trusted after it has answered. Losing history is a degraded mode, not
    # a fatal error. Every recorded fact in the ledger is still valid and still
    # worth showing; we just say plainly that the history half is missing.
    store_ok = wwm_session.has_store()
    store_max = 0
    checkpoint = None
    newer: list[dict] = []
    origin: list[dict] = []
    files: list[str] = []
    if store_ok:
        try:
            store_max = wwm_history.max_turn_index(session_id)

            # Fetch the checkpoint first so any unspent allowance can be handed
            # to the turn slices. 44% of sessions have no checkpoint; for those,
            # reserving 2500 chars for a row that does not exist would throw
            # away the only evidence the skill actually has.
            checkpoint = wwm_history.latest_checkpoint(session_id)
            spent = checkpoint["spent"] if checkpoint else 0
            turn_budget = wwm_history.BUDGET_RECENT + max(
                0, wwm_history.BUDGET_CHECKPOINT - spent
            )

            if has_ledger:
                newer = wwm_history.turns_after(
                    session_id,
                    after=min(led.last_synced_turn, store_max),
                    budget=turn_budget,
                )
            else:
                newer = wwm_history.recent_turns(session_id, budget=turn_budget)

            origin = wwm_history.earliest_turns(session_id)
            files = wwm_history.session_files(session_id)
        except wwm_history.StoreUnavailable:
            store_ok = False
            store_max = 0
            checkpoint = None
            newer = []
            origin = []
            files = []

    # A marker at or beyond the store is stale, not current. It can only mean an
    # unflushed turn or a tampered file; either way, reconcile from what the
    # store actually confirms rather than trusting the claim.
    #
    # With no store there is nothing to compare against, so staleness is simply
    # unknowable. Claiming "newer turns folded in below" when zero turns were
    # folded in points the reader at content that is not there, which is a
    # worse failure than staying quiet.
    stale = False
    if store_ok:
        stale = bool(led.decisions or led.state) and led.last_synced_turn < store_max
        if led.last_synced_turn > store_max:
            stale = True

    recorded_texts = [i["text"] for i in items]
    for turn in newer:
        if any(_same_event(turn["text"], rec) for rec in recorded_texts):
            continue
        items.append(
            {
                "label": "Discussed",
                "text": turn["text"],
                "source": "inf",
                "turn_index": turn["turn_index"],
            }
        )

    if checkpoint and not led.state:
        items.append(
            {"label": "State", "text": checkpoint["overview"], "source": "inf"}
        )
        if checkpoint["next_steps"] and not led.next:
            items.append(
                {"label": "Next", "text": checkpoint["next_steps"], "source": "inf"}
            )

    # Where the session began. `origin` was collected, counted as evidence by
    # `insufficient`, and then never rendered by anything, so a session whose
    # only evidence was its own history answered with a blank screen.
    #
    # Shown only when it is not simply a restatement of the current goal: a
    # session that never pivoted gains nothing from being told twice where it
    # started. Placed directly under the goal it diverges from, so the shift
    # reads as one thought rather than two facts pages apart.
    if origin:
        first = origin[0]
        already_shown = any(i.get("turn_index") == first["turn_index"] for i in items)
        echoes_goal = bool(led.goal) and _same_event(first["text"], led.goal)
        if not already_shown and not echoes_goal:
            anchor = (1 if led.goal else 0) + len(led.goal_history)
            items.insert(
                anchor,
                {
                    "label": "Started",
                    "text": first["text"],
                    "source": "inf",
                    "turn_index": first["turn_index"],
                },
            )
            # Promoted, not copied. Leaving the turn in `origin` as well put
            # the same string in the bundle twice and pushed the payload past
            # BUDGET_TOTAL by exactly its own length.
            origin = origin[1:]

    return {
        "session_id": session_id,
        "goal": led.goal,
        "adopted_from": led.adopted_from,
        "origin": origin,
        "items": items,
        "files": files,
        "stale": stale,
        "has_ledger": has_ledger,
        "has_history": store_ok,
        "ledger_damaged": led.damaged,
        "ledger_omitted": ledger_omitted,
        # Origin is no longer counted separately: it now becomes a `Started`
        # item above, so anything that suppresses this guard is guaranteed to
        # be something the renderer can actually show. The old form counted
        # origin as evidence while nothing rendered it, which let the skill
        # answer confidently with an empty screen.
        "insufficient": not items,
    }
