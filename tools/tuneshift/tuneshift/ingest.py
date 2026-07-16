"""Platform playlist ingestion: import playlists into canonical DB."""

from tuneshift.db import Database


def ingest_from_platform(
    db: Database,
    client: object,
    playlist_id: str,
    enrich: bool = True,
) -> tuple[str, int, int, int]:
    """Ingest a playlist from a platform into the canonical database.

    Returns (playlist_name, total_tracks, new_tracks_created, skipped_tracks).
    """
    platform_name = client.platform_name  # type: ignore[attr-defined]

    # Fetch playlist info
    pl_info = client.get_playlist(playlist_id)  # type: ignore[attr-defined]
    if pl_info is None:
        raise ValueError(f"Playlist {playlist_id} not found on {platform_name}")

    name = pl_info.name
    raw_tracks = client.get_playlist_tracks(playlist_id)  # type: ignore[attr-defined]

    # Create or find canonical playlist
    existing_playlists = db.list_playlists()
    canonical_playlist = None
    for p in existing_playlists:
        if p.name == name:
            canonical_playlist = p
            break

    if canonical_playlist is None:
        playlist_db_id = db.create_playlist(name)
    else:
        playlist_db_id = canonical_playlist.id

    # Process each track
    new_count = 0
    skipped_count = 0
    track_ids_to_enrich: list[tuple[int, str]] = []

    for position, tr in enumerate(raw_tracks, start=1):
        try:
            # Find or create canonical track
            existing = db.find_track(tr.title, tr.artist, tr.album)
            if existing:
                track_id = existing.id
            else:
                from tuneshift.models import Track

                new_track = Track(
                    title=tr.title,
                    artist=tr.artist,
                    album=tr.album,
                    duration_seconds=tr.duration_seconds,
                    isrc=tr.isrc,
                )
                track_id = db.add_track(new_track)
                new_count += 1
                track_ids_to_enrich.append((track_id, tr.platform_id))

            # Add to playlist (skip if already there)
            db.add_track_to_playlist(playlist_db_id, track_id, position)

            # Store platform mapping
            from tuneshift.models import PlatformMapping

            db.upsert_platform_mapping(
                PlatformMapping(
                    track_id=track_id,
                    platform=platform_name,
                    platform_track_id=tr.platform_id,
                    platform_title=tr.title,
                    platform_artist=tr.artist,
                    platform_album=tr.album,
                    match_score=100,
                    status="matched",
                    user_approved=True,
                )
            )
        except Exception as exc:
            import sys

            print(f"  Skipping track at position {position}: {exc}", file=sys.stderr)
            skipped_count += 1

    # Link platform playlist
    db.link_platform_playlist(playlist_db_id, platform_name, playlist_id)

    # Enrich new tracks with audio metadata (BPM, key)
    if enrich and track_ids_to_enrich and hasattr(client, "get_track_metadata"):
        _enrich_tracks(db, client, track_ids_to_enrich)

    # Auto-classify new tracks and enrich artists via LLM
    if track_ids_to_enrich:
        _auto_classify_batch(db, track_ids_to_enrich)

    return name, len(raw_tracks), new_count, skipped_count


def _enrich_tracks(db: Database, client: object, tracks: list[tuple[int, str]]) -> None:
    """Fetch and store audio metadata (BPM, key) for new tracks.

    For Tidal, also captures Atmos/catalog metadata and derives the
    atmos-available tag (AC10) so an imported Atmos track is tagged without a
    later manual ``enrich --catalog`` run.
    """
    import sys

    platform_name = getattr(client, "platform_name", None)
    capture_catalog = platform_name == "tidal"
    if capture_catalog:
        from tuneshift.library.enrichment import capture_tidal_catalog

    enriched = 0
    for track_id, platform_track_id in tracks:
        try:
            meta = client.get_track_metadata(platform_track_id)  # type: ignore[attr-defined]
            if meta:
                db.update_track_metadata(track_id, meta)
                enriched += 1
        except (OSError, RuntimeError, ValueError, KeyError, AttributeError):
            continue
        finally:
            if capture_catalog:
                capture_tidal_catalog(
                    db, track_id, "tidal", platform_track_id, client=client
                )

    if enriched:
        print(
            f"  Enriched {enriched}/{len(tracks)} tracks with audio metadata",
            file=sys.stderr,
        )


def _auto_classify_batch(
    db: Database, track_ids_to_enrich: list[tuple[int, str]]
) -> None:
    """Classify new tracks and enrich their artists after ingest.

    Uses the search-grounded pipeline (Last.fm + Genius + LLM synthesis).
    """
    import json
    import sys

    from tuneshift.enrichment.pipeline import classify_track_grounded
    from tuneshift.sequencer.classifier import TrackClassifier

    classifier = TrackClassifier()

    # Enrich artists first (so we have genre context)
    seen_artists: set[str] = set()
    from tuneshift.library.enrichment import _enrich_artist_via_llm

    for track_id, _ in track_ids_to_enrich:
        track = db.get_track(track_id)
        if not track or track.artist in seen_artists:
            continue
        seen_artists.add(track.artist)
        artist = db.get_artist_by_name(track.artist)
        if artist and not artist.genres and not artist.enriched_at:
            _enrich_artist_via_llm(db, artist, classifier)

    enriched_artists = sum(
        1 for a in seen_artists if (art := db.get_artist_by_name(a)) and art.enriched_at
    )
    if enriched_artists:
        print(
            f"  Enriched {enriched_artists} artist(s) with genre data", file=sys.stderr
        )

    # Classify tracks with grounded pipeline
    if not classifier.available:
        return

    classified = 0
    for i, (track_id, _) in enumerate(track_ids_to_enrich):
        track = db.get_track(track_id)
        if not track:
            continue
        metadata = track.metadata or {}
        if metadata.get("vibes"):
            continue

        artist = db.get_artist_by_name(track.artist)
        artist_genres = artist.genres if artist else []

        result = classify_track_grounded(
            track.title,
            track.artist,
            artist_genres=artist_genres,
            classifier=classifier,
        )
        if result:
            metadata.update(result)
            db.conn.execute(
                "UPDATE tracks SET metadata = ? WHERE id = ?",
                (json.dumps(metadata), track_id),
            )
            classified += 1

        if (i + 1) % 10 == 0:
            print(
                f"  Classified {i + 1}/{len(track_ids_to_enrich)}...",
                end="\r",
                file=sys.stderr,
            )

    if classified:
        db.conn.commit()
        print(
            f"  Classified {classified}/{len(track_ids_to_enrich)} tracks (search-grounded)",
            file=sys.stderr,
        )
