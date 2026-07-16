"""Resolve command: library-first platform resolution (spec section 4.1a, AC-D2/D7/X3).

``resolve`` drains the resolution queue built by the library-first ``add``/import
path: for each target track it runs the real multi-strategy platform search
(shared with reconcile), persists the top-N candidates to ``track_candidates``
(so selection later scores over a frozen set, not a live search, AC-X3), and
hydrates the track's core identity metadata (isrc/duration/album/confidence)
onto ``tracks`` (AC-D2). Rate limits are transient and re-queued, never lost
(AC-X2); a track with no platform match is quarantined for review (AC-D6).

This replaced the MusicBrainz/Discogs identity path as the primary resolver:
MusicBrainz identity remains a secondary signal, but the *platform* is the
source of truth for which concrete releases exist to select among.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from argparse import Namespace

    from tuneshift.db import Database
    from tuneshift.models import Track


def _load_client(platform_name: str):
    """Load a platform client by name (network boundary; patched in tests)."""
    if platform_name == "tidal":
        from tuneshift.platforms.tidal import TidalClient

        return TidalClient()
    if platform_name == "spotify":
        from tuneshift.platforms.spotify import SpotifyClient

        return SpotifyClient()
    if platform_name == "ytmusic":
        from tuneshift.platforms.ytmusic import YTMusicClient

        return YTMusicClient()
    return None


def run_resolve(args: Namespace, db: Database) -> None:
    """Execute the resolve command."""
    if args.status:
        _show_status(args, db)
        return

    tracks = _targets(args, db)
    if not tracks:
        return

    platform_name = getattr(args, "platform", None) or "tidal"
    client = _load_client(platform_name)
    if client is None:
        print(f"Error: unknown platform '{platform_name}'", file=sys.stderr)
        raise SystemExit(1)

    # Authenticate before use: the platform search APIs require a live session and
    # do not auto-login, so an unauthenticated client silently quarantines every
    # track as "no candidate". Fakes in tests omit load_session and are skipped.
    loader = getattr(client, "load_session", None)
    if loader is not None and not loader():
        print(
            f"Error: not logged in to {platform_name}. "
            f"Run: tuneshift login {platform_name}",
            file=sys.stderr,
        )
        raise SystemExit(1)

    from tuneshift.library.enrichment import make_enricher
    from tuneshift.library.lock import ResolveLock, ResolveLockHeld
    from tuneshift.library.resolvers import PlatformResolver
    from tuneshift.library.worker import ResolutionWorker
    from tuneshift.platforms.rate_limiter import RateLimiter

    resolver = PlatformResolver(db, client)
    # FEAT-1: --throttle caps resolve operations/second for local resource
    # pacing (Ollama/SQLite/network), independent of upstream API limits.
    throttle = getattr(args, "throttle", None)
    if throttle is not None and throttle <= 0:
        print("Error: --throttle must be a positive number", file=sys.stderr)
        raise SystemExit(1)
    # Wire the enricher (FL1 left this None): resolved tracks get artist genres +
    # grounded classification + Atmos/catalog capture + energy/valence, out of
    # the interactive add path (AC-D7). Reuse the client already loaded for
    # resolution so we don't re-login per track.
    worker = ResolutionWorker(
        db,
        resolver,
        enricher=make_enricher(
            tidal_client=client if platform_name == "tidal" else None
        ),
        rate_limiter=RateLimiter(max_per_second=throttle or 3.0),
    )

    # --force / --upgrade both mean "re-resolve tracks already resolved".
    force = bool(args.force or args.upgrade)
    # BUG-2: serialize resolve runs so concurrent writers cannot corrupt the DB.
    try:
        with ResolveLock(db.path):
            print(f"Resolving {len(tracks)} track(s) via {platform_name}...")
            worker.resolve_tracks([t.id for t in tracks], force=force)
    except ResolveLockHeld as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    for track in tracks:
        _print_result(track, db)
    _print_summary(tracks, db)


def _targets(args: Namespace, db: Database) -> list[Track]:
    """Resolve the CLI selectors to the concrete set of tracks to process."""
    if args.track:
        track = _select_single_track(db, *args.track)
        return [track] if track is not None else []
    if args.all:
        below_tier = "CONFIRMED" if args.upgrade else None
        return db.find_unresolved(below_tier=below_tier)
    if args.playlist:
        playlist = db.find_playlist_by_name(args.playlist)
        if playlist is None:
            print(f"Error: playlist '{args.playlist}' not found", file=sys.stderr)
            raise SystemExit(1)
        return db.get_playlist_tracks(playlist.id)
    print("Error: specify a playlist name, --track, or --all", file=sys.stderr)
    raise SystemExit(1)


def _select_single_track(db: Database, title: str, artist: str) -> Track | None:
    """Find a single track by title/artist, prompting to disambiguate."""
    matches = db.find_tracks_by_title_artist(title=title, artist=artist)
    if not matches:
        print(
            "Error: Track not in database. Use `tuneshift add` first.", file=sys.stderr
        )
        raise SystemExit(1)
    if len(matches) == 1:
        return matches[0]
    print(f"Multiple matches for '{title}' by '{artist}':")
    for index, match in enumerate(matches, 1):
        print(
            f"  {index}. {match.title} - {match.artist} ({match.album or 'no album'})"
        )
    choice = input("Select [1]: ").strip() or "1"
    try:
        return matches[int(choice) - 1]
    except (ValueError, IndexError):
        print("Invalid selection.", file=sys.stderr)
        raise SystemExit(1) from None


def _print_result(track: Track, db: Database) -> None:
    """Print a single track's resolution outcome from its post-run state."""
    state = db.get_resolution_queue_state(track.id) or "unqueued"
    if state == "resolved":
        tier, _, _ = db.get_resolution_state(track.id)
        print(f"  [{tier or 'RESOLVED'}]  {track.title} - {track.artist}")
    elif state == "quarantined":
        current = db.get_track(track.id)
        reason = (current.quarantine_reason if current else None) or "no match"
        print(f"  [QUARANTINED] ({reason})  {track.title} - {track.artist}")
    else:
        print(f"  [{state.upper()}]  {track.title} - {track.artist}")


def _print_summary(tracks: list[Track], db: Database) -> None:
    """Print a resolution summary keyed by resulting queue state."""
    from collections import Counter

    states: Counter[str] = Counter()
    for track in tracks:
        states[db.get_resolution_queue_state(track.id) or "unqueued"] += 1
    parts = [f"{count} {state}" for state, count in sorted(states.items())]
    print(f"\nDone. {len(tracks)} track(s): {', '.join(parts)}")


def _show_status(args: Namespace, db: Database) -> None:
    """Show resolution statistics."""
    if args.playlist:
        playlist = db.find_playlist_by_name(args.playlist)
        if playlist is None:
            print(f"Error: playlist '{args.playlist}' not found", file=sys.stderr)
            raise SystemExit(1)
        tracks = db.get_playlist_tracks(playlist.id)
        resolved = sum(
            1 for t in tracks if db.get_resolution_queue_state(t.id) == "resolved"
        )
        platforms = db.get_linked_platforms(playlist.id)
        platform_names = ", ".join(platforms) if platforms else "none"
        percent = 100 * resolved // len(tracks) if tracks else 0
        print(f"  {args.playlist}")
        print(f"    Tracks: {len(tracks)}")
        print(f"    Resolved: {resolved}/{len(tracks)} ({percent}%)")
        print(f"    Platforms: {platform_names}")
    else:
        _print_library_status(db, verbose=bool(getattr(args, "verbose", False)))


def _print_library_status(db: Database, *, verbose: bool) -> None:
    """Print the whole-library resolution status (richer coverage report)."""
    s = db.resolution_status_summary()
    total = s["total"]
    unresolved = s["unresolved_in_playlist"] + s["unresolved_orphaned"]

    print("Resolution status")
    print(
        f"  Coverage:  {s['playable_pct'] * 100:.1f}% playable  "
        f"({s['playable']} / {total} total)"
    )
    print(f"             {s['quarantined']} quarantined (unavailable on platform)")
    print(
        f"             {unresolved} unresolved  "
        f"({s['unresolved_in_playlist']} in playlists, "
        f"{s['unresolved_orphaned']} orphaned/no-playlist)"
    )

    tiers = s["tiers"]
    if tiers:
        order = ["VERIFIED", "CONFIRMED", "PROBABLE", "UNCERTAIN"]
        parts = [f"{tiers[t]} {t}" for t in order if tiers.get(t)]
        print(f"\n  Tiers:     {' - '.join(parts)}")

    if s["quarantine_reasons"]:
        print("\n  Quarantine reasons:")
        for bucket, count in s["quarantine_reasons"]:
            print(f"    {count:>4}  {bucket}")

    rows = db.per_playlist_coverage()
    needs_attention = [r for r in rows if r["pct"] < 1.0]
    fully = len(rows) - len(needs_attention)
    if needs_attention:
        print("\n  Per-playlist coverage (lowest first):")
        for r in needs_attention:
            pct = int(r["pct"] * 100)
            note = _coverage_note(r)
            print(
                f"    {pct:>3}%  {r['name'][:32]:<32} ({r['playable']}/{r['total']}){note}"  # noqa: E501
            )
    if fully:
        print(f"\n  {fully} playlist(s) fully playable.")

    if verbose and s["quarantined"]:
        print("\n  Quarantined tracks:")
        for q in db.get_quarantined_tracks():
            print(
                f"    [{q['track_id']}] {q['title']} - {q['artist']}  ({q['reason']})"
            )


def _coverage_note(row: dict) -> str:
    """Annotate a per-playlist row: distinguish 'unavailable' from 'run resolve'."""
    if row["unresolved"]:
        extra = f" - {row['quarantined']} unavailable" if row["quarantined"] else ""
        return f"  [{row['unresolved']} unresolved{extra}]"
    if row["quarantined"]:
        return f"  [done: {row['quarantined']} unavailable]"
    return ""
