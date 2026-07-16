"""MusicBrainz source for identity resolution."""

from __future__ import annotations

import logging
from typing import Any

import musicbrainzngs
from musicbrainzngs import MusicBrainzError

from tuneshift.identity.matching import (
    duration_matches,
    match_title,
    normalize_artist_for_search,
    normalize_title_for_search,
)
from tuneshift.identity.models import Evidence, RecordingCandidate, SourceResult
from tuneshift.platforms.timeout import PlatformTimeout, call_with_timeout

logger = logging.getLogger(__name__)

musicbrainzngs.set_useragent(
    "TuneShift",
    "0.1.0",
    "https://github.com/alistardust/dotfiles",
)


class MusicBrainzSource:
    """MusicBrainz recording lookup and search."""

    def lookup_isrc(
        self,
        isrc: str,
        duration_ms: int | None = None,
    ) -> SourceResult | None:
        """Look up recordings by ISRC."""
        try:
            response = call_with_timeout(
                lambda: musicbrainzngs.get_recordings_by_isrc(
                    isrc,
                    includes=[
                        "artist-credits",
                        "releases",
                        "work-rels",
                        "work-level-rels",
                    ],
                )
            )
        except (OSError, RuntimeError, MusicBrainzError, PlatformTimeout):
            logger.debug("ISRC lookup failed for %s", isrc)
            return None

        recording_list = response.get("isrc", {}).get("recording-list", [])
        if not recording_list:
            return None

        candidates: list[RecordingCandidate] = []
        for recording in recording_list:
            candidate = self._recording_to_candidate(recording)
            if duration_ms and candidate.duration_ms:
                if not duration_matches(duration_ms, candidate.duration_ms):
                    continue
            candidates.append(candidate)

        if not candidates:
            return None

        evidence = Evidence(
            source="musicbrainz",
            evidence_type="isrc_lookup",
            confidence=0.90 if len(candidates) == 1 else 0.70,
        )
        return SourceResult(recordings=candidates, evidence=evidence)

    def search(
        self,
        artist: str,
        title: str,
        duration_ms: int | None = None,
    ) -> SourceResult:
        """Text search for recordings."""
        normalized_artist = normalize_artist_for_search(artist)
        normalized_title = normalize_title_for_search(title)
        query = f'artist:"{normalized_artist}" AND recording:"{normalized_title}"'

        try:
            response = call_with_timeout(
                lambda: musicbrainzngs.search_recordings(query=query, limit=10)
            )
        except (OSError, RuntimeError, MusicBrainzError, PlatformTimeout):
            logger.debug("MB search failed for %s, %s", artist, title)
            return SourceResult(recordings=[])

        recording_list = response.get("recording-list", [])
        candidates: list[RecordingCandidate] = []
        for recording in recording_list:
            candidate = self._recording_to_candidate(recording)
            if duration_ms and candidate.duration_ms:
                if not duration_matches(duration_ms, candidate.duration_ms):
                    continue
            candidate.score = match_title(title, candidate.title)
            candidates.append(candidate)

        candidates.sort(key=lambda candidate: candidate.score, reverse=True)
        best_confidence = candidates[0].score * 0.85 if candidates else 0.0
        evidence = (
            Evidence(
                source="musicbrainz",
                evidence_type="text_search",
                confidence=best_confidence,
            )
            if candidates
            else None
        )
        return SourceResult(recordings=candidates, evidence=evidence)

    def _recording_to_candidate(self, recording: dict[str, Any]) -> RecordingCandidate:
        """Convert a MusicBrainz recording dict to a RecordingCandidate."""
        artist_name = self._extract_artist_name(recording.get("artist-credit", []))
        duration_ms = self._parse_duration_ms(recording.get("length"))

        release_groups: list[dict[str, str]] = []
        for release in recording.get(
            "release-list", recording.get("release-group-list", [])
        ):
            release_group = release.get("release-group", release)
            if release_group.get("id"):
                release_groups.append(
                    {
                        "id": release_group["id"],
                        "title": release_group.get("title", ""),
                        "type": release_group.get("type", ""),
                    }
                )

        return RecordingCandidate(
            title=recording.get("title", ""),
            artist=artist_name,
            mb_recording_id=recording.get("id"),
            duration_ms=duration_ms,
            release_groups=release_groups,
            language=self._extract_language(recording),
            composer=self._extract_composer(recording),
            mb_work_id=self._extract_work_id(recording),
        )

    @staticmethod
    def _extract_work_id(recording: dict[str, Any]) -> str | None:
        """Pull the MB WORK id (composition identity) from a recording (M2, AC-M2).

        Returns the id of the first related work in ``work-relation-list`` (present
        when ``inc=work-rels`` was requested). The work-id ties covers and
        re-recordings of the same composition together and separates same-titled
        different songs. Returns ``None`` when no work relation is present (parity
        rule §5.1) — MB work coverage is inconsistent, so absence is expected.
        """
        for relation in recording.get("work-relation-list", []):
            work_id = relation.get("work", {}).get("id")
            if work_id:
                return str(work_id)
        return None

    @staticmethod
    def _extract_language(recording: dict[str, Any]) -> str | None:
        """Pull the sung language (ISO 639-3) from a MB recording or its work (M6).

        Prefers the recording-level ``language`` (present when ``inc=language``
        was requested); falls back to the first related work's ``language``.
        Returns ``None`` when neither is present — the criterion then emits no
        verdict rather than fabricating a value (parity rule §5.1).
        """
        language = recording.get("language")
        if language:
            return str(language)
        for relation in recording.get("work-relation-list", []):
            work = relation.get("work", {})
            if work.get("language"):
                return str(work["language"])
        return None

    @staticmethod
    def _extract_composer(recording: dict[str, Any]) -> str | None:
        """Pull composer name(s) from the MB WORK relations (M6, AC-M6).

        Walks the recording's ``work-relation-list`` and, within each work's
        ``artist-relation-list``, collects artists whose relation ``type`` is
        ``composer``. Multiple composers are joined with ``", "`` in first-seen
        order. Returns ``None`` when no composer relation is present (parity rule
        §5.1) — this data is inconsistent in MB, so absence is expected, not a bug.
        """
        composers: list[str] = []
        for relation in recording.get("work-relation-list", []):
            work = relation.get("work", {})
            for artist_relation in work.get("artist-relation-list", []):
                if artist_relation.get("type") != "composer":
                    continue
                name = artist_relation.get("artist", {}).get("name")
                if name and name not in composers:
                    composers.append(name)
        return ", ".join(composers) if composers else None

    def _extract_artist_name(self, artist_credit: list[dict[str, Any]] | str) -> str:
        """Build a display artist name from MusicBrainz artist-credit entries.

        MusicBrainz interleaves plain join-phrase strings between artist dicts
        (and occasionally supplies a bare string), so tolerate both shapes.
        """
        if isinstance(artist_credit, str):
            return artist_credit
        parts: list[str] = []
        for credit in artist_credit:
            if isinstance(credit, str):
                parts.append(credit)
                continue
            artist = credit.get("artist", {})
            if artist.get("name"):
                parts.append(artist["name"])
            elif credit.get("name"):
                parts.append(credit["name"])
            join_phrase = credit.get("joinphrase")
            if join_phrase:
                parts.append(join_phrase)
        artist_name = "".join(parts).strip()
        return artist_name or "Unknown"

    def _parse_duration_ms(self, length_value: Any) -> int | None:
        """Parse a MusicBrainz duration value in milliseconds."""
        if length_value in (None, ""):
            return None
        try:
            return int(length_value)
        except (TypeError, ValueError):
            logger.debug("Invalid MusicBrainz duration: %r", length_value)
            return None
