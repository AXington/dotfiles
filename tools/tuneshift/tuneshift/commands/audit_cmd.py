"""Audit command: unified playlist health check."""

from __future__ import annotations

import sys

from tuneshift.db import Database


def handle_audit(args, db: Database) -> int:
    """Run all audit checks on one or all playlists."""
    if args.playlist:
        playlist = db.find_playlist_by_name(args.playlist)
        if not playlist:
            print(f"Playlist not found: {args.playlist}", file=sys.stderr)
            return 1
        playlists = [playlist]
    else:
        playlists = db.list_playlists()

    run_all = not any(
        [
            getattr(args, "matching_only", False),
            getattr(args, "vibes_only", False),
            getattr(args, "concept_only", False),
        ]
    )

    total_findings = 0

    concept_judge = None
    if run_all or getattr(args, "concept_only", False):
        from tuneshift.composer.concept_llm import make_concept_judge

        concept_judge = make_concept_judge()

    for playlist in playlists:
        findings: list[str] = []

        if run_all or getattr(args, "matching_only", False):
            findings.extend(_audit_mappings(db, playlist))

        if run_all or getattr(args, "concept_only", False):
            findings.extend(_audit_concept(db, playlist, llm_judge=concept_judge))

        if run_all or getattr(args, "vibes_only", False):
            findings.extend(_audit_vibes(db, playlist))

        if run_all:
            findings.extend(_audit_banned(db, playlist))

        if findings:
            print(f"\n{'=' * 60}")
            print(f"  {playlist.name} ({len(findings)} finding(s))")
            print(f"{'=' * 60}")
            for f in findings:
                print(f"  {f}")
            total_findings += len(findings)

    if total_findings == 0:
        print("All playlists clean.")
    else:
        print(
            f"\n{total_findings} total finding(s) across {len(playlists)} playlist(s)."
        )
        if getattr(args, "fix", False):
            print("\nGenerating fix plan...")
            # Delegate to batch --review-findings for the specified playlist
            if args.playlist:
                from tuneshift.commands.batch_cmd import (
                    BatchPlan,
                    plan_review_fixes,
                    render_plan,
                )

                ops = plan_review_fixes(db, playlists[0].id, llm_judge=concept_judge)
                if ops:
                    plan = BatchPlan(
                        playlist_name=playlists[0].name,
                        playlist_id=playlists[0].id,
                        operations=ops,
                    )
                    plan.save()
                    print(render_plan(plan))
                    print("\nApply with: tuneshift batch --apply")

    return 0


def _audit_mappings(db: Database, playlist) -> list[str]:
    """Check platform mapping quality for all tracks in a playlist."""
    from difflib import SequenceMatcher

    from tuneshift.matching import normalize_artist, score_match_with_version

    findings: list[str] = []
    tracks = db.get_playlist_tracks(playlist.id)

    for track in tracks:
        # Check each platform mapping
        cols = [
            r[1]
            for r in db.conn.execute("PRAGMA table_info(platform_tracks)").fetchall()
        ]
        rows = db.conn.execute(
            "SELECT * FROM platform_tracks WHERE track_id = ?", (track.id,)
        ).fetchall()

        for row in rows:
            mapping = dict(zip(cols, row, strict=True))
            platform = mapping["platform"]
            p_title = mapping.get("platform_title") or ""
            p_artist = mapping.get("platform_artist") or ""
            p_album = mapping.get("platform_album") or ""

            # Skip if no metadata stored (can't audit without it)
            if not p_title and not p_artist:
                continue

            # Re-score with current algorithm
            score = score_match_with_version(
                track.title,
                track.artist,
                track.album,
                p_title,
                p_artist,
                p_album,
            )

            # Artist mismatch check
            src_norm = normalize_artist(track.artist)
            res_norm = normalize_artist(p_artist)
            artist_ratio = (
                SequenceMatcher(None, src_norm, res_norm).ratio()
                if src_norm and res_norm
                else 1.0
            )

            if score < 50:
                findings.append(
                    f'[MAPPING] {platform}: "{track.title}" by {track.artist} -> '
                    f'matched to "{p_title}" by {p_artist} (score: {score}, REJECT)'
                )
            elif artist_ratio < 0.5:
                findings.append(
                    f'[MAPPING] {platform}: "{track.title}" -> artist mismatch: '
                    f'expected "{track.artist}", got "{p_artist}" (ratio: {artist_ratio:.2f})'
                )

    return findings


def _audit_concept(db: Database, playlist, llm_judge=None) -> list[str]:
    """Check concept rule compliance. Reports unverifiable tracks separately.

    Enforces the same rule routing as ``review``: artist-tag and era rules
    deterministically, thematic rules via ``llm_judge`` when supplied, and
    suppresses accepted (track, rule) pairs.
    """
    from tuneshift.commands.compose_cmd import _build_artist_lookup, _get_concept
    from tuneshift.composer.reviewer import review_playlist
    from tuneshift.sequencer.metadata import track_to_metadata

    concept = _get_concept(db, playlist.id)
    if not concept:
        return []

    tracks_raw = db.get_playlist_tracks(playlist.id)
    tracks = [track_to_metadata(t) for t in tracks_raw]
    artist_lookup = _build_artist_lookup(db, playlist.id)

    review_findings = review_playlist(
        tracks,
        concept=concept,
        artist_lookup=artist_lookup,
        year_lookup=db.get_release_years_for_playlist(playlist.id),
        llm_judge=llm_judge,
        accepted=db.get_concept_acceptances(playlist.id),
    )

    findings: list[str] = []
    unverifiable = 0
    for f in review_findings:
        if f.severity >= 0.8:
            findings.append(f"[CONCEPT] VIOLATION: {f.description}")
        elif f.severity >= 0.5:
            findings.append(f"[VIBE] {f.description}")
        elif f.severity <= 0.3 and "cannot verify" in f.description:
            unverifiable += 1

    if unverifiable:
        findings.append(
            f"[UNVERIFIED] {unverifiable} track(s) cannot be verified against concept rules "
            f'(artists not enriched). Run: tuneshift enrich "{playlist.name}" --classify'
        )

    return findings


def _audit_vibes(db: Database, playlist) -> list[str]:
    """Check for vibe outliers (already included in review_playlist)."""
    from tuneshift.composer.reviewer import _review_vibe_outliers
    from tuneshift.sequencer.metadata import track_to_metadata

    tracks = [track_to_metadata(t) for t in db.get_playlist_tracks(playlist.id)]
    outlier_findings = _review_vibe_outliers(tracks)

    return [f"[OUTLIER] {f.description}" for f in outlier_findings]


def _audit_banned(db: Database, playlist) -> list[str]:
    """Check for banned artists."""
    from tuneshift.commands.batch_cmd import check_track_against_bans

    findings: list[str] = []
    tracks = db.get_playlist_tracks(playlist.id)

    for track in tracks:
        banned = check_track_against_bans(db, track.title, track.artist)
        if banned:
            findings.append(
                f'[BANNED] "{track.title}" by {track.artist} (banned: {banned})'
            )

    return findings
