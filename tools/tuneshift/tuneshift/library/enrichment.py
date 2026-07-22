"""Async track enrichment invoked by the resolution worker (AC-D7).

This is the enrichment that used to run *inline* on every ``add`` (track vibe/
theme classification + artist genre lookup). Library-first (AC-D7) forbids
blocking the add path on MusicBrainz/LLM calls, so this work is deferred: the
resolution worker calls :func:`enrich_track` after a track resolves, out of the
interactive path.

Scope note (spec section 14 ownership): this is *sequencer classification + artist
genre* enrichment, already owned by this codebase. It is distinct from the
enrichment spec's ``get_track_metadata``/``derive_tags`` capture, which remains
that spec's concern.
"""

from __future__ import annotations

import json
import logging

from tuneshift.db import Database

logger = logging.getLogger(__name__)


def enrich_track(
    db: Database,
    track_id: int,
    artist_name: str,
    *,
    classifier=None,
    tidal_client=None,
    refresh: bool = False,
) -> None:
    """Classify a track and enrich its artist. Runs out-of-band; never fatal.

    Uses the search-grounded pipeline (MusicBrainz for artist genres, LLM as
    fallback, grounded classifier for track vibes/themes). When the track has a
    Tidal mapping, also captures Atmos/catalog metadata and derives tags
    (AC10/AC11). Energy/valence are estimated when absent (AC8).

    ``classifier``/``tidal_client`` may be injected so a batch enricher builds
    them once (see :func:`make_enricher`); when omitted they are constructed
    lazily. ``refresh=True`` re-runs enrichment even when fields are already
    populated (bypasses the fill-only-if-null skip guards).
    """
    from tuneshift.enrichment.pipeline import classify_track_grounded
    from tuneshift.sequencer.classifier import TrackClassifier

    if classifier is None:
        classifier = TrackClassifier()
    track = db.get_track(track_id)
    if not track:
        return

    # Enrich artist first (so we have genre context for track classification)
    artist = db.get_artist_by_name(artist_name)
    if artist and (refresh or (not artist.genres and not artist.enriched_at)):
        _enrich_artist_via_llm(db, artist, classifier)
        artist = db.get_artist_by_name(artist_name)  # reload

    # Classify track if missing vibes/themes (using grounded pipeline)
    metadata = track.metadata or {}
    if refresh or (not metadata.get("vibes") and not metadata.get("narrator_stance")):
        artist_genres = artist.genres if artist else []
        result = classify_track_grounded(
            track.title,
            track.artist,
            artist_genres=artist_genres,
            classifier=classifier if classifier.available else None,
        )
        if result:
            metadata.update(result)
            db.set_track_metadata(track_id, metadata, commit=True)
            logger.info("classified track: %s - %s", track.title, track.artist)

    # Atmos / Tidal catalog capture (AC10/AC11): any Tidal-mapped track captures
    # audio-quality + catalog metadata and derives the atmos-available tag,
    # automatically (no manual `enrich --catalog`). Best-effort, non-fatal.
    _capture_tidal_catalog(db, track_id, client=tidal_client, refresh=refresh)

    # Energy/valence (AC8): estimate when absent so the wave sequencer is not
    # ordering blind. Uses the injected classifier for the LLM estimate.
    _ensure_energy_valence(db, track_id, classifier=classifier, refresh=refresh)


def make_enricher(
    *,
    classifier=None,
    tidal_client=None,
):
    """Build the resolution worker's ``enricher`` callback (AC-D7 wiring).

    Constructs the classifier (and reuses the Tidal client already loaded for
    resolution) ONCE, then returns a ``(db, track) -> None`` closure so a batch
    resolve does not re-detect the backend or re-login per track. This is the
    seam FL1 left as ``None``; wiring it here is what makes ``resolve``
    actually enrich (artist genres + classification + Atmos + energy/valence).
    """
    from tuneshift.sequencer.classifier import TrackClassifier

    shared_classifier = classifier if classifier is not None else TrackClassifier()

    def _enrich(db: Database, track) -> None:
        enrich_track(
            db,
            track.id,
            track.artist,
            classifier=shared_classifier,
            tidal_client=tidal_client,
        )

    return _enrich


def _enrich_artist_via_llm(db: Database, artist, classifier) -> None:
    """Enrich artist using MusicBrainz lookup for genres, LLM only as fallback."""
    import musicbrainzngs

    from tuneshift.enrichment.retry import RetryConfig, retry_api_call

    musicbrainzngs.set_useragent(
        "tuneshift", "1.0", "https://github.com/alistardust/dotfiles"
    )

    genres: list[str] = []
    mb_artist_id: str | None = None

    # Retry on 503/network errors. The musicbrainzngs library handles 1 req/s
    # pacing internally; this adds backoff for sustained overload.
    mb_config = RetryConfig(max_retries=3, base_delay=2.0)

    # Try MusicBrainz first (real data, not hallucinated)
    try:
        results = retry_api_call(
            musicbrainzngs.search_artists,
            artist=artist.name,
            limit=3,
            config=mb_config,
        )
        artists_found = results.get("artist-list", [])
        if artists_found:
            best = artists_found[0]
            mb_artist_id = best.get("id")
            # Get tags (genres) from MB
            mb_tags = best.get("tag-list", [])
            genres = [t["name"] for t in mb_tags if int(t.get("count", 0)) > 0]

            # If no tags on search result, fetch full artist for tags
            if not genres and mb_artist_id:
                full = retry_api_call(
                    musicbrainzngs.get_artist_by_id,
                    mb_artist_id,
                    includes=["tags"],
                    config=mb_config,
                )
                mb_tags = full.get("artist", {}).get("tag-list", [])
                genres = [t["name"] for t in mb_tags if int(t.get("count", 0)) > 0]
    except (musicbrainzngs.MusicBrainzError, OSError, KeyError):
        pass

    update_fields: dict = {
        "enrichment_sources": ["musicbrainz"] if genres else [],
        "enriched_at": "auto",
    }

    if genres:
        update_fields["genres"] = genres
    if mb_artist_id:
        update_fields["mb_artist_id"] = mb_artist_id

    # If MusicBrainz didn't return genres, fall back to LLM (less reliable)
    if not genres and classifier and classifier.available:
        prompt = (
            f'What genre(s) is the musical artist "{artist.name}"? '
            f"Return ONLY a JSON array of genre strings. Be specific. "
            f'Example: ["pop", "boyband", "teen pop"] or ["country", "country pop"]. '
            f'If unsure, return ["unknown"].'
        )
        try:
            response = classifier._backend.complete(
                prompt, classifier._model, max_tokens=100
            )
            response = response.strip()
            if response.startswith("```"):
                response = response.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            parsed = json.loads(response)
            if isinstance(parsed, list) and parsed and parsed != ["unknown"]:
                update_fields["genres"] = parsed
                update_fields["enrichment_sources"] = ["llm_fallback"]
        except (json.JSONDecodeError, OSError, RuntimeError, ValueError):
            pass

    db.update_artist(artist.id, **update_fields)


def capture_tidal_catalog(
    db: Database,
    track_id: int,
    platform: str,
    platform_track_id: str | None,
    *,
    client=None,
    refresh: bool = False,
) -> list[str]:
    """Best-effort Atmos/catalog capture right after a Tidal mapping is written.

    The shared post-mapping hook used by the paths that create or update a Tidal
    platform mapping (``map`` / ``ingest`` / ``enrich`` / the resolve worker), so
    the ``atmos-available`` tag is derived automatically (AC4/AC10/AC11) instead
    of only via the manual ``enrich --catalog`` flag. ``doctor --apply`` performs
    the equivalent capture through its own retry-wrapped ``_reenrich_track`` (it
    needs doctor's RetryStats and an unconditional refetch after a remap), so it
    does not call this hook. No-ops for non-Tidal platforms or when no client/id
    is available; never raises. Returns the derived tags (empty on no-op/failure).
    """
    if platform != "tidal" or client is None or not platform_track_id:
        return []
    try:
        from tuneshift.enrichment.platform_metadata import enrich_track_from_tidal

        tags = enrich_track_from_tidal(
            db, track_id, platform_track_id, client=client, refresh=refresh
        )
        if tags:
            logger.info(
                "derived tidal tags for track=%s: %s", track_id, ", ".join(tags)
            )
        return tags
    except Exception:  # noqa: BLE001
        logger.warning(
            "tidal catalog capture failed: track=%s", track_id, exc_info=True
        )
        return []


def _capture_tidal_catalog(
    db: Database, track_id: int, *, client=None, refresh: bool = False
) -> None:
    """Resolve-worker helper: capture catalog for a track's existing Tidal mapping.

    Looks up the mapping written by an earlier path and delegates to the shared
    :func:`capture_tidal_catalog` hook. No-ops when the track has no Tidal
    mapping yet -- capture then happens on the next resolve/mapping pass.
    """
    if client is None:
        return
    mapping = db.get_platform_mapping(track_id, "tidal")
    if not mapping:
        return
    capture_tidal_catalog(
        db, track_id, "tidal", mapping.platform_track_id, client=client, refresh=refresh
    )


def _ensure_energy_valence(
    db: Database, track_id: int, *, classifier=None, refresh: bool = False
) -> None:
    """Populate energy/valence when absent so the wave sequencer isn't blind (AC8).

    Fallback chain (Spotify audio-features -> LLM estimate); manual override lives
    in the edit command. Fill-only-if-null unless ``refresh``. A field whose
    provenance is ``manual`` is NEVER overwritten (even on ``refresh``), and only
    the field(s) that actually need filling are written, so a partially
    hand-edited pair (e.g. energy set, valence null) keeps the manual value and
    its provenance. Best-effort.
    """
    track = db.get_track(track_id)
    if track is None:
        return

    provenance = track.field_provenance or {}

    def _needs(column: str, value) -> bool:
        if provenance.get(column, {}).get("source") == "manual":
            return False  # never clobber a manual value or flip its provenance
        return refresh or value is None

    need_energy = _needs("energy", track.energy)
    need_valence = _needs("valence", track.valence)
    if not (need_energy or need_valence):
        return

    from tuneshift.enrichment.audio_features import (
        estimate_energy_valence,
        spotify_audio_features_via_isrc,
    )

    result = spotify_audio_features_via_isrc(track.isrc)
    if result is None:
        artist = db.get_artist_by_name(track.artist)
        result = estimate_energy_valence(
            track.title,
            track.artist,
            genres=artist.genres if artist else None,
            tempo=track.tempo,
            classifier=classifier,
        )
    if result is None:
        return
    energy, valence = result
    fields: dict[str, float] = {}
    if need_energy:
        fields["energy"] = energy
    if need_valence:
        fields["valence"] = valence
    db.set_track_fields(track_id, fields, source="enrichment")
    logger.info("estimated energy/valence for track=%s: %s", track_id, fields)
