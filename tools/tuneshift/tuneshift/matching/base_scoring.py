"""Base scoring criteria — the single, authoritative scoring sequence.

Historically the title/artist/album/isrc/version/duration signal sequence lived
inline inside :func:`~tuneshift.matching.track.score_track_match`. This module
extracts it into an ordered list of *base scoring criteria*, each delegating to
the exact :mod:`tuneshift.matching.penalties` builder, so there is one scoring
sequence rather than two that can silently diverge (``score_track_match`` now
builds its :class:`~tuneshift.matching.engine.Distance` from :func:`score_signals`).

These base criteria are always active — they are the identity/similarity spine,
distinct from the preference-gated criteria in :mod:`tuneshift.matching.criteria`
(which emit *nothing* unless a preference references them, per the AC-C5
winner-parity contract). The ``version`` criterion emits the whole
source-aware version group, which includes the edition and lyric residual
signals.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from tuneshift.matching.normalize import (
    base_title,
    normalize_artist,
    normalize_title,
    strip_album_from_title,
    strip_version_markers,
)
from tuneshift.matching.penalties import (
    DEFAULT_WEIGHTS,
    SignalPenalty,
    Weights,
    album_signal,
    artist_signal,
    duration_signal,
    isrc_signal,
    source_aware_version_signals,
    title_signal,
)

if TYPE_CHECKING:
    from tuneshift.matching.aliases import AliasResolver

# Residual title cost applied when two titles agree only after trailing
# descriptive subtitles are removed (a regional/edition retitle). Large enough
# to keep a gap below an exact-title match so genuinely different songs sharing
# a base title do not silently merge, small enough to rescue a true retitle
# that would otherwise fall below the match threshold.
_SUBTITLE_PENALTY = 10


def _blended_title_signal(
    sim_source: str, sim_result: str, weights: Weights
) -> SignalPenalty:
    """Title signal that rescues trailing-subtitle retitles.

    Scores the (already version-stripped, normalized) titles as-is, then again
    on their base titles (trailing descriptive subtitles removed), and keeps the
    stronger result minus :data:`_SUBTITLE_PENALTY`. When neither title carries a
    trailing subtitle the base leg is a no-op and the full signal is returned
    unchanged, preserving byte-parity for the common case.
    """

    full = title_signal(sim_source, sim_result, weights)
    base_src, base_res = base_title(sim_source), base_title(sim_result)
    if base_src == sim_source and base_res == sim_result:
        return full
    base = title_signal(base_src, base_res, weights)
    points = base.points - _SUBTITLE_PENALTY
    if points <= full.points:
        return full
    budget = weights.title_exact
    penalty = max(0.0, min(1.0, 1.0 - points / budget)) if budget > 0 else 0.0
    return SignalPenalty("title", points, penalty, budget)


@dataclass(frozen=True)
class ScoringContext:
    """Normalized inputs the base criteria compare, prepared once per candidate.

    Mirrors the exact preparation the legacy inline scorer applied: album-name
    parentheticals stripped from titles on both sides; *similarity* titles
    additionally version-marker-stripped; the version axis reads the raw
    (album-stripped, not version-stripped) titles.
    """

    sim_src_title: str
    sim_cand_title: str
    src_artist: str
    cand_artist: str
    album_src_norm: str
    album_cand_norm: str
    src_album_present: bool
    src_isrc: str | None
    cand_isrc: str | None
    raw_src_title: str
    raw_cand_title: str
    raw_src_album: str
    raw_cand_album: str
    raw_src_version: str | None
    raw_cand_version: str | None
    src_explicit: bool | None
    cand_explicit: bool | None
    src_duration: int | None
    cand_duration: int | None
    all_durations: list[int] | None
    prefer: frozenset[str]
    avoid: frozenset[str]
    owned_residuals: frozenset[str]
    alias_resolver: AliasResolver | None


def build_context(
    source: object,
    candidate: object,
    *,
    all_durations: list[int] | None = None,
    prefer: frozenset[str] = frozenset(),
    avoid: frozenset[str] = frozenset(),
    owned_residuals: frozenset[str] = frozenset(),
    alias_resolver: AliasResolver | None = None,
) -> ScoringContext:
    """Prepare a :class:`ScoringContext` from two track-like objects."""

    src_title = getattr(source, "title", "") or ""
    src_album = getattr(source, "album", None)
    cand_title = getattr(candidate, "title", "") or ""
    cand_album = getattr(candidate, "album", None) or ""
    src_title = strip_album_from_title(src_title, src_album)
    cand_title = strip_album_from_title(cand_title, cand_album)
    return ScoringContext(
        sim_src_title=normalize_title(strip_version_markers(src_title)),
        sim_cand_title=normalize_title(strip_version_markers(cand_title)),
        src_artist=normalize_artist(getattr(source, "artist", "") or ""),
        cand_artist=normalize_artist(getattr(candidate, "artist", "") or ""),
        album_src_norm=normalize_title(src_album or ""),
        album_cand_norm=normalize_title(cand_album),
        src_album_present=bool(src_album),
        src_isrc=getattr(source, "isrc", None),
        cand_isrc=getattr(candidate, "isrc", None),
        raw_src_title=src_title,
        raw_cand_title=cand_title,
        raw_src_album=src_album or "",
        raw_cand_album=cand_album,
        raw_src_version=getattr(source, "tidal_version", None),
        raw_cand_version=getattr(candidate, "tidal_version", None),
        src_explicit=getattr(source, "explicit", None),
        cand_explicit=getattr(candidate, "explicit", None),
        src_duration=getattr(source, "duration_seconds", None),
        cand_duration=getattr(candidate, "duration_seconds", None),
        all_durations=all_durations,
        prefer=prefer,
        avoid=avoid,
        owned_residuals=owned_residuals,
        alias_resolver=alias_resolver,
    )


@dataclass(frozen=True)
class ScoringCriterion:
    """A base scoring signal: a name plus a function emitting its penalties."""

    name: str
    emit: Callable[[ScoringContext, Weights], list[SignalPenalty]]


def _title_emit(ctx: ScoringContext, weights: Weights) -> list[SignalPenalty]:
    return [_blended_title_signal(ctx.sim_src_title, ctx.sim_cand_title, weights)]


def _artist_emit(ctx: ScoringContext, weights: Weights) -> list[SignalPenalty]:
    return [
        artist_signal(
            ctx.src_artist, ctx.cand_artist, weights, resolver=ctx.alias_resolver
        )
    ]


def _album_emit(ctx: ScoringContext, weights: Weights) -> list[SignalPenalty]:
    return [
        album_signal(
            ctx.album_src_norm,
            ctx.album_cand_norm,
            weights,
            source_present=ctx.src_album_present,
        )
    ]


def _isrc_emit(ctx: ScoringContext, weights: Weights) -> list[SignalPenalty]:
    return [isrc_signal(ctx.src_isrc, ctx.cand_isrc, weights)]


def _version_emit(ctx: ScoringContext, weights: Weights) -> list[SignalPenalty]:
    return source_aware_version_signals(
        ctx.raw_src_title,
        ctx.raw_src_album,
        ctx.raw_cand_title,
        ctx.raw_cand_album,
        source_version=ctx.raw_src_version,
        cand_version=ctx.raw_cand_version,
        source_explicit=ctx.src_explicit,
        cand_explicit=ctx.cand_explicit,
        prefer=ctx.prefer,
        avoid=ctx.avoid,
        owned=ctx.owned_residuals,
        weights=weights,
    )


def _duration_emit(ctx: ScoringContext, weights: Weights) -> list[SignalPenalty]:
    return [
        duration_signal(ctx.cand_duration, ctx.src_duration, ctx.all_durations, weights)
    ]


_DEFAULT_CRITERIA: tuple[ScoringCriterion, ...] = (
    ScoringCriterion("title", _title_emit),
    ScoringCriterion("artist", _artist_emit),
    ScoringCriterion("album", _album_emit),
    ScoringCriterion("isrc", _isrc_emit),
    ScoringCriterion("version", _version_emit),
    ScoringCriterion("duration", _duration_emit),
)


def default_scoring_criteria() -> list[ScoringCriterion]:
    """The ordered base scoring criteria (the single scoring sequence)."""

    return list(_DEFAULT_CRITERIA)


def score_signals(
    source: object,
    candidate: object,
    *,
    weights: Weights = DEFAULT_WEIGHTS,
    all_durations: list[int] | None = None,
    prefer: frozenset[str] = frozenset(),
    avoid: frozenset[str] = frozenset(),
    owned_residuals: frozenset[str] = frozenset(),
    alias_resolver: AliasResolver | None = None,
) -> list[SignalPenalty]:
    """Produce the full base scoring signal list for one candidate.

    Walking :func:`default_scoring_criteria` in order over a shared
    :class:`ScoringContext`, this reproduces exactly the sequence
    ``score_track_match`` accumulates. It is the single source of scoring truth.
    """

    ctx = build_context(
        source,
        candidate,
        all_durations=all_durations,
        prefer=prefer,
        avoid=avoid,
        owned_residuals=owned_residuals,
        alias_resolver=alias_resolver,
    )
    signals: list[SignalPenalty] = []
    for criterion in _DEFAULT_CRITERIA:
        signals.extend(criterion.emit(ctx, weights))
    return signals


__all__ = [
    "ScoringContext",
    "ScoringCriterion",
    "build_context",
    "default_scoring_criteria",
    "score_signals",
]
