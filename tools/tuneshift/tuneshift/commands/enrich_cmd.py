"""Enrich command: fetch audio metadata from platform for existing tracks."""

import logging
import sys

from tuneshift.db import Database

logger = logging.getLogger(__name__)


def handle_enrich(args, db: Database) -> int:
    """Fetch BPM, key, and other audio metadata for tracks in a playlist."""
    from tuneshift.commands.ingest_cmd import _load_client

    # Whole-DB enrichment: --all flag or no playlist name given
    enrich_all = getattr(args, "all", False) or not getattr(args, "playlist", None)
    if enrich_all:
        from tuneshift.enrichment.platform_metadata import enrich_all_playlists

        platform_name = getattr(args, "platform", None) or "tidal"
        if platform_name != "tidal":
            print(
                f"--all only supports Tidal metadata enrichment (got {platform_name})",
                file=sys.stderr,
            )
            return 1
        return enrich_all_playlists(
            db,
            refresh=getattr(args, "refresh", False),
            max_retries=getattr(args, "max_retries", 3),
            dry_run=getattr(args, "dry_run", False),
        )

    playlist = db.find_playlist_by_name(args.playlist)
    if not playlist:
        print(f"Playlist not found: {args.playlist}", file=sys.stderr)
        return 1

    platform_name = getattr(args, "platform", None)
    tracks = db.get_playlist_tracks(playlist.id)
    if not tracks:
        print(f'Playlist "{playlist.name}" is empty.')
        return 0

    enriched = 0
    skipped = 0
    platform_client = None

    # Audio metadata (BPM, key, energy) via the platform's per-track endpoint
    if platform_name:
        client = _load_client(platform_name)
        if not client:
            print(f"Unknown platform: {platform_name}", file=sys.stderr)
            return 1

        if not client.load_session():
            print(
                f"Not logged in to {platform_name}. Run: tuneshift login {platform_name}",
                file=sys.stderr,
            )
            return 1

        platform_client = client
        if not hasattr(client, "get_track_metadata"):
            print(
                f"{platform_name} does not support metadata enrichment.",
                file=sys.stderr,
            )
        else:
            for track in tracks:
                if track.tempo and track.key:
                    skipped += 1
                    continue

                mappings = db.get_platform_mappings_for_tracks(
                    [track.id], platform_name
                )
                mapping = mappings.get(track.id)
                if not mapping or not mapping.platform_track_id:
                    continue

                try:
                    meta = client.get_track_metadata(mapping.platform_track_id)
                    if meta:
                        db.update_track_metadata(track.id, meta)
                        enriched += 1
                        if enriched % 10 == 0:
                            print(f"  Enriched {enriched} tracks...", end="\r")
                except (OSError, RuntimeError, ValueError, KeyError, AttributeError):
                    continue

            print(
                f'Enriched "{playlist.name}": {enriched} tracks updated, {skipped} already had metadata'
            )

    # Catalog metadata (Atmos, release year, genres, quality tiers) from Tidal,
    # retry-aware. AC11: this now runs on ANY Tidal enrichment update -- not only
    # behind the explicit --catalog flag -- so an Atmos-mapped track always gets
    # its atmos-available tag derived, and `--refresh` re-fetches it. Reuses the
    # client already loaded above to avoid a second login.
    if getattr(args, "catalog", False) or platform_name == "tidal":
        from tuneshift.enrichment.platform_metadata import enrich_playlist_from_tidal

        meta_enriched, meta_skipped, meta_failed = enrich_playlist_from_tidal(
            db,
            playlist.id,
            refresh=getattr(args, "refresh", False),
            max_retries=getattr(args, "max_retries", 3),
            client=platform_client if platform_name == "tidal" else None,
        )
        msg = (
            f'Catalog metadata for "{playlist.name}": '
            f"{meta_enriched} updated, {meta_skipped} skipped"
        )
        if meta_failed:
            msg += f", {meta_failed} failed (retries exhausted)"
        print(msg)

    # LLM classification for narrative fields
    if getattr(args, "classify", False) or not platform_name:
        model = getattr(args, "model", None)
        reclassify = getattr(args, "reclassify", False)
        classified = _run_classification(
            db,
            tracks,
            playlist.name,
            model=model,
            playlist_id=playlist.id,
            force=reclassify,
        )
        if classified < 0:
            return 1

    return 0


def _run_classification(
    db: Database,
    tracks: list,
    playlist_name: str,
    model: str | None = None,
    playlist_id: int | None = None,
    force: bool = False,
) -> int:
    """Run LLM classification on tracks missing narrative metadata.

    If force=True, re-classifies all tracks regardless of existing metadata.
    Returns number of tracks classified, or -1 on backend error.
    """
    from tuneshift.sequencer.classifier import TrackClassifier

    classifier = TrackClassifier(model=model)
    if not classifier.available:
        print(
            "No LLM backend available for classification. Set one of:\n"
            "  ANTHROPIC_API_KEY, OPENAI_API_KEY, TUNESHIFT_LLM_BASE_URL, or OLLAMA_HOST\n"
            "  (or TUNESHIFT_LLM_BACKEND to select explicitly)",
            file=sys.stderr,
        )
        return -1

    print(f"Classifying with {classifier.backend_info}...")

    # Load playlist narrative for context
    narrative = db.get_narrative(playlist_id) if playlist_id else None
    if narrative:
        print("  Using playlist narrative as classification context")

    # Only classify tracks missing narrative fields (unless --reclassify)
    to_classify = []
    for track in tracks:
        meta = track.metadata or {}
        if (
            force
            or meta.get("narrator_stance") is None
            or meta.get("emotional_intensity") is None
        ):
            to_classify.append(
                {"title": track.title, "artist": track.artist, "id": track.id}
            )

    if not to_classify:
        print("  All tracks already classified. Use --reclassify to force.")
        return 0

    def progress(done: int, total: int) -> None:
        print(f"  Classified {done}/{total}...", end="\r")

    results = classifier.classify_batched(
        [{"title": t["title"], "artist": t["artist"]} for t in to_classify],
        batch_size=20,
        progress_callback=progress,
        narrative=narrative,
    )

    classified = 0
    # Build lookup map to match results by title/artist (guards against LLM reordering/dropping)
    track_lookup: dict[tuple[str, str], dict] = {}
    for t in to_classify:
        key = (t["title"].lower().strip(), t["artist"].lower().strip())
        track_lookup[key] = t

    for result in results:
        result_title = result.get("title", "").lower().strip()
        result_artist = result.get("artist", "").lower().strip()
        matched_track = None

        if result_title or result_artist:
            # Try exact match first
            matched_track = track_lookup.get((result_title, result_artist))
            # Fallback: match by title only (handles artist name variants)
            if not matched_track and result_title:
                for key, t in track_lookup.items():
                    if key[0] == result_title:
                        matched_track = t
                        break

        if not matched_track:
            # If LLM omitted identifying fields, skip (no silent miswrite)
            logger.warning(
                "Skipping unmatched LLM result: title='%s' artist='%s'",
                result.get("title"),
                result.get("artist"),
            )
            continue

        db.update_track_metadata(matched_track["id"], result)
        classified += 1

    print(f'  Classified {classified}/{len(to_classify)} tracks for "{playlist_name}"')
    return classified
