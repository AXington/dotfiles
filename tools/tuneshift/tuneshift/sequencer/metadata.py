"""Track metadata adapters for the playlist sequencer."""

from dataclasses import dataclass, field
from typing import Any

from tuneshift.db import Database
from tuneshift.models import Track

_CAMELOT_MAP: dict[tuple[int, int], str] = {
    (0, 1): "8B",
    (0, 0): "5A",
    (1, 1): "3B",
    (1, 0): "12A",
    (2, 1): "10B",
    (2, 0): "7A",
    (3, 1): "5B",
    (3, 0): "2A",
    (4, 1): "12B",
    (4, 0): "9A",
    (5, 1): "7B",
    (5, 0): "4A",
    (6, 1): "2B",
    (6, 0): "11A",
    (7, 1): "9B",
    (7, 0): "6A",
    (8, 1): "4B",
    (8, 0): "1A",
    (9, 1): "11B",
    (9, 0): "8A",
    (10, 1): "6B",
    (10, 0): "3A",
    (11, 1): "1B",
    (11, 0): "10A",
}


@dataclass
class TrackMetadata:
    """Sequencer-friendly metadata for a database track."""

    track_id: int
    title: str
    artist: str
    isrc: str | None = None
    duration_ms: int | None = None
    bpm: float | None = None
    key_note: int | None = None
    mode: int | None = None
    energy: float | None = None
    valence: float | None = None
    acousticness: float | None = None
    loudness: float | None = None
    danceability: float | None = None
    themes: list[str] = field(default_factory=list)
    vibes: list[str] = field(default_factory=list)
    instruments: list[str] = field(default_factory=list)
    density: str | None = None
    era_mood: list[str] = field(default_factory=list)
    lastfm_tags: list[str] = field(default_factory=list)
    camelot_code: str | None = None
    source: str | None = None
    emotional_intensity: float | None = None
    lyrical_subject: str | None = None
    narrator_stance: str | None = None
    sonic_texture: str | None = None
    space: str | None = None
    groove_feel: str | None = None
    opens_with: str | None = None
    closes_with: str | None = None
    energy_arc_within: str | None = None
    classification_confidence: float | None = None


def isrc_to_camelot(key_note: int | None, mode: int | None) -> str | None:
    """Convert key plus mode values to a Camelot code."""
    if key_note is None or mode is None:
        return None
    return _CAMELOT_MAP.get((key_note, mode))


def _list_value(value: Any) -> list[str]:
    """Normalize a metadata field to a string list."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if item]
    if isinstance(value, str):
        stripped = value.strip()
        return [stripped] if stripped else []
    return [str(value)]


def _float_value(value: Any) -> float | None:
    """Convert a metadata field to float when possible."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_value(value: Any) -> int | None:
    """Convert a metadata field to int when possible."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def track_to_metadata(track: Track) -> TrackMetadata:
    """Adapt a tuneshift Track model to sequencer metadata."""
    metadata = track.metadata or {}
    key_note = _int_value(metadata.get("key_note"))
    mode = _int_value(metadata.get("mode"))
    camelot_code = (
        track.key or metadata.get("camelot_code") or isrc_to_camelot(key_note, mode)
    )
    density = metadata.get("density")
    source = metadata.get("source")

    return TrackMetadata(
        track_id=track.id or 0,
        title=track.title,
        artist=track.artist,
        isrc=track.isrc,
        duration_ms=track.duration_seconds * 1000
        if track.duration_seconds is not None
        else None,
        bpm=track.tempo
        if track.tempo is not None
        else _float_value(metadata.get("tempo") or metadata.get("bpm")),
        key_note=key_note,
        mode=mode,
        energy=track.energy
        if track.energy is not None
        else _float_value(metadata.get("energy")),
        valence=track.valence
        if track.valence is not None
        else _float_value(metadata.get("valence")),
        acousticness=_float_value(metadata.get("acousticness")),
        loudness=_float_value(metadata.get("loudness")),
        danceability=_float_value(metadata.get("danceability")),
        themes=[str(item) for item in track.themes if item],
        vibes=_list_value(metadata.get("vibes")),
        instruments=_list_value(metadata.get("instruments")),
        density=str(density) if density else None,
        era_mood=_list_value(metadata.get("era_mood")),
        lastfm_tags=_list_value(metadata.get("lastfm_tags")),
        camelot_code=str(camelot_code) if camelot_code else None,
        source=str(source) if source else None,
        emotional_intensity=_float_value(metadata.get("emotional_intensity")),
        lyrical_subject=metadata.get("lyrical_subject"),
        narrator_stance=metadata.get("narrator_stance"),
        sonic_texture=metadata.get("sonic_texture"),
        space=metadata.get("space"),
        groove_feel=metadata.get("groove_feel"),
        opens_with=metadata.get("opens_with"),
        closes_with=metadata.get("closes_with"),
        energy_arc_within=metadata.get("energy_arc_within"),
        classification_confidence=_float_value(
            metadata.get("classification_confidence")
        ),
    )


def get_track_metadata(db: Database, track_id: int) -> TrackMetadata | None:
    """Load sequencer metadata for a single database track."""
    track = db.get_track(track_id)
    if track is None:
        return None
    return track_to_metadata(track)


def get_track_metadata_map(
    db: Database,
    track_ids: list[int],
) -> dict[int, TrackMetadata]:
    """Load sequencer metadata for a list of database track IDs."""
    result: dict[int, TrackMetadata] = {}
    for track_id in track_ids:
        metadata = get_track_metadata(db, track_id)
        if metadata is not None:
            result[track_id] = metadata
    return result
