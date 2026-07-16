"""``triage`` command: cluster tracks needing attention for bulk decisions.

Long, ordered libraries generate many low-confidence outcomes. Prompting once
per track does not scale, so ``triage`` reads the durable match audits, groups
the tracks that need a human by *why* they were flagged and *which artist* they
concern, and reports the review burden (tracks per 1,000 needing attention, and
the fraction of playlists that sailed through untouched).

It is read-only: it shows what needs deciding and where, so a reviewer can act
in bulk (e.g. via ``tuneshift map`` / ``tuneshift edit``) instead of answering N
identical prompts mid-sync.
"""

import sys

from tuneshift.db import Database
from tuneshift.matching import cluster_reviews, compute_burden


def handle_triage(args, db: Database) -> int:
    """Show clustered review items and the review-burden summary."""
    playlist_id = None
    if getattr(args, "playlist", None):
        playlist = db.find_playlist_by_name(args.playlist)
        if not playlist:
            print(f"Playlist not found: {args.playlist}", file=sys.stderr)
            return 1
        playlist_id = playlist.id

    platform = getattr(args, "platform", None)
    items = db.get_review_items(playlist_id=playlist_id, platform=platform)
    quarantined = db.get_quarantined_tracks()

    if not items and not quarantined:
        print("No match decisions recorded yet. Run `tuneshift sync` first.")
        return 0

    if items:
        burden = compute_burden(items, total_tracks=len(items))
        clusters = cluster_reviews(items)

        scope = f'"{args.playlist}"' if playlist_id is not None else "all playlists"
        print(
            f"Review burden for {scope}" + (f" on {platform}" if platform else "") + ":"
        )
        print(
            f"  {burden.needs_review} of {burden.total_tracks} tracks need review "
            f"({burden.per_1000:g} per 1,000)"
        )
        print(f"    ambiguous: {burden.ambiguous}   hard-fail: {burden.hard_fail}")
        print(
            f"  zero-intervention playlists: {burden.zero_intervention_playlists}"
            f"/{burden.total_playlists} ({burden.zero_intervention_pct:g}%)"
        )

        if clusters:
            print(f"\n{len(clusters)} cluster(s), largest first:\n")
            for cluster in clusters:
                print(f"  - {cluster.summary}")
                for item in cluster.items:
                    where = f" [{item.playlist_name}]" if item.playlist_name else ""
                    print(f"      #{item.track_id} {item.title} - {item.artist}{where}")
        else:
            print("\nNothing to review - every track resolved cleanly.")

    if quarantined:
        print(
            f"\nQuarantined ({len(quarantined)}) - "
            "excluded from selection until resolved or approved:"
        )
        for q in quarantined:
            reason = f": {q['reason']}" if q["reason"] else ""
            print(f"  #{q['track_id']} {q['title']} - {q['artist']}{reason}")

    return 0
