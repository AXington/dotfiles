"""``explain`` command: explain a track's match decision on each platform.

Surfaces the persisted :class:`~tuneshift.matching.MatchAudit` for a track so a
human can answer "why did it pick that / why did it say not-found?" without
reading logs or re-running a search. Selection is per-playlist (AC-CLI3), so an
optional ``<playlist>`` argument scopes the explanation to that playlist's stored
decision; omitting it reads the global-scope audit. Reads the durable audit
written by sync / doctor / add; ``--live`` forces a fresh reconcile (needs auth)
and re-persists.

``explain`` renders the full decision (AC-CLI3): the criteria that fired (hard vs
soft), the winner's weighted signal breakdown, the precedence tie-break, and —
for a miss (AC-CLI5) — every rejected candidate with its per-candidate rejection
reason. ``why`` is a deprecated alias kept for one release.
"""

import sys

from tuneshift.db import Database
from tuneshift.matching import describe_availability, describe_reason

_ALL_PLATFORMS = ("spotify", "tidal", "ytmusic")

_GLOBAL_SCOPE = 0

_REJECTION_LABELS = {
    "hard_filter": "failed hard filter",
    "unavailable": "unavailable here",
    "below_threshold": "below match threshold",
    "lost": "out-ranked",
}


def _resolve_playlist(args, db: Database) -> tuple[int, str | None, int | None]:
    """Resolve the optional ``<playlist>`` argument to a (scope_id, label, err).

    Returns ``(playlist_id, label, None)`` on success, or ``(0, None, exit_code)``
    when a named playlist could not be found. A missing argument scopes to the
    global sentinel (``0``) — the pre-playlist ``why`` behaviour.
    """
    name = getattr(args, "playlist", None)
    if not name:
        return _GLOBAL_SCOPE, None, None
    playlist = db.find_playlist_by_name(name)
    if playlist is None:
        print(f"Playlist not found: {name}", file=sys.stderr)
        return _GLOBAL_SCOPE, None, 1
    return playlist.id, playlist.name, None


def handle_explain(args, db: Database) -> int:
    """Explain the stored (or live) match decision for a track."""
    track = db.get_track(args.track_id)
    if track is None:
        print(f"No track with id {args.track_id}", file=sys.stderr)
        return 1

    playlist_id, playlist_label, err = _resolve_playlist(args, db)
    if err is not None:
        return err

    album = f" [{track.album}]" if track.album else ""
    print(f"Track #{track.id}: {track.title} — {track.artist}{album}")
    if track.isrc:
        print(f"  ISRC: {track.isrc}")
    if playlist_label is not None:
        print(f"  playlist: {playlist_label}")
    print()

    platforms = [args.platform] if args.platform else list(_ALL_PLATFORMS)

    if getattr(args, "live", False):
        return _explain_live(db, track, platforms, playlist_id)

    audits = db.get_match_audits_for_track(track.id, playlist_id)
    if args.platform:
        audits = {p: a for p, a in audits.items() if p == args.platform}
    if not audits:
        scope_hint = f" {playlist_label}" if playlist_label else ""
        print(f"No stored match decision for this track{scope_hint} yet.")
        print(
            "Run `tuneshift sync` / `tuneshift doctor`, or `tuneshift explain "
            f"{track.id} --live` to reconcile now."
        )
        return 1

    for platform in sorted(audits):
        _print_audit(platform, audits[platform])
    return 0


# Deprecated alias: ``tuneshift why`` still works for one release.
def handle_why(args, db: Database) -> int:
    """Deprecated alias for :func:`handle_explain` (kept for one release)."""
    print(
        "note: `tuneshift why` is deprecated; use `tuneshift explain`.", file=sys.stderr
    )
    return handle_explain(args, db)


def _explain_live(db: Database, track, platforms: list[str], playlist_id: int) -> int:
    """Run a fresh reconcile against each platform, persist, and explain."""
    from tuneshift.commands.ingest_cmd import _load_client
    from tuneshift.reconcile import reconcile_track

    any_done = False
    scope = None if playlist_id == _GLOBAL_SCOPE else playlist_id
    for platform in platforms:
        client = _load_client(platform)
        if client is None:
            print(f"{platform}: unknown platform, skipped", file=sys.stderr)
            continue
        if not client.load_session():
            print(
                f"{platform}: not logged in (run `tuneshift login {platform}`), skipped",
                file=sys.stderr,
            )
            continue
        result = reconcile_track(db, track.id, client, force=True, playlist_id=scope)
        db.save_match_audit(track.id, platform, result.audit, playlist_id)
        if result.audit is not None:
            _print_audit(platform, result.audit)
            any_done = True
    return 0 if any_done else 1


def _print_audit(platform: str, audit) -> None:
    """Render one platform's audit as a compact, human-readable block."""
    avail = describe_availability(audit.availability)
    reason = describe_reason(audit.reason_code)
    print(f"  {platform}: {avail}")
    print(f"    reason: {reason} [{audit.reason_code}]")
    if audit.locked:
        print("    locked: yes (durable user lock)")
    if audit.chosen_platform_id:
        detail = f"    chosen: {audit.chosen_platform_id} (score {audit.chosen_score}"
        if audit.distance is not None:
            detail += f", distance {audit.distance}"
        detail += ")"
        print(detail)
        if audit.decisive_signal:
            print(f"    decisive signal: {audit.decisive_signal}")

    _print_criteria(audit)
    _print_breakdown(audit)
    if getattr(audit, "tie_break", None):
        print(f"    tie-break: resolved by '{audit.tie_break}' (precedence)")

    if audit.rejected:
        print("    rejected:")
        for cand in audit.rejected:
            print(f"      {_format_rejected(cand)}")
    if audit.note:
        print(f"    note: {audit.note}")
    print()


def _print_criteria(audit) -> None:
    """Render the active user criteria that shaped the decision (AC-CLI3)."""
    criteria = getattr(audit, "criteria", None)
    if not criteria:
        return
    print("    criteria:")
    for c in criteria:
        target = f"={c.target}" if c.target else ""
        status = "fired" if c.fired else "in force, no effect"
        print(f"      [{c.kind}] {c.criterion}{target} ({c.strength}) — {status}")


def _print_breakdown(audit) -> None:
    """Render the winner's weighted per-signal breakdown, worst first (AC-CLI3)."""
    breakdown = getattr(audit, "signal_breakdown", None)
    if not breakdown:
        return
    print("    weighted breakdown (higher = hurt the match more):")
    for s in breakdown:
        print(f"      {s.name}: {s.contribution:g} (weight {s.weight:g})")


def _format_rejected(cand) -> str:
    """One rejected-candidate line with its per-candidate rejection reason (AC-CLI5)."""
    rejection = getattr(cand, "rejection", None)
    if rejection:
        label = _REJECTION_LABELS.get(rejection, rejection)
        detail = getattr(cand, "rejection_detail", None)
        why = f"{label} ({detail})" if detail else label
    elif cand.decisive_signal:
        why = cand.decisive_signal
    else:
        why = "not chosen"
    return f"[{cand.score}] {cand.title} — {cand.artist} ({cand.album}) — {why}"
