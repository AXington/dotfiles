"""Search-grounded track classification pipeline.

Searches MusicBrainz, Last.fm, and Genius for real data, then asks
the LLM to synthesize that data into our metadata schema. The LLM
never classifies from title alone.
"""

from __future__ import annotations

import json
import logging
import time

logger = logging.getLogger(__name__)


def classify_track_grounded(
    title: str,
    artist: str,
    artist_genres: list[str] | None = None,
    classifier=None,
) -> dict | None:
    """Classify a track using search-grounded data.

    Pipeline:
    1. Last.fm track tags
    2. Genius lyrics (full text)
    3. LLM synthesis with all context

    Each source is independently failable (graceful skip on error).
    Returns the classification dict or None on failure.
    """
    from tuneshift.enrichment.genius import get_lyrics
    from tuneshift.enrichment.genius import is_available as genius_ok
    from tuneshift.enrichment.lastfm import get_track_tags
    from tuneshift.enrichment.lastfm import is_available as lastfm_ok

    context_parts: list[str] = []

    # Artist genre context
    if artist_genres:
        context_parts.append(f"Artist genres: {', '.join(artist_genres)}")

    # Last.fm track tags (graceful failure)
    track_tags: list[str] = []
    if lastfm_ok():
        try:
            track_tags = get_track_tags(title, artist)
            if track_tags:
                context_parts.append(f"Last.fm tags: {', '.join(track_tags)}")
        except (OSError, ValueError):
            pass
        time.sleep(0.2)

    # Genius lyrics (graceful failure)
    lyrics: str | None = None
    if genius_ok():
        try:
            lyrics = get_lyrics(title, artist)
            if lyrics:
                context_parts.append(f"Lyrics:\n{lyrics}")
        except (OSError, ValueError):
            pass

    # If no search results at all, still provide artist context
    if not context_parts and not artist_genres:
        context_parts.append("(No external data found; classify conservatively)")

    # LLM synthesis
    if classifier is None or not classifier.available:
        return None

    context = "\n".join(context_parts)
    prompt = _build_synthesis_prompt(title, artist, context)

    try:
        response = classifier._backend.complete(
            prompt, classifier._model, max_tokens=400
        )
        return _parse_response(response)
    except (OSError, RuntimeError, ValueError) as exc:
        logger.warning("Classification failed for %s - %s: %s", title, artist, exc)
        return None


def classify_batch_grounded(
    tracks: list[dict],
    artist_genres_map: dict[str, list[str]] | None = None,
    classifier=None,
    progress_callback=None,
) -> list[dict | None]:
    """Classify a batch of tracks with search grounding.

    Each track is classified individually (search per track).
    Respects rate limits between API calls. Prints per-track progress.
    """
    import sys
    import time

    results: list[dict | None] = []
    genres_map = artist_genres_map or {}

    for i, track in enumerate(tracks):
        title = track["title"]
        artist = track["artist"]
        genres = genres_map.get(artist, [])

        print(
            f"  [{i + 1}/{len(tracks)}] {title} - {artist}...",
            end="",
            flush=True,
            file=sys.stderr,
        )

        result = classify_track_grounded(
            title,
            artist,
            artist_genres=genres,
            classifier=classifier,
        )
        results.append(result)

        status = "ok" if result else "skip"
        print(f" {status}", file=sys.stderr)

        if progress_callback and (i + 1) % 5 == 0:
            progress_callback(i + 1, len(tracks))

        # Rate limit: be gentle with APIs
        time.sleep(0.3)

    return results


_SYNTHESIS_PROMPT = """Classify this track using the provided context. Return ONLY a JSON object.

Track: "{title}" by {artist}

Context (from search results):
{context}

Return JSON with these fields:
{{
  "title": "{title}",
  "artist": "{artist}",
  "vibes": ["vibe1", "vibe2", "vibe3"],
  "themes": ["theme1", "theme2"],
  "emotional_intensity": 0.0-1.0,
  "lyrical_subject": "brief 3-5 word summary",
  "narrator_stance": "one of: defiant, vulnerable, observational, celebratory, resigned, nostalgic, bitter, triumphant, playful, inviting, peaceful",
  "sonic_texture": "one of: raw, polished, warm, cold, lush, sparse, gritty, ethereal",
  "groove_feel": "one of: driving, floating, stomping, swaying, pulsing, static",
  "energy_arc_within": "one of: steady, builds to peak, opens hot and fades, roller coaster, slow burn"
}}

IMPORTANT: Base your classification on the context data above (tags, lyrics, genres).
Do NOT guess or hallucinate. If the tags say "upbeat" and "happy", the vibes should
reflect that. If lyrics are provided, use them to determine lyrical_subject and
narrator_stance."""


def _build_synthesis_prompt(title: str, artist: str, context: str) -> str:
    return _SYNTHESIS_PROMPT.format(title=title, artist=artist, context=context)


def _parse_response(response: str) -> dict | None:
    """Parse LLM JSON response, handling common formatting issues."""
    response = response.strip()
    if response.startswith("```"):
        response = response.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    try:
        data = json.loads(response)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        # Try to find JSON in the response
        import re

        match = re.search(r"\{[^{}]*\}", response, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
    return None
