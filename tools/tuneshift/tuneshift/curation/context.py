"""Playlist context for curation decisions."""

from dataclasses import dataclass


@dataclass
class PlaylistContext:
    goal: str
    narrative_sections: list[dict]
    mood_profile: dict | None
    all_tracks: list
