"""Energy / valence estimation for the wave sequencer (AC8).

The wave-arc sequencer orders tracks by energy and valence. Historically nothing
populated ``tracks.energy`` / ``tracks.valence``, so the sequencer fell back to
tempo alone. This module supplies a real source with a graceful fallback chain:

1. **Spotify audio-features via ISRC** -- the historical "gold standard", but
   Spotify DEPRECATED the ``/audio-features`` endpoint for new app registrations
   on 2024-11-27. :func:`spotify_audio_features_via_isrc` therefore returns
   ``None`` unless a pre-deprecation client is explicitly injected; it never
   fabricates values. This tier is kept as a documented seam, not dead-code
   pretending to work.
2. **LLM numeric estimate** -- :func:`estimate_energy_valence` asks the
   configured classifier backend for a relative 0.0-1.0 energy/valence read from
   genre + tempo + artist + title. This is the de-facto primary source today.
3. **Manual override** -- ``tuneshift edit <id> --energy --valence`` (handled by
   the edit command, outside this module).

All values are floats in ``[0.0, 1.0]``. Estimates are relative, not
psychoacoustically exact; that is sufficient for ordering.
"""

from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)

EnergyValence = tuple[float, float]


def spotify_audio_features_via_isrc(
    isrc: str | None, *, client=None
) -> EnergyValence | None:
    """Return (energy, valence) from Spotify audio-features, or None.

    Spotify deprecated ``/audio-features`` for new apps on 2024-11-27; without an
    explicitly injected pre-deprecation ``client`` exposing ``audio_features``,
    this returns ``None`` rather than fabricating data.
    """
    if not isrc or client is None:
        return None
    getter = getattr(client, "audio_features_by_isrc", None)
    if getter is None:
        return None
    try:
        features = getter(isrc)
    except Exception:  # noqa: BLE001
        logger.warning(
            "spotify audio-features lookup failed for isrc=%s", isrc, exc_info=True
        )
        return None
    if not features:
        return None
    energy = _clamp(features.get("energy"))
    valence = _clamp(features.get("valence"))
    if energy is None or valence is None:
        return None
    return energy, valence


_ESTIMATE_PROMPT = """Estimate the ENERGY and VALENCE of this track on a 0.0-1.0 scale.

Track: "{title}" by {artist}
{context}

Definitions:
- energy: perceived intensity/activity. 0.0 = calm/quiet/slow; 1.0 = fast/loud/intense.
- valence: musical positivity. 0.0 = sad/dark/angry; 1.0 = happy/cheerful/euphoric.

Return ONLY a JSON object, no prose:
{{"energy": 0.0-1.0, "valence": 0.0-1.0}}"""


def estimate_energy_valence(
    title: str,
    artist: str,
    *,
    genres: list[str] | None = None,
    tempo: float | None = None,
    classifier=None,
) -> EnergyValence | None:
    """Estimate (energy, valence) via the LLM classifier, or None if unavailable."""
    if classifier is None or not getattr(classifier, "available", False):
        return None

    context_parts: list[str] = []
    if genres:
        context_parts.append(f"Genres: {', '.join(genres)}")
    if tempo:
        context_parts.append(f"Tempo: {tempo:.0f} BPM")
    context = "\n".join(context_parts)
    prompt = _ESTIMATE_PROMPT.format(title=title, artist=artist, context=context)

    try:
        response = classifier._backend.complete(
            prompt, classifier._model, max_tokens=60
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "energy/valence estimate failed for %s - %s", title, artist, exc_info=True
        )
        return None

    parsed = _parse_energy_valence(response)
    if parsed is None:
        return None
    energy, valence = parsed
    if energy is None or valence is None:
        return None
    return energy, valence


def _parse_energy_valence(response: str) -> tuple[float | None, float | None] | None:
    response = (response or "").strip()
    if response.startswith("```"):
        response = response.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    data: dict | None = None
    try:
        loaded = json.loads(response)
        if isinstance(loaded, dict):
            data = loaded
    except json.JSONDecodeError:
        match = re.search(r"\{[^{}]*\}", response, re.DOTALL)
        if match:
            try:
                loaded = json.loads(match.group())
                if isinstance(loaded, dict):
                    data = loaded
            except json.JSONDecodeError:
                data = None
    if data is None:
        return None
    return _clamp(data.get("energy")), _clamp(data.get("valence"))


def _clamp(value: object) -> float | None:
    """Coerce to a float in [0.0, 1.0], or None if not a finite number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):  # NaN / inf
        return None
    return max(0.0, min(1.0, number))
